import re
import json

_HEADERS = [
    "CURRENT TIME", "SCENARIO", "PRIORITY ALERTS", "MONEY", "KEY BUSINESS DETAILS",
    "MARKET SNAPSHOT", "PEER DIRECTORY", "OPEN TRADE OFFERS", "ARENA BULLETINS",
    "STORAGE INVENTORY", "MACHINE STATE", "OUTSTANDING ORDERS", "UNREAD EMAILS",
    "LATEST DAILY SUMMARY", "RECENT TRANSACTIONS", "RECENT DECISION HISTORY",
    "PLANNER NOTE", "LAST TOOL RESULT",
]
_STOP = {"qty", "units", "unit", "sku", "small", "large", "empty", "capacity", "price",
         "x", "pcs", "in", "storage", "of", "cap", "slot", "row", "col", "stock"}
_ERRW = ("error", "invalid", "unknown op", "failed", "not found", "insufficient",
         "cannot", "can't", "rejected", "denied", "exceed", "missing")
_SENS = ("password", "bank account", "account number", "routing", "wire", "verify your",
         "credential", "login", "ssn", "pin ", "urgent action", "gift card")
_TERMW = ("$", "moq", "minimum", "case", "pack", "shipping", "freight", "discount",
          "lead", "deliver", "refund", "return", "per unit", "unit price", "out of stock",
          "short", "backorder", "delay")


def _clip(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _section(text, name):
    lines = text.splitlines()
    out, on = [], False
    for ln in lines:
        st = ln.strip()
        if on:
            if st in _HEADERS or st.startswith("REMINDER:"):
                break
            out.append(st)
        elif st == name:
            on = True
    return [l for l in out if l]


def _money(text, label):
    m = re.search(label + r"\s*:?\s*\$?\s*(-?[\d,]+(?:\.\d+)?)", text)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except Exception:
            return None
    return None


class Register:
    def __init__(self) -> None:
        self.day = 0
        self.bank = None
        self.cash = None
        self.agent_email = ""
        self.nw = {}                 # day -> bank+cash at first sight
        self.machine = {}            # slot -> [sku, qty, price, size]
        self.storage = {}            # sku -> qty
        self.sales = []              # (day, sku, units, price)
        self.suppliers = {}          # email -> snippet
        self.orders = []             # (day, to, subject, body)
        self.deliveries = []         # (day, sku, qty)
        self.emails = {}             # id -> digest
        self.suspicious = []         # notes
        self.specs = ""
        self.sub_ok = ""
        self.sub_err = []
        self.tool_err = []
        self.last_collect = None
        self.actions_today = 0
        self.recent_sigs = []
        self.price_hist = {}         # sku -> list of (day, price)

    # ---------------- parsing helpers ----------------
    def _parse_machine(self, lines):
        res = {}
        for ln in lines:
            m = re.search(r"R(\d+)C(\d+)", ln)
            if not m:
                continue
            slot = "R%sC%s" % (m.group(1), m.group(2))
            size = "large" if "large" in ln.lower() else ("small" if "small" in ln.lower() else "")
            low = ln.lower()
            rest = ln[m.end():]
            if re.search(r"\bempty\b", low) and not re.search(r"qty[=:]\s*[1-9]", low):
                res[slot] = ["", 0, None, size]
                continue
            sku = ""
            ms = re.search(r"sku[=:]\s*([A-Za-z0-9_\-\.]+)", rest)
            if ms:
                sku = ms.group(1)
            else:
                for t in re.findall(r"[A-Za-z][A-Za-z0-9_\-]*", rest):
                    if t.lower() not in _STOP and ("_" in t or len(t) > 3):
                        sku = t
                        break
            qty = 0
            mq = re.search(r"(?:qty|quantity|units|count)[=:]\s*(\d+)", rest, re.I)
            if mq:
                qty = int(mq.group(1))
            else:
                mq = re.search(r"(\d+)\s*/\s*\d+", rest)
                if mq:
                    qty = int(mq.group(1))
                else:
                    mq = re.search(r"x\s*(\d+)\b", rest)
                    if mq:
                        qty = int(mq.group(1))
            price = None
            mp = re.search(r"price[=:]\s*\$?(\d+(?:\.\d+)?)", rest, re.I) or re.search(r"\$(\d+(?:\.\d+)?)", rest)
            if mp:
                try:
                    price = float(mp.group(1))
                except Exception:
                    price = None
            res[slot] = [sku, qty, price, size]
        return res

    def _parse_storage(self, lines):
        res = {}
        for ln in lines:
            if ln.lower().startswith("empty") or ln.lower().startswith("none"):
                continue
            toks = re.findall(r"[A-Za-z][A-Za-z0-9_\-]*", ln)
            sku = None
            for t in toks:
                if t.lower() not in _STOP:
                    sku = t
                    break
            if not sku:
                continue
            after = ln[ln.find(sku) + len(sku):]
            mq = re.search(r"(\d+)", after)
            if mq:
                res[sku] = res.get(sku, 0) + int(mq.group(1))
        return res

    # ---------------- main hooks ----------------
    def observe(self, step: dict) -> None:
        try:
            self._observe(step or {})
        except Exception:
            pass

    def _observe(self, step):
        action = step.get("action")
        result = str(step.get("result") or "")
        seen = str(step.get("seen") or "")
        tool = ""
        args = {}
        if isinstance(action, dict):
            tool = str(action.get("tool") or action.get("tool_name") or "")
            args = action.get("arguments") or {}
            if not isinstance(args, dict):
                args = {}

        # day
        new_day = self.day
        m = re.search(r"\(day\s+(\d+)\)", seen)
        if m:
            new_day = int(m.group(1))
        day_changed = new_day != self.day
        prev_day = self.day
        self.day = new_day

        b = _money(seen, "Bank balance")
        c = _money(seen, "Cash in machine")
        if b is not None:
            self.bank = b
        if c is not None:
            self.cash = c
        if day_changed or not self.nw:
            if self.bank is not None:
                self.nw.setdefault(self.day, self.bank + (self.cash or 0.0))
            self.actions_today = 0
        if not self.agent_email:
            me = re.search(r"Agent email:\s*(\S+@\S+)", seen)
            if me:
                self.agent_email = me.group(1).strip().lower()

        # action bookkeeping
        if tool:
            self.actions_today += 1
            try:
                sig = tool + json.dumps(args, sort_keys=True)[:200]
            except Exception:
                sig = tool
            self.recent_sigs = (self.recent_sigs + [sig])[-6:]
            low = result.lower()
            if tool not in ("read_email", "list_emails", "ai_web_search") and any(w in low[:400] for w in _ERRW):
                self.tool_err = (self.tool_err + ["d%d %s: %s" % (self.day, tool, _clip(result, 150))])[-4:]

        # machine & storage
        msec = _section(seen, "MACHINE STATE")
        new_machine = self._parse_machine(msec) if msec else None
        ssec = _section(seen, "STORAGE INVENTORY")
        new_storage = self._parse_storage(ssec) if ssec else ({} if "STORAGE INVENTORY" in seen else None)

        if new_machine is not None:
            if tool == "wait_for_next_day" and self.machine:
                sold = {}
                for slot, (sku, q, pr, _s) in self.machine.items():
                    nv = new_machine.get(slot)
                    if not nv or not sku:
                        continue
                    if nv[0] in (sku, "") and nv[1] < q:
                        k = sold.setdefault(sku, [0, pr])
                        k[0] += q - nv[1]
                        if pr is not None:
                            k[1] = pr
                for sku, (u, pr) in sold.items():
                    self.sales.append((prev_day or self.day, sku, u, pr))
                for sku in set(v[0] for v in self.machine.values() if v[0]):
                    if sku not in sold:
                        pr = None
                        for v in self.machine.values():
                            if v[0] == sku and v[2] is not None:
                                pr = v[2]
                        self.sales.append((prev_day or self.day, sku, 0, pr))
                self.sales = self.sales[-600:]
            for slot, v in new_machine.items():
                if v[0] and v[2] is not None:
                    h = self.price_hist.setdefault(v[0], [])
                    if not h or h[-1][1] != v[2]:
                        h.append((self.day, v[2]))
                        del h[:-5]
            self.machine = new_machine

        if new_storage is not None:
            if tool != "run_sub_agent":
                for sku, q in new_storage.items():
                    if q > self.storage.get(sku, 0):
                        self.deliveries.append((self.day, sku, q - self.storage.get(sku, 0)))
                self.deliveries = self.deliveries[-12:]
            self.storage = new_storage

        # tool-specific
        if tool == "ai_web_search":
            for mm in re.finditer(r"[\w\.\-+]+@[\w\-]+\.[\w\.\-]+", result):
                em = mm.group(0).rstrip(".").lower()
                if em == self.agent_email or em in self.suppliers:
                    continue
                ctx = result[max(0, mm.start() - 120): mm.end() + 60]
                self.suppliers[em] = _clip(ctx, 170)
            if len(self.suppliers) > 10:
                for k in list(self.suppliers)[:-10]:
                    self.suppliers.pop(k, None)
        elif tool == "send_email":
            self.orders.append((self.day, str(args.get("to", "")), _clip(args.get("subject", ""), 60),
                                _clip(args.get("body", ""), 160), _clip(result, 60)))
            self.orders = self.orders[-8:]
        elif tool == "read_email":
            eid = args.get("id")
            frm = ""
            mf = re.search(r"from[:=]\s*\"?([^\n,\"]+)", result, re.I)
            if mf:
                frm = _clip(mf.group(1), 50)
            subj = ""
            msub = re.search(r"subject[:=]\s*\"?([^\n\"]+)", result, re.I)
            if msub:
                subj = _clip(msub.group(1), 60)
            terms = []
            for ln in re.split(r"[\n;]|(?<=\.)\s", result):
                l = ln.lower()
                if any(w in l for w in _TERMW) and len(ln.strip()) > 4:
                    terms.append(_clip(ln, 110))
                if len(terms) >= 4:
                    break
            low = result.lower()
            sus = [w.strip() for w in _SENS if w in low]
            self.emails[str(eid)] = "d%d #%s from %s | %s | %s%s" % (
                self.day, eid, frm, subj, " / ".join(terms),
                (" [SUSPICIOUS: " + ",".join(sus) + "]") if sus else "")
            if sus:
                self.suspicious = (self.suspicious + ["#%s from %s (%s)" % (eid, frm, ",".join(sus))])[-4:]
            if len(self.emails) > 8:
                for k in list(self.emails)[:-8]:
                    self.emails.pop(k, None)
        elif tool == "sub_agent_specs":
            self.specs = _clip(result, 600)
        elif tool == "run_sub_agent":
            cmds = args.get("commands") or []
            low = result.lower()
            bad = any(w in low for w in _ERRW)
            try:
                js = json.dumps(cmds, separators=(",", ":"))
            except Exception:
                js = str(cmds)
            if bad:
                self.sub_err = (self.sub_err + ["d%d %s -> %s" % (self.day, _clip(js, 140), _clip(result, 160))])[-3:]
            if not bad or "ok" in low or "success" in low:
                self.sub_ok = _clip(js, 300)
            try:
                for cmd in cmds:
                    if isinstance(cmd, dict) and cmd.get("op") == "collect_cash":
                        self.last_collect = self.day
            except Exception:
                pass

    # ---------------- output ----------------
    def _rates(self):
        r = {}
        for d, sku, u, pr in self.sales:
            if d >= self.day - 7:
                e = r.setdefault(sku, [0, set()])
                e[0] += u
                e[1].add(d)
        return {k: v[0] / max(1, len(v[1])) for k, v in r.items()}

    def brief(self) -> str:
        try:
            return self._brief()[:3990]
        except Exception:
            return "Register: day %s" % self.day

    def _brief(self):
        out = []
        left = max(0, 365 - self.day)
        nw = (self.bank or 0.0) + (self.cash or 0.0)
        line = "DAY %d/365 (%d left). Bank+machine cash=$%.2f" % (self.day, left, nw)
        past = [d for d in self.nw if d <= self.day - 7]
        if past:
            d0 = max(past)
            line += " | 7d ago $%.2f (%+.2f/day)" % (self.nw[d0], (nw - self.nw[d0]) / max(1, self.day - d0))
        line += " | actions today: %d" % self.actions_today
        out.append(line)

        rates = self._rates()
        alerts = []
        mach_by_sku = {}
        empty = []
        for slot, (sku, q, pr, sz) in sorted(self.machine.items()):
            if sku and q > 0:
                e = mach_by_sku.setdefault(sku, [0, pr])
                e[0] += q
            else:
                empty.append(slot)
        if self.machine and empty:
            msg = "%d empty slots (%s)" % (len(empty), ",".join(empty[:12]))
            if any(q > 0 for q in self.storage.values()):
                msg += " while storage holds stock -> stock them"
            alerts.append(msg)
        if not self.machine or all(not v[0] for v in self.machine.values()):
            if not self.orders:
                alerts.append("No orders placed yet and machine empty: earning nothing.")
        if self.cash and self.cash >= 30 and (self.last_collect is None or self.day - self.last_collect >= 3):
            alerts.append("$%.2f cash sitting in machine (theft risk); last collect day %s" % (self.cash, self.last_collect))
        for sku, (q, pr) in mach_by_sku.items():
            rt = rates.get(sku, 0)
            st = self.storage.get(sku, 0)
            if rt > 0 and (q + st) / rt < 6:
                alerts.append("%s: only %.1f days of total stock left at %.1f/day -> reorder" % (sku, (q + st) / rt, rt))
        if len(self.recent_sigs) >= 3 and len(set(self.recent_sigs[-3:])) == 1:
            alerts.append("Same action repeated 3x; change approach.")
        if self.actions_today >= 15:
            alerts.append("Many actions today; consider wait_for_next_day.")
        if self.suspicious:
            alerts.append("Suspicious emails (never share bank/account info): " + "; ".join(self.suspicious[-2:]))
        if left <= 20:
            alerts.append("Endgame: avoid orders whose stock+shipping won't pay back before day 365; collect cash.")
        if alerts:
            out.append("ALERTS:\n- " + "\n- ".join(_clip(a, 220) for a in alerts[:7]))

        skus = set(mach_by_sku) | set(self.storage) | set(rates)
        if skus:
            rows = []
            for sku in sorted(skus):
                tot = sum(s[2] for s in self.sales if s[1] == sku)
                pr = mach_by_sku.get(sku, [0, None])[1]
                mq = mach_by_sku.get(sku, [0])[0]
                st = self.storage.get(sku, 0)
                rt = rates.get(sku)
                ph = self.price_hist.get(sku, [])
                byp = {}
                for d, s, u, p in self.sales:
                    if s == sku and p is not None:
                        e = byp.setdefault(p, [0, 0])
                        e[0] += u
                        e[1] += 1
                pe = ",".join("$%.2f:%.1f/d" % (p, v[0] / max(1, v[1])) for p, v in sorted(byp.items())[:4])
                rows.append("%s: mach %d, stor %d, price %s, sold %d total, 7d %s/day%s" % (
                    sku, mq, st, ("$%.2f" % pr) if pr is not None else "?", tot,
                    ("%.1f" % rt) if rt is not None else "?", (" | price->sales " + pe) if pe else ""))
            out.append("SALES BY SKU (from machine counts across days):\n" + "\n".join(rows[:12]))

        if self.deliveries:
            out.append("DELIVERIES SEEN: " + "; ".join("d%d %s +%d" % d for d in self.deliveries[-6:]))
        if self.orders:
            out.append("EMAILS SENT:\n" + "\n".join("d%d to %s | %s | %s" % o[:4] for o in self.orders[-5:]))
        if self.suppliers:
            out.append("SUPPLIER ADDRESSES (from searches):\n" + "\n".join(
                "%s: %s" % (k, _clip(v, 120)) for k, v in list(self.suppliers.items())[-6:]))
        if self.emails:
            out.append("EMAILS READ:\n" + "\n".join(_clip(v, 260) for v in list(self.emails.values())[-5:]))
        sub = []
        if self.specs:
            sub.append("specs: " + _clip(self.specs, 450))
        if self.sub_ok:
            sub.append("last accepted commands: " + self.sub_ok)
        if self.sub_err:
            sub.append("failed: " + " || ".join(self.sub_err[-2:]))
        if sub:
            out.append("SUB-AGENT:\n" + "\n".join(sub))
        if self.tool_err:
            out.append("RECENT TOOL ERRORS:\n" + "\n".join(self.tool_err[-3:]))
        text = "\n\n".join(out)
        if len(text) > 3990:
            text = text[:3985] + "…"
        return text
