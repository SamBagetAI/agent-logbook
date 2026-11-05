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


def _money_to_cents(s):
    try:
        return int(round(float(s.replace(",", "")) * 100))
    except Exception:
        return None


class Register:
    def __init__(self) -> None:
        self.now = None
        self.horizon = None
        self.funds = None
        self.start_funds = None
        self.payroll = None
        self.start_payroll = None
        self.active_n = None
        self.planned_n = None
        self.next_payroll = None
        self.prestige = {}
        self.emps = {}
        self.tasks = {}
        self.order = []
        self.market = {}
        self.clients = {}
        self.cmds = {}
        self.last_errors = []
        self.idle_resumes = 0
        self.idle_net = 0
        self.bankrupt = False
        self.payrolls = 0
        self.factors = []

    # ---------- helpers ----------
    def _client(self, name):
        name = name or "?"
        c = self.clients.get(name)
        if c is None:
            c = {"ok": 0, "fail": 0, "cancel": 0, "earned": 0, "claw": 0,
                 "disputes": 0, "trust": None, "tdelta": 0.0, "last_fail": "", "hist": ""}
            self.clients[name] = c
        return c

    def _task(self, tid):
        t = self.tasks.get(tid)
        if t is None:
            t = {"id": tid, "client": None, "reward": None, "reqs": {}, "rem": {},
                 "deadline": None, "team": [], "status": "?", "disp_at": None,
                 "disp_est": None, "result": None, "margin": "", "bump": 0,
                 "progress": None, "elapsed": None, "factor": None, "at_risk": None}
            self.tasks[tid] = t
            self.order.append(tid)
            m = self.market.get(tid)
            if m:
                self._fill_from(t, m)
        return t

    def _fill_from(self, t, d):
        if d.get("client_name"):
            t["client"] = d.get("client_name")
        if d.get("reward_funds_cents") is not None:
            t["reward"] = d.get("reward_funds_cents")
        reqs = d.get("requirements")
        if isinstance(reqs, list):
            for r in reqs:
                if isinstance(r, dict) and r.get("domain"):
                    try:
                        t["reqs"][r["domain"]] = float(r.get("required_qty") or 0)
                        if r.get("remaining_qty") is not None:
                            t["rem"][r["domain"]] = float(r.get("remaining_qty"))
                    except Exception:
                        pass
        if d.get("deadline"):
            t["deadline"] = d.get("deadline")
        if d.get("progress_pct") is not None:
            t["progress"] = d.get("progress_pct")
        if d.get("at_risk") is not None:
            t["at_risk"] = d.get("at_risk")
        st = d.get("status")
        if st and t.get("result") is None:
            t["status"] = st
        a = d.get("assignments")
        if isinstance(a, list) and a:
            team = []
            for x in a:
                if isinstance(x, str):
                    team.append(x)
                elif isinstance(x, dict):
                    n = x.get("employee_name") or x.get("name") or x.get("employee") or x.get("employee_id")
                    if n:
                        team.append(str(n))
            if team:
                t["team"] = team

    def _load(self, emp, exclude):
        n = 1
        for tid, t in self.tasks.items():
            if tid != exclude and t["status"] == "active" and emp in t["team"]:
                n += 1
        return n

    def _est(self, t):
        """raw work-hours: max over domains of remaining qty / team rate (split-adjusted)."""
        if not t["team"] or not t["reqs"] or not self.emps:
            return None
        worst = 0.0
        for d, q in t["reqs"].items():
            q = t["rem"].get(d, q)
            if t["progress"] is not None and d not in t["rem"]:
                try:
                    q = q * max(0.0, 1 - float(t["progress"]) / 100.0)
                except Exception:
                    pass
            rate = 0.0
            for e in t["team"]:
                r = self.emps.get(e, {}).get("rates", {}).get(d)
                if r:
                    rate += float(r) / self._load(e, t["id"])
            if rate <= 0:
                return None
            worst = max(worst, q / rate)
        return worst

    def _factor(self):
        f = sorted(self.factors)
        if not f:
            return None
        return f[len(f) // 2]

    # ---------- observe ----------
    def observe(self, step: dict) -> None:
        try:
            self._observe(step)
        except Exception:
            pass

    def _observe(self, step):
        self.last_errors = []
        action = step.get("action") or {}
        res = _j(step.get("result") or "")
        if isinstance(res, list):
            for item in res:
                try:
                    if isinstance(item, dict):
                        self._process(str(item.get("command", "")), item.get("output", ""))
                except Exception:
                    pass
        self._parse_seen(str(step.get("seen") or ""))

    def _parse_seen(self, s):
        m = re.search(r"Current time\*\*:\s*(\S+)", s) or re.search(r"current_time:\s*(\S+)", s)
        if m:
            self.now = m.group(1)
        m = re.search(r"Horizon end\*\*:\s*(\S+)", s) or re.search(r"horizon_end:\s*(\S+)", s)
        if m:
            self.horizon = m.group(1)
        m = re.search(r"\((-?\d+) cents\)", s)
        if m:
            self.funds = int(m.group(1))
        else:
            m = re.search(r"funds:\s*\$(-?[\d,\.]+)", s)
            if m:
                v = _money_to_cents(m.group(1))
                if v is not None:
                    self.funds = v
        m = re.search(r"[Mm]onthly[ _]payroll\**:\s*\$(-?[\d,\.]+)", s)
        if m:
            v = _money_to_cents(m.group(1))
            if v is not None:
                self.payroll = v
                if self.start_payroll is None:
                    self.start_payroll = v
        m = re.search(r"Active tasks\*\*:\s*(\d+)", s) or re.search(r"active_tasks:\s*(\d+)", s)
        if m:
            self.active_n = int(m.group(1))
        m = re.search(r"Planned tasks\*\*:\s*(\d+)", s) or re.search(r"planned_tasks:\s*(\d+)", s)
        if m:
            self.planned_n = int(m.group(1))
        if self.start_funds is None and self.funds is not None:
            self.start_funds = self.funds
        if "bankrupt. Run will terminate" in s or "BANKRUPTCY" in s:
            self.bankrupt = True

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
            err = (err or str(stdout)).strip().replace("\n", " ")[:150]
            extra = ""
            if verb == "task accept" and tid in self.market:
                mk = self.market[tid]
                doms = [r.get("domain") for r in mk.get("requirements", []) if isinstance(r, dict)]
                yp = ",".join("%s%.2f" % (_ab(d), self.prestige.get(d, 0)) for d in doms if d in self.prestige)
                extra = " [needs prestige %s trust %s; you: %s; client %s]" % (
                    mk.get("required_prestige"), mk.get("required_trust"), yp or "?", mk.get("client_name"))
            rec["fail"] += 1
            k = err
            mm = rec["msgs"].setdefault(k, [0, ""])
            mm[0] += 1
            mm[1] = (tid + extra) if tid else extra
            self.last_errors.append("%s %s: %s%s" % (verb, tid, err[:110], extra))
            return
        rec["ok"] += 1
        if not isinstance(data, dict):
            return
        if verb == "market browse":
            for t in data.get("tasks") or []:
                if isinstance(t, dict) and t.get("task_id"):
                    self.market[t["task_id"]] = t
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
            self.next_payroll = data.get("next_payroll") or self.next_payroll
            if data.get("sim_time"):
                self.now = data["sim_time"]
            if data.get("horizon_end"):
                self.horizon = data["horizon_end"]
        elif verb == "task accept":
            t = self._task(data.get("task_id") or tid)
            t["status"] = "planned"
            if data.get("deadline"):
                t["deadline"] = data["deadline"]
            t["accepted_at"] = data.get("accepted_at")
        elif verb in ("task inspect",):
            t = self._task(data.get("task_id") or tid)
            self._fill_from(t, data)
        elif verb == "task list":
            for x in data.get("tasks") or []:
                if isinstance(x, dict) and x.get("task_id"):
                    t = self._task(x["task_id"])
                    self._fill_from(t, x)
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
            t["disp_est"] = self._est(t)
        elif verb == "task cancel":
            t = self._task(data.get("task_id") or tid)
            t["status"] = "cancelled"
            t["result"] = "CANCELLED"
            t["margin"] = "penalty %s/domain" % data.get("cancel_penalty_per_domain")
            self._client(t["client"])["cancel"] += 1
        elif verb == "client list" or verb == "client history":
            for key in ("clients", "history", "items"):
                lst = data.get(key)
                if isinstance(lst, list):
                    for c in lst:
                        if not isinstance(c, dict):
                            continue
                        n = c.get("client_name") or c.get("name")
                        if not n:
                            continue
                        cr = self._client(n)
                        bits = []
                        for k, v in c.items():
                            if isinstance(v, (int, float)) and not isinstance(v, bool):
                                if "trust" in k and verb == "client list":
                                    cr["trust"] = v
                                elif verb == "client history":
                                    bits.append("%s=%s" % (k, round(v, 2) if isinstance(v, float) else v))
                        if bits:
                            cr["hist"] = " ".join(bits)[:90]
        elif verb == "sim resume":
            self._resume(data)

    def _resume(self, data):
        if self.active_n == 0:
            self.idle_resumes += 1
            try:
                self.idle_net += int(data.get("balance_delta") or 0)
            except Exception:
                pass
        newt = data.get("new_sim_time")
        if newt:
            self.now = newt
        try:
            self.payrolls += int(data.get("payrolls_applied") or 0)
        except Exception:
            pass
        if data.get("bankrupt"):
            self.bankrupt = True
        for ev in data.get("wake_events") or []:
            if not isinstance(ev, dict):
                continue
            typ = ev.get("type")
            if typ == "task_completed":
                tid = ev.get("task_title") or ev.get("task_id") or "?"
                t = self._task(tid)
                if ev.get("client_name"):
                    t["client"] = ev["client_name"]
                c = self._client(t["client"])
                succ = bool(ev.get("success"))
                t["result"] = "OK" if succ else "FAILED"
                t["status"] = "done"
                t["margin"] = str(ev.get("deadline_margin") or "")
                t["bump"] = ev.get("salary_bump_total_cents") or 0
                t["funds"] = ev.get("funds_delta") or 0
                try:
                    c["tdelta"] += float(ev.get("trust_delta") or 0)
                except Exception:
                    pass
                if succ:
                    c["ok"] += 1
                    c["earned"] += int(ev.get("funds_delta") or 0)
                else:
                    c["fail"] += 1
                    c["last_fail"] = "%s %s" % (tid, t["margin"])
                a, b = _pt(t["disp_at"]), _pt(newt)
                el = _hrs(a, b) if a and b else None
                t["elapsed"] = el
                if el is not None and t["disp_est"]:
                    t["factor"] = el / t["disp_est"]
                    if succ:
                        self.factors.append(t["factor"])
            elif typ == "payment_dispute":
                c = self._client(ev.get("client_name"))
                c["disputes"] += 1
                try:
                    c["claw"] += int(ev.get("clawback_cents") or 0)
                except Exception:
                    pass
            elif typ == "bankruptcy":
                self.bankrupt = True

    # ---------- brief ----------
    def brief(self) -> str:
        try:
            return self._brief()[:3990]
        except Exception:
            return "[Register unavailable this step]"

    def _brief(self):
        now = _pt(self.now)
        fac = self._factor()
        L = []
        # STATUS
        st = "== STATUS == %s | funds %s" % ((self.now or "?")[:16], _m(self.funds) if self.funds is not None else "?")
        if self.payroll:
            st += " | payroll %s/mo" % _m(self.payroll)
            if self.start_payroll and self.payroll != self.start_payroll:
                st += " (start %s)" % _m(self.start_payroll)
            if self.funds is not None:
                st += " | covers %.1f payrolls" % (self.funds / float(self.payroll))
        if self.next_payroll:
            st += " | next payroll %s" % str(self.next_payroll)[:10]
        h = _pt(self.horizon)
        if h and now:
            st += " | %d days to end" % int(_hrs(now, h) / 24)
        st += " | active %s planned %s" % (self.active_n, self.planned_n)
        if self.prestige:
            st += "\nprestige: " + " ".join("%s%.2f" % (_ab(d), v) for d, v in self.prestige.items() if isinstance(v, (int, float)))
        L.append(st)
        # BLOCKERS
        bl = []
        if self.bankrupt:
            bl.append("BANKRUPT.")
        if self.funds is not None and self.payroll and self.funds < self.payroll:
            bl.append("Funds below one monthly payroll.")
        if self.active_n == 0 and not self.planned_n:
            bl.append("No tasks running: sim resume now earns nothing.")
        pend = [t for t in self.tasks.values() if t["status"] == "planned"]
        for t in pend:
            bl.append("%s accepted but not dispatched (team: %s)." % (t["id"], ",".join(t["team"]) or "none assigned"))
        for t in self.tasks.values():
            if t["status"] != "active" or not t["deadline"] or not now:
                continue
            dl = _pt(t["deadline"])
            left = _hrs(now, dl) if dl else None
            if left is None:
                continue
            est = self._est(t)
            if left < 0:
                bl.append("%s is past its deadline (%.0fh ago)." % (t["id"], -left))
            elif est and fac and est * fac > left:
                bl.append("%s projected %.0fh clock (est x run factor) > %.0fh left." % (t["id"], est * fac, left))
            elif t["at_risk"]:
                bl.append("%s flagged at_risk by task list." % t["id"])
        split = {}
        for t in self.tasks.values():
            if t["status"] == "active":
                for e in t["team"]:
                    split[e] = split.get(e, 0) + 1
        sp = ["%s x%d" % (e, n) for e, n in sorted(split.items()) if n > 1]
        if sp:
            bl.append("Employees split across tasks (rate/N): " + ", ".join(sp))
        if self.idle_resumes:
            bl.append("sim resume with 0 active tasks: %d times, net %s." % (self.idle_resumes, _m(self.idle_net)))
        for e in self.last_errors[:4]:
            bl.append("LAST STEP ERROR " + e)
        L.append("== BLOCKERS ==\n" + ("\n".join("- " + b for b in bl) if bl else "- none"))
        # YOUR TASKS
        tl = []
        for tid in self.order:
            t = self.tasks[tid]
            if t["status"] not in ("active", "planned"):
                continue
            req = " ".join("%s%.0f" % (_ab(d), q) for d, q in t["reqs"].items())
            line = "%s [%s] %s %s | %s" % (tid, t["status"], t["client"] or "?", _m(t["reward"]) if t["reward"] else "$?", req or "reqs ?")
            if t["progress"] is not None:
                line += " prog %s%%" % t["progress"]
            line += " | team %s" % (",".join(t["team"]) or "-")
            dl = _pt(t["deadline"])
            if dl:
                line += " | dl %s" % str(t["deadline"])[5:16]
                if now:
                    line += " (%.0fh left)" % _hrs(now, dl)
            est = self._est(t)
            if est:
                line += " | qty/rate %.0fh" % est
                if fac:
                    line += " ~%.0fh clock" % (est * fac)
            tl.append(line)
        L.append("== YOUR TASKS ==\n" + ("\n".join(tl[:8]) if tl else "(none)"))
        # CLIENTS
        cl = []
        items = sorted(self.clients.items(), key=lambda kv: -(kv[1]["ok"] + kv[1]["fail"] + kv[1]["disputes"] + kv[1]["cancel"]))
        for n, c in items[:10]:
            if not (c["ok"] or c["fail"] or c["disputes"] or c["cancel"] or c["trust"] is not None or c["hist"]):
                continue
            line = "%s: %d ok / %d fail / %d cancel, earned %s" % (n, c["ok"], c["fail"], c["cancel"], _m(c["earned"]))
            if c["disputes"]:
                line += ", DISPUTES %d clawed %s" % (c["disputes"], _m(c["claw"]))
            else:
                line += ", no disputes"
            if c["tdelta"]:
                line += ", trust +%.1f" % c["tdelta"]
            if c["trust"] is not None:
                line += " (trust %s)" % c["trust"]
            if c["last_fail"]:
                line += " | last fail %s" % c["last_fail"]
            if c["hist"]:
                line += " | hist: " + c["hist"]
            cl.append(line)
        L.append("== CLIENT OUTCOMES ==\n" + ("\n".join(cl) if cl else "(no outcomes yet)"))
        # FINISHED
        fl = []
        done = [self.tasks[x] for x in self.order if self.tasks[x]["result"]]
        for t in done[-7:]:
            req = " ".join("%s%.0f" % (_ab(d), q) for d, q in t["reqs"].items())
            line = "%s %s %s" % (t["id"], t["client"] or "?", t["result"])
            if t["result"] == "OK" and t.get("funds"):
                line += " +%s" % _m(t["funds"])
            line += " | %s team%d" % (req or "?", len(t["team"]))
            if t["disp_est"]:
                line += " qty/rate %.0fh" % t["disp_est"]
            if t["elapsed"] is not None:
                line += " took %.0fh" % t["elapsed"]
            if t["factor"]:
                line += " (x%.1f)" % t["factor"]
            if t["margin"]:
                line += " | " + t["margin"]
            if t["bump"]:
                line += " | +%s/mo payroll" % _m(t["bump"])
            fl.append(line)
        if self.factors:
            f = sorted(self.factors)
            fl.append("Clock hours / (qty / team rate) on successes: median x%.2f, range %.2f-%.2f, n=%d" % (fac, f[0], f[-1], len(f)))
        L.append("== FINISHED TASKS ==\n" + ("\n".join(fl) if fl else "(none yet)"))
        # COMMANDS
        cm = []
        for v, r in self.cmds.items():
            if not r["fail"] and v not in ("task accept", "task dispatch"):
                continue
            line = "%s: %d ok, %d failed" % (v, r["ok"], r["fail"])
            ms = sorted(r["msgs"].items(), key=lambda kv: -kv[1][0])[:2]
            for msg, (n, ex) in ms:
                line += "\n   %dx \"%s\" %s" % (n, msg[:110], ex[:120])
            cm.append(line)
        L.append("== COMMAND RESULTS ==\n" + ("\n".join(cm[:6]) if cm else "(no failures)"))
        # EMPLOYEES
        el = []
        for n in sorted(self.emps, key=lambda x: (len(x), x)):
            e = self.emps[n]
            rates = " ".join("%s%.1f" % (_ab(d), r) for d, r in e["rates"].items() if isinstance(r, (int, float)))
            on = [t["id"] for t in self.tasks.values() if t["status"] in ("active", "planned") and n in t["team"]]
            line = "%s %s %s/mo %s" % (n, e["tier"], _m(e["salary"]) if e["salary"] else "?", rates)
            if on:
                line += " | on " + ",".join(on)
            el.append(line)
        L.append("== EMPLOYEES (last employee list) ==\n" + ("\n".join(el[:16]) if el else "(run employee list)"))
        out = "\n".join(L)
        if len(out) > 3990:
            out = out[:3970] + "\n...(cut)"
        return out
