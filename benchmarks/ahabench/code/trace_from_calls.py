"""Turn an AhaBench run with a written Register into the trace that author/trial_report.py reads.

  python trace_from_calls.py runs/trial/opusGP1_seed201 trace.jsonl

Reads <run>.calls.jsonl (every model call, written by register_provider.py), and <run>.timeseries.csv
and <run>.json (written by aha_run_ckpt.py). Writes one line per decision: the time, the Register's
text the agent saw, the action it chose and the result it got. Prints how the run ended, as the
sentence passed to author.py with --trial-outcome.
"""
import csv
import json
import re
import sys
from pathlib import Path

PLANNER = re.compile(r"PLANNER NOTE\n(.*?)\n\s*\n\s*LAST TOOL RESULT", re.S)
LAST_RESULT = re.compile(r"LAST TOOL RESULT\s*\n(.*?)\n\s*\n\s*REMINDER:", re.S)
DAY = re.compile(r"CURRENT TIME\s*\n\s*(\S+) \(day (\d+)\)")
DEFAULT = "- Keep the machine stocked, protect bank liquidity, and use explicit supplier evidence instead of assumptions."


def first_json(text: str):
    dec = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            return dec.raw_decode(text[i:])[0]
        except Exception:  # noqa: BLE001
            i = text.find("{", i + 1)
    return None


def main() -> int:
    run, out = sys.argv[1], Path(sys.argv[2])
    calls = [json.loads(line) for line in open(f"{run}.calls.jsonl", encoding="utf-8") if line.strip()]
    by_step: dict = {}
    for c in calls:  # a decision can take several attempts; they share one prompt
        by_step.setdefault(c["step"], []).append(c)
    decisions = [by_step[k] for k in sorted(by_step)]
    rows = []
    for i, rs in enumerate(decisions):
        prompt, reply = rs[0]["prompt"], rs[-1]["reply"] or ""
        m, d = PLANNER.search(prompt), DAY.search(prompt)
        brief = "\n".join(line for line in (m.group(1).splitlines() if m else []) if line.strip() != DEFAULT)
        act = first_json(reply)
        nxt = LAST_RESULT.search(decisions[i + 1][0]["prompt"]) if i + 1 < len(decisions) else None
        rows.append({"time": f"day {d.group(2)}, {d.group(1)[11:16]}" if d else None, "brief": brief,
                     "action": act if isinstance(act, dict) else reply,
                     "result": nxt.group(1).strip() if nxt else None})
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    ts = list(csv.DictReader(open(f"{run}.timeseries.csv")))
    summary = Path(f"{run}.json")
    term = json.loads(summary.read_text()).get("termination_reason", "") if summary.exists() else ""
    last_day, net_worth = int(ts[-1]["day"]), float(ts[-1]["net_worth"])
    ended = term and not term.startswith("stopped_after_day")
    outcome = f"The trial run stopped on day {last_day}: {term}. " if ended else f"The trial ran for {last_day - 1} days. "
    outcome += f"Its net worth at the end was ${net_worth:.2f}; it started with $500.00."
    print(json.dumps({"outcome": outcome, "decisions": len(rows)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
