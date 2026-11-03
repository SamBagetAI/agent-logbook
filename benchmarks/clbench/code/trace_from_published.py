"""Turn one rollout of a published run into a trace for author/rewrite.py: every decision as the agent saw it,
with the text the task's written Logbook showed at that decision, rebuilt by replaying the rollout through it.

  python trace_from_published.py exploitable_poker logbook-icl-claude-sonnet-4-6 0 trace.jsonl

Each line: {"time", "seen", "brief", "action", "result"}, the shape author/adapter.py writes with trace_seen=True.
"""
import gzip
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "author"))
from adapter import LogbookAdapter  # noqa: E402

task, run, index, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), Path(sys.argv[4])
d = json.load(gzip.open(HERE.parent / "data" / "runs" / run / "tasks" / f"{task}.json.gz"))
rt = next(r for r in d["run_traces"] if r["run_index"] == index)
out.unlink(missing_ok=True)
book = LogbookAdapter(HERE.parent / "written_logbooks" / f"{task}.py", trace=out, trace_seen=True)
action, result = None, ""
for it in rt["trace"]["interactions"]:
    q, r, o = it["query"], it["response"], it["observation"]
    head = re.search(r"Hand #\d+ - \w+", q["prompt"])
    book.step(action, result, q["prompt"], time=head.group(0) if head else f"instance {q.get('instance_index')}")
    action, result = {"tool": q["response_schema"], "arguments": r["action"]}, o["content"]
book.close(action, result)
print(json.dumps({"task": task, "run": run, "rollout": index, "decisions": len(book.rows), "logbook_errors": book.errors}))
