"""The trace figures quoted in the README, from the run artifacts in ../data/runs.

  python traces.py        # writes ../results/traces.txt
"""
import gzip, json
from pathlib import Path

RUNS = Path(__file__).resolve().parent.parent / "data" / "runs"
NAMES = {"icl-claude-sonnet-4-6-rerun": "ICL", "logbook-icl-claude-sonnet-4-6": "ICL + Logbook"}
out = []


def load(run, task):
    return json.loads(gzip.decompress((RUNS / run / "tasks" / f"{task}.json.gz").read_bytes()))


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else float("nan")


out.append("Blind Spectrum Monitoring: bandwidth errors per scan (MHz), first vs second half of each rollout")
for run, label in NAMES.items():
    a = load(run, "blind_spectrum_monitoring")
    halves = [{"u": [], "m": [], "r": []} for _ in range(2)]
    for it in a["run_traces"]:
        outs = it["trace"]["result"]["instance_outcomes"]
        for i, o in enumerate(outs):
            h, md = halves[0 if i < len(outs) // 2 else 1], o.get("metadata") or {}
            h["u"].append(md.get("unsafe_bandwidth_claimed", 0)); h["m"].append(md.get("missed_available_bandwidth", 0))
            h["r"].append(o.get("reward", 0))
    b = a["baseline_trace"]["result"]["instance_outcomes"]
    out.append(f"  {label:14s} occupied spectrum declared free: {mean(halves[0]['u']):5.1f} -> {mean(halves[1]['u']):5.1f}"
               f" | free spectrum missed: {mean(halves[0]['m']):5.1f} -> {mean(halves[1]['m']):5.1f}"
               f" | reward per scan: {mean(halves[0]['r']):.3f} -> {mean(halves[1]['r']):.3f}"
               f" | stateless baseline: {mean((o.get('metadata') or {}).get('unsafe_bandwidth_claimed', 0) for o in b):5.1f}"
               f" declared free, {mean((o.get('metadata') or {}).get('missed_available_bandwidth', 0) for o in b):4.1f} missed")

out.append("\nExploitable Poker: per rollout")
for run, label in NAMES.items():
    a = load(run, "exploitable_poker")
    for it in sorted(a["run_traces"], key=lambda x: x["run_index"]):
        tr = it["trace"]; u = ((tr.get("execution") or {}).get("usage") or {}).get("respond") or {}
        inter = tr["interactions"]
        chars = mean(len(json.dumps(x["response"]["action"])) for x in inter)
        out.append(f"  {label:14s} rollout {it['run_index']}: {u.get('call_count', 0)} calls, {u.get('output_tokens', 0):,} output tokens,"
                   f" answer length {chars:,.0f} characters, cache hits {u.get('cached_input_tokens', 0) / max(1, u.get('input_tokens', 1)):.0%}"
                   f" of input, cost ${u.get('cost_usd') or 0:.2f}" if u.get("cost_usd") else
                   f"  {label:14s} rollout {it['run_index']}: {u.get('call_count', 0)} calls, {u.get('output_tokens', 0):,} output tokens,"
                   f" answer length {chars:,.0f} characters")

out.append("\nCohort Studies: signed information gain per rollout (sum over the 20 studies) and the worst study")
for run, label in NAMES.items():
    a = load(run, "cohort_studies")
    for it in sorted(a["run_traces"], key=lambda x: x["run_index"]):
        outs = it["trace"]["result"]["instance_outcomes"]
        vals = [(o.get("metadata") or {}).get("mean_reference_kl", 0) - (o.get("metadata") or {}).get("mean_kl_divergence", 0) for o in outs]
        k = min(range(len(vals)), key=vals.__getitem__)
        steps = [(o.get("metadata") or {}).get("steps") for o in outs]
        out.append(f"  {label:14s} rollout {it['run_index']}: {sum(vals):+.3f} | worst study {k + 1}/20"
                   f" ({(outs[k].get('metadata') or {}).get('study_name')}) {vals[k]:+.3f} | actions per study {steps}")

out.append("\nLogbook: errors and text length, every call of every rollout and of the stateless baseline")
for task in ("exploitable_poker", "blind_spectrum_monitoring", "cohort_studies", "database_exploration",
             "codebase_adaptation", "sales_prediction"):
    a = load("logbook-icl-claude-sonnet-4-6", task)
    traces = [it["trace"] for it in a["run_traces"]] + [a["baseline_trace"]]
    errs, chars = [], []
    for tr in traces:
        for x in tr.get("interactions") or []:
            m = (x.get("response") or {}).get("metadata") or {}
            if "logbook_chars" in m:
                errs.append(m.get("logbook_errors", 0)); chars.append(m["logbook_chars"])
    out.append(f"  {task:26s} calls {len(chars):5d} | errors {max(errs) if errs else 'n/a'} | text {mean(chars):,.0f} characters"
               f" on average, {max(chars) if chars else 0:,} at most")

text = "\n".join(out)
(Path(__file__).resolve().parent.parent / "results" / "traces.txt").write_text(text + "\n")
print(text)
