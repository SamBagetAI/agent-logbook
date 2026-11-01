"""Rebuild every Logbook block of the published AhaBench runs, offline, and compare.

For each logged model call, this feeds the Logbook the prompt the agent received and the
action it chose, exactly as logbook_provider.py does during a run, then compares the block
it produces with the PLANNER NOTE block the agent actually saw. No API call, no simulator.

  python verify_replay.py            # reads ../data/logs_calls.zip

A full match means two things: the code in logbook/ahabench is the code that ran, and every
line of the Logbook came from what the agent was shown and what it did.
"""
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
spec = importlib.util.spec_from_file_location("logbook_provider", HERE / "logbook_provider.py")
P = importlib.util.module_from_spec(spec)
sys.modules["logbook_provider"] = P
spec.loader.exec_module(P)  # loaded the way AhaBench's --provider python loads it

import generic_logbook  # noqa: E402  (found in logbook/ahabench, see logbook_provider.py)
import vending_logbook  # noqa: E402

for mod in (vending_logbook, generic_logbook):
    print(f"{mod.__name__} loaded from {Path(mod.__file__).resolve().relative_to(REPO)}")

LOGS = zipfile.ZipFile(HERE.parent / "data" / "logs_calls.zip")


def replay(name: str, arm: str) -> tuple[int, int]:
    rows = [json.loads(line) for line in LOGS.read(name).decode("utf-8").splitlines() if line.strip()]
    generic = arm in ("generic", "generic2")
    book = (generic_logbook.GenericLogbook(v2=arm == "generic2") if generic
            else vending_logbook.VendingLogbook(checks=False, outcomes=arm != "logbook_noout"))
    last, same = None, 0
    for r in rows:
        shown_prompt = r["prompt"]
        m = P.PLANNER.search(shown_prompt)
        if generic:
            if r["attempt"] == 1 and last:
                res = P.LAST_RESULT.search(shown_prompt)
                book.record(last.get("tool_name", "?"), last.get("arguments"), res.group(1) if res else "", r["ts"])
        elif r["attempt"] == 1:
            book.observe(P.put_planner(shown_prompt, P.DEFAULT_PLANNER), last)
        same += bool(m) and m.group(2) == P.DEFAULT_PLANNER + "\n" + book.brief()
        act = P.first_json(r["reply"] or "")
        if isinstance(act, dict) and act.get("tool_name"):
            last = act
            if not generic:
                book.note_action(act, r["ts"])
    return len(rows), same


SETUPS = [("Haiku 5.5, Logbook", "logbook", "logbook_seed{}.calls.jsonl", 15),
          ("Haiku 5.5, Logbook without money and sales lines", "logbook_noout", "logbook_noout_seed{}.calls.jsonl", 15),
          ("Haiku 5.5, generic Logbook v1", "generic", "generic_seed{}.calls.jsonl", 15),
          ("Haiku 5.5, generic Logbook v2", "generic2", "generic2_seed{}.calls.jsonl", 15),
          ("Sonnet 5.5, Logbook", "logbook", "sonnet/logbook_seed{}.calls.jsonl", 5)]
total = total_same = 0
print(f"\n{'setup':50s} {'runs':>4s} {'model calls':>11s} {'identical':>9s}")
for label, arm, pattern, seeds in SETUPS:
    n = s = 0
    for seed in range(seeds):
        a, b = replay(pattern.format(seed), arm)
        n, s = n + a, s + b
    total, total_same = total + n, total_same + s
    print(f"{label:50s} {seeds:4d} {n:11d} {s:9d}")
print(f"\n{total_same} of {total} Logbook blocks rebuilt byte for byte.")
sys.exit(0 if total_same == total else 1)
