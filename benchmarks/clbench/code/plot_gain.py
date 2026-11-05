"""Chart: normalized gain per task, every rollout, ICL vs ICL + Register (from ../results/compare.json).

  python plot_gain.py        # writes ../results/gain_by_task.png
"""
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
d = json.loads((HERE.parent / "results" / "compare.json").read_text())
TASKS = [("blind_spectrum_monitoring", "Blind Spectrum\nMonitoring"), ("exploitable_poker", "Exploitable\nPoker"),
         ("cohort_studies", "Cohort\nStudies")]
# same colours as the AhaBench charts: blue = no Register, green = Register
SERIES = [("icl-claude-sonnet-4-6-rerun", "ICL (whole history in context)", "#2a78d6", -0.17),
          ("register-icl-claude-sonnet-4-6", "ICL + Register", "#1baf7a", 0.17)]
INK, MUTED, GRID, SURFACE = "#1f1f1e", "#6b6a64", "#e6e5e0", "#ffffff"
plt.rcParams["font.family"] = "Inter"

fig, ax = plt.subplots(figsize=(8, 4.4), dpi=150)
for run, label, color, dx in SERIES:
    for i, (task, _) in enumerate(TASKS):
        pts = sorted(p["normalized_gain"] * 100 for p in d["points"][run][task])
        mean = d["summary"][run][task]["normalized_gain_mean"] * 100
        x = i + dx
        jitter = [x + 0.03 * (0, 2, -2, 1, -1)[k % 5] for k in range(len(pts))]  # each run its own x, not ordered by value
        ax.plot([x - 0.12, x + 0.12], [mean, mean], color=INK, linewidth=2, solid_capstyle="round", zorder=2,
                label="mean of the 5 runs" if (i == 0 and dx > 0) else None)
        ax.scatter(jitter, pts, s=46, color=color, edgecolor=SURFACE, linewidth=2, zorder=3,
                   label=label if i == 0 else None)
        side = 1 if dx > 0 else -1  # ICL labels on the left of their line, Register labels on the right
        ax.annotate(f"{mean:+.1f}%".replace("-", "\u2212"), xy=(x + side * 0.14, mean), va="center",
                    ha="left" if side > 0 else "right", fontsize=9, color=INK)
ax.axhline(0, color=MUTED, linewidth=0.8, zorder=1)
ax.set_xticks(range(len(TASKS)), [t for _, t in TASKS], fontsize=9, color=INK)
ax.set_xlim(-0.75, len(TASKS) - 0.3)
ax.set_ylabel("Normalized gain (%)", color=MUTED, fontsize=9)
ax.tick_params(axis="y", colors=MUTED, labelsize=9)
ax.tick_params(axis="x", length=0)
for side in ("top", "right"):
    ax.spines[side].set_visible(False)
for side in ("left", "bottom"):
    ax.spines[side].set_color(GRID)
ax.grid(axis="y", color=GRID, linewidth=0.8)
ax.set_axisbelow(True)
ax.set_title("Continual Learning Bench: learning gain per task (Claude Sonnet 4.6, 5 runs per system)",
             loc="left", fontsize=11, color=INK)
h, l = ax.get_legend_handles_labels()
order = [l.index(s[1]) for s in SERIES] + [l.index("mean of the 5 runs")]
ax.legend([h[k] for k in order], [l[k] for k in order], frameon=False, fontsize=9, loc="lower left",
          labelcolor=INK, handletextpad=0.4)
fig.tight_layout()
out = HERE.parent / "results" / "gain_by_task.png"
fig.savefig(out, facecolor="white")
print(out.name)
