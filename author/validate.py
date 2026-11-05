"""Run a written Register on the steps its author has seen, and print one JSON line.

  python validate.py my_register.py spec.json start|past|<steps file> [max_chars]

start: the spec's example step only. past: every step of the spec's past runs
(`past_runs_steps_file`, a JSON list of {"action", "result", "seen"}, optionally .gz). A path:
that steps file, in the same format, e.g. the run so far of a mid-run rewrite (rewrite.py).
"""
import gzip
import importlib.util
import json
import pickle
import sys
import time
import traceback
from pathlib import Path

path, spec_path, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
max_chars = int(sys.argv[4]) if len(sys.argv) > 4 else 4000
spec = json.loads(spec_path.read_text())
base = spec_path.parent
res = {"ok": False}
try:
    m = importlib.util.spec_from_file_location("written_register", path)
    mod = importlib.util.module_from_spec(m)
    sys.modules["written_register"] = mod
    m.loader.exec_module(mod)
    book = mod.Register()
    if mode == "start":
        steps = [{"action": None, "result": "", "seen": (base / spec["example_step_file"]).read_text()}]
    else:
        f = base / spec["past_runs_steps_file"] if mode == "past" else Path(mode)
        steps = json.loads(gzip.decompress(f.read_bytes()) if f.suffix == ".gz" else f.read_text())
    errors, first_error, longest, slowest = 0, None, 0, 0.0
    for step in steps:
        for fn, args in ((book.observe, (step,)), (book.brief, ())):
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
            slowest = max(slowest, time.perf_counter() - t)
    pickle.loads(pickle.dumps(book))
    res = {"ok": errors == 0 and longest <= max_chars and slowest < 1.0, "steps": len(steps), "errors": errors,
           "first_error": first_error, "longest_brief": longest, "slowest_call_s": round(slowest, 3),
           "sample_brief": book.brief()[:max_chars]}
    if longest > max_chars:
        res["problem"] = f"brief() returned {longest} characters; the limit is {max_chars:,}"
except Exception:  # noqa: BLE001
    res = {"ok": False, "fatal": traceback.format_exc()[-3000:]}
print(json.dumps(res))
