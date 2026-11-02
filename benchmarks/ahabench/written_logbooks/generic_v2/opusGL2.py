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


def _cap(lines, limit):
    out, n = [], 0
    for l in lines:
        if n + len(l) + 1 > limit:
            out.append("  ...")
            break
        out.append(l)
        n += len(l) + 1
    return out


COMPLAINT_RE = re.compile(r"missing|wrong|discrepanc|refund|not received|undelivered|short|mis-?ship", re.I)

# sizes observed in earlier runs (evidence, not rules)
PRIOR_SIZE = {
    "coca_cola_12oz": "large", "sprite_12oz": "large", "bottled_water_16_9oz": "large",
    "monster_16oz": "large", "lays_classic_1_5oz": "small", "kitkat_1_5oz": "small",
    "doritos_nacho_1_75oz": "small", "snickers_1_86oz": "small",
}


class Logbook:
    def __init__(self) -> None:
        self.day = 0
        self.date = ""
        self.bank = None
        self.cash = None
        self.acct = "BIZ-0001"
        self.slots = {}
        self.storage = {}
        self.orders = []
        self.fault = False
        self.jams = []
        self.size = {}          # sku -> size (this run evidence)
        self.size_fail = {}     # sku -> last failure text
        self.slot_ok = 0
        self.rowcol_fail = 0
        self.suppliers = {}
        self.directory = {}
        self.free_ship = None
        self.demand = []        # (name, base)
        self.sent = []
        self.read = {}
        self.suspicious = []
        self.unreachable = set()
        self.sales = {}
        self.instock = {}       # date -> {sku: price}
        self.stats = {}         # sku -> {"units","days","byprice":{p:[u,d]}}
        self.cost = {}
        self.cash_collected = 0.0
        self.service = []
        self.deliveries = []
        self.issues = []
        self.ship_paid = 0.0
        self.goods_paid = 0.0
        self.n_charged = 0
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
            self.suppliers[addr] = {"need": 0, "oos": 0, "sent": 0}
        return self.suppliers[addr]

    def _observe(self, step):
        act = step.get("action") or None
        res = str(step.get("result") or "")
        seen = str(step.get("seen") or "")
        try:
            self._parse_seen(seen)
        except Exception:
            pass
        if act:
            tool = act.get("tool") or act.get("tool_name") or ""
            args = act.get("arguments") or {}
            try:
                sig = tool + repr(sorted(args.items()))
            except Exception:
                sig = tool
            if sig == self.last_sig and tool != "wait_for_next_day":
                self.repeat += 1
            else:
                self.repeat = 0
            self.last_sig = sig
            try:
                if tool == "run_sub_agent":
                    self._parse_sub(args, res)
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
        m = re.search(r"CURRENT TIME\s*(\d{4}-\d{2}-\d{2})", seen) or re.search(r"(\d{4}-\d{2}-\d{2})T", seen)
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
                m2 = re.search(r"sku=(\S+)\s+qty=(\d+)/(\d+)(?:\s+price=\$?([\d.]+))?(?:\s+unit_cost_avg=([\d.]+))?", rest)
                if m2:
                    d["sku"] = m2.group(1)
                    d["qty"] = int(m2.group(2))
                    d["cap"] = int(m2.group(3))
                    d["price"] = _num(m2.group(4)) if m2.group(4) else None
                    if m2.group(5):
                        self.cost[d["sku"]] = _num(m2.group(5))
                    self.size[d["sku"]] = size
                else:
                    m3 = re.search(r"capacity=(\d+)", rest)
                    if m3:
                        d["cap"] = int(m3.group(1))
                slots[sid] = d
            if slots:
                self.slots = slots
                if self.date:
                    snap = self.instock.setdefault(self.date, {})
                    for d in slots.values():
                        if d["sku"] and d["qty"] > 0:
                            snap[d["sku"]] = d["price"]
                    if len(self.instock) > 12:
                        for k in sorted(self.instock.keys())[:-12]:
                            del self.instock[k]
        st = _section(seen, "STORAGE INVENTORY", ["MACHINE STATE"])
        if st:
            items = {}
            for mm in re.finditer(r"-\s*([^:\n]+):\s*(\d+)", st):
                if int(mm.group(2)) > 0:
                    items[mm.group(1).strip()] = int(mm.group(2))
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
        if m and m.group(1) not in self.sales:
            d = m.group(1)
            bd = {}
            m2 = re.search(r"SKU breakdown:\s*([^\n]*)", seen)
            if m2:
                for mm in re.finditer(r"(\S+):\s*(\d+)", m2.group(1)):
                    bd[mm.group(1).strip(",")] = int(mm.group(2))
            self.sales[d] = (int(m.group(2)), _num(m.group(3)) or 0.0, bd)
            snap = dict(self.instock.get(d, {}))
            for k in bd:
                if k not in snap:
                    snap[k] = None
            for sku, price in snap.items():
                s = self.stats.setdefault(sku, {"units": 0, "days": 0, "byprice": {}})
                u = bd.get(sku, 0)
                s["units"] += u
                s["days"] += 1
                if price is not None:
                    bp = s["byprice"].setdefault(price, [0, 0])
                    bp[0] += u
                    bp[1] += 1

    def _slot_size(self, slot):
        if slot in self.slots:
            return self.slots[slot]["size"]
        m = re.match(r"R(\d)", slot or "")
        if m:
            return "small" if int(m.group(1)) <= 2 else "large"
        return None

    def _parse_sub(self, args, res):
        cmds = args.get("commands") if isinstance(args, dict) else None
        for mm in re.finditer(r"Loaded (\d+) units of (\S+) into (R\dC\d)", res):
            self.slot_ok += 1
            sz = self._slot_size(mm.group(3))
            if sz:
                self.size[mm.group(2)] = sz
        for mm in re.finditer(r"Command (\{.*?\}) failed: ([^\n]*)", res):
            reason = mm.group(2).strip()
            try:
                cmd = ast.literal_eval(mm.group(1))
            except Exception:
                cmd = {}
            if "row" in cmd or "col" in cmd or reason.strip("'\"") == "slot":
                if cmd.get("op") == "stock" or "row" in cmd:
                    self.rowcol_fail += 1
                continue
            m2 = re.search(r"expects (\w+), got (\w+)", reason)
            sku = cmd.get("sku")
            if sku and m2:
                self.size[sku] = m2.group(2)
                self.size_fail[sku] = "%s rejected (%s slot)" % (cmd.get("slot", "?"), m2.group(1))
        for mm in re.finditer(r"Collected \$([\d.]+) in cash", res):
            self.cash_collected += _num(mm.group(1)) or 0
        if "Cleared jam in" in res:
            self.service.append("d%d clear_jam ok" % self.day)
        if "Machine service completed" in res:
            self.service.append("d%d repair_machine" % self.day)
        self.service = self.service[-4:]

    def _parse_send(self, args):
        to = str(args.get("to", "")).strip().lower()
        subj = str(args.get("subject", ""))
        body = str(args.get("body", ""))
        has_acct = self.acct.lower() in body.lower()
        complaint = bool(COMPLAINT_RE.search(subj + " " + body[:400]))
        if complaint:
            self.complaint_sent = True
        self.sent.append({"day": self.day, "to": to, "subj": subj[:50], "norm": _norm_subj(subj),
                          "acct": has_acct, "complaint": complaint, "out": "awaiting reply"})
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
        mf = re.search(r"From:\s*(\S+)", res)
        frm = mf.group(1).lower() if mf else ""
        ms = re.search(r"Subject:\s*([^\n]*)", res)
        subj = ms.group(1) if ms else ""
        if eid in self.read:
            self.read[eid]["n"] += 1
            return
        gist = ""
        known = frm in self.directory or frm in self.suppliers
        if "charged account" in res:
            m2 = re.search(r"charged account \S+ \$([\d.]+)", res)
            amt = m2.group(1).rstrip(".") if m2 else "?"
            pd = re.search(r"Promised delivery:\s*(\S+)", res)
            gs = re.search(r"Goods subtotal:\s*\$([\d.]+)", res)
            sh = re.search(r"Shipping:\s*\$([\d.]+)", res)
            self.goods_paid += _num(gs.group(1).rstrip(".")) or 0 if gs else 0
            self.ship_paid += _num(sh.group(1).rstrip(".")) or 0 if sh else 0
            self.n_charged += 1
            ex = re.search(r"excluded as temporarily out of stock:\s*([^\n]*)", res)
            gist = "CHARGED $%s, delivery %s%s" % (amt, pd.group(1)[:10] if pd else "?",
                                                 (" | OOS excluded: " + ex.group(1)[:40]) if ex else "")
            rec = self._link(frm, subj, gist)
            if rec and rec["complaint"]:
                rec["out"] = gist + " (complaint billed as NEW order)"
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
            gist = "terms list only (no order placed)"
            self._link(frm, subj, gist)
        elif "Delivery completed" in res:
            po = re.search(r"Delivery completed for (\S+)", res)
            items = re.findall(r"-\s*(\d+) x ([^\n]+)", res)
            iss = re.search(r"Reported issues:\s*([^\n]*)", res)
            itxt = ", ".join("%s %s" % (q, n.replace(" can", "")[:16]) for q, n in items)
            pon = po.group(1) if po else "?"
            gist = "DELIVERED %s: %s" % (pon, itxt)
            self.deliveries.append("d%d %s" % (self.day, itxt[:70]))
            self.deliveries = self.deliveries[-3:]
            if iss:
                sup = re.sub(r"^po-|-\d+$", "", pon)
                self.issues.append("%s %s" % (sup, iss.group(1)[:50]))
                self.issues = self.issues[-4:]
                gist += " | ISSUE " + iss.group(1)[:40]
        elif "could not identify the purchase order" in res:
            gist = "treated as refund request w/o PO number"
            self._link(frm, subj, gist)
        elif "not reachable" in res or "mailer-daemon" in frm:
            gist = "UNDELIVERABLE"
            m2 = re.search(r"address (\S+) is not reachable", res)
            if m2:
                self.unreachable.add(m2.group(1))
        else:
            body = res.split("\n\n", 1)[-1] if "\n\n" in res else res
            gist = " ".join(body.split())[:70]
            if not known and re.search(r"bank|account number|password|verify|verification", res, re.I):
                self.suspicious.append("%s: %s" % (frm, gist[:60]))
                self.suspicious = self.suspicious[-3:]
                gist = "UNKNOWN SENDER asks for account: " + gist[:50]
        self.read[eid] = {"from": frm, "gist": gist[:90], "n": 1}

    def _parse_search(self, res):
        for block in re.split(r"\n\s*\n", res):
            m = re.search(r"Contact [^\n]*? at (\S+@[\w.\-]+?)\.\s", block + " ")
            if m:
                email = m.group(1).lower()
                name = re.sub(r"^\d+\.\s*", "", block.strip().splitlines()[0]).strip()
                cats = re.search(r"Carries categories:\s*([^.]*)", block)
                sp = re.search(r"Sample prices:\s*([^:]+):\s*\$([\d.]+)", block)
                self.directory[email] = "%s [%s]%s" % (
                    name[:24], cats.group(1) if cats else "?",
                    (" %s $%s" % (sp.group(1)[:12], sp.group(2))) if sp else "")
                fs = re.search(r"free shipping over \$([\d.]+)", block)
                if fs:
                    self.free_ship = _num(fs.group(1).rstrip("."))
            elif "baseline demand" in block:
                dem = []
                for n, b in re.findall(r"([A-Za-z][A-Za-z'&\- ]+?)\s*(?:[\d.]+oz)?\s*(?:can)?\s*\(base ([\d.]+)/day\)", block):
                    n = n.strip().strip(",:")
                    n = re.sub(r"^.*demand:\s*", "", n)
                    dem.append((n, b))
                if dem:
                    self.demand = dem[:8]

    # ---------------- helpers ----------------
    def _sz(self, sku):
        s = self.size.get(sku)
        if s:
            return s, ""
        s = PRIOR_SIZE.get(sku)
        if s:
            return s, "*"
        return None, ""

    def _rate(self, sku):
        s = self.stats.get(sku)
        if not s or s["days"] == 0:
            return None
        return s["units"] / float(s["days"])

    # ---------------- brief ----------------
    def brief(self) -> str:
        try:
            t = self._brief()
            return t[:3990]
        except Exception:
            return "LOGBOOK unavailable this step."

    def _brief(self):
        out = []
        out.append("=== LOGBOOK day %s | bank $%s | machine cash $%s ===" % (
            self.day, "%.2f" % self.bank if self.bank is not None else "?",
            "%.2f" % self.cash if self.cash is not None else "?"))

        mach = {}
        for d in self.slots.values():
            if d.get("sku"):
                mach[d["sku"]] = mach.get(d["sku"], 0) + d.get("qty", 0)
        empty = {"small": [], "large": []}
        for sid in sorted(self.slots):
            d = self.slots[sid]
            if not d.get("sku") or d.get("qty", 0) == 0:
                empty.setdefault(d["size"], []).append(sid)

        # 1 blockers
        b = []
        if self.fault:
            b.append("Machine fault active: nothing dispenses until repaired.")
        if self.jams:
            b.append("Jammed slots: %s" % ", ".join(self.jams))
        for size in ("small", "large"):
            es = empty.get(size) or []
            if not es:
                continue
            fit = [k for k in self.storage if self._sz(k)[0] == size]
            if fit:
                b.append("%d %s slots empty (%s); storage has %s-size: %s." % (
                    len(es), size, ",".join(es), size, ", ".join("%s:%d" % (k, self.storage[k]) for k in fit)))
            else:
                known = sorted(k for k in set(list(self.size) + list(PRIOR_SIZE)) if self._sz(k)[0] == size)
                b.append("%d %s slots empty (%s) earning $0; nothing %s-size in storage. %s-size SKUs seen: %s." % (
                    len(es), size, ",".join(es), size, size.capitalize(), ", ".join(known[:6]) or "?"))
        if not mach and not self.orders and not self.storage:
            b.append("Machine, storage empty and no outstanding order: zero revenue until an order is charged.")
        low = []
        for sku, q in mach.items():
            r = self._rate(sku)
            tot = q + self.storage.get(sku, 0)
            if r and r > 0 and tot / r < 2.5:
                low.append("%s %.1fd" % (sku, tot / r))
        for sku, s in self.stats.items():
            if sku not in mach and s["units"] > 0 and self.storage.get(sku, 0) == 0:
                low.append("%s SOLD OUT (sold %.1f/day)" % (sku, self._rate(sku) or 0))
        if low:
            b.append("Stock cover (machine+storage / sales rate): " + ", ".join(low[:6])
                     + (" | outstanding orders: %d" % len(self.orders)))
        for rec in reversed(self.sent[-8:]):
            if rec["out"].startswith("NOT placed"):
                later = [r for r in self.sent if r["to"] == rec["to"] and r["day"] >= rec["day"] and r is not rec]
                if not any(r["out"].startswith("CHARGED") for r in later):
                    b.append("%s d%d '%s' -> %s" % (rec["to"], rec["day"], rec["subj"][:30], rec["out"]))
                    break
        if self.repeat >= 1:
            b.append("Last action repeated %d times with identical args." % (self.repeat + 1))
        out.append("[1 BLOCKERS]")
        out += _cap(["- " + x for x in b] or ["- none detected"], 1000)

        # 2 loading
        out.append("[2 LOADING: format & slot sizes]")
        lines = ["- format: {\"op\":\"stock\",\"slot\":\"R3C1\",...} loaded OK %d times; row/col args failed %d times this run (error 'slot')."
                 % (self.slot_ok, self.rowcol_fail)]
        skus = sorted(set(list(self.size) + list(self.storage) + list(mach)))
        L = [k for k in skus if self._sz(k)[0] == "large"]
        S = [k for k in skus if self._sz(k)[0] == "small"]
        lines.append("- LARGE (R3-R4, cap 8): " + (", ".join(k + self._sz(k)[1] for k in L) or "-"))
        lines.append("- SMALL (R1-R2, cap 12): " + (", ".join(k + self._sz(k)[1] for k in S) or "-"))
        fails = ["%s %s" % (k, v) for k, v in self.size_fail.items()]
        if fails:
            lines.append("- size rejections: " + "; ".join(fails[:4]))
        lines.append("  (* = from earlier runs: cans/bottles were LARGE, chips/candy SMALL there)")
        out += _cap(lines, 700)

        # 3 sales & cover
        out.append("[3 SALES BY SKU (units/day while in machine)]")
        sl = []
        allsku = sorted(set(list(self.stats) + list(mach) + list(self.storage)),
                        key=lambda k: -(self._rate(k) or 0))
        for sku in allsku[:9]:
            r = self._rate(sku)
            s = self.stats.get(sku, {"byprice": {}, "days": 0})
            bp = ", ".join("@%.2f:%.1f/d(%dd)" % (p, v[0] / float(v[1]), v[1])
                           for p, v in sorted(s["byprice"].items()) if v[1])
            c = self.cost.get(sku)
            line = "- %s: mach %d, stor %d, %s%s" % (
                sku, mach.get(sku, 0), self.storage.get(sku, 0),
                ("%.1f/d over %dd" % (r, s["days"])) if r is not None else "no sales data",
                (" " + bp) if bp else "")
            if c:
                line += ", cost $%.2f" % c
            sl.append(line)
        days = sorted(self.sales.keys())[-5:]
        if days:
            sl.append("- last days: " + " ".join("%s:%du/$%.0f" % (d[5:], self.sales[d][0], self.sales[d][1]) for d in days))
        if self.demand:
            have = " ".join(set(list(self.stats) + list(mach) + list(self.storage) + list(self.size)))
            never = []
            for n, base in self.demand:
                tok = re.sub(r"[^a-z]", "", n.lower().split()[0]) if n.split() else ""
                if tok and tok not in have.replace("_", ""):
                    never.append("%s %s/d" % (n, base))
            if never:
                sl.append("- search top-demand SKUs never stocked this run: " + ", ".join(never))
        sl.append("- cash collected this run: $%.2f" % self.cash_collected)
        out += _cap(sl, 1100)

        # 4 suppliers
        out.append("[4 SUPPLIER ORDERS -> OUTCOMES]")
        su = []
        for addr, s in list(self.suppliers.items())[:5]:
            recs = [r for r in self.sent if r["to"] == addr]
            wa = [r for r in recs if r["acct"]]
            na = [r for r in recs if not r["acct"]]
            su.append("- %s: with %s %d->%d charged | without %d->%d charged, %d 'need account' | oos %d" % (
                addr.split("@")[-1], self.acct, len(wa), sum(r["out"].startswith("CHARGED") for r in wa),
                len(na), sum(r["out"].startswith("CHARGED") for r in na), s["need"], s["oos"]))
        if self.n_charged:
            su.append("- %d orders charged: goods $%.2f, shipping $%.2f (avg goods $%.0f/order; free shipping over $%s)" % (
                self.n_charged, self.goods_paid, self.ship_paid, self.goods_paid / self.n_charged,
                ("%.0f" % self.free_ship) if self.free_ship else "?"))
        if self.issues:
            su.append("- delivery issues: " + "; ".join(self.issues))
        for r in self.sent[-3:]:
            su.append("  d%d ->%s '%s' acct=%s: %s" % (r["day"], r["to"].split("@")[0], r["subj"][:30],
                                                     "y" if r["acct"] else "n", r["out"][:70]))
        if self.deliveries:
            su.append("- recent deliveries: " + " ; ".join(self.deliveries))
        if self.need_acct_seen:
            su.append("- earlier runs: orders stating 'Account to charge: %s' to search-listed suppliers were charged; "
                      "~15 withholding it got 'need account number' and nothing shipped." % self.acct)
        if self.complaint_sent or self.issues:
            su.append("- earlier run: complaint emails listing qty+account were billed as NEW orders.")
        out += _cap(su or ["- no supplier emails yet"], 1100)

        # 5 senders/service
        out.append("[5 SENDERS & SERVICE]")
        ss = []
        if self.directory:
            ss.append("- listed in search: " + " | ".join("%s %s" % (e, v) for e, v in list(self.directory.items())[:5]))
        else:
            ss.append("- no search yet")
        if self.suspicious:
            ss.append("- unknown senders asking for account: " + " ; ".join(self.suspicious))
        if self.unreachable:
            ss.append("- unreachable: " + ", ".join(sorted(self.unreachable)))
        if self.fault or self.jams or self.service:
            ss.append("- service this run: " + (", ".join(self.service) or "none")
                      + " | earlier runs: repair_machine ($18) and clear_jam ($3) cleared incidents")
        out += _cap(ss, 650)

        # 6 emails read
        out.append("[6 EMAILS ALREADY READ]")
        items = sorted(self.read.items())[-4:]
        el = ["- #%d %s: %s%s" % (eid, e["from"].split("@")[0][:10], e["gist"][:80],
                                  " (read %dx)" % e["n"] if e["n"] > 1 else "") for eid, e in items]
        out += _cap(el or ["- none"], 400)

        text = "\n".join(out)
        if len(text) > 3990:
            text = text[:3980] + "\n[cut]"
        return text
