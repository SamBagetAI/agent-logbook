"""Leaderboard metrics for ICL + Register (6 tasks) and our ICL rerun (3 tasks), with CL-Bench's own analysis code.

  CLBENCH_REPO=/path/to/continual-learning-bench python compare.py

Reads our runs from ../data/runs and the published runs from the CL-Bench checkout (final_results/runs),
loading one run at a time, plus icl-gpt-5.4, which fixes the reward baseline. r_max values are read from
the task sources, because importing every task needs the Docker tasks' packages.
Writes ../results/compare.json and prints the tables.
"""
import gc, itertools, json, os, re, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLBENCH = Path(os.environ.get("CLBENCH_REPO", "continual-learning-bench")).resolve()
sys.path.insert(0, str(CLBENCH / "scripts")); sys.path.insert(0, str(CLBENCH))
os.chdir(CLBENCH)
import analyze_final_results as A  # noqa: E402


def r_max_from_sources():
    out = {}
    for f in sorted((CLBENCH / "src" / "tasks").glob("*/task.py")):
        m = re.search(r"^\s+r_max(?::[^=]+)?\s*=\s*([0-9.]+)", f.read_text(), re.M)
        if m:
            out[A.clean_task_name(f.parent.name)] = float(m.group(1))
    return out


A.load_task_r_max_values = r_max_from_sources
TASKS = ["exploitable_poker", "blind_spectrum_monitoring", "cohort_studies"]  # the three with our ICL rerun
ALL_TASKS = TASKS + ["database_exploration", "codebase_adaptation", "sales_prediction"]
OURS = ["icl-claude-sonnet-4-6-rerun", "register-icl-claude-sonnet-4-6"]
RUNS = sorted(A.DEFAULT_RUN_NAMES) + OURS

# one results folder with the published runs and ours side by side
results = Path(tempfile.mkdtemp())
for d in (CLBENCH / "final_results" / "runs").iterdir():
    if d.is_dir() and d.name not in OURS:
        (results / d.name).symlink_to(d)
for d in (HERE.parent / "data" / "runs").iterdir():
    (results / d.name).symlink_to(d.resolve())

summ, points, warns = {}, {}, {}
for run in RUNS:
    p = A.build_analysis_payload(results_dir=results, runs=[run, "icl-gpt-5.4"])
    for row in p["task_summary"]:
        if row["run_name"] == run and row["task"] in ALL_TASKS:
            summ.setdefault(run, {})[row["task"]] = {k: row[k] for k in (
                "n_runs", "n_instances", "reward_mean", "baseline_reward", "reference_reward", "gain_mean",
                "normalized_gain_mean", "normalized_gain_se", "normalized_reward_mean", "normalized_reward_se",
                "cost_mean", "warnings")}
    for pt in p["run_points"]:
        if pt["run_name"] == run and pt["task"] in ALL_TASKS and run in OURS + ["icl-claude-sonnet-4.6"]:
            points.setdefault(run, {}).setdefault(pt["task"], []).append(
                {k: pt[k] for k in ("run_index", "reward", "baseline_reward", "gain", "normalized_gain",
                                    "normalized_reward", "cost_usd")})
    warns[run] = {k: v for k, v in p["warnings"].items() if k.startswith(run + "/")}
    del p; gc.collect()


def perm_p(a, b):
    """One-sided exact permutation p-value for mean(a) > mean(b)."""
    pool, k, obs = a + b, len(a), sum(a) / len(a) - sum(b) / len(b)
    hits = tot = 0
    for idx in itertools.combinations(range(len(pool)), k):
        x = [pool[i] for i in idx]; y = [pool[i] for i in range(len(pool)) if i not in idx]
        tot += 1; hits += (sum(x) / len(x) - sum(y) / len(y)) >= obs - 1e-12
    return hits / tot


tests = {}
for t in TASKS:
    a = [x["normalized_gain"] for x in points.get(OURS[1], {}).get(t, [])]
    b = [x["normalized_gain"] for x in points.get(OURS[0], {}).get(t, [])]
    if a and b:
        tests[t] = {"register": a, "icl": b, "p_one_sided": perm_p(a, b),
                    "register_min_above_icl_max": min(a) > max(b)}

board = []
for run, by_task in summ.items():
    if all(t in by_task for t in TASKS):
        g = [by_task[t]["normalized_gain_mean"] for t in TASKS]
        r = [by_task[t]["normalized_reward_mean"] for t in TASKS]
        c = [by_task[t]["cost_mean"] or 0 for t in TASKS]
        board.append({"run": run, "gain_3": sum(g) / 3, "reward_3": sum(r) / 3, "cost_3": sum(c)})
board.sort(key=lambda r: -r["gain_3"])

# the leaderboard: runs with all 6 tasks, ranked by mean normalized reward, as on the benchmark's site
board6 = []
for run, by_task in summ.items():
    if all(t in by_task for t in ALL_TASKS):
        board6.append({"run": run,
                       "reward_6": sum(by_task[t]["normalized_reward_mean"] for t in ALL_TASKS) / 6,
                       "gain_6": sum(by_task[t]["normalized_gain_mean"] for t in ALL_TASKS) / 6,
                       "cost_6": sum(by_task[t]["cost_mean"] or 0 for t in ALL_TASKS)})
board6.sort(key=lambda r: -r["reward_6"])

out = {"tasks": ALL_TASKS, "summary": summ, "points": points, "tests": tests, "board_3_tasks": board,
       "board_6_tasks": board6, "warnings": warns}
(HERE.parent / "results").mkdir(exist_ok=True)
(HERE.parent / "results" / "compare.json").write_text(json.dumps(out, indent=1, default=str))

for t in ALL_TASKS:
    print(f"\n{t}")
    for run in OURS + ["icl-claude-sonnet-4.6", "icl-notepad-claude-sonnet-4-6"]:
        s = summ.get(run, {}).get(t)
        if s:
            print(f"  {run:34s} gain {100*s['normalized_gain_mean']:6.1f}% ±{100*(s['normalized_gain_se'] or 0):4.1f}"
                  f" | reward {100*s['normalized_reward_mean']:6.1f}% ±{100*(s['normalized_reward_se'] or 0):4.1f}"
                  f" | cost/rollout ${s['cost_mean'] or 0:5.2f} | n {s['n_runs']}")
    if t in tests:
        print(f"  permutation p (Register > ICL rerun, one-sided): {tests[t]['p_one_sided']:.4f};"
              f" every Register rollout above every ICL rollout: {tests[t]['register_min_above_icl_max']}")
print("\nThree-task board (mean over poker, BSM, cohort), sorted by normalized gain:")
for i, r in enumerate(board, 1):
    print(f"  {i:2d}. {r['run']:36s} gain {100*r['gain_3']:6.1f}% | reward {100*r['reward_3']:6.1f}% | cost ${r['cost_3']:.2f}")
print("\nSix-task leaderboard (runs with all 6 tasks), ranked by mean normalized reward:")
for i, r in enumerate(board6, 1):
    print(f"  {i:2d}. {r['run']:36s} reward {100*r['reward_6']:6.1f}% | gain {100*r['gain_6']:6.1f}% | cost ${r['cost_6']:.2f}")
for run in OURS:
    if warns.get(run):
        print("warnings", run, warns[run])
