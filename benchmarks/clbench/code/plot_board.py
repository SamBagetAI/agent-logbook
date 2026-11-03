"""Chart: the six-task ranking, mean normalized reward per system (from ../results/compare.json).

  python plot_board.py        # writes ../results/board.png
"""
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
board = json.loads((HERE.parent / "results" / "compare.json").read_text())["board_6_tasks"]
NAMES = {
    "logbook-icl-claude-sonnet-4-6": "ICL + Logbook · Claude Sonnet 4.6",
    "icl-claude-sonnet-4.6": "ICL · Claude Sonnet 4.6", "icl-gpt-5.4": "ICL · GPT-5.4",
    "claude-code-sonnet-4.6": "Claude Code · Sonnet 4.6", "icl-claude-opus-4.7": "ICL · Claude Opus 4.7",
    "mem0-gpt-5.4": "Mem0 · GPT-5.4", "icl-notepad-claude-sonnet-4-6": "ICL Notepad · Claude Sonnet 4.6",
    "icl-notepad-gpt-5.4": "ICL Notepad · GPT-5.4", "icl-gemini-3-flash": "ICL · Gemini 3 Flash",
    "ace-gpt-5.4": "ACE · GPT-5.4", "codex-gpt-5.4": "Codex · GPT-5.4",
    "icl-notepad-gemini-3.1-pro-preview": "ICL Notepad · Gemini 3.1 Pro Preview",
    "icl-gemini-3.1-pro-preview": "ICL · Gemini 3.1 Pro Preview",
}
OURS = "logbook-icl-claude-sonnet-4-6"
GREEN, OTHER = "#1baf7a", "#b9b7ae"  # green = with the Logbook, as in the other charts; gray = published systems
INK, MUTED, GRID = "#1f1f1e", "#6b6a64", "#e6e5e0"
plt.rcParams["font.family"] = "Inter"

rows = board[::-1]  # best at the top
fig, ax = plt.subplots(figsize=(8, 5.2), dpi=150)
ys = range(len(rows))
vals = [100 * r["reward_6"] for r in rows]
ax.barh(list(ys), vals, height=0.52, color=[GREEN if r["run"] == OURS else OTHER for r in rows], zorder=2)
for y, v, r in zip(ys, vals, rows):
    ax.annotate(f"{v:.1f}%".replace("-", "−"), xy=(v, y), xytext=(4 if v >= 0 else -4, 0), textcoords="offset points",
                va="center", ha="left" if v >= 0 else "right", fontsize=9, color=INK,
                fontweight="semibold" if r["run"] == OURS else "normal")
ax.set_yticks(list(ys), [NAMES.get(r["run"], r["run"]) for r in rows], fontsize=9, color=INK)
for lab, r in zip(ax.get_yticklabels(), rows):
    if r["run"] == OURS:
        lab.set_fontweight("semibold")
ax.axvline(0, color=MUTED, linewidth=0.8, zorder=3)
ax.set_xlim(-12, 33)
ax.tick_params(axis="x", colors=MUTED, labelsize=9)
ax.tick_params(axis="y", length=0)
for side in ("top", "right", "left"):
    ax.spines[side].set_visible(False)
ax.spines["bottom"].set_color(GRID)
ax.grid(axis="x", color=GRID, linewidth=0.8)
ax.set_axisbelow(True)
ax.set_xlabel("Mean normalized reward over the 6 tasks (%), the leaderboard's ranking metric", color=MUTED, fontsize=9)
fig.suptitle("Continual Learning Bench, all 6 tasks: the 12 published systems and ICL with a Logbook",
             x=0.015, ha="left", fontsize=11, color=INK)
fig.tight_layout()
out = HERE.parent / "results" / "board.png"
fig.savefig(out, facecolor="white")
print(out.name)
