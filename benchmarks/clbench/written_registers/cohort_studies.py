import re

_VARS = [
    ("age", [r"(^|_)age(_|$)", r"^age"]),
    ("years_of_education", [r"educ"]),
    ("family_history", [r"family"]),
    ("genotype_group", [r"genotyp", r"(^|_)gg"]),
    ("prb1_ratio", [r"prb1"]),
    ("acron_use_avg", [r"acron"]),
    ("hhbp_serum_level", [r"hhbp"]),
    ("voc_mg_l", [r"voc"]),
    ("ambient_voc_ppb", [r"ambient"]),
    ("cognitive_score", [r"mmse", r"moca", r"ad8", r"cognit"]),
]

_ERR_RE = re.compile(r"(error|exception|invalid|not allowed|no such|syntax|failed|unknown column|"
                     r"not permitted|traceback|could not|cannot)", re.I)


def _match_cols(var, pats, columns):
    out = []
    for name in columns:
        n = name.lower()
        if var == "voc_mg_l" and "ambient" in n:
            continue
        if var == "ambient_voc_ppb" and "ambient" not in n:
            continue
        for p in pats:
            if re.search(p, n):
                out.append(name)
                break
    return out


def _short(s, n):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 3] + "..."


class Register:
    def __init__(self) -> None:
        self.studies = []
        self.cur = None
        self.step_used = None
        self.step_total = None
        self.step_left = None
        self.in_submission = False

    # ---------------- helpers ----------------
    def _new_study(self, idx, total, label):
        st = {"idx": idx, "total": total, "label": label, "patients": "?",
              "region": "", "columns": {}, "actions": 0, "errors": [],
              "evidence": [], "fits": [], "seen_args": {}, "dups": 0,
              "submitted": False, "sub_stats": None, "sub_result": ""}
        self.studies.append(st)
        self.cur = st
        self.step_used = None
        self.step_left = None
        self.in_submission = False

    def _ensure(self):
        if self.cur is None:
            self._new_study("?", "?", "(unknown study)")
        return self.cur

    def _parse_seen(self, seen):
        m = re.search(r"## Study (\d+)/(\d+):\s*(.+)", seen)
        if m:
            idx, total, label = m.group(1), m.group(2), m.group(3).strip()
            if self.cur is None or self.cur["label"] != label or self.cur["idx"] != idx:
                self._new_study(idx, total, label)
            mp = re.search(r"\*\*Patients:\*\*\s*(\d+)", seen)
            if mp:
                self.cur["patients"] = mp.group(1)
            mr = re.search(r"\*\*Region\(s\) in this dataset:\*\*\s*(.+)", seen)
            if mr:
                self.cur["region"] = _short(mr.group(1), 60)
        m = re.search(r"\[Step (\d+)/(\d+)\D+?(\d+) remaining", seen)
        if m:
            self.step_used, self.step_total, self.step_left = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if "Provide your survival estimates" in seen:
            self.in_submission = True

    def _parse_columns(self, st, text):
        part = text.split("=== Columns ===", 1)
        body = part[1] if len(part) > 1 else text
        for line in body.splitlines():
            m = re.match(r"^\s+([A-Za-z0-9_]+):\s*(.*)$", line)
            if m:
                st["columns"][m.group(1)] = m.group(2).strip()

    def _process(self, action, result):
        tool = action.get("tool")
        args = action.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        st = self._ensure()
        result = str(result or "")
        if tool == "CohortSubmission":
            vals = {}
            for h in ("12", "24", "36"):
                xs = [float(v) for k, v in args.items()
                      if str(k).endswith("__s" + h) and isinstance(v, (int, float))]
                if xs:
                    vals[h] = (sum(xs) / len(xs), min(xs), max(xs))
            st["sub_stats"] = vals
            st["submitted"] = True
            st["sub_result"] = _short(result, 150)
            return
        tc = args.get("tool_call") if isinstance(args.get("tool_call"), dict) else {}
        name = tc.get("tool", "?")
        st["actions"] += 1
        arg = tc.get("sql") or tc.get("group_expression") or ""
        key = (name, " ".join(str(arg).split()))
        if arg:
            if key in st["seen_args"]:
                st["dups"] += 1
            st["seen_args"][key] = st["seen_args"].get(key, 0) + 1
        head = result[:300]
        if name not in ("get_database_metadata",) and _ERR_RE.search(head):
            line = ""
            for l in result.splitlines():
                if _ERR_RE.search(l):
                    line = l
                    break
            st["errors"].append((name, _short(arg, 110), _short(line or head, 170)))
            return
        if name == "get_database_metadata":
            self._parse_columns(st, result)
            return
        if name == "get_data_summary":
            if not st["columns"]:
                for l in result.splitlines():
                    m = re.match(r"^\s*([a-z][a-z0-9_]+)\s*[:|]", l)
                    if m:
                        st["columns"].setdefault(m.group(1), "")
            lines = [l.strip() for l in result.splitlines()
                     if re.search(r"(surviv|died|time_to|mean)", l, re.I) and re.search(r"\d", l)]
            st["evidence"].append((name, "", [_short(x, 110) for x in lines[:3]]))
            return
        if name in ("query_sql", "estimate_survival_by_group", "predict_cohort_survival"):
            lines = [l.strip() for l in result.splitlines() if l.strip() and re.search(r"\d", l)]
            st["evidence"].append((name, _short(arg, 100), [_short(x, 110) for x in lines[:4]]))
            if name == "predict_cohort_survival":
                m = re.search(r"KL[^0-9\-\n]*(-?[0-9]*\.?[0-9]+)", result)
                if m:
                    try:
                        st["fits"].append((float(m.group(1)), _short(arg, 120)))
                    except Exception:
                        pass

    # ---------------- interface ----------------
    def observe(self, step: dict) -> None:
        try:
            action = step.get("action")
            result = step.get("result", "")
            seen = str(step.get("seen") or "")
            if isinstance(action, dict):
                try:
                    self._process(action, result)
                except Exception:
                    pass
            self._parse_seen(seen)
        except Exception:
            pass

    def brief(self) -> str:
        try:
            return self._brief()[:4000]
        except Exception:
            return "=== REGISTER === (unavailable this step)"

    def _brief(self):
        st = self._ensure()
        out = ["=== REGISTER ==="]
        # 1 STATUS
        used = self.step_used if self.step_used is not None else st["actions"]
        tot = self.step_total or 20
        left = self.step_left if self.step_left is not None else max(0, tot - used)
        s = ("[1 STATUS] Study %s/%s %s | patients %s %s| actions %s/%s, %s left"
             % (st["idx"], st["total"], _short(st["label"], 40), st["patients"],
                ("region " + st["region"] + " ") if st["region"] else "", used, tot, left))
        if self.in_submission:
            s += " | SUBMISSION phase: estimates for all 36 cohorts due now"
        elif left <= 2:
            s += " | at 0 left submission is forced"
        if st["dups"]:
            s += " | %d repeated identical calls this study" % st["dups"]
        out.append(s)
        # 2 FAILURES
        out.append("[2 FAILURES this study] " + ("none" if not st["errors"] else
                   "%d (exact messages):" % len(st["errors"])))
        for name, arg, msg in st["errors"][-4:]:
            out.append("  x %s `%s` -> %s" % (name, arg, msg))
        ok = [e for e in st["evidence"] if e[0] != "get_data_summary"]
        if st["errors"] and ok:
            out.append("  ok for contrast: %s `%s`" % (ok[-1][0], ok[-1][1]))
        prev_err = sum(len(x["errors"]) for x in self.studies if x is not st)
        if prev_err:
            last = [e for x in self.studies if x is not st for e in x["errors"]][-1]
            out.append("  earlier studies: %d failures; last: %s -> %s" % (prev_err, last[1][:60], last[2][:90]))
        # 3 VARIABLES
        out.append("[3 COHORT VARIABLES -> columns in this study]")
        if not st["columns"]:
            out.append("  columns not yet read this study")
        else:
            missing = []
            for var, pats in _VARS:
                cols = _match_cols(var, pats, st["columns"])
                if cols:
                    c = cols[0]
                    out.append("  %s: %s %s" % (var, ",".join(cols[:2]), _short(st["columns"].get(c, ""), 85)))
                else:
                    missing.append(var)
            if missing:
                out.append("  absent here: " + ", ".join(missing))
        cov = []
        for var, pats in _VARS:
            labs = [x["label"].split()[0] for x in self.studies if _match_cols(var, pats, x["columns"])]
            if labs:
                cov.append("%s[%s]" % (var.split("_")[0], ",".join(labs[-4:])))
        if len(self.studies) > 1 and cov:
            out.append("  across run: " + " ".join(cov))
        # 4 EVIDENCE
        out.append("[4 EVIDENCE this study] %d results" % len(st["evidence"]))
        for name, arg, lines in st["evidence"][-4:]:
            out.append("  %s `%s`" % (name, arg))
            for l in lines[:3]:
                out.append("    " + l)
        if st["fits"]:
            fs = sorted(st["fits"])[:3]
            out.append("  predict KL (lower=closer fit): " +
                       " | ".join("%.4g `%s`" % (k, a[:70]) for k, a in fs))
        # 5 PRIOR STUDIES
        prev = [x for x in self.studies if x is not st]
        out.append("[5 PRIOR STUDIES] " + ("none" if not prev else ""))
        for x in prev[-5:]:
            line = "  %s/%s %s: %d actions, %d errors, %s" % (
                x["idx"], x["total"], _short(x["label"], 30), x["actions"], len(x["errors"]),
                "submitted" if x["submitted"] else "not submitted")
            if x["sub_stats"]:
                line += " | submitted mean(min-max) " + " ".join(
                    "s%s %.2f(%.2f-%.2f)" % (h, v[0], v[1], v[2]) for h, v in sorted(x["sub_stats"].items()))
            out.append(line)
            if x["fits"]:
                k, a = sorted(x["fits"])[0]
                out.append("    best KL %.4g `%s`" % (k, a[:90]))
            if x["sub_result"]:
                out.append("    feedback: " + x["sub_result"])
        text = "\n".join(out)
        if len(text) > 4000:
            text = text[:3990] + "\n..."
        return text
