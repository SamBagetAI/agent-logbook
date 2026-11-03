"""Turn a trial run with a written Logbook into what its author reads before rewriting it.

  python trial_report.py trace.jsonl report.txt [--seen]

trace.jsonl has one line per decision of the run, in order (adapter.py writes it):

  {"time": "day 3, 08:05",
   "seen": "<what the agent received at this decision>",         (with trace_seen=True)
   "brief": "<the text the Logbook showed before this decision>",
   "action": <the action the agent chose, as a JSON object, or its raw reply when it is not one>,
   "result": "<the text that action returned>"}

"result" is null for the last decision when the run ended before it came back.

For each decision the report gives the lines that were new in the Logbook's text (a line where only
numbers changed does not count as new), the action and its result. With --seen, also the lines that
were new in what the agent received, and at the end the full prompt of the last decision: a mid-run
rewrite needs it (rewrite.py), since the author must see what the agent saw. Results and prompt
lines are cut so that the report stays under 250,000 characters. Then pass the report to author.py
with --revise-from.
"""
import json
import re
import sys
from pathlib import Path

LIMIT = 250_000
LAST_PROMPT = 20_000


def _shape(line: str) -> str:
    return re.sub(r"\d+(\.\d+)?", "#", line.strip())


def _new_lines(text, prev: set) -> tuple[list[str], set]:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return [line.strip() for line in lines if _shape(line) not in prev], {_shape(line) for line in lines}


def _cut(line: str, cut: int) -> str:
    if line.startswith(("RESULT: ", "NEW IN PROMPT: ")) and len(line) > cut:
        return line[:cut] + " [...]"
    return line


def report(steps: list[dict], seen: bool = False, end: str = "(end of the trial)") -> tuple[str, int]:
    """The report text, and the length at which results (and prompt lines) were cut."""
    blocks, prev, prev_seen = [], set(), set()
    for s in steps:
        new, prev = _new_lines(s.get("brief"), prev)
        lines = [f"[{s.get('time') or '?'}]"]
        if seen:
            new_seen, prev_seen = _new_lines(s.get("seen"), prev_seen)
            if new_seen:
                lines.append("NEW IN PROMPT: " + " | ".join(new_seen))
        if new:
            lines.append("NEW IN LOGBOOK: " + " | ".join(new))
        act, res = s.get("action"), s.get("result")
        if act is None and res is None:
            lines.append("ACTION: (not taken yet)")
        else:
            lines.append("ACTION: " + (json.dumps(act, ensure_ascii=False) if isinstance(act, dict) else str(act or "")[:200]))
        lines.append("RESULT: " + (end if res is None else str(res)))
        blocks.append(lines)
    tail = ""
    if seen and steps:
        last = steps[-1].get("seen") or ""
        tail = "\n\nThe full prompt at the last decision:\n" + (last if len(last) <= LAST_PROMPT else last[:LAST_PROMPT] + " [...]")
    cut, text = 800, ""
    for _ in range(4):  # shorten the results until the report fits
        text = "\n\n".join("\n".join(_cut(line, cut) for line in b) for b in blocks) + tail
        if len(text) <= LIMIT:
            break
        cut //= 2
    return text, cut


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    trace, out = Path(args[0]), Path(args[1])
    steps = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()]
    text, cut = report(steps, seen="--seen" in sys.argv)
    out.write_text(text, encoding="utf-8")
    print(json.dumps({"steps": len(steps), "chars": len(text), "result_cut": cut}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
