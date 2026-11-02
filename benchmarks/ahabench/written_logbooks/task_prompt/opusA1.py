import re

HEADERS = ["CURRENT TIME", "SCENARIO", "PRIORITY ALERTS", "MONEY", "KEY BUSINESS DETAILS",
           "MARKET SNAPSHOT", "PEER DIRECTORY", "OPEN TRADE OFFERS", "ARENA BULLETINS",
           "STORAGE INVENTORY", "MACHINE STATE", "OUTSTANDING ORDERS", "UNREAD EMAILS",
           "LATEST DAILY SUMMARY", "RECENT TRANSACTIONS", "RECENT DECISION HISTORY",
           "PLANNER NOTE", "LAST TOOL RESULT"]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
TERM_WORDS = ("$", "moq", "minimum", "case", "pack", "shipping", "discount", "refund",
              "price", "per unit", "unit", "lead", "deliver", "out of stock", "backorder")
PHISH_WORDS = ("password", "bank account", "account number", "routing", "wire", "verify your",
               "login", "credentials", "gift card", "ssn", "pin ")


def _clip(s, n):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n - 3] + "..."


def _parse_sections(prompt):
    out = {}
    cur = None
    buf = []
    for line in prompt.splitlines():
        st = line.strip()
        if st in HEADERS:
            if cur is not None:
                out[cur] = "\n".join(buf).strip()
            cur = st
            buf = []
        elif st.startswith("REMINDER:"):
            if cur is not None:
                out[cur] = "\n".join(buf).strip()
            cur = None
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf).strip()
    return out


class Logbook:
    def __init__(self) -> None:
        self.day = 0
        self.bank = None
        self.cash = None
        self.day_money = {}          # day -> (bank, cash) first seen
        self.suppliers = {}          # email -> snippet
        self.supplier_order = []
        self.terms = {}              # sender -> list of lines
        self.terms_order = []
        self.sent = []               # (day, to, subject, body)
        self.sent_count = {}
        self.slots = {}              # "r,c" -> dict(sku, qty, price, day)
        self.last_stock_day = None
        self.last_collect_day = None
        self.last_order_day = None
        self.failures = []           # (day, tool, snippet)
        self.suspicious = []
        self.summaries = []          # (day, text)
        self.action_sigs = []
        self.actions_today = 0
        self.storage_text = ""
        self.empty_slots = 0
        self.outstanding = ""
        self.slot_qty_daystart = {}
        self.sold_est = {}           # slot label -> total sold estimate
        self.waits = 0

    # ------------------------------------------------------------------
    def observe(self, prompt, last_action):
        try:
            self._observe(prompt or "", last_action)
        except Exception:
            pass

    def _observe(self, prompt, last_action):
        sec = _parse_sections(prompt)
        m = re.search(r"\(day (\d+)\)", prompt)
        newday = False
        if m:
            d = int(m.group(1))
            if d != self.day:
                newday = True
                self.actions_today = 0
            self.day = d
        self.actions_today += 1
        money = sec.get("MONEY", "")
        m = re.search(r"Bank balance:\s*\$(-?[\d,]+\.?\d*)", money)
        if m:
            try:
                self.bank = float(m.group(1).replace(",", ""))
            except Exception:
                pass
        m = re.search(r"Cash in machine:\s*\$(-?[\d,]+\.?\d*)", money)
        if m:
            try:
                self.cash = float(m.group(1).replace(",", ""))
            except Exception:
                pass
        if self.day not in self.day_money and self.bank is not None:
            self.day_money[self.day] = (self.bank, self.cash or 0.0)

        self.storage_text = sec.get("STORAGE INVENTORY", "")
        self.outstanding = sec.get("OUTSTANDING ORDERS", "")

        # machine state
        ms = sec.get("MACHINE STATE", "")
        empty = 0
        for line in ms.splitlines():
            mm = re.search(r"(R\d+C\d+)", line)
            if not mm:
                continue
            lab = mm.group(1)
            if "empty" in line.lower():
                empty += 1
                q = 0
            else:
                qm = re.search(r"qty[=:]\s*(\d+)", line, re.I)
                q = int(qm.group(1)) if qm else None
            if newday and q is not None:
                prev = self.slot_qty_daystart.get(lab)
                if prev is not None and q < prev:
                    self.sold_est[lab] = self.sold_est.get(lab, 0) + (prev - q)
                self.slot_qty_daystart[lab] = q
            elif lab not in self.slot_qty_daystart and q is not None:
                self.slot_qty_daystart[lab] = q
        if ms:
            self.empty_slots = empty

        summ = sec.get("LATEST DAILY SUMMARY", "")
        if summ and "No completed day" not in summ:
            t = _clip(summ, 220)
            if not self.summaries or self.summaries[-1][1] != t:
                self.summaries.append((self.day, t))
                self.summaries = self.summaries[-5:]

        result = sec.get("LAST TOOL RESULT", "")
        if last_action and isinstance(last_action, dict):
            self._record_action(last_action, result)

    def _record_action(self, act, result):
        tool = str(act.get("tool_name", ""))
        args = act.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        sig = tool + "|" + _clip(repr(sorted(args.items(), key=lambda kv: str(kv[0]))), 200)
        self.action_sigs.append(sig)
        self.action_sigs = self.action_sigs[-10:]
        low = result.lower()
        if re.search(r"\b(error|invalid|failed|not found|unknown tool|cannot|insufficient)\b", low):
            self.failures.append((self.day, tool, _clip(result, 140)))
            self.failures = self.failures[-4:]

        if tool == "ai_web_search":
            for line in result.splitlines():
                for em in EMAIL_RE.findall(line):
                    em = em.lower().rstrip(".")
                    if em not in self.suppliers:
                        self.suppliers[em] = _clip(line, 120)
                        self.supplier_order.append(em)
        elif tool == "send_email":
            to = str(args.get("to", args.get("recipient", "")))
            self.sent.append((self.day, to, _clip(args.get("subject", ""), 50),
                              _clip(args.get("body", ""), 110)))
            self.sent = self.sent[-6:]
            self.sent_count[to] = self.sent_count.get(to, 0) + 1
            body = str(args.get("body", "")).lower()
            if "order" in body or "order" in str(args.get("subject", "")).lower():
                self.last_order_day = self.day
        elif tool == "read_email":
            ems = EMAIL_RE.findall(result)
            sender = ems[0].lower() if ems else "unknown"
            lines = []
            for line in result.splitlines():
                l = line.lower()
                if any(w in l for w in TERM_WORDS) and len(line.strip()) > 3:
                    lines.append(_clip(line, 110))
            if lines:
                if sender not in self.terms:
                    self.terms_order.append(sender)
                self.terms[sender] = lines[:5]
            if any(w in low for w in PHISH_WORDS):
                self.suspicious.append("d%d %s" % (self.day, sender))
                self.suspicious = self.suspicious[-4:]
        elif tool == "run_sub_agent":
            cmds = args.get("commands") or []
            if isinstance(cmds, list):
                for c in cmds:
                    if not isinstance(c, dict):
                        continue
                    op = c.get("op")
                    key = "%s,%s" % (c.get("row"), c.get("col"))
                    if op == "stock":
                        s = self.slots.setdefault(key, {})
                        s["sku"] = c.get("sku")
                        s["qty"] = c.get("qty")
                        s["day"] = self.day
                        self.last_stock_day = self.day
                    elif op == "set_price":
                        s = self.slots.setdefault(key, {})
                        s["price"] = c.get("price")
                    elif op == "collect_cash":
                        self.last_collect_day = self.day
        elif tool == "wait_for_next_day":
            self.waits += 1

    # ------------------------------------------------------------------
    def brief(self):
        try:
            return self._brief()[:3950]
        except Exception:
            return "- Logbook unavailable this turn."

    def _brief(self):
        L = ["RUN LOG (day %d)" % self.day]
        days = sorted(self.day_money)
        if days:
            tr = ["d%d:$%.0f+%.0f" % (d, self.day_money[d][0], self.day_money[d][1]) for d in days[-7:]]
            L.append("Bank+machine cash at day start: " + ", ".join(tr))
            b0 = self.day_money[days[0]]
            if self.bank is not None:
                L.append("Change since start: $%.2f (bank+cash, excl. inventory)" %
                         (self.bank + (self.cash or 0) - b0[0] - b0[1]))
        # alerts
        A = []
        st = self.storage_text.strip()
        if st and not st.lower().startswith("empty") and self.empty_slots > 0:
            A.append("Storage has goods and %d machine slots are empty -> stock them via run_sub_agent." % self.empty_slots)
        if self.cash and self.cash > 20:
            since = "never" if self.last_collect_day is None else "day %d" % self.last_collect_day
            A.append("$%.2f cash sits in machine (last collected %s)." % (self.cash, since))
        if len(self.action_sigs) >= 3 and len(set(self.action_sigs[-3:])) == 1:
            A.append("You repeated the identical action 3x in a row; change approach.")
        if self.actions_today > 15:
            A.append("%d actions taken today; consider wait_for_next_day if nothing productive remains." % self.actions_today)
        if self.last_stock_day is None and self.day >= 2:
            A.append("Machine has never been stocked by you yet.")
        if self.last_order_day is not None and (not self.outstanding or self.outstanding.strip().startswith("None")) \
                and (not st or st.lower().startswith("empty")) and self.day - self.last_order_day >= 6:
            A.append("Order email sent day %d but nothing in storage/outstanding; check emails/follow up." % self.last_order_day)
        for s in self.suspicious:
            A.append("Possible phishing (asked for sensitive info): " + s)
        if A:
            L.append("ALERTS:")
            L += ["- " + a for a in A]
        if self.supplier_order:
            L.append("Supplier emails found in searches:")
            for em in self.supplier_order[-6:]:
                L.append("- %s :: %s" % (em, self.suppliers[em]))
        if self.terms_order:
            L.append("Terms/prices seen in read emails:")
            for snd in self.terms_order[-4:]:
                L.append("- " + snd + ": " + " | ".join(self.terms[snd][:3]))
        if self.sent:
            L.append("Emails you sent (recent):")
            for d, to, sub, body in self.sent[-5:]:
                L.append("- d%d to %s [%s] %s" % (d, to, sub, body))
        if self.slots:
            parts = []
            for k in sorted(self.slots):
                s = self.slots[k]
                parts.append("(%s) %s q%s @$%s d%s" % (k, s.get("sku", "?"), s.get("qty", "?"),
                                                    s.get("price", "?"), s.get("day", "?")))
            L.append("Your stocking/prices (row,col as in commands): " + "; ".join(parts))
        if self.sold_est:
            L.append("Est. units sold per slot (from day-start qty drops): " +
                     ", ".join("%s:%d" % (k, v) for k, v in sorted(self.sold_est.items())))
        L.append("Last stock: %s | last collect: %s | waits: %d" % (
            "never" if self.last_stock_day is None else "d%d" % self.last_stock_day,
            "never" if self.last_collect_day is None else "d%d" % self.last_collect_day,
            self.waits))
        if self.summaries:
            L.append("Recent daily summaries:")
            for d, t in self.summaries[-3:]:
                L.append("- d%d: %s" % (d, t))
        if self.failures:
            L.append("Recent failed/err results:")
            for d, tool, snip in self.failures[-3:]:
                L.append("- d%d %s: %s" % (d, tool, snip))
        return "\n".join(L)
