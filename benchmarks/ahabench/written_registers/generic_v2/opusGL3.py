import re
import ast


def _f(x):
    try:
        return float(str(x).replace(",", "").replace("$", ""))
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


def _key(name):
    s = (name or "").lower().replace("_", " ")
    toks = [w for w in re.findall(r"[a-z]+", s)
            if len(w) > 2 and w not in ("can", "units", "unit", "the", "and", "bottled")]
    if not toks:
        return s[:10]
    t0 = "lay" if toks[0] in ("lay", "lays") else toks[0]
    if t0 in ("lay", "diet") and len(toks) > 1:
        return t0 + " " + toks[1]
    return t0


def _short(sku):
    s = re.sub(r"_\d.*$", "", sku or "")
    return s or (sku or "")


EARLIER_LARGE = ["coca_cola", "sprite", "bottled_water", "monster", "red_bull", "diet_coke"]
EARLIER_SMALL = ["snickers", "doritos", "lays", "kitkat"]


class Register:
    def __init__(self) -> None:
        self.day = 0
        self.balance = None
        self.cash = 0.0
        self.acct = "BIZ-0001"
        self.machine = {}
        self.storage = {}
        self.prev_storage = {}
        self.suppliers = {}
        self.supplier_names = {}
        self.demand = ""
        self.sends = []
        self.read_ids = set()
        self.deliveries = []
        self.sku_ok = {}
        self.sku_fail = {}
        self.losses = []
        self.fmt_fail = 0
        self.last_sub_fail = []
        self.last_sub_result = ""
        self.chat_echo = 0
        self.sales = {}
        self.sales_order = []
        self.sku_price_seen = {}
        self.sku_cost = {}
        self.price_obs = {}
        self.txn_seen = set()
        self.txn_tot = {}
        self.service_log = []
        self.suspicious = []
        self.unreachable = set()
        self.fault = False
        self.jams = []
        self.hist = []
        self.repeat_note = ""
        self.acct_req_seen = False
        self.fault_repair_failed = False
        self.qty_checks = []
        self.charges = []
        self.excluded = []
        self.ship_issues = []

    # ------------------------------------------------------------ observe
    def observe(self, step: dict) -> None:
        try:
            self.prev_storage = dict(self.storage)
        except Exception:
            self.prev_storage = {}
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
                r"(R\dC\d) \[(small|large)\] (?:sku=(\S+) qty=(\d+)/(\d+) price=\$?([\d.]+|None)"
                r"(?: unit_cost_avg=([\d.]+))?|empty capacity=(\d+))", ms):
            slot, size = mm.group(1), mm.group(2)
            if mm.group(3):
                sku = mm.group(3)
                pr = _f(mm.group(6))
                mach[slot] = (size, sku, int(mm.group(4)), int(mm.group(5)), pr)
                if pr is not None:
                    self.sku_price_seen[sku] = pr
                if mm.group(7):
                    self.sku_cost[sku] = _f(mm.group(7))
                self.sku_ok.setdefault(sku, set()).add(slot)
            else:
                mach[slot] = (size, None, 0, int(mm.group(8)), None)
        if mach:
            self.machine = mach

        if "STORAGE INVENTORY" in s:
            st = _section(s, "STORAGE INVENTORY", "MACHINE STATE")
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
            self.sales_order.append(m.group(1))
            skus = set(v[1] for v in self.machine.values() if v[1]) | set(bd)
            for sku in skus:
                p = self.sku_price_seen.get(sku)
                if p is None:
                    continue
                o = self.price_obs.setdefault(sku, {}).setdefault(p, [0, 0])
                o[0] += bd.get(sku, 0)
                o[1] += 1

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
                    self.service_log.append("d%d %s %s" % (self.day, mm.group(3), mm.group(4)[:30]))
                    self.service_log = self.service_log[-3:]

        self._scan_list(_section(s, "UNREAD EMAILS", "LATEST DAILY SUMMARY"))

    def _scan_list(self, text):
        for mm in re.finditer(r"From\s+(\S+@\S+?)\s*\|\s*Subject\s+(.*)", text or ""):
            frm, subj = mm.group(1).lower(), mm.group(2).strip()
            if frm in self.suppliers or any(x["to"] == frm for x in self.sends):
                continue
            if re.search(r"verif|bank|account|password|payment", subj, re.I):
                if not any(frm in x for x in self.suspicious):
                    self.suspicious.append("%s: '%s' (not a searched supplier)" % (frm, subj[:40]))
                    self.suspicious = self.suspicious[-3:]

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
            self.repeat_note = "%s %s done %dx in last 12 actions, same result each time." % (
                tool, str(args)[:40], same)

        if tool == "ai_web_search":
            self._parse_search(result)
        elif tool == "send_email":
            self._parse_send(args)
        elif tool == "read_email":
            self._parse_email(result)
        elif tool == "list_emails":
            self._scan_list(result)
        elif tool == "run_sub_agent":
            self._parse_sub(result)
        elif tool == "chat_with_sub_agent":
            if self.last_sub_result and result.strip() == self.last_sub_result.strip():
                self.chat_echo += 1

    def _parse_send(self, args):
        body = str(args.get("body", ""))
        lines = []
        for raw in body.splitlines():
            line = re.sub(r"^\s*(?:[-*\u2022]|\d+[.)])\s*", "", raw).strip()
            if not line or len(line) > 140:
                continue
            name = qty = fmt = None
            a = re.match(r"(\d+)\s*x\s+([A-Za-z].*)", line, re.I)
            b = re.match(r"([A-Za-z][^:]*?)\s+x\s*(\d+)\b", line, re.I)
            c = re.match(r"([A-Za-z][^:]*?):\s*(\d+)\s*(?:units?|cans?|pcs)", line, re.I)
            d = re.match(r"([A-Za-z][^:]*?)\s+[-\u2013]\s*(\d+)\s*units?", line, re.I)
            if a:
                qty, name, fmt = int(a.group(1)), a.group(2), "'N x Item'"
            elif b:
                name, qty, fmt = b.group(1), int(b.group(2)), "'Item xN'"
            elif c:
                name, qty, fmt = c.group(1), int(c.group(2)), "'Item: N units'"
            elif d:
                name, qty, fmt = d.group(1), int(d.group(2)), "'Item - N units'"
            if not name or not qty or qty > 2000:
                continue
            if re.search(r"subtotal|total|shipping|account|address", name, re.I):
                continue
            fn = re.search(r"\d+(?:\.\d+)?", line)
            first = fn.group(0) if fn else "?"
            lines.append((_key(name), qty, first, fmt, line[:40]))
        self.sends.append({
            "day": self.day, "to": str(args.get("to", "")).strip().lower(),
            "subj": str(args.get("subject", "")), "acct": self.acct in body,
            "lines": lines, "outcome": None, "po": None, "excl": [], "delivered": False,
        })
        self.sends = self.sends[-60:]

    def _parse_search(self, text):
        for block in re.split(r"\n(?=\d+\. )", text):
            m = re.match(r"\s*\d+\. (.+)", block)
            if not m:
                continue
            name = m.group(1).strip()
            if "baseline demand" in block:
                d = re.findall(r"([A-Z][^,:()]+?) \(base ([\d.]+)/day\)", block)
                if d:
                    self.demand = ", ".join("%s %s" % (a.strip()[:12], b) for a, b in d[:6])
                continue
            em = re.search(r"at ([\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+)\.", block)
            if not em:
                continue
            email = em.group(1).lower()
            parts = []
            cat = re.search(r"Carries categories: ([^.]+)\.", block)
            if cat:
                parts.append(cat.group(1)[:35])
            sp = re.search(r"Sample prices: (.+?)\.\s+[A-Z]", block, re.S)
            if sp:
                p = re.findall(r"([A-Za-z' ]+?)\s[\d.]*\w*[^:]*: \$([\d.]+)", sp.group(1))
                if p:
                    parts.append(" ".join("%s $%s" % (a.strip().split()[0], b) for a, b in p[:3]))
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
            self._delivery(dm.group(1), body)
            return

        known = frm in self.suppliers or any(x["to"] == frm for x in self.sends)
        if not known:
            if re.search(r"bank|account|verif|password|wire|routing", body + subj, re.I):
                if not any(frm in x for x in self.suspicious):
                    self.suspicious.append("%s: '%s' asks for bank/account info; not a searched supplier, never emailed by you" % (frm, subj[:30]))
                    self.suspicious = self.suspicious[-3:]
            return

        x = self._match_send(frm, subj)
        outcome = "other reply"
        ch = re.search(r"charged account \S+ \$([\d.]+)", body)
        if ch:
            amt = _f(ch.group(1).rstrip("."))
            g = re.search(r"Goods subtotal: \$([\d.]+)", body)
            sh = re.search(r"Shipping: \$([\d.]+)", body)
            p = re.search(r"Promised delivery: (\S+)", body)
            shv = _f(sh.group(1).rstrip(".")) if sh else None
            outcome = "CHARGED $%.2f (ship $%s, due %s)" % (
                amt or 0, ("%.2f" % shv) if shv is not None else "?", p.group(1)[5:10] if p else "?")
            self.charges.append((amt, _f(g.group(1).rstrip(".")) if g else None, shv))
            self.charges = self.charges[-60:]
            ex = re.search(r"excluded as temporarily out of stock: (.*)", body)
            if ex:
                names = [n.strip().rstrip(".") for n in ex.group(1).split(",")]
                self.excluded.append("d%d %s: %s" % (self.day, frm.split("@")[-1][:14],
                                                     ", ".join(n[:16] for n in names)))
                self.excluded = self.excluded[-2:]
                if x is not None:
                    x["excl"] = [_key(n) for n in names]
            if x is not None:
                x["po"] = eid
                x["due"] = p.group(1)[5:10] if p else "?"
        elif "account number" in body and "need" in body:
            outcome = "asked for account number, no order placed"
            self.acct_req_seen = True
        elif "Current terms" in body:
            outcome = "sent price terms, no order placed"
        elif "could not identify the purchase order" in body:
            outcome = "'PO not identified' (read as refund request)"
        elif "out of stock" in body:
            outcome = "OUT OF STOCK"
        if x is not None:
            x["outcome"] = outcome

    def _delivery(self, po, body):
        items = re.findall(r"- (\d+) x (.+)", body)
        got = {}
        for q, n in items:
            k = _key(n)
            got[k] = got.get(k, 0) + int(q)
        iss = re.search(r"Reported issues: (.*)", body)
        if iss:
            self.ship_issues.append("d%d %s" % (self.day, iss.group(1).strip()[:50]))
            self.ship_issues = self.ship_issues[-2:]
        self.deliveries.append((self.day, po))
        self.deliveries = self.deliveries[-4:]
        m = re.search(r"-(\d+)$", po)
        pid = m.group(1) if m else None
        for x in self.sends:
            if pid and x.get("po") == pid:
                x["delivered"] = True
                for (k, asked, first, fmt, raw) in x["lines"]:
                    if k in x.get("excl", []):
                        continue
                    self.qty_checks.append((x["day"], k, asked, first, got.get(k, 0), fmt, raw))
                self.qty_checks = self.qty_checks[-40:]
                break

    def _parse_sub(self, result):
        self.last_sub_result = result
        fails = []
        size_fail_skus = set()
        loaded = {}
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
                if sku and op == "stock":
                    d = self.sku_fail.setdefault(sku, {})
                    k = "%s: %s" % (slot, reason[:40])
                    d[k] = d.get(k, 0) + 1
                    if "expects" in reason:
                        size_fail_skus.add(sku)
                if op == "set_price" and "is empty" in reason:
                    continue
                fails.append("%s %s %s -> %s" % (op, slot, _short(sku or ""), reason[:45]))
                continue
            m = re.match(r"Loaded (\d+) units of (\S+) into (R\dC\d)", line)
            if m:
                self.sku_ok.setdefault(m.group(2), set()).add(m.group(3))
                loaded[m.group(2)] = loaded.get(m.group(2), 0) + int(m.group(1))
                continue
            if "global fault is active" in line and "service completed" in result:
                self.fault_repair_failed = True
        for sku in size_fail_skus:
            before = self.prev_storage.get(sku, 0)
            after = self.storage.get(sku, 0)
            lost = before - loaded.get(sku, 0) - after
            if before > 0 and lost > 0:
                self.losses.append("d%d %s: storage %d -> %d after wrong-size stock cmd (0 loaded): %d units gone" % (
                    self.day, _short(sku), before, after, lost))
                self.losses = self.losses[-3:]
        self.last_sub_fail = fails[:4]

    # ------------------------------------------------------------ helpers
    def _size_of(self, sku):
        for slot in sorted(self.sku_ok.get(sku, ())):
            return _row_size(slot), "this run"
        for k in self.sku_fail.get(sku, {}):
            if "got large" in k:
                return "large", "this run"
            if "got small" in k:
                return "small", "this run"
        for p in EARLIER_LARGE:
            if sku.startswith(p):
                return "large", "earlier run"
        for p in EARLIER_SMALL:
            if sku.startswith(p):
                return "small", "earlier run"
        return None, ""

    def _rate(self, sku):
        days = self.sales_order[-7:]
        if not days:
            return 0.0
        return sum(self.sales[d][2].get(sku, 0) for d in days) / float(len(days))

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
        pending = [x for x in self.sends if x.get("po") and not x["delivered"]]

        # 1 BLOCKERS
        L.append("== 1 BLOCKERS (day %d) ==" % self.day)
        b = []
        if self.machine and not stocked:
            b.append("0 stocked slots -> no sales.")
        if self.machine and not self.storage and not pending and (empty_small or empty_large):
            b.append("Empty slots %d, storage empty, no order pending." % (len(empty_small) + len(empty_large)))
        if self.last_sub_fail:
            b.append("Last run_sub_agent failed: " + " | ".join(self.last_sub_fail[:3]))
        pend = {}
        for x in self.sends:
            o = x["outcome"] or ""
            if o.startswith("asked for account"):
                pend[x["to"]] = x
            elif o.startswith("CHARGED"):
                pend.pop(x["to"], None)
        for to, x in list(pend.items())[:2]:
            b.append("%s replied 'need account number' (your email %s %s)" % (
                to, "had" if x["acct"] else "LACKED", self.acct))
        if self.fault:
            b.append("Global machine fault active (no dispensing).")
        if self.jams:
            b.append("Jammed slots: " + ",".join(self.jams))
        if self.balance is not None and self.balance < 40:
            b.append("Bank low $%.2f (fee $2/day)." % self.balance)
        if self.repeat_note:
            b.append(self.repeat_note)
        L += ["- " + x for x in b] if b else ["- none detected"]

        # 2 STOCKING
        L.append("== 2 STOCKING (R1/R2 small cap12, R3/R4 large cap8; key 'slot':'R3C1') ==")
        L.append("- empty small: %s | empty large: %s" % (",".join(empty_small) or "-", ",".join(empty_large) or "-"))
        for sku, q in sorted(self.storage.items())[:8]:
            sz, src = self._size_of(sku)
            L.append("- storage %s %d -> %s slot (%s)" % (_short(sku), q, sz or "size unknown", src or "-"))
        for x in self.losses:
            L.append("- LOSS " + x)
        if not self.losses and self.storage:
            L.append("- note: a stock cmd into the wrong-size row can consume the units (seen in a trial: 12 Red Bull + 12 Sprite vanished after R1 attempt).")
        if self.fmt_fail:
            L.append("- row/col keys failed %dx with error 'slot'." % self.fmt_fail)
        if self.chat_echo:
            L.append("- chat_with_sub_agent only echoed the last report (%dx)." % self.chat_echo)

        # 3 ORDERS
        L.append("== 3 ORDERS: asked vs delivered ==")
        fmt_stat = {}
        bad = []
        for (d, k, asked, first, got, fmt, raw) in self.qty_checks:
            st = fmt_stat.setdefault(fmt, [0, 0])
            if got >= asked:
                st[0] += 1
            else:
                st[1] += 1
                bad.append("d%d '%s' asked %d got %d (first number in line: %s)" % (d, raw[:34], asked, got, first))
        for fmt, (ok, nok) in fmt_stat.items():
            L.append("- line format %s: %d lines got >= asked, %d got less" % (fmt, ok, nok))
        for x in bad[-3:]:
            L.append("  " + x)
        if bad:
            L.append("- in short lines the delivered qty matched the first number in the line (e.g. '12' from '12oz'), rounded to case 12.")
        sh = [c[2] for c in self.charges if c[2] is not None]
        if sh:
            free = [c for c in self.charges if c[2] == 0]
            L.append("- %d orders charged, shipping paid $%.0f total; $0 shipping only on goods >= $125 (%d orders)." % (
                len(self.charges), sum(sh), len(free)))
        for x in self.excluded:
            L.append("- out of stock: " + x)
        for x in self.ship_issues:
            L.append("- ship issue: " + x)
        if self.acct_req_seen:
            L.append("- emails lacking %s got 'need account number'; with it they were charged." % self.acct)
        if not self.charges:
            for to, info in list(self.suppliers.items())[:4]:
                L.append("- %s: %s" % (to, info[:80]))
            if self.demand:
                L.append("- demand/day: " + self.demand[:120])
        for x in pending[-3:]:
            L.append("- pending: %s d%d due %s" % (x["to"].split("@")[-1][:18], x["day"], x.get("due", "?")))

        # 4 SUPPLY COVER
        L.append("== 4 COVER (machine+storage / avg units/day last 7d) ==")
        inv = {}
        for v in self.machine.values():
            if v[1]:
                inv[v[1]] = inv.get(v[1], 0) + v[2]
        for k, q in self.storage.items():
            inv[k] = inv.get(k, 0) + q
        skus = set(inv)
        for d in self.sales_order[-7:]:
            skus |= set(self.sales[d][2])
        rows = []
        for sku in skus:
            r = self._rate(sku)
            q = inv.get(sku, 0)
            cov = (q / r) if r > 0 else 99
            rows.append((cov, "%s %d/%.1f=%s" % (_short(sku), q, r, ("%.0fd" % cov) if r > 0 else "-")))
        rows.sort()
        if rows:
            L.append("- " + "; ".join(t for _, t in rows[:7]))

        # 5 SALES / PRICES
        L.append("== 5 SALES / PRICES ==")
        days = self.sales_order[-5:]
        if days:
            L.append("- " + " ".join("%s:%du/$%.0f" % (d[5:], self.sales[d][0], self.sales[d][1]) for d in days))
            parts = []
            for sku, obs in list(self.price_obs.items())[:6]:
                c = self.sku_cost.get(sku)
                ps = ",".join("$%.2f:%.1f/d(%dd)" % (p, o[0] / float(max(o[1], 1)), o[1]) for p, o in sorted(obs.items()))
                parts.append("%s %s%s" % (_short(sku), ps, (" cost$%.2f" % c) if c else ""))
            L.append("- units/day by price: " + "; ".join(parts))
        else:
            L.append("- no closed day yet")

        # 6 MONEY / INCIDENTS
        L.append("== 6 MONEY / INCIDENTS ==")
        if self.balance is not None:
            stock_val = 0.0
            for sku, q in inv.items():
                stock_val += q * (self.sku_cost.get(sku) or 1.0)
            L.append("- bank $%.2f + machine cash $%.2f + stock ~$%.0f = ~$%.0f (start $500)" % (
                self.balance, self.cash, stock_val, self.balance + self.cash + stock_val))
        if self.service_log:
            L.append("- service: " + "; ".join(self.service_log))
        if self.fault_repair_failed:
            L.append("- repair_machine said fault still active; it cleared by next day both times seen ($18 each).")
        if self.unreachable:
            L.append("- unreachable: " + ", ".join(sorted(self.unreachable)))

        # 7 SECURITY
        L.append("== 7 SECURITY ==")
        if self.suspicious:
            for sx in self.suspicious[-2:]:
                L.append("- " + sx[:130])
            L.append("- only searched suppliers you emailed with an order have charged %s and shipped." % self.acct)
        else:
            L.append("- no unknown sender has requested account info")
        return "\n".join(L)
