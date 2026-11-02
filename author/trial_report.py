"""Turn a trial run with a written Logbook into what its author reads before rewriting it.

  python trial_report.py trace.jsonl report.txt

trace.jsonl has one line per decision of the trial run, in order (adapter.py writes it):

  {"time": "day 3, 08:05",
   "brief": "<the text the Logbook showed before this decision>",
   "action": <the action the agent chose, as a JSON object, or its raw reply when it is not one>,
   "result": "<the text that action returned>"}

"result" is null for the last decision when the run ended before it came back.

For each decision the report gives the lines that were new in the Logbook's text (a line where only
numbers changed does not count as new), the action and its result. Results are cut so that the
report stays under 250,000 characters. Then pass the report to author.py with --revise-from.
"""
import json
import re
import sys
from pathlib import Path

LIMIT = 250_000


def _shape(line: str) -> str:
    return re.sub(r"\d+(\.\d+)?", "#", line.strip())


def report(steps: list[dict]) -> tuple[str, int]:
    """The report text, and the length at which results were cut."""
    blocks, prev = [], set()
    for s in steps:
        brief = [line for line in (s.get("brief") or "").splitlines() if line.strip()]
        new = [line.strip() for line in brief if _shape(line) not in prev]
        prev = {_shape(line) for line in brief}
        act = s.get("action")
        lines = [f"[{s.get('time') or '?'}]"]
        if new:
            lines.append("NEW IN LOGBOOK: " + " | ".join(new))
        lines.append("ACTION: " + (json.dumps(act, ensure_ascii=False) if isinstance(act, dict) else str(act or "")[:200]))
        res = s.get("result")
        lines.append("RESULT: " + ("(end of the trial)" if res is None else str(res)))
        blocks.append(lines)
    cut, text = 800, ""
    for _ in range(4):  # shorten the results until the report fits
        text = "\n\n".join("\n".join(b[:-1] + [b[-1] if len(b[-1]) <= cut else b[-1][:cut] + " [...]"]) for b in blocks)
        if len(text) <= LIMIT:
            break
        cut //= 2
    return text, cut


def main() -> int:
    trace, out = Path(sys.argv[1]), Path(sys.argv[2])
    steps = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()]
    text, cut = report(steps)
    out.write_text(text, encoding="utf-8")
    print(json.dumps({"steps": len(steps), "chars": len(text), "result_cut": cut}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
