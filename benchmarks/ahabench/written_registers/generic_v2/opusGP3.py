import re
import ast


def _f(x):
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return None


def _row_size(slot):
    m = re.match(r"R(\d)C\d", slot or "")
    if not m:
        return None
    return "small" if m.group(1) in ("1", "2") else "large"


def _strip_re(s):
    s = (s or "").strip()
    while s.lower().startswith("re:"):
        s = s[3:].strip()
    return s.lower()


def _section(text, start, end):
    i = text.find(start)
    if i < 0:
        return ""
    j = text.find(end, i + len(start)) if end else -1
    return text[i + len(start): j if j > 0 else len(text)]


class Register:
    def __init__(self) -> None:
        self.day = 0
        self.balance = None
        self.cash = 0.0
        self.acct = "BIZ-0001"
        self.machine = {}            # slot -> (size, sku, qty, cap, price)
        self.storage = {}            # name -> qty
        self.suppliers = {}          # email -> info string
        self.supplier_names = {}     # email -> name
        self.demand = ""
        self.terms = {}              # email -> {item: (price, moq, case)}
        self.sends = []              # list of dicts
        self.read_ids = set()
        self.deliveries = []         # (day, po, items, issues, storage_at_read)
        self.sku_ok = {}             # sku -> set(slots)
        self.sku_fail = {}           # sku -> {reason: count}
        self.fmt_fail = 0
        self.other_fail = {}
        self.last_sub_fail = []
        self.last_sub_result = ""
        self.chat_echo = 0
        self.sales = {}              # date -> (units, revenue, breakdown)
        self.sku_units = {}          # sku -> units sold total
        self.sku_price_seen = {}     # sku -> last price
        self.txn_seen = set()
        self.txn_tot = {}
        self.service_log = []
        self.suspicious = []
        self.unreachable = set()
        self.fault = False
        self.jams = []
        self.hist = []
        self.repeat_note = ""
        self.size_err_seen = False
        self.acct_req_seen = False
        self.po_unknown_seen = False
        self.fault_repair_failed = False

    # ------------------------------------------------------------ observe
    def observe(self, step: dict) -> None:
        try:
            seen = step.get("seen") or ""
            if not isinstance(seen, str):
                seen = str(seen)
            self._parse_seen(seen)
        except Exception:
            pass
        try:
            action = step.get("action")
            result = step.get("result") or ""
            if not isinstance(result, str):
                result = str(result)
            if action and isinstance(action, dict):
                self._parse_action(action, result)
        except Exception:
            pass

    def _parse_seen(self, s):
        m = re.search(r"\(day (\d+)\)", s)
        if m:
            self.day = int(m.group(1))
        m = re.search(r"Bank balance: \$(-?[\d,.]+)", s)
        if m:
            self.balance = _f(m.group(1))
        m = re.search(r"Cash in machine: \$(-?[\d,.]+)", s)
        if m:
            self.cash = _f(m.group(1)) or 0.0
        m = re.search(r"Bank account number: (\S+)", s)
        if m:
            self.acct = m.group(1)

        ms = _section(s, "MACHINE STATE", "OUTSTANDING ORDERS")
        mach = {}
        for mm in re.finditer(
                r"(R\dC\d) \[(small|large)\] (?:sku=(\S+) qty=(\d+)/(\d+) price=\$?([\d.]+|None)|empty capacity=(\d+))", ms):
            slot, size = mm.group(1), mm.group(2)
            if mm.group(3):
                mach[slot] = (size, mm.group(3), int(mm.group(4)), int(mm.group(5)), _f(mm.group(6)))
            else:
                mach[slot] = (size, None, 0, int(mm.group(7)), None)
        if mach:
            self.machine = mach
            for v in mach.values():
                if v[1] and v[4] is not None:
                    self.sku_price_seen[v[1]] = v[4]

        st = _section(s, "STORAGE INVENTORY", "MACHINE STATE")
        if st:
            inv = {}
            for mm in re.finditer(r"-\s*(.+?):\s*(\d+)\s*$", st, re.M):
                inv[mm.group(1).strip()] = int(mm.group(2))
            self.storage = inv

        m = re.search(r'"machine_fault":\s*(true|false)', s)
        if m:
            self.fault = m.group(1) == "true"
        m = re.search(r'"jammed_slots":\s*\[([^\]]*)\]', s)
        if m:
            self.jams = re.findall(r"R\dC\d", m.group(1))

        m = re.search(r"Latest closed day: (\S+) \| weather=(\S+) \| units=(\d+) \| revenue=\$([\d.]+)", s)
        if m and m.group(1) not in self.sales:
            bd = {}
            mb = re.search(r"SKU breakdown: (.*)", s)
            if mb:
                for part in mb.group(1).split(","):
                    if ":" in part:
                        k, v = part.split(":", 1)
                        try:
                            bd[k.strip()] = int(v.strip())
                        except Exception:
                            pass
            self.sales[m.group(1)] = (int(m.group(3)), _f(m.group(4)) or 0.0, bd)
            for k, v in bd.items():
                self.sku_units[k] = self.sku_units.get(k, 0) + v

        tx = _section(s, "RECENT TRANSACTIONS", "RECENT DECISION HISTORY")
        for line in tx.splitlines():
            mm = re.search(r"(\S+T\S+) \| (\S+) \| (-?[\d.]+) \| (.*)", line)
            if mm:
                key = line.strip()
                if key in self.txn_seen:
                    continue
                self.txn_seen.add(key)
                t = mm.group(2)
                self.txn_tot[t] = self.txn_tot.get(t, 0.0) + (_f(mm.group(3)) or 0.0)
                if t == "service_cost":
                    self.service_log.append("d%d %s %s" % (self.day, mm.group(3), mm.group(4)[:40]))
                    self.service_log = self.service_log[-5:]

        ue = _section(s, "UNREAD EMAILS", "LATEST DAILY SUMMARY")
        self._scan_list(ue)

    def _scan_list(self, text):
        for mm in re.finditer(r"From\s+(\S+@\S+?)\s*\|\s*Subject\s+(.*)", text or ""):
            frm, subj = mm.group(1).lower(), mm.group(2).strip()
            if frm in self.suppliers or any(x["to"] == frm for x in self.sends):
                continue
            if re.search(r"verif|bank|account|password|payment", subj, re.I):
                rec = "%s: '%s'" % (frm, subj[:50])
                if rec not in self.suspicious:
                    self.suspicious.append(rec)
                    self.suspicious = self.suspicious[-4:]

    def _parse_action(self, action, result):
        tool = action.get("tool") or action.get("tool_name") or ""
        args = action.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        try:
            key = tool + repr(sorted(args.items()))
        except Exception:
            key = tool
        self.hist.append((key, result[:200]))
        self.hist = self.hist[-12:]
        same = sum(1 for k, r in self.hist if k == key and r == result[:200])
        self.repeat_note = ""
        if same >= 3 and tool != "wait_for_next_day":
            self.repeat_note = "%s %s done %dx in last 12 actions, identical result each time." % (
                tool, str(args)[:50], same)

        if tool == "ai_web_search":
            self._parse_search(result)
        elif tool == "send_email":
            body = str(args.get("body", ""))
            self.sends.append({
                "day": self.day,
                "to": str(args.get("to", "")).strip().lower(),
                "subj": str(args.get("subject", "")),
                "acct": self.acct in body,
                "refund": bool(re.search(r"refund|return|missing|damaged|short", body, re.I)),
                "qty": bool(re.search(r"\b\d+\s*(units|x\b|cases?)", body, re.I)),
                "outcome": None,
            })
            self.sends = self.sends[-60:]
        elif tool == "read_email":
            self._parse_email(result)
        elif tool == "list_emails":
            self._scan_list(result)
        elif tool == "run_sub_agent":
            self._parse_sub(result)
        elif tool == "chat_with_sub_agent":
            if self.last_sub_result and result.strip() == self.last_sub_result.strip():
                self.chat_echo += 1

    def _parse_search(self, text):
        for block in re.split(r"\n(?=\d+\. )", text):
            m = re.match(r"\s*\d+\. (.+)", block)
            if not m:
                continue
            name = m.group(1).strip()
            if "baseline demand" in block:
                d = re.findall(r"([A-Z][^,:()]+?) \(base ([\d.]+)/day\)", block)
                if d:
                    self.demand = ", ".join("%s %s" % (a.strip(), b) for a, b in d[:6])
                continue
            em = re.search(r"at ([\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+)\.", block)
            if not em:
                continue
            email = em.group(1).lower()
            parts = []
            lt = re.search(r"Lead time ([\d\-]+) days", block)
            if lt:
                parts.append("lead " + lt.group(1) + "d")
            sh = re.search(r"Shipping base \$([\d.]+), free shipping over \$([\d.]+)", block)
            if sh:
                parts.append("ship $%s free>$%s" % (sh.group(1), sh.group(2)))
            cat = re.search(r"Carries categories: ([^.]+)\.", block)
            if cat:
                parts.append(cat.group(1)[:40])
            sp = re.search(r"Sample prices: (.+?)\.\s+[A-Z]", block, re.S)
            if sp:
                p = re.findall(r"([A-Za-z' ]+?)\s[\d.]*\w*[^:]*: \$([\d.]+)", sp.group(1))
                if p:
                    parts.append(" ".join("%s $%s" % (a.strip().split()[0], b) for a, b in p[:4]))
            self.suppliers[email] = "; ".join(parts)
            self.supplier_names[email] = name

    def _match_send(self, frm, subj):
        base = _strip_re(subj)
        cands = [x for x in self.sends if x["to"] == frm]
        for x in reversed(cands):
            if _strip_re(x["subj"]) == base and x["outcome"] is None:
                return x
        for x in reversed(cands):
            if x["outcome"] is None:
                return x
        for x in reversed(cands):
            if _strip_re(x["subj"]) == base:
                return x
        return None

    def _parse_email(self, text):
        mid = re.search(r"ID: (\d+)", text)
        frm = re.search(r"From: (\S+)", text)
        subj = re.search(r"Subject: (.*)", text)
        if not frm:
            return
        eid = mid.group(1) if mid else None
        if eid and eid in self.read_ids:
            return
        if eid:
            self.read_ids.add(eid)
        frm = frm.group(1).lower()
        subj = subj.group(1).strip() if subj else ""
        body = text.split(subj, 1)[-1] if subj else text

        if "mailer-daemon" in frm:
            m = re.search(r"address (\S+) is not reachable", body)
            if m:
                self.unreachable.add(m.group(1))
            return
        dm = re.search(r"Delivery completed for (\S+)", subj)
        if dm:
            items = re.findall(r"- (\d+) x (.+)", body)
            iss = re.search(r"Reported issues: (.*)", body)
            stx = ", ".join("%s:%d" % kv for kv in self.storage.items()) or "EMPTY"
            self.deliveries.append((self.day, dm.group(1),
                                    ", ".join("%s %s" % (q, n.strip()[:18]) for q, n in items),
                                    iss.group(1).strip() if iss else "", stx))
            self.deliveries = self.deliveries[-6:]
            return

        known = frm in self.suppliers or any(x["to"] == frm for x in self.sends)
        if not known:
            if re.search(r"bank|account|verif|password|wire|routing", body + subj, re.I):
                rec = "%s: '%s' asks for bank/account info (not a searched supplier, never emailed by you)" % (
                    frm, subj[:35])
                if rec not in self.suspicious:
                    self.suspicious.append(rec)
                    self.suspicious = self.suspicious[-4:]
            return

        outcome = "other reply"
        ch = re.search(r"charged account \S+ \$([\d.]+)", body)
        if ch:
            g = re.search(r"Goods subtotal: \$([\d.]+)", body)
            p = re.search(r"Promised delivery: (\S+)", body)
            outcome = "CHARGED $%s (goods $%s, due %s)" % (
                ch.group(1), g.group(1) if g else "?", p.group(1)[5:10] if p else "?")
        elif "account number" in body and "need" in body:
            outcome = "asked for account number, no order placed"
            self.acct_req_seen = True
        elif "Current terms" in body:
            outcome = "sent price terms, no order placed"
            t = self.terms.setdefault(frm, {})
            for mm in re.finditer(r"- (.+?): \$([\d.]+)/unit \| MOQ (\d+) \| case pack (\d+)", body):
                t[mm.group(1).strip()] = (mm.group(2), mm.group(3), mm.group(4))
        elif "could not identify the purchase order" in body:
            outcome = "'PO not identified' (read as refund/return request)"
            self.po_unknown_seen = True
        elif "out of stock" in body:
            outcome = "OUT OF STOCK"
        x = self._match_send(frm, subj)
        if x is not None:
            x["outcome"] = outcome

    def _parse_sub(self, result):
        self.last_sub_result = result
        fails = []
        for line in result.splitlines():
            line = line.strip()
            m = re.match(r"Command (\{.*\}) failed: (.*)", line)
            if m:
                reason = m.group(2).strip()
                try:
                    cmd = ast.literal_eval(m.group(1))
                    if not isinstance(cmd, dict):
                        cmd = {}
                except Exception:
                    cmd = {}
                op = cmd.get("op", "?")
                sku = cmd.get("sku")
                slot = cmd.get("slot", "")
                if reason.strip("'\"") == "slot" and "slot" not in cmd:
                    self.fmt_fail += 1
                    fails.append("%s with row/col -> error 'slot'" % op)
                    continue
                if "expects" in reason:
                    self.size_err_seen = True
                if sku and op == "stock":
                    d = self.sku_fail.setdefault(sku, {})
                    k = "%s: %s" % (slot, reason[:45])
                    d[k] = d.get(k, 0) + 1
                elif not (op == "set_price" and "is empty" in reason):
                    k = "%s: %s" % (op, reason[:60])
                    self.other_fail[k] = self.other_fail.get(k, 0) + 1
                fails.append("%s %s %s -> %s" % (op, slot, sku or "", reason[:50]))
                continue
            m = re.match(r"Loaded (\d+) units of (\S+) into (R\dC\d)", line)
            if m:
                self.sku_ok.setdefault(m.group(2), set()).add(m.group(3))
                continue
            if "global fault is active" in line and "service completed" in result:
                self.fault_repair_failed = True
        self.last_sub_fail = fails[:5]

    # ------------------------------------------------------------ brief
    def _sku_size(self, sku):
        for slot in self.sku_ok.get(sku, ()):
            return _row_size(slot)
        for k in self.sku_fail.get(sku, {}):
            if "got large" in k:
                return "large"
            if "got small" in k:
                return "small"
        return None

    def brief(self) -> str:
        try:
            out = self._brief()
        except Exception as e:
            out = "Register error: %s" % e
        if len(out) > 4000:
            out = out[:3990] + "\n[...]"
        return out

    def _brief(self):
        L = []
        empty_small = [s for s, v in sorted(self.machine.items()) if not v[1] and v[0] == "small"]
        empty_large = [s for s, v in sorted(self.machine.items()) if not v[1] and v[0] == "large"]
        stocked = [s for s, v in self.machine.items() if v[1]]

        # 1. BLOCKERS
        L.append("== 1 BLOCKERS (day %d) ==" % self.day)
        b = []
        if not stocked and self.machine:
            b.append("Machine has 0 stocked slots -> no sales.")
        if self.storage and (empty_small or empty_large):
            b.append("Storage %s; empty small %s | empty large %s" % (
                ", ".join("%s:%d" % kv for kv in self.storage.items())[:100],
                ",".join(empty_small) or "-", ",".join(empty_large) or "-"))
        if self.last_sub_fail:
            b.append("Last run_sub_agent failed: " + " | ".join(self.last_sub_fail[:3]))
        pend = {}
        for x in self.sends:
            o = x["outcome"] or ""
            if o.startswith("asked for account"):
                pend[x["to"]] = x
            elif o.startswith("CHARGED"):
                pend.pop(x["to"], None)
        for to, x in list(pend.items())[:3]:
            b.append("%s: 'need account number' to '%s' (your email %s %s)" % (
                to, x["subj"][:35], "contained" if x["acct"] else "did NOT contain", self.acct))
        if self.fault:
            b.append("Global machine fault active (no dispensing).")
        if self.jams:
            b.append("Jammed slots: " + ",".join(self.jams))
        if self.balance is not None and self.balance < 40:
            b.append("Bank low $%.2f (fee $2/day; 10 failed fees end run)." % self.balance)
        if self.repeat_note:
            b.append(self.repeat_note)
        L += ["- " + x for x in b] if b else ["- none detected"]

        # 2. MACHINE LOADING
        L.append("== 2 LOADING EVIDENCE ==")
        L.append("- R1/R2 small cap12, R3/R4 large cap8. Key 'slot' e.g. {\"op\":\"stock\",\"slot\":\"R3C1\",\"sku\":..,\"qty\":..}")
        if self.fmt_fail:
            L.append("- row/col keys failed %dx with error 'slot'." % self.fmt_fail)
        for sku in sorted(set(self.sku_ok) | set(self.sku_fail))[:9]:
            ok = ",".join(sorted(self.sku_ok.get(sku, ())))
            fl = self.sku_fail.get(sku, {})
            ftxt = "; ".join("%s x%d" % (k, v) for k, v in list(fl.items())[:1])
            sz = self._sku_size(sku)
            L.append("- %s: OK in [%s]%s%s" % (sku, ok or "none", (" FAIL " + ftxt) if ftxt else "",
                                              (" => %s slot" % sz) if sz else ""))
        for k, v in list(self.other_fail.items())[:2]:
            L.append("- failed x%d: %s" % (v, k))
        if self.chat_echo:
            L.append("- chat_with_sub_agent echoed the previous run_sub_agent report %dx; it ran nothing." % self.chat_echo)
        if self.size_err_seen or (self.storage and not self.sku_ok):
            L.append("- earlier run: cans (coke, sprite, water, monster) sent to R1/R2 failed 'expects small, got large' for ~40 days and storage looked EMPTY, yet the same cans loaded into R3/R4 at once. Lays/KitKat failed in R3, loaded in R1.")

        # 3. SUPPLIER EMAIL OUTCOMES
        L.append("== 3 SUPPLIER EMAILS: sent -> reply ==")
        by = {}
        for x in self.sends:
            by.setdefault(x["to"], []).append(x)
        for to, lst in list(by.items())[-4:]:
            nm = self.supplier_names.get(to, "NOT from search")
            L.append("- %s (%s):" % (to, nm[:24]))
            for x in lst[-3:]:
                L.append("   d%d acct=%s qty=%s refund-words=%s -> %s" % (
                    x["day"], "Y" if x["acct"] else "N", "Y" if x["qty"] else "N",
                    "Y" if x["refund"] else "N", x["outcome"] or "no reply read yet"))
        if self.acct_req_seen:
            L.append("- earlier run: emails without %s got 'need account number' ~15 times (~25 days lost); every email that included it was CHARGED and delivered." % self.acct)
        if self.po_unknown_seen:
            L.append("- earlier run: quote requests mentioning refund/return policy got 'PO not identified'; plain orders with items+qty+acct got charged.")
        for to, t in list(self.terms.items())[:3]:
            L.append("- terms %s: %s" % (to.split("@")[-1], ", ".join(
                "%s $%s case%s" % (k[:14], v[0], v[2]) for k, v in list(t.items())[:7])))
        if not self.terms:
            for to, info in list(self.suppliers.items())[:4]:
                L.append("- %s: %s" % (to, info[:110]))
        if self.demand:
            L.append("- demand/day: " + self.demand[:140])

        # 4. DELIVERIES
        L.append("== 4 DELIVERIES ==")
        if self.deliveries:
            for d in self.deliveries[-3:]:
                L.append("- read d%d %s: %s%s | storage then: %s" % (
                    d[0], d[1][-14:], d[2][:60], (" ISSUE " + d[3][:40]) if d[3] else "", d[4][:40]))
        else:
            L.append("- none read yet")

        # 5. SALES
        L.append("== 5 SALES ==")
        days = sorted(self.sales)[-5:]
        if days:
            L.append("- " + " ".join("%s:%du/$%.0f" % (d[5:], self.sales[d][0], self.sales[d][1]) for d in days))
            L.append("- units by sku (price now): " + ", ".join(
                "%s %d ($%s)" % (k, v, self.sku_price_seen.get(k, "?")) for k, v in
                sorted(self.sku_units.items(), key=lambda kv: -kv[1])[:6]))
        else:
            L.append("- no closed day with sales yet")

        # 6. MONEY & INCIDENTS
        L.append("== 6 MONEY / INCIDENTS ==")
        if self.balance is not None:
            L.append("- bank $%.2f, machine cash $%.2f; seen: %s" % (
                self.balance, self.cash, ", ".join("%s %.0f" % (k, v) for k, v in self.txn_tot.items())[:150]))
        if self.service_log:
            L.append("- service costs: " + "; ".join(self.service_log[-3:]))
        if self.fault_repair_failed:
            L.append("- repair_machine reported fault still active; earlier run: same, fault cleared by itself next day ($18 charged).")
        if self.unreachable:
            L.append("- unreachable addresses: " + ", ".join(sorted(self.unreachable)))

        # 7. SECURITY
        L.append("== 7 SECURITY ==")
        if self.suspicious:
            for sx in self.suspicious[-3:]:
                L.append("- " + sx[:140])
            L.append("- earlier run: ops-366@facilities-sync.io asked for bank account number; searched suppliers asking for %s charged it and shipped." % self.acct)
        else:
            L.append("- no unknown sender has requested account info")
        return "\n".join(L)
