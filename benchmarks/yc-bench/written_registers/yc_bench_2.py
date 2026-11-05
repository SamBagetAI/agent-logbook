import json
import re
import shlex
from datetime import datetime


def _j(x):
    if isinstance(x, (dict, list)):
        return x
    try:
        return json.loads(x)
    except Exception:
        return None


def _pt(s):
    try:
        return datetime.fromisoformat(str(s)[:26])
    except Exception:
        return None


def _hrs(a, b):
    try:
        return (b - a).total_seconds() / 3600.0
    except Exception:
        return None


def _m(c):
    try:
        return "${:,.0f}".format(c / 100.0)
    except Exception:
        return "$?"


def _ab(d):
    p = [x for x in str(d).split("_") if x]
    if len(p) > 1:
        return "".join(x[0] for x in p)
    return str(d)[:3]


def _cents(s):
    try:
        return int(round(float(str(s).replace(",", "")) * 100))
    except Exception:
        return None


FINAL = ("done", "cancelled", "gone")


class Register:
    def __init__(self) -> None:
        self.now = None
        self.start_time = None
        self.horizon = None
        self.funds = None
        self.start_funds = None
        self.payroll = None
        self.start_payroll = None
        self.active_n = None
        self.planned_n = None
        self.prestige = {}
        self.emps = {}
        self.tasks = {}
        self.order = []
        self.market = {}
        self.last_browse = []
        self.disputes = {}
        self.cmds = {}
        self.last_errors = []
        self.idle_resumes = 0
        self.bankrupt = False
        self.decisions = 0
        self.since_resume = 0
        self.resume_decisions = 0
        self.oneshot = 0
        self.mismatch = ""

    # ---------- task records ----------
    def _task(self, tid):
        t = self.tasks.get(tid)
        if t is None:
            t = {"id": tid, "client": None, "reward": None, "listed": {}, "reqs": {},
                 "inspected": False, "deadline": None, "team": [], "status": "?",
                 "disp_at": None, "result": None, "margin": "", "bump": 0, "funds": 0,
                 "progress": None, "prog_at": None, "elapsed": None, "at_risk": None,
                 "tdelta": 0.0, "req_trust": None, "req_prest": None}
            self.tasks[tid] = t
            self.order.append(tid)
            mk = self.market.get(tid)
            if mk:
                self._fill_listing(t, mk)
        return t

    def _fill_listing(self, t, d):
        if d.get("client_name"):
            t["client"] = d["client_name"]
        if d.get("reward_funds_cents") is not None:
            t["reward"] = d["reward_funds_cents"]
        t["req_trust"] = d.get("required_trust", t["req_trust"])
        t["req_prest"] = d.get("required_prestige", t["req_prest"])
        for r in d.get("requirements") or []:
            try:
                t["listed"][r["domain"]] = float(r.get("required_qty") or 0)
            except Exception:
                pass

    def _fill_detail(self, t, d):
        if d.get("client_name"):
            t["client"] = d["client_name"]
        if d.get("reward_funds_cents") is not None:
            t["reward"] = d["reward_funds_cents"]
        reqs = d.get("requirements")
        if isinstance(reqs, list) and reqs:
            for r in reqs:
                try:
                    t["reqs"][r["domain"]] = float(r.get("required_qty") or 0)
                except Exception:
                    pass
            t["inspected"] = True
        if d.get("deadline"):
            t["deadline"] = d["deadline"]
        if d.get("progress_pct") is not None:
            try:
                t["progress"] = float(d["progress_pct"])
                t["prog_at"] = self.now
            except Exception:
                pass
        if d.get("at_risk") is not None:
            t["at_risk"] = d["at_risk"]
        st = d.get("status")
        if st in ("planned", "active") and t["status"] not in FINAL:
            t["status"] = st
        a = d.get("assignments")
        if isinstance(a, list) and a:
            team = []
            for x in a:
                if isinstance(x, str):
                    team.append(x)
                elif isinstance(x, dict):
                    n = x.get("employee_name") or x.get("name") or x.get("employee")
                    if n:
                        team.append(str(n))
            if team:
                t["team"] = team

    def _work(self, t):
        return t["reqs"] if t["reqs"] else t["listed"]

    def _load(self, emp, tid):
        n = 1
        for k, t in self.tasks.items():
            if k != tid and t["status"] == "active" and emp in t["team"]:
                n += 1
        return n

    def _est(self, t):
        w = self._work(t)
        if not t["team"] or not w or not self.emps:
            return None
        frac = 1.0
        if t["progress"] is not None:
            frac = max(0.0, 1 - t["progress"] / 100.0)
        worst = 0.0
        for d, q in w.items():
            rate = 0.0
            for e in t["team"]:
                r = self.emps.get(e, {}).get("rates", {}).get(d)
                if isinstance(r, (int, float)):
                    rate += float(r) / self._load(e, t["id"])
            if rate <= 0:
                return None
            worst = max(worst, q * frac / rate)
        return worst

    def _ratio(self, t):
        if not t["inspected"] or not t["listed"]:
            return None
        l = sum(t["listed"].values())
        if l <= 0:
            return None
        return sum(t["reqs"].values()) / l

    def _complete(self, tid, client, succ, funds, margin, bump, tdelta, when):
        t = self._task(tid)
        if t["result"]:
            return
        if client:
            t["client"] = client
        t["result"] = "OK" if succ else "FAILED"
        t["status"] = "done"
        t["margin"] = margin or ""
        t["bump"] = bump or 0
        t["funds"] = funds or 0
        try:
            t["tdelta"] = float(tdelta or 0)
        except Exception:
            pass
        a, b = _pt(t["disp_at"]), _pt(when)
        t["elapsed"] = _hrs(a, b) if a and b else None

    # ---------- observe ----------
    def observe(self, step: dict) -> None:
        try:
            self._observe(step)
        except Exception:
            pass

    def _observe(self, step):
        self.decisions += 1
        self.since_resume += 1
        self.last_errors = []
        action = step.get("action") or {}
        cmds = []
        try:
            args = action.get("arguments") or {}
            cmds = args.get("commands") or ([args["command"]] if args.get("command") else [])
        except Exception:
            cmds = []
        self._cycle_stats(cmds)
        res = _j(step.get("result") or "")
        if isinstance(res, list):
            for item in res:
                try:
                    if isinstance(item, dict):
                        self._process(str(item.get("command", "")), item.get("output", ""))
                except Exception:
                    pass
        self._parse_seen(str(step.get("seen") or ""))

    def _cycle_stats(self, cmds):
        try:
            acc, asg, dis = set(), set(), set()
            resume = False
            for c in cmds:
                c = str(c)
                m = re.search(r"--task-id\s+(\S+)", c)
                tid = m.group(1).strip("'\"") if m else None
                if "task accept" in c and tid:
                    acc.add(tid)
                elif "task assign" in c and tid:
                    asg.add(tid)
                elif "task dispatch" in c and tid:
                    dis.add(tid)
                elif "sim resume" in c:
                    resume = True
            if resume:
                self.resume_decisions += 1
                if acc & asg & dis:
                    self.oneshot += 1
        except Exception:
            pass

    def _parse_seen(self, s):
        m = re.search(r"Current time\*\*:\s*(\S+)", s) or re.search(r"current_time:\s*(\S+)", s)
        if m:
            self.now = m.group(1)
            if self.start_time is None:
                self.start_time = self.now
        m = re.search(r"Horizon end\*\*:\s*(\S+)", s) or re.search(r"horizon_end:\s*(\S+)", s)
        if m:
            self.horizon = m.group(1)
        m = re.search(r"\((-?\d+) cents\)", s)
        if m:
            self.funds = int(m.group(1))
        else:
            m = re.search(r"funds:\s*\$(-?[\d,\.]+)", s)
            if m and _cents(m.group(1)) is not None:
                self.funds = _cents(m.group(1))
        if self.start_funds is None and self.funds is not None:
            self.start_funds = self.funds
        m = re.search(r"[Mm]onthly[ _]payroll\**:\s*\$(-?[\d,\.]+)", s)
        if m and _cents(m.group(1)) is not None:
            self.payroll = _cents(m.group(1))
            if self.start_payroll is None:
                self.start_payroll = self.payroll
        m = re.search(r"Active tasks\*\*:\s*(\d+)", s) or re.search(r"active_tasks:\s*(\d+)", s)
        if m:
            self.active_n = int(m.group(1))
        m = re.search(r"Planned tasks\*\*:\s*(\d+)", s) or re.search(r"planned_tasks:\s*(\d+)", s)
        if m:
            self.planned_n = int(m.group(1))
        if "bankrupt. Run will terminate" in s or "BANKRUPTCY" in s:
            self.bankrupt = True
        if "Events since last turn" in s:
            part = s.split("Events since last turn", 1)[1]
            for line in part.splitlines():
                line = line.strip()
                mm = re.match(r"^- (\S+?)(?: \(client: (.*?)\))?: (SUCCESS|FAILED)(.*)$", line)
                if mm:
                    rest = mm.group(4)
                    f = re.search(r"^\s*\+\$([\d,]+)", rest)
                    mg = re.search(r"\[([^\]]+)\]", rest)
                    b = re.search(r"\+\$([\d,]+)/mo payroll", rest)
                    self._complete(mm.group(1), mm.group(2), mm.group(3) == "SUCCESS",
                                   _cents(f.group(1)) if f else 0, mg.group(1) if mg else "",
                                   _cents(b.group(1)) if b else 0, 0, self.now)
                    continue
                mm = re.match(r"^- PAYMENT DISPUTE from (.*?): -\$([\d,\.]+)", line)
                if mm:
                    d = self.disputes.setdefault(mm.group(1), [0, 0])
                    d[0] += 1
                    d[1] += _cents(mm.group(2)) or 0
        mine_a = [t for t in self.tasks.values() if t["status"] == "active"]
        mine_p = [t for t in self.tasks.values() if t["status"] == "planned"]
        self.mismatch = ""
        if self.active_n == 0:
            for t in mine_a:
                t["status"] = "gone"
        if self.planned_n == 0:
            for t in mine_p:
                t["status"] = "gone"
        mine_a = [t for t in self.tasks.values() if t["status"] == "active"]
        if self.active_n is not None and len(mine_a) != self.active_n:
            self.mismatch = "Register tracks %d active, state says %d (task list --status active resyncs)." % (
                len(mine_a), self.active_n)

    def _process(self, cmd, out):
        o = _j(out)
        if not isinstance(o, dict):
            o = {"stdout": str(out)}
        stdout = o.get("stdout") or ""
        data = _j(stdout) if stdout else None
        ok = (o.get("ok") is not False) and (o.get("exit_code") in (0, None))
        if isinstance(data, dict) and data.get("ok") is False:
            ok = False
        try:
            toks = shlex.split(cmd)
        except Exception:
            toks = cmd.split()
        if "yc-bench" in toks:
            toks = toks[toks.index("yc-bench") + 1:]
        words, args, i = [], {}, 0
        while i < len(toks):
            tk = toks[i]
            if tk.startswith("--"):
                v = toks[i + 1] if i + 1 < len(toks) and not toks[i + 1].startswith("--") else ""
                args[tk[2:]] = v
                i += 2 if v else 1
            else:
                if len(words) < 2:
                    words.append(tk)
                i += 1
        verb = " ".join(words) or cmd[:30]
        tid = args.get("task-id", "")
        rec = self.cmds.setdefault(verb, {"ok": 0, "fail": 0, "msgs": {}})
        if not ok:
            err = str(o.get("stderr") or "").strip()
            if isinstance(data, dict):
                err = err or str(data.get("error") or data.get("detail") or data.get("message") or "")
            err = (err or str(stdout)).strip().replace("\n", " ")[:120]
            extra = ""
            if verb == "task accept" and tid in self.market:
                mk = self.market[tid]
                extra = " [needs prestige %s trust %s, client %s]" % (
                    mk.get("required_prestige"), mk.get("required_trust"), mk.get("client_name"))
            rec["fail"] += 1
            mm = rec["msgs"].setdefault(err, [0, ""])
            mm[0] += 1
            mm[1] = tid + extra
            self.last_errors.append("%s %s: %s%s" % (verb, tid, err[:100], extra))
            return
        rec["ok"] += 1
        if not isinstance(data, dict):
            return
        if verb == "market browse":
            self.last_browse = []
            for t in data.get("tasks") or []:
                if isinstance(t, dict) and t.get("task_id"):
                    self.market[t["task_id"]] = t
                    self.last_browse.append(t["task_id"])
            if len(self.market) > 400:
                for k in list(self.market)[:len(self.market) - 400]:
                    self.market.pop(k, None)
        elif verb == "employee list":
            for e in data.get("employees") or []:
                if isinstance(e, dict) and e.get("name"):
                    self.emps[e["name"]] = {"rates": e.get("skill_rates") or {},
                                            "salary": e.get("salary_cents"),
                                            "tier": e.get("tier") or ""}
        elif verb == "company status":
            if isinstance(data.get("prestige"), dict):
                self.prestige = data["prestige"]
            if data.get("horizon_end"):
                self.horizon = data["horizon_end"]
        elif verb == "task accept":
            t = self._task(data.get("task_id") or tid)
            if t["status"] not in FINAL:
                t["status"] = "planned"
            if data.get("deadline"):
                t["deadline"] = data["deadline"]
        elif verb == "task inspect":
            self._fill_detail(self._task(data.get("task_id") or tid), data)
        elif verb == "task list":
            seen_ids = set()
            for x in data.get("tasks") or []:
                if isinstance(x, dict) and x.get("task_id"):
                    seen_ids.add(x["task_id"])
                    self._fill_detail(self._task(x["task_id"]), x)
            if args.get("status") == "active":
                for t in self.tasks.values():
                    if t["status"] == "active" and t["id"] not in seen_ids:
                        t["status"] = "gone"
        elif verb.startswith("task assign"):
            t = self._task(data.get("task_id") or tid)
            team = data.get("total_assigned") or data.get("assigned_employees") or data.get("newly_assigned")
            if isinstance(team, list):
                t["team"] = [str(x) for x in team]
        elif verb == "task dispatch":
            t = self._task(data.get("task_id") or tid)
            team = data.get("assigned_employees")
            if isinstance(team, list) and team:
                t["team"] = [str(x) for x in team]
            t["status"] = "active"
            t["disp_at"] = self.now
        elif verb == "task cancel":
            t = self._task(data.get("task_id") or tid)
            t["status"] = "cancelled"
            t["result"] = "CANCELLED"
            note = "prestige -%s" % data.get("cancel_penalty_per_domain")
            if t["progress"] is not None:
                note += ", at %.0f%%" % t["progress"]
            dl, nw = _pt(t["deadline"]), _pt(self.now)
            if dl and nw and nw > dl:
                note += ", %.0fh past deadline" % _hrs(dl, nw)
            t["margin"] = note
        elif verb == "sim resume":
            self.since_resume = 0
            if self.active_n == 0:
                self.idle_resumes += 1
            newt = data.get("new_sim_time")
            if newt:
                self.now = newt
            if data.get("bankrupt"):
                self.bankrupt = True
            for ev in data.get("wake_events") or []:
                if not isinstance(ev, dict):
                    continue
                typ = ev.get("type")
                if typ == "task_completed":
                    self._complete(ev.get("task_title") or ev.get("task_id") or "?",
                                   ev.get("client_name"), bool(ev.get("success")),
                                   ev.get("funds_delta") or 0, str(ev.get("deadline_margin") or ""),
                                   ev.get("salary_bump_total_cents") or 0,
                                   ev.get("trust_delta"), newt)
                elif typ == "task_half":
                    tt = ev.get("task_title")
                    if tt and tt in self.tasks:
                        try:
                            self.tasks[tt]["progress"] = float(ev.get("milestone_pct") or 50)
                            self.tasks[tt]["prog_at"] = newt
                        except Exception:
                            pass

    # ---------- brief ----------
    def brief(self) -> str:
        try:
            out = self._brief()
            if len(out) > 3990:
                out = out[:3975] + "\n...(cut)"
            return out
        except Exception:
            return "[Register unavailable this step]"

    def _client_stats(self):
        cs = {}
        for tid in self.order:
            t = self.tasks[tid]
            if t["status"] == "?":
                continue
            c = cs.setdefault(t["client"] or "?", {"n": 0, "ok": 0, "fail": 0, "cancel": 0,
                                                    "open": 0, "earned": 0, "trust": 0.0,
                                                    "bad": [], "ratios": []})
            c["n"] += 1
            c["trust"] += t["tdelta"] or 0
            if t["result"] == "OK":
                c["ok"] += 1
                c["earned"] += t["funds"] or 0
            elif t["result"] == "FAILED":
                c["fail"] += 1
                c["bad"].append("%s failed" % tid)
            elif t["result"] == "CANCELLED":
                c["cancel"] += 1
                c["bad"].append("%s cancelled (%s)" % (tid, t["margin"]))
            elif t["status"] in ("active", "planned"):
                c["open"] += 1
            r = self._ratio(t)
            if r is not None:
                c["ratios"].append("%s x%.1f" % (tid, r))
        return cs

    def _proj(self, t, now):
        p, pa, da = t["progress"], _pt(t["prog_at"]), _pt(t["disp_at"])
        if p and p > 0 and pa and da and now:
            el = _hrs(da, pa)
            if el and el > 0:
                total = el / (p / 100.0)
                return max(0.0, total - _hrs(da, now)), "from %.0f%% progress" % p
        est = self._est(t)
        if est:
            return est, "qty/rate" + ("" if t["inspected"] else ", listed qty")
        return None, ""

    def _brief(self):
        now = _pt(self.now)
        cs = self._client_stats()
        any_big_ratio = any(self._ratio(t) and self._ratio(t) > 1.2 for t in self.tasks.values())
        L = []
        st = "== STATUS == %s | funds %s" % ((self.now or "?")[:16], _m(self.funds) if self.funds is not None else "?")
        if self.start_funds is not None and self.funds is not None:
            st += " (start %s)" % _m(self.start_funds)
        if self.payroll:
            st += " | payroll %s/mo" % _m(self.payroll)
        h = _pt(self.horizon)
        if h and now:
            st += " | %d days to end" % int(_hrs(now, h) / 24)
        st += " | active %s planned %s" % (self.active_n, self.planned_n)
        if self.prestige:
            st += "\nprestige: " + " ".join("%s%.2f" % (_ab(d), v) for d, v in self.prestige.items()
                                            if isinstance(v, (int, float)))
        L.append(st)
        # BLOCKERS
        bl = []
        if self.bankrupt:
            bl.append("BANKRUPT.")
        if self.funds is not None and self.payroll and self.funds < self.payroll:
            bl.append("Funds below one monthly payroll.")
        if self.active_n == 0 and not self.planned_n:
            bl.append("No tasks running: sim resume now earns nothing.")
        for tid in self.order:
            t = self.tasks[tid]
            if t["status"] not in ("active", "planned"):
                continue
            if t["status"] == "planned":
                bl.append("%s accepted, not dispatched (team: %s)." % (tid, ",".join(t["team"]) or "none"))
            c = cs.get(t["client"] or "?")
            if c and (c["fail"] or c["cancel"]):
                msg = "%s is from %s: its earlier tasks %d ok, %d failed, %d cancelled" % (
                    tid, t["client"], c["ok"], c["fail"], c["cancel"])
                if c["ratios"]:
                    msg += "; actual/listed work " + ", ".join(c["ratios"][-2:])
                bl.append(msg + ".")
            r = self._ratio(t)
            if r and r > 1.2:
                bl.append("%s actual work x%.1f its market listing." % (tid, r))
            elif any_big_ratio and t["listed"] and not t["inspected"]:
                bl.append("%s actual qty not yet seen (task inspect shows it)." % tid)
            if t["status"] == "active" and t["deadline"] and now:
                dl = _pt(t["deadline"])
                left = _hrs(now, dl) if dl else None
                if left is None:
                    continue
                pj, how = self._proj(t, now)
                if left < 0:
                    bl.append("%s is %.0fh past its deadline." % (tid, -left))
                elif pj and pj > left:
                    bl.append("%s needs ~%.0fh (%s) > %.0fh left." % (tid, pj, how, left))
                elif t["at_risk"]:
                    bl.append("%s flagged at_risk by task list." % tid)
        if self.mismatch:
            bl.append(self.mismatch)
        if self.idle_resumes:
            bl.append("sim resume run with 0 active tasks: %d times." % self.idle_resumes)
        for e in self.last_errors[:3]:
            bl.append("LAST STEP ERROR " + e)
        L.append("== BLOCKERS ==\n" + ("\n".join("- " + b for b in bl[:10]) if bl else "- none"))
        # CLIENTS
        cl = []
        items = sorted(cs.items(), key=lambda kv: (-(kv[1]["fail"] + kv[1]["cancel"]), -kv[1]["ok"]))
        for n, c in items[:7]:
            line = "%s: %d ok, %d failed, %d cancelled, %d open; earned %s; trust +%.1f" % (
                n, c["ok"], c["fail"], c["cancel"], c["open"], _m(c["earned"]), c["trust"])
            d = self.disputes.get(n)
            if d:
                line += "; DISPUTES %d, clawed %s" % (d[0], _m(d[1]))
            if c["ratios"]:
                line += "; actual/listed: " + ", ".join(c["ratios"][-3:])
            if c["bad"]:
                line += "\n   " + "; ".join(c["bad"][-2:])
            cl.append(line)
        bad_c = set(n for n, c in cs.items() if c["fail"] or c["cancel"])
        mk = [x for x in self.last_browse if self.market.get(x, {}).get("client_name") in bad_c]
        if mk:
            cl.append("Last market browse, tasks from clients with fails/cancels: " + ", ".join(mk[:8]))
        gated = [x for x in self.last_browse if (self.market.get(x, {}).get("required_trust") or 0) > 0]
        if gated:
            top = sorted(gated, key=lambda x: -(self.market[x].get("reward_funds_cents") or 0))[:4]
            cl.append("Gated tasks in last browse: " + ", ".join(
                "%s %s %s trust%s prest%s" % (x, self.market[x].get("client_name"),
                                              _m(self.market[x].get("reward_funds_cents") or 0),
                                              self.market[x].get("required_trust"),
                                              self.market[x].get("required_prestige")) for x in top))
        L.append("== CLIENT RECORD (this run) ==\n" + ("\n".join(cl) if cl else "(no tasks taken yet)"))
        # YOUR TASKS
        tl = []
        for tid in self.order:
            t = self.tasks[tid]
            if t["status"] not in ("active", "planned"):
                continue
            req = " ".join("%s%.0f" % (_ab(d), q) for d, q in self._work(t).items())
            line = "%s [%s] %s %s | %s%s" % (tid, t["status"], t["client"] or "?",
                                            _m(t["reward"]) if t["reward"] else "$?", req or "?",
                                            "" if t["inspected"] else "(listed)")
            if t["progress"] is not None:
                line += " prog %.0f%%" % t["progress"]
            line += " | team %s" % (",".join(t["team"]) or "-")
            dl = _pt(t["deadline"])
            if dl and now:
                line += " | %.0fh to deadline" % _hrs(now, dl)
            pj, how = self._proj(t, now)
            if pj:
                line += " | ~%.0fh to finish (%s)" % (pj, how)
            tl.append(line)
        L.append("== YOUR TASKS ==\n" + ("\n".join(tl[:6]) if tl else "(none)"))
        # FINISHED
        fl = []
        done = [self.tasks[x] for x in self.order if self.tasks[x]["result"]]
        for t in done[-5:]:
            req = " ".join("%s%.0f" % (_ab(d), q) for d, q in self._work(t).items())
            line = "%s %s %s" % (t["id"], t["client"] or "?", t["result"])
            if t["result"] == "OK" and t["funds"]:
                line += " +%s" % _m(t["funds"])
            line += " | %s team%d" % (req or "?", len(t["team"]))
            if t["elapsed"] is not None:
                line += " took %.0fh" % t["elapsed"]
            if t["margin"]:
                line += " | " + t["margin"]
            fl.append(line)
        fl.append("Totals: %d ok, %d failed, %d cancelled." % (
            sum(1 for t in done if t["result"] == "OK"),
            sum(1 for t in done if t["result"] == "FAILED"),
            sum(1 for t in done if t["result"] == "CANCELLED")))
        L.append("== FINISHED TASKS ==\n" + "\n".join(fl))
        # PACE
        pace = "Decisions: %d; since last sim resume: %d; decisions with sim resume: %d (of which %d also accepted+assigned+dispatched in the same decision)." % (
            self.decisions, self.since_resume, self.resume_decisions, self.oneshot)
        s0 = _pt(self.start_time)
        if s0 and now:
            days = _hrs(s0, now) / 24.0
            pace += " Sim advanced %.0f days = %.2f days/decision." % (days, days / max(1, self.decisions))
        L.append("== PACE ==\n" + pace)
        # EMPLOYEES
        el = []
        for n in sorted(self.emps, key=lambda x: (len(x), x)):
            e = self.emps[n]
            rates = " ".join("%s%.1f" % (_ab(d), r) for d, r in e["rates"].items() if isinstance(r, (int, float)))
            on = [t["id"] for t in self.tasks.values() if t["status"] in ("active", "planned") and n in t["team"]]
            el.append("%s %s %s %s%s" % (n, e["tier"][:3], _m(e["salary"]) if e["salary"] else "?", rates,
                                         (" | on " + ",".join(on)) if on else " | free"))
        L.append("== EMPLOYEES ==\n" + ("\n".join(el[:12]) if el else "(run employee list)"))
        return "\n".join(L)
