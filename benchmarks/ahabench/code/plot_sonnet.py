"""Chart: Haiku 5.5 + Register vs Sonnet 5.5 with no memory, paper seeds 0-4 (mean net profit by day)."""
import csv, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from recorded import run_stem  # noqa: E402
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HAIKU = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/eval")
SONNET = Path(sys.argv[2] if len(sys.argv) > 2 else "runs/sonnet")
OUT = Path(sys.argv[3] if len(sys.argv) > 3 else "results")
# colour = memory setup (same as the other charts), line style = model
SERIES = [(HAIKU, "none", "Haiku 5.5, no memory", "Haiku, no memory", "#2a78d6", "-"),
          (HAIKU, "register", "Haiku 5.5 + Register", "Haiku + Register", "#1baf7a", "-"),
          (SONNET, "none", "Sonnet 5.5, no memory", "Sonnet, no memory", "#2a78d6", "--"),
          (SONNET, "register", "Sonnet 5.5 + Register", "Sonnet + Register", "#1baf7a", "--")]
INK, MUTED, GRID = "#1f1f1e", "#6b6a64", "#e6e5e0"
days = list(range(1, 366))
fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
ends = []
for root, arm, label, short, color, dash in SERIES:
    if not (root / f"{run_stem(root, arm, 0)}.timeseries.csv").exists():
        continue
    runs = []
    for s in range(5):
        prof = {}
        with (root / f"{run_stem(root, arm, s)}.timeseries.csv").open() as fh:
            for r in csv.DictReader(fh):
                prof[int(r["day"])] = float(r["net_profit"])
        runs.append(prof)
    ys = []
    for d in days:
        vals = [p[max(k for k in p if k <= d)] for p in runs]
        ys.append(sum(vals) / len(vals))
    ax.plot(days, ys, color=color, linewidth=2, linestyle=dash, label=label)
    ends.append([ys[-1], short])
# direct labels at the line ends, pushed apart so they never overlap
lo, hi = ax.get_ylim()
gap = 0.06 * (hi - lo)
ends.sort()
pos = [y for y, _ in ends]
for i in range(1, len(pos)):
    pos[i] = max(pos[i], pos[i - 1] + gap)
for (y, short), yl in zip(ends, pos):
    ax.annotate(short, xy=(365, y), xytext=(368, yl), textcoords="data", va="center", fontsize=9, color=INK,
                annotation_clip=False)
for side in ("top", "right"):
    ax.spines[side].set_visible(False)
for side in ("left", "bottom"):
    ax.spines[side].set_color(GRID)
ax.tick_params(colors=MUTED, labelsize=9)
ax.grid(axis="y", color=GRID, linewidth=0.8)
ax.set_axisbelow(True)
ax.axhline(0, color=MUTED, linewidth=0.8)
ax.set_xlim(1, 365)
ax.set_xlabel("Simulated day", color=MUTED, fontsize=9)
ax.set_ylabel("Mean net profit (\\$, start \\$500)", color=MUTED, fontsize=9)
ax.set_title("Paper seeds 0 to 4: mean net profit by day (a stopped run keeps its last value)", loc="left", fontsize=11, color=INK)
ax.legend(frameon=False, fontsize=9, loc="upper left", labelcolor=INK)
fig.subplots_adjust(right=0.80)
fig.savefig(OUT / "sonnet_vs_haiku.png", facecolor="white")
print("ok")
