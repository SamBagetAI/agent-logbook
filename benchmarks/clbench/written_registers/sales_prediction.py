import re


def _num(s):
    try:
        return float(s)
    except Exception:
        return None


def _fmt(v):
    try:
        if v is None:
            return "?"
        if abs(v - round(v)) < 1e-9:
            return str(int(round(v)))
        return "%.1f" % v
    except Exception:
        return "?"


class Register:
    def __init__(self) -> None:
        self.instances = []
        self.cur = None
        self.inst_label = None
        self.step_info = None
        self.phase = "analysis"
        self.files = {}
        self.actuals = {}
        self.last_cmd_failed = None

    # ---------- helpers ----------
    def _new_instance(self, year):
        inst = {
            "year": year,
            "label": self.inst_label,
            "required": [],
            "schema": {},
            "retention": None,
            "cmds": 0,
            "fails": [],
            "preds": {},
            "score": None,
            "submitted": False,
            "entry": {},
        }
        self.instances.append(inst)
        self.cur = inst
        self.phase = "analysis"
        self.step_info = None
        self.last_cmd_failed = None

    def _ensure(self):
        if self.cur is None:
            self._new_instance(None)

    # ---------- observe ----------
    def observe(self, step: dict) -> None:
        try:
            self._observe(step)
        except Exception:
            pass

    def _observe(self, step):
        action = step.get("action") if isinstance(step, dict) else None
        result = str(step.get("result") or "") if isinstance(step, dict) else ""
        seen = str(step.get("seen") or "") if isinstance(step, dict) else ""

        if action:
            self._ensure()
            tool = action.get("tool")
            args = action.get("arguments") or {}
            if tool == "BashCommandResponse":
                self._bash(args, result)
            elif tool == "PredictionResponse":
                self._preds(args, result)
        m = re.search(r"Moving to instance (\d+)/(\d+)", result)
        if m:
            self.inst_label = "%s/%s" % (m.group(1), m.group(2))

        # process the new prompt
        ym = re.search(r"This is year (\d{4})", seen)
        if ym:
            y = int(ym.group(1))
            if self.cur is None or "=== Brief ===" in seen or self.cur["year"] != y or self.cur["submitted"]:
                self._new_instance(y)
        self._ensure()
        req = re.findall(r"^\s*-\s*([^/\n]+?)\s*/\s*([^/\n]+?)\s*/\s*(\d{4})\s*$", seen, re.M)
        if req:
            seen_set = []
            s = set()
            for loc, prod, yr in req:
                k = (loc.strip(), prod.strip(), int(yr))
                if k not in s:
                    s.add(k)
                    seen_set.append(k)
            self.cur["required"] = seen_set
        for fm in re.finditer(r"`data/([^`]+)`\s*[—-]+\s*columns:\s*([^\n]+)", seen):
            self.cur["schema"][fm.group(1)] = fm.group(2).strip()
        rm = re.search(r"retains (\d+) years?", seen)
        if rm:
            self.cur["retention"] = int(rm.group(1))
        sm = re.search(r"\[Step (\d+)/(\d+)\D+?(\d+) remaining", seen)
        if sm:
            self.step_info = (int(sm.group(1)), int(sm.group(2)), int(sm.group(3)))
        if "Provide your best predictions" in seen or "Provide your predictions" in seen:
            self.phase = "predict"

    def _bash(self, args, result):
        cmd = str(args.get("command") or "")
        self.cur["cmds"] += 1
        rc = None
        m = re.search(r"<returncode>(-?\d+)</returncode>", result)
        if m:
            rc = int(m.group(1))
        if "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in cmd:
            self.phase = "predict"
        if rc is not None and rc != 0:
            out = result
            om = re.search(r"<output>(.*?)</output>", result, re.S)
            if om:
                out = om.group(1)
            lines = [l.strip() for l in out.splitlines() if l.strip()]
            msg = lines[-1] if lines else "(no output)"
            for l in reversed(lines):
                if re.search(r"(Error|Exception|No such file|not found|denied)", l):
                    msg = l
                    break
            self.cur["fails"].append((cmd[:70].replace("\n", " "), msg[:120], rc))
            self.last_cmd_failed = (cmd[:70].replace("\n", " "), msg[:120])
        else:
            self.last_cmd_failed = None
            for fm in re.finditer(r">\s*([\w./-]+\.(?:py|csv|json|txt|md|pkl|joblib|parquet))", cmd):
                self.files[fm.group(1)] = self.cur["year"]

    def _preds(self, args, result):
        preds = args.get("predictions") or []
        d = {}
        for p in preds:
            try:
                k = (str(p.get("locality")), str(p.get("furniture_name")), int(p.get("year")))
                d[k] = float(p.get("items_sold"))
            except Exception:
                continue
        self.cur["preds"] = d
        self.cur["submitted"] = True
        self.phase = "done"
        m = re.search(r"[Cc]omposite score:\s*(-?[\d.]+)", result)
        if m:
            self.cur["score"] = _num(m.group(1))
        for line in result.splitlines():
            lm = re.search(r"([A-Za-z][A-Za-z .'-]*?)\s*/\s*([^/]+?)\s*/\s*(\d{4})", line)
            if not lm:
                continue
            k = (lm.group(1).strip(), lm.group(2).strip(), int(lm.group(3)))
            rest = line[lm.end():]
            em = re.search(r"error\D{0,6}(-?[\d.]+)", rest, re.I)
            am = re.search(r"actual\D{0,6}(-?[\d.]+)", rest, re.I)
            err = _num(em.group(1)) if em else None
            act = _num(am.group(1)) if am else None
            self.cur["entry"][k] = (err, act)
            if act is None and err is not None and k in d:
                pv = d[k]
                if pv - err < 0:
                    act = pv + err
                elif err == 0:
                    act = pv
            if act is not None:
                self.actuals[k] = act

    # ---------- brief ----------
    def brief(self) -> str:
        try:
            return self._brief()[:4000]
        except Exception:
            return "REGISTER: (unavailable this step)"

    def _brief(self):
        out = []
        c = self.cur or {}
        # 1. STATUS / BLOCKERS
        out.append("== STATUS ==")
        st = "instance %s | year %s | phase %s" % (c.get("label") or "?", c.get("year"), self.phase)
        if self.step_info:
            st += " | step %d/%d, %d remaining" % self.step_info
        out.append(st)
        if self.step_info and self.phase == "analysis" and self.step_info[2] <= 3:
            out.append("! Only %d steps left and not yet submitted (unsubmitted = budget exhausted)." % self.step_info[2])
        if self.last_cmd_failed:
            out.append("! Last command failed: %s -> %s" % self.last_cmd_failed)
        if self.phase == "predict" and c.get("required"):
            out.append("Required entries this instance: %d (omitted ones get worst score)." % len(c["required"]))

        # 2. SCORE HISTORY
        out.append("== SCORES vs VOLUME SUBMITTED ==")
        done = [i for i in self.instances if i["submitted"]]
        if not done:
            out.append("(no submissions yet)")
        for i in done[-10:]:
            tot = sum(i["preds"].values()) if i["preds"] else 0.0
            n = len(i["preds"])
            byloc = {}
            for (loc, _, _), v in i["preds"].items():
                byloc[loc] = byloc.get(loc, 0.0) + v
            locs = " ".join("%s %s" % (l[:3], _fmt(v)) for l, v in sorted(byloc.items()))
            line = "y%s: score %s | total %s in %d cells (req %d) | %s" % (
                i["year"], _fmt(i["score"]) if i["score"] is None else "%.3f" % i["score"],
                _fmt(tot), n, len(i["required"]), locs)
            kn = [(k, v) for k, v in i["preds"].items() if k in self.actuals]
            if kn:
                sp = sum(v for _, v in kn)
                sa = sum(self.actuals[k] for k, _ in kn)
                if sa > 0:
                    line += " | on %d known cells pred/actual=%.2f" % (len(kn), sp / sa)
            out.append(line)
        scored = [i for i in done if i["score"] is not None]
        if len(scored) >= 2:
            b = max(scored, key=lambda i: i["score"])
            w = min(scored, key=lambda i: i["score"])
            out.append("best y%s %.3f (total %s); worst y%s %.3f (total %s)" % (
                b["year"], b["score"], _fmt(sum(b["preds"].values())),
                w["year"], w["score"], _fmt(sum(w["preds"].values()))))

        # 3. ACTUALS / ENTRY ERRORS
        out.append("== REALIZED ACTUALS FROM FEEDBACK ==")
        if not self.actuals:
            errs = [(k, e) for i in done for k, e in i["entry"].items() if e[0] is not None]
            if errs:
                for k, e in errs[-6:]:
                    out.append("%s/%s/%d err %s" % (k[0], k[1], k[2], _fmt(e[0])))
            else:
                out.append("(none reported per entry)")
        else:
            grp = {}
            for (loc, prod, yr), v in self.actuals.items():
                grp.setdefault((loc, prod), []).append((yr, v))
            for (loc, prod), lst in sorted(grp.items())[:15]:
                out.append("%s|%s: %s" % (loc, prod, " ".join("%d=%s" % (y, _fmt(v)) for y, v in sorted(lst))))

        # 4. PRIOR FORECASTS FOR CURRENT CELLS
        out.append("== YOUR EARLIER FORECASTS FOR THIS INSTANCE'S CELLS ==")
        req = c.get("required") or []
        prior = [i for i in self.instances if i["submitted"] and i is not c]
        rows = {}
        for k in req:
            for i in reversed(prior):
                if k in i["preds"]:
                    rows.setdefault((k[0], k[1]), []).append((k[2], i["preds"][k], i["year"]))
                    break
        if not rows:
            out.append("(no overlap with earlier submissions)")
        else:
            for (loc, prod), lst in sorted(rows.items())[:18]:
                out.append("%s|%s: %s (made y%s)" % (
                    loc, prod, " ".join("%d=%s" % (y, _fmt(v)) for y, v, _ in sorted(lst)), lst[0][2]))

        # 5. DATA ROOM
        out.append("== DATA ROOM (this year vs previous) ==")
        prev = None
        for i in reversed(self.instances):
            if i is not c and i["schema"]:
                prev = i
                break
        sch = c.get("schema") or {}
        out.append("retention: %s yrs | files: %d" % (c.get("retention"), len(sch)))
        for f, cols in sorted(sch.items()):
            tag = ""
            if prev is not None:
                pc = prev["schema"].get(f)
                if pc is None:
                    tag = " [NEW file]"
                elif pc != cols:
                    tag = " [cols changed; was: %s]" % pc[:80]
            out.append("%s: %s%s" % (f, cols[:110], tag))
        if prev is not None:
            gone = [f for f in prev["schema"] if f not in sch]
            if gone:
                out.append("gone since y%s: %s" % (prev["year"], ", ".join(gone)))

        # 6. COMMAND FAILURES
        out.append("== COMMAND FAILURES (this instance) ==")
        fails = c.get("fails") or []
        if not fails:
            out.append("(none; %d commands run)" % c.get("cmds", 0))
        else:
            cnt = {}
            for cmd, msg, rc in fails:
                cnt.setdefault(msg, []).append(cmd)
            for msg, cmds in list(cnt.items())[-5:]:
                out.append("x%d %s <- e.g. %s" % (len(cmds), msg, cmds[-1]))

        # 7. SAVED FILES
        out.append("== FILES YOU WROTE (persist across years) ==")
        if not self.files:
            out.append("(none recorded)")
        else:
            items = list(self.files.items())[-12:]
            out.append(", ".join("%s(y%s)" % (f, y) for f, y in items))

        text = "\n".join(out)
        if len(text) > 4000:
            text = text[:3990] + "\n..."
        return text
