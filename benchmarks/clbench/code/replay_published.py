"""Replay published CL-Bench traces through a written Register, before any run: errors, slowest step, longest text.
Nothing goes back to the author.

  CLBENCH_REPO=/path/to/continual-learning-bench python replay_published.py exploitable_poker [run name]
"""
import gzip, json, os, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "register_icl"))
from adapter import RegisterAdapter  # noqa: E402
REPO = Path(os.environ.get("CLBENCH_REPO", "continual-learning-bench")).resolve()
task, run = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "icl-claude-sonnet-4.6"
d = json.load(gzip.open(REPO / "final_results" / "runs" / run / "tasks" / f"{task}.json.gz"))
book_path = HERE.parent / "written_registers" / f"{task}.py"
worst, total_steps, errors, longest = 0.0, 0, 0, 0
samples = {}
for rt in d["run_traces"]:
    inter = rt["trace"]["interactions"]
    book = RegisterAdapter(book_path, cap=4200)
    last_action, last_result = None, ""
    for k, it in enumerate(inter):
        q, r, o = it["query"], it["response"], it["observation"]
        t0 = time.perf_counter()
        text = book.step(last_action, last_result, q["prompt"])
        worst = max(worst, time.perf_counter() - t0)
        longest = max(longest, len(text))
        total_steps += 1
        if rt["run_index"] == d["run_traces"][0]["run_index"] and k in (len(inter) // 3, len(inter) - 1):
            samples[k] = text
        last_action = {"tool": q["response_schema"], "arguments": r["action"]}
        last_result = o["content"]
    errors += book.errors
print(json.dumps({"task": task, "rollouts": len(d["run_traces"]), "steps": total_steps, "errors": errors,
                  "slowest_step_s": round(worst, 4), "longest_text": longest}))
for k, t in samples.items():
    print(f"\n----- run 0, step {k} -----\n{t}")
