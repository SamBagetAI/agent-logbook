import json
import re

HEADERS = [
    "CURRENT TIME", "SCENARIO", "PRIORITY ALERTS", "MONEY", "KEY BUSINESS DETAILS",
    "MARKET SNAPSHOT", "PEER DIRECTORY", "OPEN TRADE OFFERS", "ARENA BULLETINS",
    "STORAGE INVENTORY", "MACHINE STATE", "OUTSTANDING ORDERS", "UNREAD EMAILS",
    "LATEST DAILY SUMMARY", "RECENT TRANSACTIONS", "RECENT DECISION HISTORY",
    "PLANNER NOTE", "LAST TOOL RESULT",
]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
QUOTE_KW = ("$", "moq", "minimum", "case", "pack", "shipping", "discount", "refund",
            "lead time", "deliver", "per unit", "unit price", "out of stock")
SUSP_KW = ("password", "account number", "routing", "wire", "verify your", "login",
           "credentials", "ssn", "gift card", "urgent payment", "bank details")


def _num(s):
    try:
        return float(s.replace(",", ""))
    except Exception:
        return None


def _short(s, n):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 3] + "..."


class Register:
    def __init__(self) -> None:
        self.day = 0
        self.bank = None
        self.cash = None
        self.worth_by_day = {}
        self.agent_email = ""
        self.suppliers = {}
        self.quotes = {}
        self.orders = []
        self.emails_read = 0
        self.read_senders = []
        self.suspicious = []
        self.stock_log = []
        self.prices = {}
        self.last_stock_day = None
        self.last_collect_day = None
        self.errors = []
        self.summaries = []
        self.sigs = []
        self.consec_waits = 0
        self.empty_slots = None
        self.total_slots = None
        self.storage = []
        self.unread_text = ""
        self.outstanding = ""
        self.market_flags = ""
        self.last_order_day = None

    # ---------- parsing ----------
    def _sections(self, prompt):
        secs = {}
        cur = None
        for line in prompt.splitlines():
            st = line.strip()
            if st in HEADERS:
                cur = st
                secs[cur] = []
                continue
            if st.startswith("REMINDER:"):
                cur = None
                continue
            if cur is not None:
                secs[cur].append(line)
        return {k: "\n".join(v).strip() for k, v in secs.items()}

    def observe(self, prompt, last_action):
        try:
            self._observe(prompt or "", last_action)
        except Exception:
            pass

    def _observe(self, prompt, last_action):
        secs = self._sections(prompt)
        m = re.search(r"\(day (\d+)\)", prompt)
        if m:
            self.day = int(m.group(1))
        m = re.search(r"Bank balance:\s*\$?(-?[\d,]+\.?\d*)", prompt)
        if m:
            self.bank = _num(m.group(1))
        m = re.search(r"Cash in machine:\s*\$?(-?[\d,]+\.?\d*)", prompt)
        if m:
            self.cash = _num(m.group(1))
        if self.bank is not None:
            self.worth_by_day[self.day] = self.bank + (self.cash or 0.0)
        m = re.search(r"Agent email:\s*(\S+)", prompt)
        if m:
            self.agent_email = m.group(1).strip().lower()

        ms = secs.get("MACHINE STATE", "")
        slots = [l for l in ms.splitlines() if re.search(r"R\d+C\d+", l)]
        if slots:
            self.total_slots = len(slots)
            self.empty_slots = sum(1 for l in slots if " empty" in l.lower())
        st = secs.get("STORAGE INVENTORY", "")
        if st:
            self.storage = [] if st.strip().lower().startswith("empty") else \
                [_short(l, 80) for l in st.splitlines() if l.strip()][:10]
        self.outstanding = _short(secs.get("OUTSTANDING ORDERS", ""), 300)
        self.unread_text = _short(secs.get("UNREAD EMAILS", ""), 300)

        try:
            snap = json.loads(secs.get("MARKET SNAPSHOT", "{}") or "{}")
            fl = []
            if snap.get("jammed_slots"):
                fl.append("jammed=%s" % snap.get("jammed_slots"))
            if snap.get("machine_fault"):
                fl.append("MACHINE FAULT")
            if snap.get("capacity_reductions"):
                fl.append("capacity_red=%s" % snap.get("capacity_reductions"))
            self.market_flags = _short(", ".join(fl), 200)
        except Exception:
            self.market_flags = ""

        summ = secs.get("LATEST DAILY SUMMARY", "")
        if summ and not summ.lower().startswith("no completed"):
            s = _short(summ, 220)
            if not self.summaries or self.summaries[-1][1] != s:
                self.summaries.append((self.day, s))
                self.summaries = self.summaries[-3:]

        result = secs.get("LAST TOOL RESULT", "")
        if last_action:
            self._record_action(last_action, result)

    def _record_action(self, act, result):
        tool = str(act.get("tool_name", ""))
        args = act.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        low = result.lower()
        try:
            sig = tool + json.dumps(args, sort_keys=True, default=str)
        except Exception:
            sig = tool
        self.sigs.append(sig)
        self.sigs = self.sigs[-6:]

        if tool == "wait_for_next_day":
            self.consec_waits += 1
        else:
            self.consec_waits = 0

        if ("error" in low or "fail" in low or "invalid" in low or "rejected" in low) and tool != "read_email":
            self.errors.append("d%d %s: %s" % (self.day, tool, _short(result, 150)))
            self.errors = self.errors[-4:]

        if tool == "ai_web_search":
            for line in result.splitlines():
                for e in EMAIL_RE.findall(line):
                    e = e.lower().rstrip(".")
                    if e == self.agent_email:
                        continue
                    if e not in self.suppliers:
                        self.suppliers[e] = {"day": self.day, "ctx": _short(line, 110)}
        elif tool == "read_email":
            self.emails_read += 1
            sender = ""
            m = re.search(r"[Ff]rom[\"']?\s*[:=]\s*[\"']?([^\n\"',]+)", result)
            if m:
                em = EMAIL_RE.findall(m.group(1))
                sender = em[0].lower() if em else _short(m.group(1), 50)
            if not sender:
                em = [e.lower() for e in EMAIL_RE.findall(result) if e.lower() != self.agent_email]
                sender = em[0] if em else "?"
            lines = []
            for l in result.splitlines():
                ll = l.lower()
                if any(k in ll for k in QUOTE_KW):
                    lines.append(_short(l, 90))
                if len(lines) >= 6:
                    break
            if lines:
                self.quotes[sender] = (self.day, _short(" | ".join(lines), 330))
            if any(k in low for k in SUSP_KW):
                known = sender in self.suppliers or any(o["to"] == sender for o in self.orders)
                self.suspicious.append("d%d from %s (%s sender) asks for sensitive info/payment" %
                                       (self.day, sender, "known" if known else "UNVERIFIED"))
                self.suspicious = self.suspicious[-3:]
        elif tool == "send_email":
            to = str(args.get("to", args.get("recipient", ""))).lower()
            self.orders.append({"day": self.day, "to": to,
                                "subj": _short(args.get("subject", ""), 50),
                                "body": _short(args.get("body", args.get("content", "")), 110),
                                "res": _short(result, 70)})
            self.orders = self.orders[-8:]
            self.last_order_day = self.day
        elif tool == "run_sub_agent":
            cmds = args.get("commands") or []
            if isinstance(cmds, list):
                for c in cmds:
                    if not isinstance(c, dict):
                        continue
                    op = c.get("op")
                    slot = "row%s/col%s" % (c.get("row"), c.get("col"))
                    if op == "stock":
                        self.last_stock_day = self.day
                        self.stock_log.append("d%d %s %s x%s" % (self.day, slot, c.get("sku"), c.get("qty")))
                        self.stock_log = self.stock_log[-6:]
                        old = self.prices.get(slot)
                        self.prices[slot] = [c.get("sku"), old[1] if old else None]
                    elif op == "set_price":
                        old = self.prices.get(slot) or [None, None]
                        self.prices[slot] = [old[0], c.get("price")]
                    elif op == "collect_cash":
                        self.last_collect_day = self.day

    # ---------- output ----------
    def brief(self):
        try:
            return self._brief()[:3990]
        except Exception:
            return "Register unavailable this turn."

    def _brief(self):
        L = ["REGISTER (run memory, day %d)" % self.day]
        w = self.worth_by_day.get(self.day)
        if w is not None:
            s = "Bank+machine cash: $%.2f" % w
            days = sorted(self.worth_by_day)
            if days and days[0] != self.day:
                s += " | day%d: $%.2f" % (days[0], self.worth_by_day[days[0]])
            past = [d for d in days if d <= self.day - 7]
            if past:
                s += " | 7d ago: $%.2f" % self.worth_by_day[past[-1]]
            L.append(s)
        if self.cash:
            L.append("Uncollected machine cash $%.2f; last collect_cash: %s" %
                     (self.cash, "day %d" % self.last_collect_day if self.last_collect_day else "never"))
        if self.total_slots:
            L.append("Machine: %d/%d slots empty. Last stock op: %s." %
                     (self.empty_slots or 0, self.total_slots,
                      "day %d" % self.last_stock_day if self.last_stock_day else "never"))
        if self.market_flags:
            L.append("Incidents: " + self.market_flags)
        L.append("Storage: " + ("; ".join(self.storage) if self.storage else "empty"))
        if self.prices:
            L.append("Slots you set (your row/col args): " + "; ".join(
                "%s %s @%s" % (k, v[0], ("$%s" % v[1]) if v[1] is not None else "no price")
                for k, v in list(self.prices.items())[:12]))
        if self.suppliers:
            L.append("Supplier emails seen in searches:")
            for e, v in list(self.suppliers.items())[-8:]:
                L.append(" - %s (d%d): %s" % (e, v["day"], v["ctx"]))
        if self.quotes:
            L.append("Terms/quotes read in emails:")
            for e, (d, q) in list(self.quotes.items())[-5:]:
                L.append(" - %s (d%d): %s" % (e, d, q))
        if self.orders:
            L.append("Emails you sent:")
            for o in self.orders[-6:]:
                L.append(" - d%d to %s [%s] %s" % (o["day"], o["to"], o["subj"], o["body"]))
        if self.outstanding and not self.outstanding.lower().startswith("none"):
            L.append("Outstanding (prompt): " + self.outstanding)
        if self.summaries:
            L.append("Recent daily summaries:")
            for d, s in self.summaries:
                L.append(" - d%d: %s" % (d, s))
        if self.suspicious:
            L.append("Suspicious emails: " + " || ".join(self.suspicious))
        if self.errors:
            L.append("Recent tool errors:")
            for e in self.errors:
                L.append(" - " + e)
        warn = []
        if len(self.sigs) >= 3 and len(set(self.sigs[-3:])) == 1 and "wait_for_next_day" not in self.sigs[-1]:
            warn.append("You repeated the identical action 3 times in a row; results did not change.")
        if self.storage and self.empty_slots:
            warn.append("Storage holds goods while %d slots are empty." % self.empty_slots)
        if self.consec_waits >= 3 and self.empty_slots and self.total_slots and self.empty_slots == self.total_slots:
            warn.append("%d waits in a row with an empty machine." % self.consec_waits)
        if not self.storage and self.empty_slots and (self.last_order_day is None or self.day - self.last_order_day >= 5):
            warn.append("Storage empty, slots empty, last email sent: %s." %
                        ("day %d" % self.last_order_day if self.last_order_day else "never"))
        if warn:
            L.append("FLAGS: " + " ".join(warn))
        L.append("Emails read so far: %d." % self.emails_read)
        return "\n".join(L)
