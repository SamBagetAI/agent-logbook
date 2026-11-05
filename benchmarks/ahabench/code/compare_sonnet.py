"""Haiku 5.5 + Register vs Sonnet 5.5 with no memory, on Aha-Vending (paper seeds 0-4 and all Haiku seeds)."""
import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_aha import early_late, series  # noqa: E402
from recorded import run_stem  # noqa: E402

EVAL = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/eval")
SONNET = Path(sys.argv[2] if len(sys.argv) > 2 else "runs/sonnet")


def load(root, arm, seeds):
    out = []
    for s in seeds:
        stem = run_stem(root, arm, s)
        p = root / f"{stem}.json"
        if not p.exists():
            continue
        sm = json.loads(p.read_text())
        rows = series(root / f"{stem}.timeseries.csv")
        e, l = early_late(rows)
        u = sm.get("usage") or json.loads((root / f"{stem}.usage.json").read_text())
        out.append({"seed": s, "days": sm["simulated_days"], "term": sm["termination_reason"], "profit": sm["final_net_profit"],
                    "early": e, "late": l, "cost": u["cost_usd"], "tokens": u["input"] + u["output"], "units": sm["units_sold"]})
    return out


def line(name, v):
    if not v:
        return f"| {name} | 0 runs |"
    prof = [r["profit"] for r in v]
    late = [r for r in v if r["late"] is not None]
    lm = (f"{st.mean(r['early'] for r in late):.2f} vs {st.mean(r['late'] for r in late):.2f} (n={len(late)})" if late else "n/a")
    return (f"| {name} | {len(v)} | {sum(r['days'] >= 60 for r in v)}/{len(v)} | {sum(r['term'] == 'completed_horizon' for r in v)}/{len(v)} | "
            f"{st.median(prof):+.0f} | {st.mean(prof):+.0f} | {lm} | {sum(r['tokens'] for r in v) / 1e6:.1f} | {sum(r['cost'] for r in v):.2f} | "
            + ", ".join(f"s{r['seed']}:{r['profit']:+.0f}" for r in v) + " |")


print("| Setup | Runs | In business at day 60 | Full year | Median profit $ | Mean profit $ | $/day d1-90 vs d90-end | Tokens M | Cost $ | Per seed |")
print("|---|---|---|---|---|---|---|---|---|---|")
print(line("Haiku 5.5, no memory, seeds 0-4", load(EVAL, "none", range(5))))
print(line("Haiku 5.5 + Register, seeds 0-4", load(EVAL, "register", range(5))))
print(line("Sonnet 5.5, no memory, seeds 0-4", load(SONNET, "none", range(5))))
print(line("Sonnet 5.5 + Register, seeds 0-4", load(SONNET, "register", range(5))))
print(line("Haiku 5.5 + Register, seeds 0-14", load(EVAL, "register", range(15))))
for arm in ("none", "register"):
    for r in load(SONNET, arm, range(5)):
        print("sonnet", arm, r)
