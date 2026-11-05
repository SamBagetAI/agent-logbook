import re
import ast


def _num(s):
    try:
        return float(str(s).replace(",", "").replace("$", ""))
    except Exception:
        return None


def _norm_subj(s):
    s = (s or "").strip()
    while True:
        n = re.sub(r"^(re|fwd?)\s*:\s*", "", s, flags=re.I)
        if n == s:
            break
        s = n
    return s.lower().strip()


def _section(text, start, ends):
    i = text.find(start)
    if i < 0:
        return ""
    i += len(start)
    j = len(text)
    for e in ends:
        k = text.find(e, i)
        if 0 <= k < j:
            j = k
    return text[i:j]


COMPLAINT_RE = re.compile(r"missing|wrong|discrepanc|refund|not received|undelivered|short|mis-?ship", re.I)


class Register:
    def __init__(self) -> None:
        self.day = 0
        self.date = ""
        self.bank = None
        self.cash = None
        self.acct = "BIZ-0001"
        self.slots = {}
        self.storage = []
        self.orders = []
        self.fault = False
        self.jams = []
        self.sku = {}
        self.fmt_errors = {}
        self.suppliers = {}
        self.directory = {}
        self.demand = ""
        self.sent = []
        self.read = {}
        self.suspicious = []
        self.unreachable = set()
        self.sales = {}
        self.cash_collected = 0.0
        self.service = []
        self.deliveries = []
        self.any_stock_attempt = False
        self.last_sig = None
        self.repeat = 0
        self.need_acct_seen = False
        self.complaint_sent = False

    # ---------------- observe ----------------
    def observe(self, step: dict) -> None:
        try:
            self._observe(step or {})
        except Exception:
            pass

    def _sup(self, addr):
        addr = (addr or "").lower()
        if addr not in self.suppliers:
            self.suppliers[addr] = {"charged": [], "need": 0, "oos": 0, "nopo": 0, "terms": [], "sent": 0}
        return self.suppliers[addr]

    def _observe(self, step):
        act = step.get("action") or None
        res = str(step.get("result") or "")
        seen = str(step.get("seen") or "")
        self._parse_seen(seen)
        if act:
            tool = act.get("tool") or act.get("tool_name") or ""
            args = act.get("arguments") or {}
            sig = tool + repr(sorted(args.items())) if isinstance(args, dict) else tool
            if sig == self.last_sig and tool != "wait_for_next_day":
                self.repeat += 1
            else:
                self.repeat = 0
            self.last_sig = sig
            try:
                if tool == "run_sub_agent":
                    self._parse_sub(args, res)
                elif tool == "chat_with_sub_agent":
                    pass
                elif tool == "send_email":
                    self._parse_send(args)
                elif tool == "read_email":
                    self._parse_read(res)
                elif tool == "ai_web_search":
                    self._parse_search(res)
            except Exception:
                pass

    def _parse_seen(self, seen):
        if not seen:
            return
        m = re.search(r"\(day (\d+)\)", seen)
        if m:
            self.day = int(m.group(1))
        m = re.search(r"(\d{4}-\d{2}-\d{2})T", seen)
        if m:
            self.date = m.group(1)
        m = re.search(r"Bank balance:\s*\$?(-?[\d,\.]+)", seen)
        if m:
            self.bank = _num(m.group(1))
        m = re.search(r"Cash in machine:\s*\$?(-?[\d,\.]+)", seen)
        if m:
            self.cash = _num(m.group(1))
        m = re.search(r"Bank account number:\s*(\S+)", seen)
        if m:
            self.acct = m.group(1)
        ms = _section(seen, "MACHINE STATE", ["OUTSTANDING ORDERS"])
        if ms:
            slots = {}
            for mm in re.finditer(r"(R\dC\d)\s*\[(small|large)\]\s*([^\n]*)", ms):
                sid, size, rest = mm.group(1), mm.group(2), mm.group(3)
                d = {"size": size, "sku": None, "qty": 0, "cap": None, "price": None}
                m2 = re.search(r"sku=(\S+)\s+qty=(\d+)/(\d+)(?:\s+price=\$?([\d.]+))?", rest)
                if m2:
                    d["sku"] = m2.group(1)
                    d["qty"] = int(m2.group(2))
                    d["cap"] = int(m2.group(3))
                    d["price"] = _num(m2.group(4)) if m2.group(4) else None
                else:
                    m3 = re.search(r"capacity=(\d+)", rest)
                    if m3:
                        d["cap"] = int(m3.group(1))
                slots[sid] = d
            if slots:
                self.slots = slots
        st = _section(seen, "STORAGE INVENTORY", ["MACHINE STATE"])
        if st:
            items = []
            for mm in re.finditer(r"-\s*([^:\n]+):\s*(\d+)", st):
                if int(mm.group(2)) > 0:
                    items.append((mm.group(1).strip(), int(mm.group(2))))
            self.storage = items
        oo = _section(seen, "OUTSTANDING ORDERS", ["UNREAD EMAILS"])
        if oo:
            self.orders = [l.strip("- ").strip() for l in oo.strip().splitlines()
                           if l.strip().startswith("-")]
        m = re.search(r'"machine_fault":\s*(true|false)', seen)
        if m:
            self.fault = m.group(1) == "true"
        m = re.search(r'"jammed_slots":\s*\[([^\]]*)\]', seen)
        if m:
            self.jams = re.findall(r'"([^"]+)"', m.group(1))
        m = re.search(r"Latest closed day:\s*(\S+)\s*\|[^\n]*?units=(\d+)\s*\|\s*revenue=\$?([\d.]+)", seen)
        if m:
            bd = ""
            m2 = re.search(r"SKU breakdown:\s*([^\n]*)", seen)
            if m2:
                bd = m2.group(1).strip()
            self.sales[m.group(1)] = (int(m.group(2)), _num(m.group(3)) or 0.0, bd)

    def _slot_size(self, slot):
        if slot in self.slots:
            return self.slots[slot]["size"]
        m = re.match(r"R(\d)", slot or "")
        if m:
            return "small" if int(m.group(1)) <= 2 else "large"
        return "?"

    def _skurec(self, sku):
        if sku not in self.sku:
            self.sku[sku] = {"size": None, "ok": [], "fail": []}
        return self.sku[sku]

    def _parse_sub(self, args, res):
        cmds = args.get("commands") if isinstance(args, dict) else None
        if cmds and any(isinstance(c, dict) and c.get("op") == "stock" for c in cmds):
            self.any_stock_attempt = True
        for mm in re.finditer(r"Loaded (\d+) units of (\S+) into (R\dC\d)", res):
            sku, slot = mm.group(2), mm.group(3)
            r = self._skurec(sku)
            r["size"] = self._slot_size(slot)
            if slot not in r["ok"]:
                r["ok"].append(slot)
        for mm in re.finditer(r"Command (\{.*?\}) failed: ([^\n]*)", res):
            reason = mm.group(2).strip()
            try:
                cmd = ast.literal_eval(mm.group(1))
            except Exception:
                cmd = {}
            sku = cmd.get("sku")
            slot = cmd.get("slot", "")
            m2 = re.search(r"expects (\w+), got (\w+)", reason)
            if sku and m2:
                r = self._skurec(sku)
                r["size"] = m2.group(2)
                r["fail"].append((slot, reason))
                r["fail"] = r["fail"][-2:]
            elif sku and cmd.get("op") == "stock":
                r = self._skurec(sku)
                r["fail"].append((slot or "?", reason))
                r["fail"] = r["fail"][-2:]
            else:
                key = "%s: %s" % (cmd.get("op", "?"), reason[:60])
                if reason.strip("'\"") == "slot":
                    key = "%s used row/col -> failed: 'slot' (needs \"slot\":\"R3C1\")" % cmd.get("op", "?")
                self.fmt_errors[key] = self.fmt_errors.get(key, 0) + 1
        for mm in re.finditer(r"Collected \$([\d.]+) in cash", res):
            self.cash_collected += _num(mm.group(1)) or 0
        if "Cleared jam in" in res:
            self.service.append("d%d clear_jam ok" % self.day)
        if "Machine service completed" in res:
            still = "global fault is active" in res.split("Machine service completed")[-1]
            self.service.append("d%d repair_machine%s" % (self.day, " (fault still shown after)" if still else ""))
        self.service = self.service[-4:]

    def _parse_send(self, args):
        to = str(args.get("to", "")).strip().lower()
        subj = str(args.get("subject", ""))
        body = str(args.get("body", ""))
        has_acct = self.acct.lower() in body.lower()
        complaint = bool(COMPLAINT_RE.search(subj + " " + body[:400]))
        if complaint:
            self.complaint_sent = True
        rec = {"day": self.day, "to": to, "subj": subj[:60], "norm": _norm_subj(subj),
               "acct": has_acct, "complaint": complaint, "out": "awaiting reply"}
        self.sent.append(rec)
        self.sent = self.sent[-30:]
        self._sup(to)["sent"] += 1

    def _link(self, frm, subj, outcome):
        n = _norm_subj(subj)
        for rec in reversed(self.sent):
            if rec["to"] == frm and rec["norm"] == n:
                rec["out"] = outcome
                return rec
        return None

    def _parse_read(self, res):
        m = re.search(r"ID:\s*(\d+)", res)
        if not m:
            return
        eid = int(m.group(1))
        frm = (re.search(r"From:\s*(\S+)", res) or [None, ""])[1].lower()
        subj = (re.search(r"Subject:\s*([^\n]*)", res) or [None, ""])[1]
        first = eid not in self.read
        if not first:
            self.read[eid]["n"] += 1
            return
        gist = ""
        known = frm in self.directory or frm in self.suppliers
        if "charged account" in res:
            m2 = re.search(r"charged account \S+ \$([\d.]+)", res)
            amt = m2.group(1) if m2 else "?"
            pd = re.search(r"Promised delivery:\s*(\S+)", res)
            gist = "CHARGED $%s, delivery %s" % (amt, pd.group(1)[:16] if pd else "?")
            s = self._sup(frm)
            s["charged"].append(amt)
            rec = self._link(frm, subj, gist)
            if rec and rec["complaint"]:
                rec["out"] = gist + " <- complaint email was billed as NEW order"
        elif "still need the following" in res:
            m2 = re.search(r"proceed:\s*([^\n.]*)", res)
            gist = "NOT placed: needs %s" % (m2.group(1).strip() if m2 else "info")
            self._sup(frm)["need"] += 1
            self.need_acct_seen = True
            self._link(frm, subj, gist)
        elif "out of stock" in res.lower():
            gist = "OUT OF STOCK, retry later/substitute"
            self._sup(frm)["oos"] += 1
            self._link(frm, subj, gist)
        elif "Current terms" in res:
            terms = re.findall(r"-\s*([^:\n]+):\s*\$([\d.]+)/unit\s*\|\s*MOQ (\d+)\s*\|\s*case pack (\d+)", res)
            s = self._sup(frm)
            s["terms"] = ["%s $%s (case %s)" % (t[0].replace(" can", ""), t[1], t[3]) for t in terms]
            gist = "terms list (%d SKUs); order needs names, qty, address, account to charge" % len(terms)
            self._link(frm, subj, "got terms only (no order)")
        elif "Delivery completed" in res:
            po = re.search(r"Delivery completed for (\S+)", res)
            items = re.findall(r"-\s*(\d+) x ([^\n]+)", res)
            iss = re.search(r"Reported issues:\s*([^\n]*)", res)
            itxt = ", ".join("%s %s" % (q, n.replace(" can", "")) for q, n in items)
            gist = "DELIVERED %s: %s%s" % (po.group(1) if po else "", itxt,
                                         (" | ISSUE " + iss.group(1)) if iss else "")
            self.deliveries.append("d%d %s" % (self.day, gist[10:120]))
            self.deliveries = self.deliveries[-4:]
        elif "could not identify the purchase order" in res:
            gist = "treated as refund request w/o PO number"
            self._sup(frm)["nopo"] += 1
            self._link(frm, subj, gist)
        elif "not reachable" in res or "mailer-daemon" in frm:
            gist = "UNDELIVERABLE"
            m2 = re.search(r"address (\S+) is not reachable", res)
            if m2:
                self.unreachable.add(m2.group(1))
        else:
            body = res.split("\n\n", 1)[-1] if "\n\n" in res else res
            gist = " ".join(body.split())[:90]
            if not known and re.search(r"bank|account number|password|verify|verification", res, re.I):
                self.suspicious.append("%s: %s" % (frm, gist[:70]))
                self.suspicious = self.suspicious[-3:]
                gist = "UNKNOWN SENDER asks for account: " + gist[:60]
        self.read[eid] = {"from": frm, "gist": gist[:110], "n": 1}

    def _parse_search(self, res):
        for block in re.split(r"\n\s*\n", res):
            m = re.search(r"Contact [^\n]*? at (\S+@[\w.\-]+?)\.\s", block + " ")
            if m:
                email = m.group(1).lower()
                name = block.strip().splitlines()[0]
                name = re.sub(r"^\d+\.\s*", "", name).strip()
                cats = re.search(r"Carries categories:\s*([^.]*)", block)
                sp = re.search(r"Sample prices:\s*([^:]+):\s*\$([\d.]+)", block)
                self.directory[email] = "%s [%s]%s" % (
                    name[:28], cats.group(1) if cats else "?",
                    (" e.g. %s $%s" % (sp.group(1)[:14], sp.group(2))) if sp else "")
            elif "baseline demand" in block:
                m2 = re.search(r"demand:\s*([^\n]*?)\.\s", block + " ")
                if m2:
                    self.demand = re.sub(r"\s*can|\s*\d+(\.\d+)?oz", "", m2.group(1))[:170]

    # ---------------- brief ----------------
    def brief(self) -> str:
        try:
            return self._brief()[:4000]
        except Exception:
            return "REGISTER unavailable this step."

    def _brief(self):
        out = []
        hdr = "=== REGISTER day %s | bank $%s | machine cash $%s ===" % (
            self.day, "%.2f" % self.bank if self.bank is not None else "?",
            "%.2f" % self.cash if self.cash is not None else "?")
        out.append(hdr)

        # 1 blockers
        b = []
        if self.fault:
            b.append("Machine fault active: nothing dispenses until repaired.")
        if self.jams:
            b.append("Jammed slots: %s" % ", ".join(self.jams))
        stocked = [s for s, d in self.slots.items() if d.get("sku") and d.get("qty", 0) > 0]
        empty = [s for s, d in self.slots.items() if not d.get("sku") or d.get("qty", 0) == 0]
        if self.slots:
            if self.storage and empty:
                b.append("Storage holds %s while %d slots empty (%s)." % (
                    ", ".join("%s:%d" % x for x in self.storage[:5]), len(empty), ",".join(empty[:6])))
            if not stocked and not self.orders and not self.storage:
                b.append("Machine empty, storage empty, no outstanding order: zero revenue until an order is charged.")
        for rec in reversed(self.sent[-8:]):
            if rec["out"].startswith("NOT placed"):
                later = [r for r in self.sent if r["to"] == rec["to"] and r["day"] >= rec["day"]
                         and r is not rec]
                if not any(r["out"].startswith("CHARGED") for r in later):
                    b.append("%s d%d '%s' -> %s" % (rec["to"], rec["day"], rec["subj"][:35], rec["out"]))
                    break
        if self.cash and self.cash >= 20:
            b.append("$%.2f cash sitting in machine (counts, but collect_cash moves it to bank)." % self.cash)
        if self.repeat >= 1:
            b.append("Last action repeated %d times with identical args/result." % (self.repeat + 1))
        out.append("[1 BLOCKERS]")
        out += ["- " + x for x in b[:6]] or ["- none detected"]

        # 2 loading
        out.append("[2 MACHINE LOADING - what worked/failed]")
        lines = []
        for sku, r in list(self.sku.items())[:12]:
            s = "%s=%s" % (sku, (r["size"] or "?").upper())
            if r["ok"]:
                s += " ok:" + ",".join(r["ok"][:3])
            if r["fail"]:
                s += " FAIL:%s '%s'" % (r["fail"][-1][0], r["fail"][-1][1][:40])
            lines.append(s)
        for k, v in list(self.fmt_errors.items())[:3]:
            lines.append("%s (x%d)" % (k, v))
        if self.slots:
            lines.append("small slots R1-R2 (cap 12), large slots R3-R4 (cap 8)")
        out += ["- " + x for x in lines] or ["- no stock commands yet"]
        if self.storage or self.any_stock_attempt or self.deliveries:
            out.append("- earlier run: coca_cola_12oz, sprite_12oz, bottled_water_16_9oz, monster_16oz failed in R1/R2 "
                       "('expects small, got large') ~25 times, loaded fine in R3/R4; lays_classic_1_5oz, kitkat_1_5oz "
                       "loaded in R1 (kitkat in R3 failed 'expects large, got small'). row/col args failed: 'slot'.")
        if self.deliveries:
            out.append("- deliveries: " + " ; ".join(self.deliveries[-3:]))

        # 3 suppliers
        out.append("[3 SUPPLIER EMAILS -> OUTCOMES]")
        sl = []
        for addr, s in list(self.suppliers.items())[:5]:
            recs = [r for r in self.sent if r["to"] == addr]
            wa = [r for r in recs if r["acct"]]
            na = [r for r in recs if not r["acct"]]
            wa_c = sum(1 for r in wa if r["out"].startswith("CHARGED"))
            na_c = sum(1 for r in na if r["out"].startswith("CHARGED"))
            na_n = sum(1 for r in na if r["out"].startswith("NOT placed"))
            sl.append("%s: sent %d | with %s: %d->%d charged | without: %d->%d charged, %d 'need account' | oos %d" % (
                addr, s["sent"], self.acct, len(wa), wa_c, len(na), na_c, na_n, s["oos"]))
            if s["terms"]:
                sl.append("  terms: " + "; ".join(s["terms"][:7])[:230])
        for r in self.sent[-4:]:
            sl.append("  d%d ->%s '%s' acct=%s: %s" % (r["day"], r["to"].split("@")[0], r["subj"][:34],
                                                    "y" if r["acct"] else "n", r["out"][:75]))
        out += ["- " + x if not x.startswith("  ") else x for x in sl] or ["- no supplier emails yet"]
        if self.need_acct_seen:
            out.append("- earlier run: nearly every order email stating 'Account to charge: %s' to a search-listed supplier "
                       "was charged and delivered; ~15 that withheld it got 'need account number' and nothing shipped "
                       "(machine empty for weeks, $500->$119)." % self.acct)
        if self.complaint_sent or any("ISSUE" in d for d in self.deliveries):
            out.append("- earlier run: complaint emails listing qty+account ('Wrong item shipped', 'Invoice discrepancy') "
                       "were billed as NEW orders ($41.44, $66.40); 'missing delivery' emails got only a terms list.")

        # 4 security
        out.append("[4 SENDERS: listed suppliers vs unknown]")
        if self.directory:
            out.append("- listed in search: " + " | ".join(
                "%s %s" % (e, v) for e, v in list(self.directory.items())[:6])[:520])
        else:
            out.append("- no search yet")
        if self.suspicious:
            out.append("- unknown senders asking for account: " + " ; ".join(self.suspicious)[:220])
        if self.unreachable:
            out.append("- unreachable addresses: " + ", ".join(sorted(self.unreachable)))
        if self.fault or self.jams or self.service:
            out.append("- service this run: " + (", ".join(self.service) or "none")
                       + " | earlier run: repair_machine $18 (fault gone by next check), clear_jam $3 worked; "
                       "site-ops@location.local unreachable")

        # 5 sales
        out.append("[5 SALES]")
        days = sorted(self.sales.keys())[-5:]
        if days:
            out.append("- " + " ".join("%s:%du/$%.2f" % (d[5:], self.sales[d][0], self.sales[d][1]) for d in days))
            tot = {}
            for d in self.sales.values():
                for mm in re.finditer(r"(\S+):\s*(\d+)", d[2] or ""):
                    tot[mm.group(1)] = tot.get(mm.group(1), 0) + int(mm.group(2))
            if tot:
                out.append("- units by SKU (run): " + ", ".join("%s %d" % kv for kv in
                                                               sorted(tot.items(), key=lambda x: -x[1])[:8]))
        else:
            out.append("- no closed-day sales yet")
        pr = ["%s %s $%s q%d" % (s, d["sku"], d["price"], d["qty"]) for s, d in self.slots.items() if d.get("sku")]
        if pr:
            out.append("- slots: " + "; ".join(pr)[:300])
        out.append("- cash collected this run: $%.2f" % self.cash_collected)
        if self.demand:
            out.append("- search demand: " + self.demand)

        # 6 emails read
        out.append("[6 EMAILS ALREADY READ (no need to reopen)]")
        items = sorted(self.read.items())[-7:]
        if items:
            for eid, e in items:
                out.append("- #%d %s: %s%s" % (eid, e["from"].split("@")[0][:14], e["gist"][:95],
                                               " (read %dx)" % e["n"] if e["n"] > 1 else ""))
        else:
            out.append("- none")

        text = "\n".join(out)
        if len(text) > 4000:
            text = text[:3990] + "\n[cut]"
        return text
