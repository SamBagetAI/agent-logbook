"""Turn a recorded run of your agent into the past-run files of a spec.

  python past_run.py run.jsonl out_dir

run.jsonl has one line per decision, in order:

  {"time": "...", "seen": "<what the agent received>", "action": <the action it chose>,
   "result": "<the text that action returned>"}

adapter.py writes this format with trace_seen=True (with path=None it records without a Logbook).
Writes two files into out_dir:

  past_run.txt            what the author reads: for each decision the time, the action and its
                          result (cut at 2,500 characters), then three steps in full, as the agent
                          received them
  past_run_steps.json.gz  what validate.py replays: the steps as observe() receives them

Then add both to the spec, with a past_runs_intro sentence saying how that run ended.
"""
import gzip
import json
import sys
from pathlib import Path

MAX_RESULT = 2500


def main() -> int:
    run, out = Path(sys.argv[1]), Path(sys.argv[2])
    rows = [json.loads(line) for line in run.read_text(encoding="utf-8").splitlines() if line.strip()]
    lines, steps = [], []
    for i, row in enumerate(rows):
        prev = rows[i - 1] if i else None
        steps.append({"action": prev["action"] if prev else None,
                      "result": (prev.get("result") or "") if prev else "", "seen": row["seen"]})
        act, res = row.get("action"), row.get("result")
        lines.append(f"[{row.get('time') or '?'}] ACTION: "
                     + (json.dumps(act, ensure_ascii=False) if isinstance(act, dict) else str(act or "")[:300]))
        body = "(no result: end of the run)" if res is None else str(res).strip()
        lines.append("RESULT:\n" + (body if len(body) <= MAX_RESULT else body[:MAX_RESULT] + " [...]"))
        lines.append("")
    n = len(rows)
    full = [f"--- Full prompt at decision {k + 1} of {n} ---\n{rows[k]['seen']}" for k in sorted({n // 4, n // 2, n - 1})]
    text = ("\n".join(lines) + "\n\nThree full prompts from this run, to show how the state looks later:\n\n"
            + "\n\n".join(full))
    out.mkdir(parents=True, exist_ok=True)
    (out / "past_run.txt").write_text(text, encoding="utf-8")
    (out / "past_run_steps.json.gz").write_bytes(gzip.compress(json.dumps(steps, ensure_ascii=False).encode(), mtime=0))
    print(json.dumps({"decisions": n, "past_run_chars": len(text)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
