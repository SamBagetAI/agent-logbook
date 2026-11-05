"""Did the agent learn that suppliers need the account number? From the calls logs (all runs).

Run it from inside the runs directory, e.g. cd runs/eval && python ../../code/acct_analysis.py
"""
import json, re, sys, os
from datetime import date
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from recorded import run_stem
def first_json(text):
    dec=json.JSONDecoder(); i=text.find("{")
    while i!=-1:
        try:
            o,_=dec.raw_decode(text[i:]); return o
        except Exception: i=text.find("{",i+1)
def day(ts): return (date.fromisoformat(ts[:10])-date(2025,1,1)).days+1
PH=re.compile(r"^ops-\d+@")
res=defaultdict(list)
for arm in ["none","notes","register","generic","generic2"]:
    for s in range(15):
        stem=run_stem(".", arm, s); f=f"{stem}.calls.jsonl"
        if not os.path.exists(f): continue
        rows=[json.loads(l) for l in open(f)]
        emails=[]; refused_ids=set(); first_refusal=None; confirmed=[]
        seen=set()
        for r in rows:
            p=r["prompt"]
            m=re.search(r"LAST TOOL RESULT\s*\n(.*?)\n\s*\n\s*REMINDER:", p, re.S)
            if m:
                t=m.group(1); i=re.search(r"^\s*ID: (\d+)", t, re.M)
                if i and i.group(1) not in seen:
                    seen.add(i.group(1))
                    if "we still need the following" in t.lower() and "account number" in t.lower().split("we still need the following",1)[1][:100]:
                        refused_ids.add(i.group(1)); first_refusal = first_refusal or day(r["ts"])
                    if "we have charged account" in t.lower():
                        confirmed.append(day(r["ts"]))
            if r["attempt"]!=1: continue
            a=first_json(r["reply"])
            if isinstance(a,dict) and a.get("tool_name")=="send_email":
                ar=a.get("arguments") or {}; to=str(ar.get("to","")).lower()
                if PH.match(to) or "@" not in to: continue
                emails.append((day(r["ts"]), "BIZ-0001" in str(ar.get("body",""))))
        done=os.path.exists(f"{stem}.json")
        last=day(rows[-1]["ts"])
        # after the first refusal for a missing account number, share of supplier emails that include it
        after=[inc for d,inc in emails if first_refusal and d>first_refusal]
        res[arm].append(dict(seed=s, last_day=last, done=done, refusals=len(refused_ids), first_refusal=first_refusal,
                             emails=len(emails), with_acct=sum(inc for _,inc in emails),
                             after_share=(sum(after)/len(after)) if after else None, reads_confirmed=len(confirmed)))
for arm,v in res.items():
    sh=[x["after_share"] for x in v if x["after_share"] is not None]
    print(f"{arm:8s} runs {len(v)}  alive>day50: {sum(1 for x in v if x['last_day']>50)}  "
          f"refusals(missing acct) mean {sum(x['refusals'] for x in v)/len(v):.1f}  "
          f"share of supplier emails WITH account no. after first refusal: {sum(sh)/len(sh):.2f} (n={len(sh)})")
    for x in v:
        print("   ", x)
