"""Rebuild every prompt of the runs with a Register written by a model, offline, and compare.

For each logged model call, this feeds the written Register what register_provider.py fed it during
the run (arm auto: the prompt and the previous action; arm auto2: one step, through
author/adapter.py), puts its text in the prompt the way the provider does, and checks that the
result is the prompt the agent received. No API call, no simulator.

  python verify_replay_written.py                  # reads ../data/written_registers_calls.zip
  python verify_replay_written.py --hash-orders 0  # skip the second pass, which takes a few minutes

Some written Registers list items in the order of a Python set. That order follows Python's string
hashing, which changes from one process to the next, so their text can differ between two runs of
the same code on the same inputs. This script fixes the hash order to 0 (PYTHONHASHSEED=0) for the
first pass, then replays the runs that differ under other hash orders (1 to 96 by default).
The counts in ../results/written_registers_replay.txt come from Python 3.13; the runs used 3.12.
Python 3.10 and earlier hash strings differently, so they give other counts for those Registers.
Like verify_replay.py, it loads register_provider.py, which needs httpx.

A full match means the files in ../written_registers are the Registers that ran, that every line they
showed came from what the agent was shown and what it did, and that author/adapter.py feeds a
Register exactly as the AhaBench runs did.

The runs were recorded when the Register was still called the Logbook, a word these Registers also
print. The replay loads each one with that word put back (load_as_recorded below), so a full match
also shows that the word is the only change.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import types
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if os.environ.get("PYTHONHASHSEED") is None or sys.flags.ignore_environment:  # fix the hash order, see above
    os.execve(sys.executable, [sys.executable, str(Path(__file__).resolve())] + sys.argv[1:],
              {**os.environ, "PYTHONHASHSEED": os.environ.get("PYTHONHASHSEED", "0")})

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
WRITTEN = HERE.parent / "written_registers"
spec = importlib.util.spec_from_file_location("register_provider", HERE / "register_provider.py")
P = importlib.util.module_from_spec(spec)
sys.modules["register_provider"] = P
spec.loader.exec_module(P)
sys.path.insert(0, str(REPO / "author"))
import adapter  # noqa: E402
from recorded import recorded  # noqa: E402


def load_as_recorded(path):
    """The Register at path as it ran. The runs were recorded before the rename, so its name, which it also
    prints in its text, is put back first: that swap is the only difference between these files and the code
    that ran (recorded(text) gives the file as it was, byte for byte)."""
    path = Path(path)
    name = "written_register_" + hashlib.sha1(str(path).encode()).hexdigest()[:10]
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(recorded(path.read_text()), str(path), "exec"), module.__dict__)
    return getattr(module, recorded("Register"))()


adapter.load_register = load_as_recorded  # RegisterAdapter loads through it too

LOGS = zipfile.ZipFile(HERE.parent / "data" / "written_registers_calls.zip")
SEP = "\n\n        "  # what separates the PLANNER NOTE block from LAST TOOL RESULT in AhaBench's prompt
# folder of the runs in the zip -> folder of the Registers, and the provider arm
FOLDERS = {"task_prompt": ("task_prompt", "auto"), "generic_v1": ("generic_v1", "auto2"),
           "generic_v2": ("generic_v2", "auto2"), "generic_v2_trial": ("generic_v2", "auto2")}


def original(shown: str):
    """The prompt as AhaBench wrote it, before the provider put the Register's text in it."""
    m = P.PLANNER.search(shown)
    if not m:
        return None
    return shown[:m.start(2)] + P.DEFAULT_PLANNER + SEP + shown[m.end(3) - len("LAST TOOL RESULT"):]


def replay(name: str) -> list[tuple[bool, str, str]]:
    """For every logged call: whether the rebuilt prompt is identical, the block shown, the block rebuilt."""
    folder, label = re.match(r"(.+)/(.+)_seed\d+\.calls\.jsonl$", name).groups()
    books, arm = FOLDERS[folder]
    path = WRITTEN / books / f"{label}.py"
    rows = [json.loads(line) for line in LOGS.read(name).decode("utf-8").splitlines() if line.strip()]
    for mod in [k for k in sys.modules if k.startswith("written_register_")]:
        del sys.modules[mod]  # a fresh module for every run, as in a new process
    if arm == "auto2":
        book = adapter.RegisterAdapter(path, cap=P.AUTO_CAP)
    else:
        book = adapter.load_register(path)
    last, text, out = None, "", []
    for r in rows:
        seen = original(r["prompt"])
        if seen is None:  # no PLANNER NOTE block where the provider puts it
            out.append((False, None, None))
            continue
        if arm == "auto2":
            if r["attempt"] == 1:
                res = P.LAST_RESULT.search(seen)
                action = {"tool": last.get("tool_name"), "arguments": last.get("arguments")} if last else None
                text = book.step(action, res.group(1).strip() if (last and res) else "", seen)
            else:  # a retry of the same decision: brief only, as in the provider
                try:
                    text = str(book.book.brief())[:P.AUTO_CAP]
                except Exception:  # noqa: BLE001
                    pass
        else:
            if r["attempt"] == 1:
                try:
                    book.observe(seen, copy.deepcopy(last))
                except Exception:  # noqa: BLE001
                    pass
            try:
                text = str(book.brief())[:P.AUTO_CAP]
            except Exception:  # noqa: BLE001
                pass
        block = P.DEFAULT_PLANNER + ("\n" + text if text else "")
        m = P.PLANNER.search(r["prompt"])
        shown = r["prompt"][m.start(2):m.end(3) - len("LAST TOOL RESULT") - len(SEP)]
        out.append((P.put_planner(seen, block) == r["prompt"], shown, block))
        act = P.first_json(r["reply"] or "")
        if isinstance(act, dict) and act.get("tool_name"):
            last = act
    return out


def same_in_another_order(a: str, b: str) -> bool:
    """The same characters on each line, lines possibly in another order."""
    if a is None or b is None:
        return False
    canon = lambda block: sorted("".join(sorted(line)) for line in block.split("\n"))  # noqa: E731
    return canon(a) == canon(b)


def under_hash_order(job: tuple[str, int]) -> list[bool]:
    name, k = job
    r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", name], capture_output=True,
                       text=True, env={**os.environ, "PYTHONHASHSEED": str(k)}, check=True)
    return json.loads(r.stdout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hash-orders", type=int, default=96, help="other hash orders tried on the runs that differ")
    ap.add_argument("--worker", default=None, help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.worker:
        print(json.dumps([ok for ok, _, _ in replay(a.worker)]))
        return 0
    names = sorted(n for n in LOGS.namelist()
                   if n.split("/")[0] in FOLDERS and not re.search(rf"/(none|{recorded('register')})_seed", n))  # references: verify_replay.py
    first = {n: replay(n) for n in names}
    differ = [n for n in names if not all(ok for ok, _, _ in first[n])]
    other = {n: [False] * len(first[n]) for n in differ}
    if a.hash_orders and differ:
        jobs = [(n, k) for n in differ for k in range(1, a.hash_orders + 1)]
        with ThreadPoolExecutor(os.cpu_count() or 2) as ex:
            for (n, _), flags in zip(jobs, ex.map(under_hash_order, jobs)):
                other[n] = [x or y for x, y in zip(other[n], flags)]
    rows, tot = {}, [0, 0, 0, 0, 0]
    for n in names:
        folder, label = re.match(r"(.+)/(.+)_seed\d+\.calls\.jsonl$", n).groups()
        r = rows.setdefault((folder, label), [0, 0, 0, 0, 0])
        r[0] += 1
        for i, (ok, shown, block) in enumerate(first[n]):
            r[1] += 1
            if ok:
                r[2] += 1
            elif n in other and other[n][i]:
                r[3] += 1
            elif same_in_another_order(shown, block):
                r[4] += 1
    print(f"{'runs':17s} {'Register':8s} {'seeds':>5s} {'prompts':>7s} {'identical':>9s} "
          f"{'under another hash order':>24s} {'same text, other order':>22s}")
    for (folder, label), r in rows.items():
        print(f"{folder:17s} {label:8s} {r[0]:5d} {r[1]:7d} {r[2]:9d} {r[3] or '':>24} {r[4] or '':>22}")
        tot = [x + y for x, y in zip(tot, r)]
    n_dep = len({label for (folder, label), r in rows.items() if r[2] < r[1]})
    print(f"\n{tot[2]:,} of {tot[1]:,} prompts rebuilt byte for byte (Python's hash order fixed to 0).")
    if tot[1] > tot[2]:
        under = (f"{tot[3]:,} are rebuilt byte for byte under one of {a.hash_orders} other orders, " if a.hash_orders
                 else "(other hash orders not tried) ")
        print(f"The other {tot[1] - tot[2]:,} come from {n_dep} Registers whose text depends on Python's hash order: "
              f"{under}{tot[4]:,} have the same text in another order, {tot[1] - sum(tot[2:]):,} differ.")
    return 0 if sum(tot[2:]) == tot[1] else 1


if __name__ == "__main__":
    sys.exit(main())
