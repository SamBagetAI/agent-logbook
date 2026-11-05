"""One YC-Bench run in figures: final funds, cost, and the contracts taken from the trap clients.

The trap clients are the ones YC-Bench marks `is_rat` in a run's result: after acceptance they multiply
the work, so the contract fails or has to be cancelled. A contract counts as taken "after a failure" when
the same client had already failed the agent once before it was accepted.

  python traps.py RESULT.json [RESULT.json ...]
"""
import collections
import json
import sys


def summary(d: dict) -> dict:
    ts = d["time_series"]
    traps = {c["name"] for c in ts["clients"] if c.get("is_rat")}
    tasks = sorted((t for t in ts["tasks"] if t.get("accepted_at")), key=lambda t: t["accepted_at"])
    failed_at = collections.defaultdict(list)  # client -> when its contracts failed
    for t in tasks:
        if t.get("success") is False and t.get("completed_at"):
            failed_at[t["client_name"]].append(t["completed_at"])
    trap = [t for t in tasks if t["client_name"] in traps]
    status = collections.Counter(t["status"] for t in trap)
    return {
        "model": d["model"].split("/")[-1], "seed": d["seed"], "end": d["terminal_reason"], "turns": d["turns_completed"],
        "final_funds_usd": round(ts["funds"][-1]["funds_cents"] / 100, 2) if ts["funds"] else None,
        "cost_usd": round(d.get("total_cost_usd") or 0.0, 4),
        "contracts_accepted": len(tasks), "contracts_succeeded": sum(1 for t in tasks if t.get("success") is True),
        "trap_contracts": len(trap), "trap_failed": status.get("completed_fail", 0), "trap_cancelled": status.get("cancelled", 0),
        "trap_after_a_failure": sum(1 for t in trap if any(x < t["accepted_at"] for x in failed_at[t["client_name"]])),
        "other_failed": sum(1 for t in tasks if t["client_name"] not in traps and t.get("success") is False),
        "trap_clients": ", ".join(sorted(traps)),
    }


if __name__ == "__main__":
    for path in sys.argv[1:]:
        print(json.dumps(summary(json.load(open(path)))))
