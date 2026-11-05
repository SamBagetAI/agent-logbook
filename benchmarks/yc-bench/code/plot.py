"""Charts: final funds per seed, and contracts taken from the trap clients (from ../results/runs.csv).

  python plot.py        # writes ../results/funds.png and ../results/traps.png
"""
import csv
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
RES = HERE.parent / "results"
rows = list(csv.DictReader(open(RES / "runs.csv")))
get = {(r["setup"], int(r["seed"])): r for r in rows}
SEEDS = [1, 2, 3, 4, 5]
NOTES, REG, OTHER = "#eb6834", "#1baf7a", "#9c9a92"  # as in the AhaBench charts: orange = own notes, green = Register
INK, MUTED, GRID = "#1f1f1e", "#6b6a64", "#e6e5e0"
plt.rcParams["font.family"] = "Inter"


def style(ax, ylabel):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.tick_params(axis="x", length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_xticks(range(len(SEEDS)), [f"seed {s}" for s in SEEDS], color=INK)
    ax.set_ylabel(ylabel, color=MUTED, fontsize=9)


# 1. Final funds per seed
fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
for i, s in enumerate(SEEDS):
    a, b = float(get[("base", s)]["final_funds_usd"]) / 1e6, float(get[("reg", s)]["final_funds_usd"]) / 1e6
    ax.plot([i, i], [a, b], color=GRID, linewidth=2, zorder=1)
    ax.scatter([i], [a], s=60, color=NOTES, zorder=3, label="Haiku 5.5, its own notes" if i == 0 else None)
    ax.scatter([i], [b], s=60, color=REG, zorder=3, label="Haiku 5.5, notes + Register" if i == 0 else None)
    for model, marker, name in (("claude-sonnet-5.5", "D", "Sonnet 5.5, its own notes (published)"),
                                ("claude-opus-5.5", "s", "Opus 5.5, its own notes (published)")):
        if (model, s) in get:
            ax.scatter([i + 0.18], [float(get[(model, s)]["final_funds_usd"]) / 1e6], s=26, marker=marker, color=OTHER,
                       zorder=2, label=name if i == 0 else None)
ax.axhline(0.2, color=MUTED, linewidth=0.8, linestyle=(0, (3, 3)))
ax.annotate("start: $0.2M", xy=(len(SEEDS) - 0.5, 0.2), xytext=(0, 4), textcoords="offset points", ha="right",
            fontsize=8, color=MUTED)
ax.set_xlim(-0.5, len(SEEDS) - 0.4)
ax.set_ylim(0, 4.1)
style(ax, "Final funds ($M)")
ax.set_title("YC-Bench, one simulated year: final funds per seed", loc="left", fontsize=11, color=INK)
ax.legend(frameon=False, fontsize=8.5, loc="upper left", labelcolor=INK, ncol=2)
fig.tight_layout()
fig.savefig(RES / "funds.png", facecolor="white")

# 2. Contracts taken from the trap clients
fig, ax = plt.subplots(figsize=(8, 3.8), dpi=150)
w = 0.36
for i, s in enumerate(SEEDS):
    for dx, setup, color, name in ((-w / 2, "base", NOTES, "Haiku 5.5, its own notes"), (w / 2, "reg", REG, "Haiku 5.5, notes + Register")):
        v = int(get[(setup, s)]["trap_contracts"])
        ax.bar(i + dx, v, width=w, color=color, zorder=2, label=name if i == 0 else None)
        ax.annotate(str(v), xy=(i + dx, v), xytext=(0, 3), textcoords="offset points", ha="center", fontsize=8.5, color=INK)
style(ax, "Contracts accepted")
ax.set_title("Contracts accepted from the 2 trap clients, per run", loc="left", fontsize=11, color=INK)
ax.legend(frameon=False, fontsize=8.5, loc="upper left", labelcolor=INK)
fig.tight_layout()
fig.savefig(RES / "traps.png", facecolor="white")
print("funds.png traps.png")
