"""Two charts for the README: share of runs still in business, mean net profit by day."""
import csv, json, sys
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/eval")
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "results")
SETUPS = [("none", "No memory (AhaBench default)", "#2a78d6"),
          ("notes", "Agent's own notes", "#eb6834"),
          ("logbook", "Logbook", "#1baf7a")]
SHORT = {"No memory (AhaBench default)": "No memory", "Agent's own notes": "own notes", "Logbook": "Logbook"}
INK, MUTED, GRID = "#1f1f1e", "#6b6a64", "#e6e5e0"
days = list(range(1, 366))


def load(arm):
    runs = []
    for s in range(15):
        sm = ROOT / f"{arm}_seed{s}.json"
        if not sm.exists():
            continue
        last_day = json.loads(sm.read_text())["simulated_days"]
        prof = {}
        with (ROOT / f"{arm}_seed{s}.timeseries.csv").open() as fh:
            for r in csv.DictReader(fh):
                prof[int(r["day"])] = float(r["net_profit"])
        runs.append((last_day, prof))
    return runs


def style(ax, ylabel):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_xlabel("Simulated day", color=MUTED, fontsize=9)
    ax.set_ylabel(ylabel, color=MUTED, fontsize=9)
    ax.set_xlim(1, 365)


data = {arm: load(arm) for arm, _, _ in SETUPS}
for name, ylabel, fn in [("survival", "% of runs still in business", None), ("profit", "Mean net profit (\\$, start \\$500)", None)]:
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
    ends = []
    for arm, label, color in SETUPS:
        runs = data[arm]
        if name == "survival":
            ys = [100 * sum(1 for last, _ in runs if last >= d) / len(runs) for d in days]
        else:
            ys = []
            for d in days:
                vals = []
                for last, prof in runs:
                    known = [k for k in prof if k <= d]
                    vals.append(prof[max(known)] if known else 0.0)
                ys.append(sum(vals) / len(vals))
        ax.plot(days, ys, color=color, linewidth=2, label=label)
        ends.append((ys[-1], label))
    # direct labels at the line ends; lines that end at the same value share one label
    lo, hi = ax.get_ylim()
    groups = []
    for y, label in sorted(ends):
        if groups and abs(y - groups[-1][0]) < 0.04 * (hi - lo):
            groups[-1][1].append(label)
        else:
            groups.append([y, [label]])
    for y, labels in groups:
        text = ", ".join(SHORT[l] for l in labels)
        ax.annotate(text[0].upper() + text[1:], xy=(365, y), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=9, color=INK)
    style(ax, ylabel)
    if name == "survival":
        ax.set_ylim(0, 105)
        ax.set_title("Runs still in business (Haiku 5.5, 15 seeds per setup)", loc="left", fontsize=11, color=INK)
    else:
        ax.axhline(0, color=MUTED, linewidth=0.8)
        ax.set_title("Mean net profit by day (a stopped run keeps its last value)", loc="left", fontsize=11, color=INK)
    if name == "profit":
        ax.legend(frameon=False, fontsize=9, loc="upper left", labelcolor=INK)
    else:
        ax.legend(frameon=False, fontsize=9, loc="center", bbox_to_anchor=(0.5, 0.42), labelcolor=INK)
    fig.subplots_adjust(right=0.80)
    fig.savefig(OUT / f"{name}.png", facecolor="white")
    plt.close(fig)
print("ok")
