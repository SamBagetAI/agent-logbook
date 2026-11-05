"""Run a Register written in the first test on the inputs its author has seen; print one JSON line.

  python validate_task_prompt.py opusB1.py A|B

A: the day-1 prompt, twice. B: every decision of the earlier run, as the provider calls it.
"""
import gzip
import importlib.util
import json
import pickle
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
INPUTS = HERE.parents[3] / "author" / "examples" / "ahabench"
path, cond = Path(sys.argv[1]), sys.argv[2]
res = {"ok": False}
try:
    spec = importlib.util.spec_from_file_location("written_register", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["written_register"] = mod
    spec.loader.exec_module(mod)
    book = mod.Register()
    if cond == "A":
        day1 = (INPUTS / "example_step.txt").read_text()
        steps = [(day1, None), (day1, {"tool_name": "ai_web_search", "arguments": {"query": "vending suppliers"}})]
    else:
        steps = [(s["prompt"], s["last_action"])
                 for s in json.loads(gzip.decompress((HERE / "past_run_prompts.json.gz").read_bytes()))]
    errors, first_error, longest, slowest, total = 0, None, 0, 0.0, 0.0
    for prompt, last in steps:
        for fn, args in ((book.observe, (prompt, last)), (book.brief, ())):
            t = time.perf_counter()
            try:
                out = fn(*args)
                if fn == book.brief:
                    if not isinstance(out, str):
                        raise TypeError(f"brief() returned {type(out).__name__}, not str")
                    longest = max(longest, len(out))
            except Exception:  # noqa: BLE001
                errors += 1
                first_error = first_error or traceback.format_exc()[-2000:]
            dt = time.perf_counter() - t
            slowest, total = max(slowest, dt), total + dt
    pickle.loads(pickle.dumps(book))
    res = {"ok": errors == 0 and longest <= 4000 and slowest < 1.0, "decisions": len(steps), "errors": errors,
           "first_error": first_error, "longest_brief": longest, "slowest_call_s": round(slowest, 3),
           "mean_call_ms": round(1000 * total / max(1, 2 * len(steps)), 2), "sample_brief": book.brief()[:4000]}
    if longest > 4000:
        res["problem"] = f"brief() returned {longest} characters; the limit is 4,000"
except Exception:  # noqa: BLE001
    res = {"ok": False, "fatal": traceback.format_exc()[-3000:]}
print(json.dumps(res))
