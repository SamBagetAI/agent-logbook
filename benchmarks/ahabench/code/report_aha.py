"""Final tables and chart series for the Aha-Vending runs (reads runs/eval)."""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import Counter, defaultdict
from math import comb
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_aha import early_late, series, trace_stats  # noqa: E402

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/eval")
ARMS = ["none", "notes", "logbook"]
SEEDS = range(15)


def fisher_two_sided(a, b, c, d):
    """2x2 table [[a,b],[c,d]], two-sided Fisher exact p."""
    n1, n2, k = a + b, c + d, a + c
    n = n1 + n2

    def p(x):
        return comb(n1, x) * comb(n2, k - x) / comb(n, k)
    p0 = p(a)
    return sum(p(x) for x in range(max(0, k - n2), min(k, n1) + 1) if p(x) <= p0 + 1e-12)


def load():
    runs = []
    for arm in ARMS:
        for s in SEEDS:
            stem = f"{arm}_seed{s}"
            sm_path = ROOT / f"{stem}.json"
            if not sm_path.exists():
                continue
            sm = json.loads(sm_path.read_text())
            rows = series(ROOT / f"{stem}.timeseries.csv")
            e, l = early_late(rows)
            tr = trace_stats(Path(sm["trace_path"]))
            u = sm.get("usage") or json.loads((ROOT / f"{stem}.usage.json").read_text())
            runs.append({"arm": arm, "seed": s, "days": sm["simulated_days"], "term": sm["termination_reason"],
                         "profit": sm["final_net_profit"], "units": sm["units_sold"], "early": e, "late": l,
                         "rows": rows, "orders": tr["orders"], "lost": sum(tr["lost_units_q"].values()),
                         "lost_first20": sum(n for d, n in tr["lost_units_days"] if d <= 20),
                         "lost_days": tr["lost_units_days"], "phish": tr["phish_replies"], "phish_sensitive": tr["phish_sensitive"],
                         "security_loss": tr["security_loss"], "revenue_q": dict(tr["revenue_q"]),
                         "tokens": u["input"] + u["output"], "tokens_in": u["input"], "tokens_out": u["output"],
                         "cost": u["cost_usd"], "calls": u["calls"], "wall": sm.get("wall_seconds")})
    return runs


def ms(xs, fmt="{:.0f}"):
    xs = [x for x in xs if x is not None]
    if not xs:
        return "n/a"
    if len(xs) == 1:
        return fmt.format(xs[0])
    return f"{fmt.format(st.mean(xs))} (median {fmt.format(st.median(xs))})"


def main():
    runs = load()
    by = defaultdict(list)
    for r in runs:
        by[r["arm"]].append(r)
    print("runs finished:", {a: len(v) for a, v in by.items()})
    print()
    print("| Setup | Runs | Still in business at day 60 | Reached day 365 | Final net profit $ | Units sold | Orders placed | Tokens (M) | Cost $ |")
    print("|---|---|---|---|---|---|---|---|---|")
    for a in ARMS:
        v = by[a]
        if not v:
            continue
        alive60 = sum(1 for r in v if r["days"] >= 60)
        full = sum(1 for r in v if r["term"] == "completed_horizon")
        print(f"| {a} | {len(v)} | {alive60}/{len(v)} | {full}/{len(v)} | {ms([r['profit'] for r in v])} | "
              f"{ms([r['units'] for r in v])} | {ms([r['orders'] for r in v])} | {sum(r['tokens'] for r in v) / 1e6:.1f} | {sum(r['cost'] for r in v):.2f} |")
    print()
    for a in ARMS:
        print(a, "terminations:", dict(Counter(r["term"] for r in by[a])))
    if by["logbook"] and by["none"]:
        L = sum(1 for r in by["logbook"] if r["days"] >= 60)
        N = sum(1 for r in by["none"] if r["days"] >= 60)
        T = sum(1 for r in by["notes"] if r["days"] >= 60)
        print(f"Fisher p (alive at day 60) logbook vs none: {fisher_two_sided(L, len(by['logbook']) - L, N, len(by['none']) - N):.2g}; "
              f"logbook vs notes: {fisher_two_sided(L, len(by['logbook']) - L, T, len(by['notes']) - T):.2g}")
    print()
    print("Paper learning metric (profit per day, day 1-90 vs day 90-end), runs that passed day 90:")
    for a in ARMS:
        v = [r for r in by[a] if r["late"] is not None]
        if v:
            better = sum(1 for r in v if r["late"] > r["early"])
            print(f"  {a}: n={len(v)} early {ms([r['early'] for r in v], '{:.2f}')} late {ms([r['late'] for r in v], '{:.2f}')} "
                  f"late>early in {better}/{len(v)}")
    print()
    print("Units destroyed by failed stock commands: days 1-20 per run (all arms), and after day 20 (logbook)")
    for a in ARMS:
        v = by[a]
        print(f"  {a}: days 1-20 {ms([r['lost_first20'] for r in v])}; after day 20 {ms([r['lost'] - r['lost_first20'] for r in v])}")
    print()
    print("Phishing: replies to phishing senders (with account number or address), security losses $")
    for a in ARMS:
        v = by[a]
        print(f"  {a}: replies {sum(r['phish'] for r in v)} (sensitive {sum(r['phish_sensitive'] for r in v)}), losses ${sum(r['security_loss'] for r in v):.0f}")
    print()
    print("| Setup | Seed | Days | Termination | Net profit | Early $/d | Late $/d | Orders | Lost units | Cost |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in runs:
        e = "-" if r["early"] is None else f"{r['early']:.2f}"
        l = "-" if r["late"] is None else f"{r['late']:.2f}"
        print(f"| {r['arm']} | {r['seed']} | {r['days']} | {r['term']} | {r['profit']:.0f} | {e} | {l} | {r['orders']} | {r['lost']} | {r['cost']:.2f} |")
    # chart series: share still in business by day, mean net profit by day (frozen after termination)
    days = list(range(1, 366, 7)) + [365]
    surv, prof = {}, {}
    for a in ARMS:
        v = by[a]
        if not v:
            continue
        surv[a] = [round(100 * sum(1 for r in v if r["days"] >= d) / len(v)) for d in days]
        series_p = []
        for d in days:
            vals = []
            for r in v:
                rows = [p for dd, p in r["rows"] if dd <= d]
                vals.append(rows[-1] if rows else 0.0)
            series_p.append(round(st.mean(vals), 1))
        prof[a] = series_p
    (ROOT / "chart_series.json").write_text(json.dumps({"days": days, "survival_pct": surv, "mean_profit": prof}))
    (ROOT / "report_runs.json").write_text(json.dumps([{k: v for k, v in r.items() if k not in ("rows", "lost_days")} for r in runs], indent=1))


if __name__ == "__main__":
    main()
