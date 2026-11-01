"""Summarise tau2 runs: pass^k, DB-only pass^k, cost, Logbook events, policy checks.

  python report_tau.py smoke-haiku-base smoke-haiku-logbook
  python report_tau.py --detail smoke-haiku-logbook
"""

import json
import os
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

SIM = Path(os.environ.get("TAU2_SIMULATIONS", "tau2-bench/data/simulations"))
AIR_TOOLS = {"get_reservation_details", "book_reservation", "cancel_reservation",
             "update_reservation_baggages", "update_reservation_flights",
             "update_reservation_passengers", "send_certificate", "search_direct_flight"}
WRITES = {
    "book_reservation", "cancel_reservation", "update_reservation_baggages",
    "update_reservation_flights", "update_reservation_passengers", "send_certificate",
    "cancel_pending_order", "exchange_delivered_order_items", "modify_pending_order_address",
    "modify_pending_order_items", "modify_pending_order_payment", "modify_user_address",
    "return_delivered_order_items",
}


def load(name):
    d = json.loads((SIM / name / "results.json").read_text())
    return d.get("simulations", [])


def pass_hat_k(results_by_task, k):
    """tau-bench pass^k: mean over tasks of C(c,k)/C(n,k)."""
    vals = []
    for rs in results_by_task.values():
        n, c = len(rs), sum(rs)
        if n >= k:
            vals.append(math.comb(c, k) / math.comb(n, k))
    return sum(vals) / len(vals) if vals else float("nan")


def violations(msgs):
    """Replay the Logbook rules on the executed trajectory (same detector for both arms).

    Returns a Counter of rule ids broken by writes that actually reached the environment.
    """
    from logbook_agent import RetailLogbook
    from logbook_airline import AirlineLogbook
    from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage, UserMessage

    airline = any(c.get("name") in AIR_TOOLS for m in msgs for c in (m.get("tool_calls") or []))
    book = AirlineLogbook() if airline else RetailLogbook()
    hist, pending, broken = [], {}, Counter()
    done_keys, dup = set(), 0
    for m in msgs:
        role = m.get("role")
        if role == "assistant":
            calls = [ToolCall(id=c["id"], name=c["name"], arguments=c.get("arguments") or {})
                     for c in (m.get("tool_calls") or [])]
            for c in calls:
                if c.name in WRITES:
                    for rid, _ in book.check(c, hist, len(calls)):
                        broken[rid] += 1
                    key = (c.name, str(c.arguments.get("order_id") or c.arguments.get("user_id")).lstrip("#").upper())
                    if key in done_keys:
                        dup += 1
                pending[c.id] = c
            hist.append(AssistantMessage(role="assistant", content=m.get("content"),
                                         tool_calls=calls or None))
        elif role == "user":
            hist.append(UserMessage(role="user", content=m.get("content")))
        elif role == "tool":
            tm = ToolMessage(id=m["id"], role="tool", content=m.get("content"),
                             error=bool(m.get("error")))
            if m["id"] in pending:
                c = pending.pop(m["id"])
                if c.name in WRITES and not tm.error:
                    done_keys.add((c.name, str(c.arguments.get("order_id") or c.arguments.get("user_id")).lstrip("#").upper()))
                book.observe(c, tm, 0)
    return broken, dup


def sim_row(s):
    ri = s.get("reward_info") or {}
    rb = ri.get("reward_breakdown") or {}
    db = rb.get("DB", rb.get("RewardType.DB"))
    nl = rb.get("NL_ASSERTION", rb.get("RewardType.NL_ASSERTION"))
    msgs = s.get("messages") or []
    events = []
    writes, batched_write, judge_err = 0, 0, 0
    for m in msgs:
        if m.get("role") == "assistant":
            calls = m.get("tool_calls") or []
            w = [c for c in calls if c.get("name") in WRITES]
            writes += len(w)
            if w and len(calls) > 1:
                batched_write += 1
            ev = (m.get("raw_data") or {}).get("logbook_events") if isinstance(m.get("raw_data"), dict) else None
            if ev:
                events += ev
    for chk in ri.get("nl_assertions") or []:
        if str(chk.get("justification", "")).startswith("JUDGE_ERROR"):
            judge_err += 1
    broken, dup = violations(msgs)
    # tau2 reports agent_cost=None when any message lacks a cost (the fixed greeting
    # does), so sum the per-message costs directly.
    agent_cost = sum(m.get("cost") or 0.0 for m in msgs if m.get("role") == "assistant")
    user_cost = sum(m.get("cost") or 0.0 for m in msgs if m.get("role") == "user")
    return {
        "task": s.get("task_id"), "trial": s.get("trial"), "reward": ri.get("reward", 0.0),
        "db": db, "nl": nl, "term": s.get("termination_reason"),
        "agent_cost": agent_cost, "user_cost": user_cost,
        "n_msgs": len(msgs), "writes": writes, "batched_write": batched_write,
        "events": events, "judge_err": judge_err, "broken": broken, "dup": dup,
    }


def summary(name, ks=(1, 2, 3, 4)):
    rows = [sim_row(s) for s in load(name)]
    by_task, by_task_db = defaultdict(list), defaultdict(list)
    for r in rows:
        by_task[r["task"]].append(1 if r["reward"] >= 0.999 else 0)
        by_task_db[r["task"]].append(1 if (r["db"] or 0) >= 0.999 and r["term"] in ("agent_stop", "user_stop") else 0)
    ev = Counter()
    rules = Counter()
    for r in rows:
        for e in r["events"]:
            ev[e["event"]] += 1
            for rid in e.get("rules", []):
                rules[rid] += 1
    n = len(rows)
    broken_total = Counter()
    sims_with_violation = 0
    for r in rows:
        broken_total.update(r["broken"])
        sims_with_violation += 1 if r["broken"] else 0
    out = {
        "name": name, "sims": n, "tasks": len(by_task),
        "pass^k": {k: round(pass_hat_k(by_task, k), 3) for k in ks},
        "db_pass^k": {k: round(pass_hat_k(by_task_db, k), 3) for k in ks},
        "agent_cost": round(sum(r["agent_cost"] for r in rows), 3),
        "user_cost": round(sum(r["user_cost"] for r in rows), 3),
        "terminations": dict(Counter(r["term"] for r in rows)),
        "batched_writes": sum(r["batched_write"] for r in rows),
        "logbook_events": dict(ev), "refusal_rules": dict(rules),
        "judge_errors": sum(r["judge_err"] for r in rows),
        "writes": sum(r["writes"] for r in rows),
        "executed_writes_breaking_a_rule": dict(broken_total),
        "sims_with_a_violation": sims_with_violation,
        "duplicate_writes": sum(r["dup"] for r in rows),
        "sims_with_duplicate": sum(1 for r in rows if r["dup"]),
        "crashes": sum(1 for r in rows for e in r["events"] if e["event"] == "crash"),
    }
    return out, rows


if __name__ == "__main__":
    args = sys.argv[1:]
    detail = "--detail" in args
    args = [a for a in args if a != "--detail"]
    for name in args:
        out, rows = summary(name)
        print(json.dumps(out, indent=1))
        if detail:
            for r in sorted(rows, key=lambda r: (int(r["task"]), r["trial"] or 0)):
                print(f"  task {r['task']:>3} t{r['trial']} reward={r['reward']:.0f} db={r['db']} nl={r['nl']} "
                      f"term={r['term']} msgs={r['n_msgs']} writes={r['writes']} "
                      f"cost={r['agent_cost']:.4f}+{r['user_cost']:.4f} events={[(e['event'], e['tool'], e.get('rules')) for e in r['events']]}")
