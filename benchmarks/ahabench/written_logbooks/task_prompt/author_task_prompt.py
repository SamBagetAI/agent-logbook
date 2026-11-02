"""The first test: a model writes the Logbook for Aha-Vending, from a prompt written for this task.

Condition A: the author sees what the agent is told (its system prompt) and its first prompt.
Condition B: the same, plus the first 60 days of an earlier run without a Logbook (seed 200).
The generic author that replaced it is in author/ at the root of the repo.

  export ANTHROPIC_API_KEY=...            # or ANTHROPIC_KEY_FILE=/path/to/key
  python author_task_prompt.py --author claude-opus-5-5 --cond B --sample 1

Writes <out>/<name>.py (the Logbook as written, after at most two repair rounds) and
<out>/<name>.json (every round's validation, tokens and cost). The inputs are the files of
author/examples/ahabench, which are the ones this test used.
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
INPUTS = HERE.parents[3] / "author" / "examples" / "ahabench"
PAST_RUN_DAYS, PAST_RUN_NET_WORTH = 60, 118.96  # how the earlier run (seed 200, no Logbook) stood after 60 days
PRICE = {"claude-opus-5-5": (4.0, 20.0), "claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0)}

AUTHOR_SYSTEM = """You write a Logbook for an AI agent.

The agent is a language model that runs a business in a simulation. It works in a loop: at every decision it receives one prompt describing the current situation, and it answers with one tool call. It has no memory of its own: each prompt shows the current state and only its last 8 actions. Anything older is gone.

A Logbook is a small piece of Python code that runs next to the agent, outside the model. At every decision, before the model is called, the Logbook:
1. reads the prompt the agent is about to receive (the full text) and the agent's previous action, and updates its own record, which it keeps for the whole run;
2. returns a short text block, which is inserted into the agent's prompt, in the PLANNER NOTE section.

Your job: write the Logbook that will help this agent most over the whole run.

Interface (exactly):

    class Logbook:
        def __init__(self) -> None: ...
        def observe(self, prompt: str, last_action: dict | None) -> None:
            # Called once per decision. `prompt` is the full text the agent is about to receive.
            # `last_action` is the agent's previous action, for example
            # {"tool_name": "send_email", "arguments": {...}}, or None before the first one.
        def brief(self) -> str:
            # Called right after observe. Returns the text shown to the agent. At most 4,000 characters.

Rules:
- Your only input is the prompt text and the agent's actions. You have no other access to the simulation.
- Show only what this run has produced: what the agent has been shown and what it did. Do not hard-code facts or advice about the environment.
- Python 3 standard library only. No files, network, subprocesses, threads or randomness.
- observe and brief must be fast (well under 100 ms) and must not raise. Catch your own errors.
- The Logbook object must be picklable: no lambdas or local functions stored on it (for example, use dict.setdefault instead of defaultdict(lambda: ...)).

Answer with the complete module in a single ```python block, and nothing else."""

PROMPT_NOTE = """Later prompts have the same sections, filled with the current state. LAST TOOL RESULT holds the full output of the previous action. RECENT DECISION HISTORY lists the last 8 actions with their results, cut to 260 characters each. The PLANNER NOTE section is where your Logbook's text will appear."""


def api_key() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"].strip()
    path = os.environ.get("ANTHROPIC_KEY_FILE")
    if path and Path(path).exists():
        return Path(path).read_text().strip()
    raise SystemExit("Set ANTHROPIC_API_KEY (or ANTHROPIC_KEY_FILE) before running.")


def user_message(cond: str) -> str:
    parts = ["Here is everything the agent is told about its task (its system prompt):",
             "<system_prompt>\n" + (INPUTS / "instructions.txt").read_text() + "\n</system_prompt>",
             "Here is the prompt the agent receives at its first decision, on day 1. " + PROMPT_NOTE,
             "<first_prompt>\n" + (INPUTS / "example_step.txt").read_text() + "\n</first_prompt>"]
    if cond == "B":
        meta = {"days": PAST_RUN_DAYS, "net_worth": PAST_RUN_NET_WORTH}
        parts += [f"Here are the first {meta['days']} simulated days of an earlier run of the same agent on the same "
                  f"task, with no Logbook (a different random seed, so the details differ). For each decision: the "
                  f"time, the action the agent chose, and the result it received (long results are cut at 2,500 "
                  f"characters). After {meta['days']} days its net worth was ${meta['net_worth']:.2f}: bank balance, "
                  f"plus cash in the machine, plus unsold inventory at cost. It started with $500.00.",
                  "<earlier_run>\n" + (INPUTS / "past_run.txt").read_text() + "\n</earlier_run>"]
    parts.append("Write the Logbook.")
    return "\n\n".join(parts)


def call(model: str, messages: list[dict], key: str) -> tuple[str, dict]:
    body = {"model": model, "max_tokens": 16000, "system": AUTHOR_SYSTEM, "messages": messages}
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


def extract(text: str) -> str | None:
    m = re.findall(r"```python\s*\n(.*?)```", text, re.S)
    return max(m, key=len) if m else None


def validate(path: Path, cond: str) -> dict:
    """Run the Logbook in a separate process on the inputs the author has seen."""
    r = subprocess.run([sys.executable, "-I", str(HERE / "validate_task_prompt.py"), str(path), cond],
                       capture_output=True, text=True, timeout=600)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return {"ok": False, "fatal": (r.stdout + r.stderr)[-3000:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--author", required=True)
    ap.add_argument("--cond", required=True, choices=["A", "B"])
    ap.add_argument("--sample", type=int, required=True)
    ap.add_argument("--out", type=Path, default=Path("written"), help="folder for the written Logbooks")
    a = ap.parse_args()
    short = {"claude-opus-5-5": "opus", "claude-haiku-5-5": "haiku", "claude-sonnet-5-5": "sonnet"}[a.author]
    name = f"{short}{a.cond}{a.sample}"
    a.out.mkdir(parents=True, exist_ok=True)
    out_py, out_json = a.out / f"{name}.py", a.out / f"{name}.json"
    if out_json.exists():
        print("exists", name)
        return 0
    key = api_key()
    messages = [{"role": "user", "content": user_message(a.cond)}]
    log = {"name": name, "author": a.author, "cond": a.cond, "sample": a.sample, "rounds": []}
    cost = 0.0
    code = None
    for rnd in range(3):  # first draft + at most two repairs
        text, usage = call(a.author, messages, key)
        pin, pout = PRICE[a.author]
        cost += (usage.get("input_tokens", 0) * pin + usage.get("output_tokens", 0) * pout) / 1e6
        new = extract(text)
        entry = {"usage": usage, "reply_chars": len(text)}
        if new is None:
            entry["problem"] = "no python block"
            log["rounds"].append(entry)
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": "Answer with the complete module in a single ```python block."}]
            continue
        code = new
        out_py.write_text(code)
        v = validate(out_py, a.cond)
        entry["validation"] = v
        log["rounds"].append(entry)
        if v.get("ok"):
            break
        feedback = v.get("fatal") or json.dumps({k: v[k] for k in v if k != "ok"})[:3000]
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "Running your Logbook on the inputs above gave this problem:\n\n"
                                                 + feedback + "\n\nFix it. Answer with the complete module in a single ```python block."}]
    log["final_ok"] = bool(log["rounds"] and log["rounds"][-1].get("validation", {}).get("ok"))
    log["cost_usd"] = round(cost, 4)
    log["lines"] = len(code.splitlines()) if code else 0
    out_json.write_text(json.dumps(log, indent=1))
    print(json.dumps({"name": name, "ok": log["final_ok"], "rounds": len(log["rounds"]), "lines": log["lines"],
                      "cost_usd": log["cost_usd"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
