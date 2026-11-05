"""Side-by-side table for the tau2 grid, with paired bootstrap intervals.

  python compare_tau.py            # prints the table and writes tau_results.json
"""

import json
import os
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report_tau import load, pass_hat_k, sim_row  # noqa: E402

STANDARD = ["eval-haiku-base", "eval-haiku-register", "eval-sonnet-base", "eval-sonnet-register"]
CRASH = ["crash-haiku-transcript", "crash-haiku-notes", "crash-haiku-register",
         "crash-sonnet-transcript", "crash-sonnet-notes", "crash-sonnet-register"]
SIM = Path(os.environ.get("TAU2_SIMULATIONS", "tau2-bench/data/simulations"))


def ok(r):
    return r["reward"] >= 0.999


def ok_db(r):
    return (r["db"] or 0) >= 0.999 and r["term"] in ("agent_stop", "user_stop")


def stats(name):
    rows = [sim_row(s) for s in load(name)]
    by, bydb = defaultdict(list), defaultdict(list)
    for r in rows:
        by[r["task"]].append(int(ok(r)))
        bydb[r["task"]].append(int(ok_db(r)))
    writes = sum(r["writes"] for r in rows)
    viol = sum(sum(r["broken"].values()) for r in rows)
    cost = sum(r["agent_cost"] + r["user_cost"] for r in rows)
    succ = sum(ok(r) for r in rows)
    return {
        "name": name, "sims": len(rows), "tasks": len(by),
        "pass": [round(pass_hat_k(by, k), 3) for k in (1, 2, 3, 4)],
        "db_pass": [round(pass_hat_k(bydb, k), 3) for k in (1, 2, 3, 4)],
        "writes": writes, "violations": viol,
        "sims_with_violation": sum(1 for r in rows if r["broken"]),
        "duplicate_writes": sum(r["dup"] for r in rows),
        "sims_with_duplicate": sum(1 for r in rows if r["dup"]),
        "refusals": sum(1 for r in rows for e in r["events"] if e["event"] == "refused"),
        "overrides": sum(1 for r in rows for e in r["events"] if e["event"] == "override"),
        "notes_saves": sum(1 for r in rows for e in r["events"] if e["event"] == "notes_saved"),
        "crashes": sum(1 for r in rows for e in r["events"] if e["event"] == "crash"),
        "agent_cost": round(sum(r["agent_cost"] for r in rows), 3),
        "user_cost": round(sum(r["user_cost"] for r in rows), 3),
        "cost_per_sim": round(cost / max(1, len(rows)), 4),
        "agent_cost_per_success": round(sum(r["agent_cost"] for r in rows) / max(1, succ), 4),
        "_by": dict(by),
    }


def paired_boot(a, b, k=1, n=4000, seed=7):
    """Bootstrap over tasks of pass^k(a) - pass^k(b); returns (diff, lo, hi)."""
    tasks = sorted(set(a) & set(b))
    rnd = random.Random(seed)

    def phk(d, ts):
        vals = [math.comb(sum(d[t]), k) / math.comb(len(d[t]), k) for t in ts if len(d[t]) >= k]
        return sum(vals) / len(vals)

    diff = phk(a, tasks) - phk(b, tasks)
    boots = []
    for _ in range(n):
        ts = [rnd.choice(tasks) for _ in tasks]
        boots.append(phk(a, ts) - phk(b, ts))
    boots.sort()
    return round(diff, 3), round(boots[int(0.025 * n)], 3), round(boots[int(0.975 * n)], 3)


def fisher_less(a_hit, a_n, b_hit, b_n):
    """One-sided Fisher exact p: P(a has this few hits or fewer) under no difference."""
    total_hit, n = a_hit + b_hit, a_n + b_n
    p = 0.0
    for x in range(0, a_hit + 1):
        if total_hit - x > b_n or x > a_n:
            continue
        p += math.comb(a_n, x) * math.comb(b_n, total_hit - x) / math.comb(n, total_hit)
    return p


if __name__ == "__main__":
    present = [n for n in STANDARD + CRASH if (SIM / n / "results.json").exists()]
    S = {n: stats(n) for n in present}
    hdr = f"{'config':26} {'sims':>4} {'pass^1':>6} {'pass^4':>6} {'db^1':>5} {'db^4':>5} {'viol':>4} {'dup':>4} {'ref':>4} {'$/sim':>7} {'agent$/ok':>9}"
    print(hdr)
    for n in present:
        s = S[n]
        print(f"{n:26} {s['sims']:>4} {s['pass'][0]:>6} {s['pass'][3]:>6} {s['db_pass'][0]:>5} {s['db_pass'][3]:>5} "
              f"{s['violations']:>4} {s['sims_with_duplicate']:>4} {s['refusals']:>4} {s['cost_per_sim']:>7} {s['agent_cost_per_success']:>9}")
    pairs = [("eval-haiku-register", "eval-haiku-base"), ("eval-haiku-register", "eval-sonnet-base"),
             ("eval-sonnet-register", "eval-sonnet-base"),
             ("crash-haiku-register", "crash-haiku-notes"), ("crash-haiku-register", "crash-haiku-transcript"),
             ("crash-haiku-notes", "crash-haiku-transcript"),
             ("crash-sonnet-register", "crash-sonnet-notes"), ("crash-haiku-register", "crash-sonnet-notes")]
    out = {"configs": {n: {k: v for k, v in S[n].items() if k != "_by"} for n in present}, "pairs": []}
    print("\npaired differences over tasks (bootstrap 95% interval)")
    for a, b in pairs:
        if a in S and b in S:
            d1 = paired_boot(S[a]["_by"], S[b]["_by"], 1)
            d4 = paired_boot(S[a]["_by"], S[b]["_by"], 4)
            fp = fisher_less(S[a]["sims_with_duplicate"], S[a]["sims"], S[b]["sims_with_duplicate"], S[b]["sims"])
            print(f"  {a} - {b}: pass^1 {d1}  pass^4 {d4}  dup-sims {S[a]['sims_with_duplicate']} vs {S[b]['sims_with_duplicate']} (one-sided p={fp:.2g})")
            out["pairs"].append({"a": a, "b": b, "pass1": d1, "pass4": d4, "dup_p": fp})
    (Path(__file__).resolve().parent.parent / "results" / "tau_results.json").write_text(json.dumps(out, indent=1))
