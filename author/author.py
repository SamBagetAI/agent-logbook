r"""Have a model write the Logbook for any agent, from a spec of that agent.

The prompt is generic: nothing in it is specific to one benchmark. Everything specific comes from
the spec (a JSON file): the agent's goal, its instructions, its tools, what it keeps in view, one
example step, and optionally the files of its harness and past runs. See README.md for the spec format and the interface the
written Logbook implements.

  export ANTHROPIC_API_KEY=...            # or ANTHROPIC_KEY_FILE=/path/to/key
  python author.py examples/ahabench/spec.json --past-runs --name my_logbook

Writes <out>/<name>.py (the Logbook, after at most two repair rounds) and <out>/<name>.json
(the prompt version, every round's validation, tokens and cost).

Rewrite a Logbook after a trial run (see trial_report.py):

  python author.py spec.json --past-runs --revise-from my_logbook --trial-report report.txt \
      --trial-outcome "The trial ran for 60 days. ..." --name my_logbook_2

Rewrite it in the middle of a run, from the run so far (rewrite.py builds the report and the steps):

  python author.py spec.json --revise-from written/my_logbook.py --trial-report so_far/report.txt --same-run \
      --steps-file so_far/steps.json.gz --name my_logbook_2
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
PRICE = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}  # $/M tokens

SYSTEM = """You design a Logbook for an AI agent.

The agent works in a loop. At each step it receives what its environment shows it, then it acts, usually by calling a tool, and the action returns a result. The agent keeps only part of this in view from one step to the next (see <view>). What leaves its view is lost to it.

A Logbook is code that runs next to the agent, outside the model. Before every decision, it reads what happened since the previous one, updates a record that it keeps for the whole run, and returns a short text that is shown to the agent.

Your job: from the agent's goal, instructions and tools, decide what this Logbook should record and show so that the agent reaches its goal. Then write it.

Interface (exactly):

    class Logbook:
        def __init__(self) -> None: ...

        def observe(self, step: dict) -> None:
            # Called before every decision, with what happened since the previous one:
            #   step["action"]: the agent's previous action, e.g. {"tool": "send_email", "arguments": {...}},
            #                   or None at the start
            #   step["result"]: the text that action returned, or "" at the start
            #   step["seen"]:   everything the agent receives now, before its next decision

        def brief(self) -> str:
            # Called right after observe. The text shown to the agent, at most {max_chars} characters.

Rules:
- Use only what the agent was shown and what it did. You have no other access to its environment.
- {lessons_rule}
- Python 3 standard library only. No files, network, subprocesses, threads or randomness.
- observe and brief must be fast (well under 100 ms) and must never raise.
- The Logbook object must be picklable: no lambdas or local functions stored on it.

Answer with the complete module in a single ```python block, and nothing else."""

# Version 2 (the default) adds these design principles and shows past runs as evidence, not rules.
DESIGN = """How to design it:
- Record outcomes. For each thing the agent tries toward its goal, record whether it worked and, if not, the exact reason it was given. Group them by the counterpart or object involved.
- Show cause and effect from this run: which attempts worked, which failed, and what differed between them.
- Prefer facts to instructions. The agent acts more readily on evidence from its own run than on advice.
- When the agent's instructions make it wary of something, show the evidence from this run that separates the safe cases from the unsafe ones.
- Put what blocks the goal first. Keep the same sections, in the same order, at every step. Short beats complete.

"""
# Version 3 gives every fact its count and forbids advice outright. Not tested: every published result used version 2.
DESIGN_V3 = DESIGN.replace(
    "Group them by the counterpart or object involved.\n",
    "Group them by the counterpart or object involved.\n"
    "- Give every fact its count: how many attempts, and how many of each outcome (\"sent 10: accepted 8, refused 2\"), "
    "so the agent can weigh it.\n",
).replace("than on advice.\n", "than on advice. Show the evidence; never tell the agent what to do.\n")
assert DESIGN_V3.count("\n- ") == DESIGN.count("\n- ") + 1

RECORD_ONLY = "Show only what this run has produced. Do not hard-code facts or advice about the environment."
WITH_PAST_V1 = ("Show what this run has produced. You may also turn what happened in <past_runs> into rules, but "
                "show a rule only when the current run reaches the same situation, and say what in this run triggered it.")
WITH_PAST_V2 = ("Show what this run has produced. You may also use <past_runs>: show what happened there as evidence "
                "(\"in an earlier run: ...\"), only when the current run reaches the same situation, next to what this run shows.")

HARNESS = ("The agent's side of its loop, as code and files: what builds each prompt, what is kept and cut between "
           "steps, and what the agent may load during the run (skills, memory, notes). It is shown so that you know "
           "exactly what the agent sees and keeps; the environment's side is not shown.")

REPAIR = ("Running your Logbook on the inputs above gave this problem:\n\n{problem}\n\n"
          "Fix it. Answer with the complete module in a single ```python block.")
REVISE = """Here is a trial run of the agent using your Logbook, on a different random seed. For each step: the lines that were new in your Logbook's text at that step (if any), the action the agent chose, and the result it received (long results are cut). {outcome}

<trial_run>
{report}
</trial_run>

Find where the agent ignored or misread what your Logbook showed, where it acted on it, and what it lacked. Then rewrite the Logbook. Answer with the complete module in a single ```python block."""
REVISE_SAME_RUN = """Here is the current run of the agent with your Logbook, from its first decision to now. For each decision: the lines that were new in what the agent received (its prompt), the lines that were new in your Logbook's text, the action the agent chose and the result it received (long texts are cut); then the full prompt of the last decision. The run goes on from here: the Logbook you write now replays all these decisions, then takes over. {outcome}

<run_so_far>
{report}
</run_so_far>

Find what your Logbook misses: a counterpart, a field or a change in what the agent receives that its record does not separate; what the agent ignored or misread in its text; what it lacked. Then rewrite the Logbook. Count what these decisions show; do not hard-code it: the rewritten Logbook must work as well on the decisions that follow, which you have not seen. Answer with the complete module in a single ```python block."""


def build(spec: dict, past_runs: bool, max_chars: int = 4000, version: int = 2) -> tuple[str, str]:
    """The author's system prompt and first message, filled from an agent spec."""
    base = Path(spec["_dir"])
    read = lambda key: (base / spec[key]).read_text() if spec.get(key) else ""  # noqa: E731
    if version >= 2:
        rule = WITH_PAST_V2 if past_runs else RECORD_ONLY
        system = SYSTEM.replace("Then write it.\n\n", "Then write it.\n\n" + (DESIGN_V3 if version == 3 else DESIGN))
    else:
        rule = WITH_PAST_V1 if past_runs else RECORD_ONLY
        system = SYSTEM
    system = system.replace("{max_chars}", f"{max_chars:,}").replace("{lessons_rule}", rule)
    parts = [f"<goal>\n{spec['goal']}\n</goal>",
             f"<instructions>\n{read('instructions_file')}\n</instructions>",
             f"<tools>\n{read('tools_file')}\n</tools>",
             f"<view>\n{spec['view']}\n</view>",
             f"<example_step>\n{read('example_step_file')}\n</example_step>"]
    if spec.get("harness_files"):  # optional: the loop's own code, and the files the agent can load during a run
        shown = [f"--- {h['file']}: {h['what']} ---\n{(base / h['file']).read_text()}" for h in spec["harness_files"]]
        parts.insert(4, f"<harness>\n{HARNESS}\n\n" + "\n\n".join(shown) + "\n</harness>")
    if past_runs:
        parts.append(f"<past_runs>\n{spec['past_runs_intro']}\n\n{read('past_runs_file')}\n</past_runs>")
    parts.append("Write the Logbook.")
    return system, "\n\n".join(parts)


def api_key(key_file: Path | None) -> str:
    if key_file:
        return key_file.read_text().strip()
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"].strip()
    path = os.environ.get("ANTHROPIC_KEY_FILE")
    if path and Path(path).exists():
        return Path(path).read_text().strip()
    raise SystemExit("Set ANTHROPIC_API_KEY (or ANTHROPIC_KEY_FILE) before running.")


def call(model: str, system: str, messages: list[dict], key: str, max_tokens: int = 16000,
         effort: str | None = None, thinking: str | None = None) -> tuple[str, dict]:
    body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
    if effort:
        body["output_config"] = {"effort": effort}
    if thinking:
        body["thinking"] = {"type": thinking}
    last = None
    for attempt in range(6):
        try:
            r = httpx.post("https://api.anthropic.com/v1/messages", json=body, timeout=900,
                           headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
            if r.status_code in (429, 500, 502, 503, 529):
                last = f"HTTP {r.status_code}"
                time.sleep(min(60, 5 * 2 ** attempt))
                continue
            r.raise_for_status()
            d = r.json()
            return "".join(b.get("text", "") for b in d["content"] if b.get("type") == "text"), d.get("usage", {})
        except httpx.HTTPError as exc:
            last = str(exc)
            time.sleep(min(60, 5 * 2 ** attempt))
    raise RuntimeError(f"author call failed: {last}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", type=Path, help="the agent spec (JSON)")
    ap.add_argument("--name", required=True, help="name of the Logbook to write")
    ap.add_argument("--author", default="claude-opus-5-5", help="the model that writes the Logbook")
    ap.add_argument("--past-runs", action="store_true", help="show the author the spec's past runs")
    ap.add_argument("--prompt-version", type=int, default=2, choices=[1, 2, 3],
                    help="2: with design principles (default); 3: version 2 plus a count on every fact (untested)")
    ap.add_argument("--max-chars", type=int, default=4000, help="longest text the Logbook may show")
    ap.add_argument("--out", type=Path, default=Path("written"), help="folder for the written Logbooks")
    ap.add_argument("--revise-from", default=None, help="a Logbook in --out to rewrite after a trial run")
    ap.add_argument("--trial-report", type=Path, default=None, help="from trial_report.py")
    ap.add_argument("--trial-outcome", default="", help="one sentence: how the trial ended")
    ap.add_argument("--same-run", action="store_true",
                    help="the trial report is the current run so far: the new Logbook replays it, then takes over")
    ap.add_argument("--steps-file", type=Path, default=None,
                    help="validate the drafts on these steps (JSON list of {action, result, seen}, .gz allowed)")
    ap.add_argument("--max-tokens", type=int, default=16000, help="longest answer, thinking included (16,000 in the AhaBench tests)")
    ap.add_argument("--effort", default=None, choices=["low", "medium", "high", "xhigh", "max"],
                    help="the author's effort (output_config.effort); the API's default when omitted")
    ap.add_argument("--thinking", default=None, choices=["adaptive", "disabled"],
                    help="the author's thinking; omitted by default (adaptive on current models; disabled is refused by Opus 5.5)")
    ap.add_argument("--key-file", type=Path, default=None)
    a = ap.parse_args()
    spec = json.loads(a.spec.read_text())
    spec["_dir"] = str(a.spec.resolve().parent)
    a.out.mkdir(parents=True, exist_ok=True)
    out_py, out_json = a.out / f"{a.name}.py", a.out / f"{a.name}.json"
    if out_json.exists():
        print("exists", a.name)
        return 0
    system, user = build(spec, a.past_runs, a.max_chars, a.prompt_version)
    key = api_key(a.key_file)
    messages, rounds, cost, code = [{"role": "user", "content": user}], [], 0.0, None
    if a.revise_from:  # the author sees its own Logbook, then how the agent behaved with it
        previous = Path(a.revise_from) if a.revise_from.endswith(".py") else a.out / f"{a.revise_from}.py"
        template = REVISE_SAME_RUN if a.same_run else REVISE
        messages += [{"role": "assistant", "content": "```python\n" + previous.read_text() + "\n```"},
                     {"role": "user", "content": template.format(outcome=a.trial_outcome, report=a.trial_report.read_text())}]
    mode = str(a.steps_file.resolve()) if a.steps_file else ("past" if a.past_runs else "start")
    for _ in range(3):  # first draft + at most two repairs
        text, usage = call(a.author, system, messages, key, a.max_tokens, a.effort, a.thinking)
        pin, pout = PRICE.get(a.author, (0.0, 0.0))
        cost += (usage.get("input_tokens", 0) * pin + usage.get("output_tokens", 0) * pout) / 1e6
        blocks = re.findall(r"```python\s*\n(.*?)```", text, re.S)
        if not blocks:
            rounds.append({"usage": usage, "answer": text, "problem": "no python block"})
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": "Answer with the complete module in a single ```python block."}]
            continue
        code = max(blocks, key=len)
        out_py.write_text(code)
        r = subprocess.run([sys.executable, "-I", str(HERE / "validate.py"), str(out_py), str(a.spec.resolve()),
                            mode, str(a.max_chars)], capture_output=True, text=True, timeout=600)
        try:
            v = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            v = {"ok": False, "fatal": (r.stdout + r.stderr)[-3000:]}
        rounds.append({"usage": usage, "answer": text, "validation": v})
        if v.get("ok"):
            break
        problem = v.get("fatal") or json.dumps({k: v[k] for k in v if k not in ("ok", "sample_brief")})[:3000]
        messages += [{"role": "assistant", "content": text}, {"role": "user", "content": REPAIR.format(problem=problem)}]
    log = {"name": a.name, "author": a.author, "effort": a.effort, "thinking": a.thinking, "max_tokens": a.max_tokens,
           "prompt_version": a.prompt_version, "past_runs": a.past_runs,
           "revised_from": a.revise_from, "same_run": a.same_run, "spec": str(a.spec), "rounds": rounds,
           "trial_report_chars": len(a.trial_report.read_text()) if a.trial_report else 0,
           "final_ok": bool(rounds and rounds[-1].get("validation", {}).get("ok")), "cost_usd": round(cost, 4),
           "lines": len(code.splitlines()) if code else 0, "system": system, "user_chars": len(user)}
    out_json.write_text(json.dumps(log, indent=1))
    print(json.dumps({k: log[k] for k in ("name", "final_ok", "lines", "cost_usd")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
