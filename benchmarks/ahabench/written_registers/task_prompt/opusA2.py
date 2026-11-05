import re

HEADERS = [
    "CURRENT TIME", "SCENARIO", "PRIORITY ALERTS", "MONEY", "KEY BUSINESS DETAILS",
    "MARKET SNAPSHOT", "PEER DIRECTORY", "OPEN TRADE OFFERS", "ARENA BULLETINS",
    "STORAGE INVENTORY", "MACHINE STATE", "OUTSTANDING ORDERS", "UNREAD EMAILS",
    "LATEST DAILY SUMMARY", "RECENT TRANSACTIONS", "RECENT DECISION HISTORY",
    "PLANNER NOTE", "LAST TOOL RESULT",
]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
SUSPICIOUS = ("password", "verify your", "account number", "routing", "wire",
              "urgent", "suspended", "login", "credentials", "gift card", "ssn",
              "bank details", "confirm your")


def _num(s):
    try:
        return float(s.replace(",", ""))
    except Exception:
        return None


def _short(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 3] + "..."


class Register:
    def __init__(self) -> None:
        self.day = 0
        self.bank = None
        self.cash = None
        self.own_email = ""
        self.day_hist = []          # (day, bank, cash)
        self.suppliers = {}         # email -> context
        self.quotes = {}            # sender -> snippet
        self.sent = []              # (day, to, subject, body snippet)
        self.stock_log = []         # (day, summary)
        self.prices = {}            # slot -> (sku, price)
        self.last_collect_day = None
        self.errors = []            # (day, tool, snippet)
        self.suspicious = {}        # sender -> day
        self.summaries = []         # (day, text)
        self.actions_today = 0
        self.action_sigs = []
        self.storage_text = ""
        self.storage_empty = True
        self.storage_events = []
        self.empty_slots = []
        self.orders_text = ""
        self.unread_text = ""
        self.waits = 0

    # ---------- parsing ----------
    def _sections(self, prompt):
        secs = {}
        cur = None
        buf = []
        for line in prompt.splitlines():
            st = line.strip()
            if st in HEADERS:
                if cur:
                    secs[cur] = "\n".join(buf).strip()
                cur, buf = st, []
                continue
            if st.startswith("REMINDER:") and cur == "LAST TOOL RESULT":
                secs[cur] = "\n".join(buf).strip()
                cur, buf = None, []
                continue
            if cur:
                buf.append(line)
        if cur:
            secs[cur] = "\n".join(buf).strip()
        return secs

    def observe(self, prompt, last_action):
        try:
            self._observe(prompt or "", last_action)
        except Exception:
            pass

    def _observe(self, prompt, last_action):
        secs = self._sections(prompt)
        m = re.search(r"\(day (\d+)\)", prompt)
        newday = False
        if m:
            d = int(m.group(1))
            if d != self.day:
                newday = True
                self.day = d
                self.actions_today = 0
        m = re.search(r"Bank balance: \$(-?[\d,]+\.?\d*)", prompt)
        if m:
            self.bank = _num(m.group(1))
        m = re.search(r"Cash in machine: \$(-?[\d,]+\.?\d*)", prompt)
        if m:
            self.cash = _num(m.group(1))
        m = re.search(r"Agent email: (\S+)", prompt)
        if m:
            self.own_email = m.group(1).lower()
        if newday:
            self.day_hist.append((self.day, self.bank, self.cash))
            self.day_hist = self.day_hist[-60:]

        result = secs.get("LAST TOOL RESULT", "")
        self._handle_action(last_action, result)

        # storage
        st = secs.get("STORAGE INVENTORY", "")
        empty = (not st) or st.strip().lower().startswith("empty")
        if self.storage_empty and not empty:
            self.storage_events.append((self.day, "goods arrived in storage"))
            self.storage_events = self.storage_events[-8:]
        self.storage_empty = empty
        self.storage_text = st
        # machine
        ms = secs.get("MACHINE STATE", "")
        es = []
        for line in ms.splitlines():
            mm = re.search(r"(R\d+C\d+)\s*\[\w+\]\s*(.*)", line)
            if mm and ("empty" in mm.group(2).lower() or re.search(r"qty\s*=\s*0\b", mm.group(2))):
                es.append(mm.group(1))
        self.empty_slots = es
        self.orders_text = secs.get("OUTSTANDING ORDERS", "")
        self.unread_text = secs.get("UNREAD EMAILS", "")
        for line in self.unread_text.splitlines():
            low = line.lower()
            if any(k in low for k in SUSPICIOUS):
                em = EMAIL_RE.search(line)
                key = em.group(0).lower() if em else _short(line, 50)
                self.suspicious.setdefault(key, self.day)
        ds = secs.get("LATEST DAILY SUMMARY", "")
        if ds and not ds.lower().startswith("no completed"):
            t = _short(ds, 260)
            if not self.summaries or self.summaries[-1][1] != t:
                self.summaries.append((self.day, t))
                self.summaries = self.summaries[-4:]

    def _handle_action(self, a, result):
        if not isinstance(a, dict):
            return
        tool = str(a.get("tool_name", ""))
        args = a.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        self.actions_today += 1
        try:
            sig = tool + ":" + _short(repr(sorted(args.items())), 200)
        except Exception:
            sig = tool
        self.action_sigs.append(sig)
        self.action_sigs = self.action_sigs[-6:]
        low = result.lower()
        if tool != "wait_for_next_day" and ("error" in low[:300] or "invalid" in low[:300]
                                            or "failed" in low[:300] or "unknown" in low[:200]):
            self.errors.append((self.day, tool, _short(result, 160)))
            self.errors = self.errors[-4:]
        if tool == "wait_for_next_day":
            self.waits += 1
        elif tool == "ai_web_search":
            for mm in EMAIL_RE.finditer(result):
                e = mm.group(0).lower()
                if e == self.own_email:
                    continue
                ctx = result[max(0, mm.start() - 70): mm.start()]
                ctx = _short(ctx.split("\n")[-1] if "\n" in ctx else ctx, 60)
                if e not in self.suppliers and len(self.suppliers) < 12:
                    self.suppliers[e] = "d%d %s" % (self.day, ctx)
        elif tool == "read_email":
            fm = re.search(r"From:\s*.*?(" + EMAIL_RE.pattern + ")", result)
            sender = fm.group(1).lower() if fm else "?"
            if any(k in low for k in SUSPICIOUS) and sender not in self.suppliers:
                self.suspicious.setdefault(sender, self.day)
            lines = [l.strip() for l in result.splitlines()
                     if "$" in l or re.search(r"\b(moq|case|minimum|ship|deliver|discount|refund)\b", l.lower())]
            if lines:
                self.quotes[sender] = "d%d %s" % (self.day, _short(" | ".join(lines), 300))
                if len(self.quotes) > 8:
                    self.quotes.pop(next(iter(self.quotes)))
        elif tool == "send_email":
            self.sent.append((self.day, str(args.get("to", "")),
                              _short(args.get("subject", ""), 50),
                              _short(args.get("body", ""), 110)))
            self.sent = self.sent[-6:]
        elif tool == "run_sub_agent":
            cmds = args.get("commands") or []
            parts = []
            if isinstance(cmds, list):
                for c in cmds:
                    if not isinstance(c, dict):
                        continue
                    op = c.get("op")
                    if op == "stock":
                        parts.append("stock r%s c%s %s x%s" % (c.get("row"), c.get("col"), c.get("sku"), c.get("qty")))
                        k = "r%sc%s" % (c.get("row"), c.get("col"))
                        p = self.prices.get(k, (None, None))[1]
                        self.prices[k] = (c.get("sku"), p)
                    elif op == "set_price":
                        k = "r%sc%s" % (c.get("row"), c.get("col"))
                        sku = self.prices.get(k, (None, None))[0]
                        self.prices[k] = (sku, c.get("price"))
                        parts.append("price %s=%s" % (k, c.get("price")))
                    elif op == "collect_cash":
                        self.last_collect_day = self.day
                        parts.append("collect")
                    else:
                        parts.append(str(op))
            self.stock_log.append((self.day, _short("; ".join(parts), 150) + " => " + _short(result, 110)))
            self.stock_log = self.stock_log[-4:]

    # ---------- output ----------
    def brief(self):
        try:
            return self._brief()[:4000]
        except Exception:
            return "- Register unavailable this turn."

    def _brief(self):
        L = []
        L.append("RUN LOG (day %s, actions today %d, days advanced %d)" % (self.day, self.actions_today, self.waits))
        if self.bank is not None:
            nw = (self.bank or 0) + (self.cash or 0)
            line = "Bank $%.2f + machine cash $%.2f = $%.2f (excl. inventory)" % (self.bank, self.cash or 0, nw)
            past = [h for h in self.day_hist if h[0] <= self.day - 7 and h[1] is not None]
            if past:
                p = past[-1]
                line += "; 7d ago (d%d) $%.2f" % (p[0], (p[1] or 0) + (p[2] or 0))
            L.append(line)
        # alerts
        al = []
        if not self.storage_empty and self.empty_slots:
            al.append("Storage has goods while %d slot(s) empty (%s) -> stock via run_sub_agent." %
                      (len(self.empty_slots), ",".join(self.empty_slots[:12])))
        if self.cash and self.cash > 20:
            lc = "never" if self.last_collect_day is None else "day %d" % self.last_collect_day
            al.append("Machine holds $%.2f cash; last collect_cash: %s." % (self.cash, lc))
        if len(self.action_sigs) >= 3 and len(set(self.action_sigs[-3:])) == 1 \
                and not self.action_sigs[-1].startswith("wait_for_next_day"):
            al.append("Same action repeated 3x in a row - change approach.")
        if self.actions_today >= 15:
            al.append("%d actions already today without advancing the day." % self.actions_today)
        if not self.sent and self.day >= 2:
            al.append("No email sent to any supplier yet.")
        if al:
            L.append("ALERTS:")
            L += ["- " + a for a in al]
        if self.suppliers:
            L.append("Supplier emails found in searches:")
            for e, c in list(self.suppliers.items())[:8]:
                L.append("- %s (%s)" % (e, c))
        if self.quotes:
            L.append("Terms/prices read in emails:")
            for s, q in list(self.quotes.items())[-5:]:
                L.append("- %s: %s" % (s, q))
        if self.sent:
            L.append("Emails sent:")
            for d, to, sub, body in self.sent[-4:]:
                L.append("- d%d to %s '%s': %s" % (d, to, sub, body))
        if self.orders_text and not self.orders_text.lower().startswith("none"):
            L.append("Outstanding orders: " + _short(self.orders_text, 220))
        for d, ev in self.storage_events[-2:]:
            L.append("d%d: %s" % (d, ev))
        if self.stock_log:
            L.append("Recent sub-agent runs:")
            for d, s in self.stock_log[-3:]:
                L.append("- d%d %s" % (d, s))
        if self.prices:
            ps = ["%s:%s@%s" % (k, v[0], v[1]) for k, v in list(self.prices.items())[:12]]
            L.append("Prices set (cmd coords): " + _short(", ".join(ps), 300))
        if self.summaries:
            d, t = self.summaries[-1]
            L.append("Latest daily summary (seen d%d): %s" % (d, t))
        if self.errors:
            L.append("Recent tool errors:")
            for d, tool, s in self.errors[-3:]:
                L.append("- d%d %s: %s" % (d, tool, s))
        if self.suspicious:
            L.append("Possibly phishing senders (do not share account info): " +
                     _short(", ".join(list(self.suspicious.keys())[-5:]), 200))
        out = "\n".join(L)
        return out[:3990]
