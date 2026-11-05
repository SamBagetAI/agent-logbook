"""Results of the three "a model writes the Register" tests, from the published data.

  python report_written.py      # reads ../data/written_registers_*.zip, writes ../results/written_registers_runs.csv

Claude Haiku 5.5 as the agent, fresh seeds 15 to 29. Runs still alive were stopped on purpose
(after day 60 in the second and third tests, between day 61 and day 334 in the first), so the
metrics are: in business at day 60, net profit at day 60 (and at day 100 in the first test), and
the share of supplier emails that include the account number after the first refusal for a
missing one.
"""
from __future__ import annotations

import csv
import io
import json
import re
import statistics as st
import zipfile
from collections import defaultdict
from datetime import date
from math import comb
from pathlib import Path

from recorded import current

HERE = Path(__file__).resolve().parent
DATA, RESULTS, WRITTEN = HERE.parent / "data", HERE.parent / "results", HERE.parent / "written_registers"
TESTS = ["task_prompt", "generic_v1", "generic_v2"]
LABEL = {"none": "No memory", "register": "Register written by hand",
         "opusA": "Opus 5.5, task prompt, prompt only", "opusB": "Opus 5.5, task prompt, after a failed run",
         "haikuA": "Haiku 5.5, task prompt, prompt only", "haikuB": "Haiku 5.5, task prompt, after a failed run",
         "opusGA": "Opus 5.5, generic prompt v1, no past run", "opusGB": "Opus 5.5, generic prompt v1, after a failed run",
         "opusGP": "Opus 5.5, generic prompt v2, after a failed run",
         "opusGL": "Opus 5.5, generic prompt v2, rewritten after a trial"}
LAST = re.compile(r"LAST TOOL RESULT\s*\n(.*?)\n\s*\n\s*REMINDER:", re.S)
PHISH = re.compile(r"^ops-\d+@")  # the scenario's phishing senders


def fisher(a, b, c, d):
    """Two-sided Fisher exact p for the 2x2 table [[a, b], [c, d]]."""
    n1, n2, k = a + b, c + d, a + c
    n = n1 + n2
    p = lambda x: comb(n1, x) * comb(n2, k - x) / comb(n, k)  # noqa: E731
    p0 = p(a)
    return sum(p(x) for x in range(max(0, k - n2), min(k, n1) + 1) if p(x) <= p0 + 1e-12)


def first_json(text):
    dec = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            return dec.raw_decode(text[i:])[0]
        except Exception:  # noqa: BLE001
            i = text.find("{", i + 1)
    return None


def acct_share(lines, until_day: int = 60):
    """Days 1 to until_day, after the first refusal for a missing account number: the share of
    supplier emails that include the account number (None if there was no refusal)."""
    day = lambda ts: (date.fromisoformat(ts[:10]) - date(2025, 1, 1)).days + 1  # noqa: E731
    seen, first, emails = set(), None, []
    for line in lines:
        r = json.loads(line)
        if day(r["ts"]) > until_day:
            break
        m = LAST.search(r["prompt"])
        if m:
            t = m.group(1)
            i = re.search(r"^\s*ID: (\d+)", t, re.M)
            if i and i.group(1) not in seen:
                seen.add(i.group(1))
                low = t.lower()
                if "we still need the following" in low and "account number" in low.split("we still need the following", 1)[1][:100]:
                    first = first or day(r["ts"])
        if r["attempt"] != 1:
            continue
        a = first_json(r["reply"] or "")
        if isinstance(a, dict) and a.get("tool_name") == "send_email":
            ar = a.get("arguments") or {}
            to = str(ar.get("to", "")).lower()
            if not PHISH.match(to) and "@" in to:
                emails.append((day(r["ts"]), "BIZ-0001" in str(ar.get("body", ""))))
    after = [inc for d, inc in emails if first and d > first]
    return (sum(after) / len(after)) if after else None


def load():
    per, calls = zipfile.ZipFile(DATA / "written_registers_per_run.zip"), zipfile.ZipFile(DATA / "written_registers_calls.zip")
    names = set(per.namelist())
    runs = []
    for test in TESTS:
        for f in sorted(n for n in names if n.startswith(test + "/") and n.endswith(".timeseries.csv")):
            stem = f[:-len(".timeseries.csv")]
            label, seed = stem.split("/", 1)[1].rsplit("_seed", 1)
            label = current(label)  # the zips keep the names the runs were recorded under
            setup = label if label in ("none", "register") else label[:-1]
            rows = list(csv.DictReader(io.StringIO(per.read(f).decode())))
            by_day = {int(r["day"]): float(r["net_profit"]) for r in rows}
            last_day = int(rows[-1]["day"])
            sm = json.loads(per.read(stem + ".json")) if stem + ".json" in names else None
            usage = {current(k): v for k, v in json.loads(per.read(stem + ".usage.json")).items()}
            ended = sm is not None and not sm["termination_reason"].startswith(("completed_horizon", "stopped_after_day"))

            def at(d):  # net profit on day d; a run that ended earlier keeps its final result
                if last_day >= d:
                    return by_day.get(d, by_day[max(x for x in by_day if x <= d)])
                return sm["final_net_profit"] if ended else None
            runs.append({"test": test, "setup": setup, "register": "" if setup == label else label, "seed": int(seed),
                         "last_day": last_day, "end": sm["termination_reason"] if sm else f"stopped by us on day {last_day}",
                         "in_business_day60": int(last_day >= 60), "net_profit_day60": at(60),
                         "net_profit_day100": at(100) if test == "task_prompt" else None,
                         "account_number_share_after_refusal_d1_60": acct_share(
                             calls.read(stem + ".calls.jsonl").decode("utf-8").splitlines()),
                         "register_errors": usage.get("register_errors", 0), "cost_usd": round(usage.get("cost_usd", 0.0), 4)})
    return runs


def cost(folder: str) -> float:
    per = zipfile.ZipFile(DATA / "written_registers_per_run.zip")
    return sum(json.loads(per.read(n))["cost_usd"] for n in per.namelist() if n.startswith(folder + "/") and n.endswith(".usage.json"))


def authoring(folder: str, prefixes: tuple) -> float:
    return sum(json.loads(f.read_text())["cost_usd"] for f in (WRITTEN / folder).glob("*.json") if f.stem.startswith(prefixes))


def main():
    runs = load()
    RESULTS.mkdir(exist_ok=True)
    with open(RESULTS / "written_registers_runs.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(runs[0]))
        w.writeheader()
        share = "account_number_share_after_refusal_d1_60"
        w.writerows({**r, share: None if r[share] is None else round(r[share], 3)} for r in runs)
    by = defaultdict(list)
    for r in runs:
        by[r["setup"]].append(r)
    none = by["none"]
    a_none = sum(r["in_business_day60"] for r in none)
    print("Claude Haiku 5.5 on Aha-Vending (scenario v2), seeds 15 to 29. Generated by code/report_written.py.\n")
    print("| Setup | In business at day 60 | p vs no memory | Net profit at day 60, median | Account number sent after a refusal | Register errors |")
    print("|---|---|---|---|---|---|")
    for s in LABEL:
        v = by[s]
        a = sum(r["in_business_day60"] for r in v)
        p = "" if s == "none" else f"{fisher(a, len(v) - a, a_none, len(none) - a_none):.2g}"
        sh = [r["account_number_share_after_refusal_d1_60"] for r in v if r["account_number_share_after_refusal_d1_60"] is not None]
        print(f"| {LABEL[s]} | {a}/{len(v)} | {p} | {st.median(r['net_profit_day60'] for r in v):+.0f} | "
              f"{100 * sum(sh) / len(sh):.0f}% (n={len(sh)}) | {sum(r['register_errors'] for r in v)} |")
    print("\nPer written Register (5 seeds each), in business at day 60:")
    lab = defaultdict(list)
    for r in runs:
        if r["register"]:
            lab[r["register"]].append(r)
    print("  " + ", ".join(f"{k} {sum(r['in_business_day60'] for r in v)}/5" for k, v in sorted(lab.items())))
    print("\nNet profit, paired by seed (first setup minus the second, same seed):")
    for key, pairs in (("net_profit_day60", (("opusB", "register"), ("opusGP", "register"), ("opusGL", "register"), ("opusGL", "opusGP"))),
                       ("net_profit_day100", (("opusB", "register"),))):
        idx = {(r["setup"], r["seed"]): r[key] for r in runs if r[key] is not None}
        for a, b in pairs:
            d = [idx[(a, s)] - idx[(b, s)] for s in range(15, 30) if (a, s) in idx and (b, s) in idx]
            t = st.mean(d) / (st.stdev(d) / len(d) ** 0.5)
            print(f"  day {key.split('day')[-1]}: {a} - {b}: mean {st.mean(d):+.0f}, ahead on {sum(x > 0 for x in d)}/{len(d)}, paired t = {t:.2f}")
    t1 = (cost("task_prompt"), authoring("task_prompt", ("opus", "haiku")), cost("past_run"))
    t2 = (cost("generic_v1"), authoring("generic_v1", ("opusG",)))
    t3 = (cost("generic_v2"), cost("generic_v2_trial"), authoring("generic_v2", ("opusG",)))
    print(f"\nCost: first test ${sum(t1):.2f} (runs ${t1[0]:.2f}, writing ${t1[1]:.2f}, the failed run ${t1[2]:.2f}); "
          f"second ${sum(t2):.2f} (runs ${t2[0]:.2f}, writing ${t2[1]:.2f}); "
          f"third ${sum(t3):.2f} (runs ${t3[0]:.2f}, trials ${t3[1]:.2f}, writing ${t3[2]:.2f})")


if __name__ == "__main__":
    main()
