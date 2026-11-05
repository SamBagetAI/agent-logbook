import re

_HEADERS = {
    "CURRENT TIME", "SCENARIO", "PRIORITY ALERTS", "MONEY", "KEY BUSINESS DETAILS",
    "MARKET SNAPSHOT", "PEER DIRECTORY", "OPEN TRADE OFFERS", "ARENA BULLETINS",
    "STORAGE INVENTORY", "MACHINE STATE", "OUTSTANDING ORDERS", "UNREAD EMAILS",
    "LATEST DAILY SUMMARY", "RECENT TRANSACTIONS", "RECENT DECISION HISTORY",
    "PLANNER NOTE", "LAST TOOL RESULT",
}
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_ERR_WORDS = ("error", "invalid", "failed", "unknown op", "not found", "rejected",
              "insufficient", "cannot", "can't", "unable", "exceeds", "not allowed")
_KEY_WORDS = ("$", "price", "moq", "minimum", "case", "pack", "ship", "discount",
              "refund", "return", "deliver", "lead", "unit", "per ", "sku", "stock",
              "delay", "short", "substitut", "eta", "total", "invoice", "order")
_SUSP_WORDS = ("bank account", "account number", "routing", "password", "wire",
               "verify your", "credentials", "login", "urgent payment", "gift card", "pin")
_STOP = {"small", "large", "capacity", "qty", "price", "empty", "units", "slot",
         "row", "col", "jammed", "jam", "fault", "none", "sku", "quantity"}


def _f(s):
    try:
        return float(str(s).replace(",", ""))
    except Exception:
        return None


def _cut(s, n):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 3] + "..."


def _sections(text):
    out = {}
    cur = None
    for line in str(text).splitlines():
        st = line.strip()
        if st in _HEADERS:
            cur = st
            out[cur] = []
            continue
        if cur is not None:
            out[cur].append(line)
    return out


class Register:
    def __init__(self) -> None:
        self.day = 0
        self.bank = None
        self.cash = None
        self.bank_acct = None
        self.money_by_day = {}          # day -> (bank, cash)
        self.suppliers = {}             # email -> dict
        self.search_notes = []          # (day, query, snippet) when no emails
        self.emails_read = []           # (day, id, from, subject, keylines, suspicious)
        self.sent = []                  # (day, to, subject, body)
        self.spec = ""
        self.sub_runs = []              # (day, cmds summary, result snippet)
        self.prices = {}                # slot -> (sku, price, day)
        self.errors = []                # (day, tool, snippet)
        self.summaries = []             # (day, text)
        self.slots = {}                 # slot -> (sku, qty, cap, price)
        self.storage = {}               # sku -> qty
        self.sales_total = {}           # sku -> units
        self.sales_daily = []           # (day, {sku: units})
        self.last_restock_day = None
        self.last_collect_day = None
        self.first_stock_day = None
        self.recent_actions = []
        self.warnings = []              # persistent important warnings
        self.n_steps = 0

    # ---------------- parsing helpers ----------------
    def _parse_slots(self, lines):
        slots = {}
        for line in lines:
            try:
                m = re.search(r"\b(R\d+C\d+)\b", line)
                if not m:
                    continue
                slot = m.group(1)
                low = line.lower()
                capm = re.search(r"capacity\s*[=:]\s*(\d+)", low)
                cap = int(capm.group(1)) if capm else None
                if re.search(r"\bempty\b", low) and not re.search(r"qty\s*[=:]\s*[1-9]", low):
                    slots[slot] = (None, 0, cap, None)
                    continue
                rest = low[m.end():]
                rest = re.sub(r"\[[^\]]*\]", " ", rest)
                sku = None
                for tok in re.findall(r"\b([a-z][a-z0-9_\-]{2,})\b", rest):
                    if tok not in _STOP:
                        sku = tok
                        break
                qm = re.search(r"(?:qty|quantity|units|count|stock)\s*[=:]\s*(\d+)", rest)
                if not qm:
                    qm = re.search(r"(\d+)\s*/\s*\d+", rest)
                qty = int(qm.group(1)) if qm else None
                pm = re.search(r"price\s*[=:]\s*\$?(\d+(?:\.\d+)?)", rest)
                if not pm:
                    pm = re.search(r"\$(\d+(?:\.\d+)?)", rest)
                price = _f(pm.group(1)) if pm else None
                slots[slot] = (sku, qty, cap, price)
            except Exception:
                continue
        return slots

    def _parse_storage(self, lines):
        inv = {}
        for line in lines:
            low = line.strip().lower()
            if not low or low.startswith("empty") or low == "none.":
                continue
            m = re.search(r"([a-z][a-z0-9_\-]{2,})\D{0,25}?(\d+)", low)
            if m and m.group(1) not in _STOP:
                try:
                    inv[m.group(1)] = inv.get(m.group(1), 0) + int(m.group(2))
                except Exception:
                    pass
        return inv

    def _add_error(self, tool, res):
        self.errors.append((self.day, tool, _cut(res, 200)))
        self.errors = self.errors[-6:]

    # ---------------- main ----------------
    def observe(self, step: dict) -> None:
        try:
            self._observe(step or {})
        except Exception:
            pass

    def _observe(self, step):
        self.n_steps += 1
        action = step.get("action") or None
        result = str(step.get("result") or "")
        seen = str(step.get("seen") or "")

        m = re.search(r"\(day\s+(\d+)\)", seen)
        if m:
            self.day = int(m.group(1))
        m = re.search(r"Bank balance:\s*\$?(-?[\d,]+\.?\d*)", seen)
        if m:
            self.bank = _f(m.group(1))
        m = re.search(r"Cash in machine:\s*\$?(-?[\d,]+\.?\d*)", seen)
        if m:
            self.cash = _f(m.group(1))
        m = re.search(r"Bank account number:\s*(\S+)", seen)
        if m:
            self.bank_acct = m.group(1).strip()
        if self.bank is not None:
            self.money_by_day[self.day] = (self.bank, self.cash or 0.0)
            if len(self.money_by_day) > 400:
                for k in sorted(self.money_by_day)[:-400]:
                    del self.money_by_day[k]

        secs = _sections(seen)
        new_slots = self._parse_slots(secs.get("MACHINE STATE", [])) if "MACHINE STATE" in secs else None
        new_storage = self._parse_storage(secs.get("STORAGE INVENTORY", [])) if "STORAGE INVENTORY" in secs else None

        tool = None
        args = {}
        if isinstance(action, dict):
            tool = action.get("tool") or action.get("tool_name")
            args = action.get("arguments") or {}
            if not isinstance(args, dict):
                args = {}

        # sales from waits
        if tool == "wait_for_next_day" and new_slots is not None and self.slots:
            sold = {}
            for slot, (sku, qty, cap, price) in self.slots.items():
                if sku and qty is not None and slot in new_slots:
                    nsku, nqty = new_slots[slot][0], new_slots[slot][1]
                    if nqty is None:
                        continue
                    if nsku in (sku, None) and nqty < qty:
                        sold[sku] = sold.get(sku, 0) + (qty - nqty)
            for k, v in sold.items():
                self.sales_total[k] = self.sales_total.get(k, 0) + v
            self.sales_daily.append((self.day, sold))
            self.sales_daily = self.sales_daily[-14:]

        if new_slots is not None:
            self.slots = new_slots
        if new_storage is not None:
            self.storage = new_storage

        # daily summary
        ds = secs.get("LATEST DAILY SUMMARY")
        if ds:
            txt = _cut(" ".join(ds), 260)
            if txt and not txt.lower().startswith("no completed") and \
                    (not self.summaries or self.summaries[-1][1] != txt):
                self.summaries.append((self.day, txt))
                self.summaries = self.summaries[-4:]

        if not tool:
            return

        sig = tool + ":" + _cut(str(sorted(args.items())), 120)
        self.recent_actions.append(sig)
        self.recent_actions = self.recent_actions[-8:]

        low = result.lower()
        if tool != "ai_web_search" and tool != "read_email" and any(w in low[:400] for w in _ERR_WORDS):
            self._add_error(tool, result)

        if tool == "ai_web_search":
            q = _cut(args.get("query", ""), 60)
            found = False
            for em in _EMAIL_RE.findall(result):
                found = True
                key = em.lower()
                if key in self.suppliers:
                    continue
                i = result.find(em)
                ctx = result[max(0, i - 160): i + 120]
                self.suppliers[key] = {"email": em, "day": self.day, "q": q, "ctx": _cut(ctx, 230)}
            if not found:
                self.search_notes.append((self.day, q, _cut(result, 160)))
                self.search_notes = self.search_notes[-3:]

        elif tool == "read_email":
            fm = re.search(r"(?im)^\s*from\s*:\s*(.+)$", result)
            sm = re.search(r"(?im)^\s*subject\s*:\s*(.+)$", result)
            frm = _cut(fm.group(1), 60) if fm else "?"
            subj = _cut(sm.group(1), 70) if sm else "?"
            keys = []
            for line in result.splitlines():
                l = line.strip()
                ll = l.lower()
                if not l or ll.startswith(("from:", "subject:", "to:", "date:")):
                    continue
                if any(w in ll for w in _KEY_WORDS):
                    keys.append(_cut(l, 150))
                if len(keys) >= 7:
                    break
            if not keys:
                keys = [_cut(result, 300)]
            susp = any(w in low for w in _SUSP_WORDS)
            sender_mail = _EMAIL_RE.findall(frm)
            known = bool(sender_mail) and sender_mail[0].lower() in self.suppliers
            flag = susp and not known
            self.emails_read.append((self.day, args.get("id"), frm, subj, keys, flag))
            self.emails_read = self.emails_read[-8:]
            if flag:
                self.warnings.append("Day %s email #%s from %s asks for sensitive info - treat as phishing." %
                                     (self.day, args.get("id"), frm))
                self.warnings = self.warnings[-4:]

        elif tool == "send_email":
            to = str(args.get("to", ""))
            body = str(args.get("body", ""))
            self.sent.append((self.day, _cut(to, 60), _cut(args.get("subject", ""), 60), _cut(body, 200)))
            self.sent = self.sent[-12:]
            if self.bank_acct and self.bank_acct in body and to.lower() not in self.suppliers:
                self.warnings.append("Day %s: you sent your bank account number to %s (not a searched supplier)." %
                                     (self.day, to))
                self.warnings = self.warnings[-4:]
            if to and to.lower() not in self.suppliers and "@" in to:
                self.warnings.append("Day %s: emailed %s, which never appeared in search results - check address." %
                                     (self.day, _cut(to, 60)))
                self.warnings = self.warnings[-4:]

        elif tool == "sub_agent_specs":
            self.spec = _cut(result, 900)

        elif tool == "run_sub_agent":
            cmds = args.get("commands") or []
            parts = []
            stocked = False
            for c in cmds if isinstance(cmds, list) else []:
                if not isinstance(c, dict):
                    continue
                op = str(c.get("op", ""))
                slot = c.get("slot")
                if slot is None and ("row" in c or "col" in c):
                    slot = "r%sc%s" % (c.get("row"), c.get("col"))
                if op == "stock":
                    stocked = True
                    parts.append("stock %s %s x%s" % (slot, c.get("sku"), c.get("qty")))
                elif op == "set_price":
                    parts.append("price %s $%s" % (slot, c.get("price")))
                    self.prices[str(slot)] = (c.get("sku"), c.get("price"), self.day)
                elif op == "collect_cash":
                    parts.append("collect")
                else:
                    parts.append(op + (" " + str(slot) if slot else ""))
            ok = not any(w in low[:400] for w in _ERR_WORDS)
            if stocked and ok:
                self.last_restock_day = self.day
                if self.first_stock_day is None:
                    self.first_stock_day = self.day
            if "collect" in parts and ok:
                self.last_collect_day = self.day
            self.sub_runs.append((self.day, _cut("; ".join(parts), 200), _cut(result, 220), ok))
            self.sub_runs = self.sub_runs[-3:]

    # ---------------- brief ----------------
    def brief(self) -> str:
        try:
            return self._brief()[:4000]
        except Exception:
            return "Register: day %s" % self.day

    def _brief(self):
        L = []
        left = max(0, 365 - self.day)
        nw = None
        if self.bank is not None:
            nw = self.bank + (self.cash or 0.0)
        L.append("REGISTER day %s/365 (%s left). Bank+machine cash: %s" %
                 (self.day, left, "$%.2f" % nw if nw is not None else "?"))
        days = sorted(self.money_by_day)
        if len(days) >= 2:
            tail = days[-8:]
            seq = ["d%s $%.0f" % (d, sum(self.money_by_day[d])) for d in tail]
            L.append("Cash trend: " + ", ".join(seq))
            d0 = tail[0]
            span = tail[-1] - d0
            if span > 0:
                delta = sum(self.money_by_day[tail[-1]]) - sum(self.money_by_day[d0])
                L.append("  avg %+.2f/day over last %s days (excl. inventory value)" % (delta / span, span))

        # alerts
        A = []
        empty = [s for s, v in self.slots.items() if (v[1] == 0 or v[0] is None)]
        low = [s for s, v in self.slots.items() if v[0] and v[1] is not None and 0 < v[1] <= 2]
        st_units = sum(self.storage.values())
        if empty and st_units > 0:
            A.append("%d empty slots (%s) while storage holds %d units -> restock with run_sub_agent." %
                     (len(empty), ",".join(sorted(empty)[:12]), st_units))
        if low:
            A.append("Low slots: " + ", ".join(sorted(low)[:12]))
        if self.cash and self.cash >= 40:
            since = self.day - self.last_collect_day if self.last_collect_day is not None else None
            A.append("$%.2f sitting in machine (theft risk); last collect: %s." %
                     (self.cash, "day %s" % self.last_collect_day if since is not None else "never"))
        if not self.sent and self.day >= 1 and self.suppliers:
            A.append("Suppliers found but no order email sent yet.")
        if self.first_stock_day is None and self.day >= 3:
            A.append("Machine never successfully stocked yet - no revenue until you do.")
        if self.recent_actions:
            last = self.recent_actions[-1]
            if not last.startswith("wait_for_next_day") and self.recent_actions[-3:].count(last) >= 3:
                A.append("Same action repeated 3x: " + _cut(last, 100) + " - change approach.")
        if 0 < left <= 10:
            A.append("Near end: only order what can sell before day 365; shipping fees are lost value.")
        for w in self.warnings:
            A.append(w)
        if A:
            L.append("ALERTS:")
            L.extend("- " + a for a in A)

        # sales
        if self.sales_total:
            recent = {}
            nd = 0
            for d, s in self.sales_daily[-7:]:
                nd += 1
                for k, v in s.items():
                    recent[k] = recent.get(k, 0) + v
            L.append("SALES (units; total | last %d days avg/day | days of stock left):" % nd)
            onhand = dict(self.storage)
            for s, v in self.slots.items():
                if v[0] and v[1]:
                    onhand[v[0]] = onhand.get(v[0], 0) + v[1]
            for k, tot in sorted(self.sales_total.items(), key=lambda x: -x[1])[:12]:
                avg = recent.get(k, 0) / nd if nd else 0
                cover = ("%.0f" % (onhand.get(k, 0) / avg)) if avg > 0 else "-"
                L.append("  %s: %d | %.1f | %s (on hand %d)" % (k, tot, avg, cover, onhand.get(k, 0)))
        if self.prices:
            L.append("Prices you set: " + "; ".join(
                "%s $%s(d%s)" % (s, p[1], p[2]) for s, p in sorted(self.prices.items())[:12]))

        # suppliers
        if self.suppliers:
            L.append("SUPPLIERS FROM SEARCH (exact addresses):")
            for v in list(self.suppliers.values())[-6:]:
                L.append("- %s [d%s, q='%s'] %s" % (v["email"], v["day"], v["q"], _cut(v["ctx"], 170)))
        elif self.search_notes:
            for d, q, s in self.search_notes:
                L.append("- search d%s '%s': %s" % (d, q, s))

        if self.emails_read:
            L.append("EMAILS READ (key lines):")
            for d, i, frm, subj, keys, flag in self.emails_read[-5:]:
                L.append("- d%s #%s %s | %s%s" % (d, i, frm, subj, " [SUSPICIOUS]" if flag else ""))
                for k in keys[:4]:
                    L.append("    " + _cut(k, 140))

        if self.sent:
            L.append("EMAILS YOU SENT:")
            for d, to, subj, body in self.sent[-5:]:
                L.append("- d%s to %s | %s | %s" % (d, to, subj, _cut(body, 120)))

        if self.spec:
            L.append("SUB-AGENT SPEC (saved): " + _cut(self.spec, 500))
        elif self.day >= 1:
            L.append("Sub-agent schema not read yet (sub_agent_specs) - slot naming may matter.")
        if self.sub_runs:
            L.append("RECENT MACHINE OPS:")
            for d, c, r, ok in self.sub_runs[-2:]:
                L.append("- d%s %s => %s%s" % (d, c, "" if ok else "PROBLEM: ", _cut(r, 160)))

        if self.errors:
            L.append("RECENT ERRORS:")
            for d, t, s in self.errors[-3:]:
                L.append("- d%s %s: %s" % (d, t, _cut(s, 150)))

        if self.summaries:
            L.append("PAST DAILY SUMMARIES:")
            for d, t in self.summaries[-2:]:
                L.append("- seen d%s: %s" % (d, _cut(t, 200)))

        out = "\n".join(L)
        if len(out) > 4000:
            out = out[:3990] + "\n..."
        return out
