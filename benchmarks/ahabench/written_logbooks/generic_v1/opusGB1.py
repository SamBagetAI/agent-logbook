import re
import json

HEADERS = ["CURRENT TIME", "SCENARIO", "PRIORITY ALERTS", "MONEY", "KEY BUSINESS DETAILS",
           "MARKET SNAPSHOT", "PEER DIRECTORY", "OPEN TRADE OFFERS", "ARENA BULLETINS",
           "STORAGE INVENTORY", "MACHINE STATE", "OUTSTANDING ORDERS", "UNREAD EMAILS",
           "LATEST DAILY SUMMARY", "RECENT TRANSACTIONS", "RECENT DECISION HISTORY",
           "PLANNER NOTE", "LAST TOOL RESULT"]

SEED_SIZES = {"coca_cola_12oz": "large", "sprite_12oz": "large", "bottled_water_16_9oz": "large",
              "monster_16oz": "large", "kitkat_1_5oz": "small", "lays_classic_1_5oz": "small"}
DRINKS = ("cola", "coke", "sprite", "water", "bull", "monster", "energy", "soda", "pepsi",
          "juice", "tea", "gatorade", "drink")


def _f(x):
    try:
        return float(str(x).replace(",", "").replace("$", ""))
    except Exception:
        return None


def _sections(text):
    out, cur = {}, None
    for ln in text.split("\n"):
        s = ln.strip()
        if s in HEADERS:
            cur = s
            out[cur] = []
        elif s.startswith("REMINDER:"):
            cur = None
        elif cur is not None:
            out[cur].append(ln)
    return dict((k, "\n".join(v)) for k, v in out.items())


def guess_size(sku):
    s = str(sku).lower()
    return "large" if any(w in s for w in DRINKS) else "small"


class Logbook:
    def __init__(self) -> None:
        self.day = 0
        self.bank = None
        self.start_bank = None
        self.cash = None
        self.account = "BIZ-0001"
        self.slots = {}
        self.storage = {}
        self.suppliers = {}      # email -> {"name", "prices"}
        self.catalog = {}        # email -> {item: (price, moq)}
        self.demand = ""
        self.sizes = dict((k, (v, "past run")) for k, v in SEED_SIZES.items())
        self.events = []
        self.sales = {}          # date -> (units, revenue, breakdown)
        self.actions = []
        self.specs_calls = 0
        self.need_account = {}   # email -> day
        self.po_confusion = {}   # email -> day
        self.oos = {}
        self.phish = {}
        self.rowcol_err = None
        self.size_err = None
        self.fault = False
        self.jams = []
        self.event_note = ""
        self.outstanding = {}
        self.deliveries = []
        self.empty_days = 0
        self.last_day_units = None
        self.read_count = {}
        self.leak = None
        self.storage_empty_after_delivery = None
        self.last_collect_day = None

    # ------------------------------------------------------------------
    def observe(self, step: dict) -> None:
        try:
            if not isinstance(step, dict):
                return
            seen = str(step.get("seen") or "")
            try:
                self._parse_seen(_sections(seen), seen)
            except Exception:
                pass
            action = step.get("action")
            if isinstance(action, dict):
                try:
                    self._parse_action(action, str(step.get("result") or ""))
                except Exception:
                    pass
        except Exception:
            pass

    def _ev(self, s):
        self.events.append("d%d %s" % (self.day, s))
        self.events = self.events[-25:]

    def _machine_units(self):
        return sum(v.get("qty", 0) for v in self.slots.values())

    def _known(self, addr):
        addr = (addr or "").lower()
        dom = addr.split("@")[-1]
        for e in list(self.suppliers) + list(self.catalog):
            if e == addr or e.split("@")[-1] == dom:
                return True
        return False

    # ------------------------------------------------------------------
    def _parse_seen(self, secs, seen):
        m = re.search(r"\(day (\d+)\)", secs.get("CURRENT TIME", "") or seen)
        if m:
            d = int(m.group(1))
            if d != self.day:
                if self.day and self.slots and self._machine_units() == 0:
                    self.empty_days += 1
                self.day = d
        money = secs.get("MONEY", "")
        m = re.search(r"Bank balance:\s*\$(-?[\d,.]+)", money)
        if m:
            self.bank = _f(m.group(1))
            if self.start_bank is None:
                self.start_bank = self.bank
        m = re.search(r"Cash in machine:\s*\$(-?[\d,.]+)", money)
        if m:
            self.cash = _f(m.group(1))
        m = re.search(r"Bank account number:\s*(\S+)", secs.get("KEY BUSINESS DETAILS", ""))
        if m:
            self.account = m.group(1)

        ms = secs.get("MACHINE STATE")
        if ms:
            slots = {}
            for ln in ms.split("\n"):
                mm = re.search(r"(R\d+C\d+)\s*\[(\w+)\]\s*(.*)", ln)
                if not mm:
                    continue
                sid, size, rest = mm.groups()
                info = {"size": size, "sku": None, "qty": 0, "cap": None, "price": None, "cost": None}
                c = re.search(r"capacity=(\d+)", rest)
                if c:
                    info["cap"] = int(c.group(1))
                s = re.search(r"sku=(\S+)", rest)
                q = re.search(r"qty=(\d+)/(\d+)", rest)
                p = re.search(r"price=\$([\d.]+)", rest)
                u = re.search(r"unit_cost_avg=([\d.]+)", rest)
                if s:
                    info["sku"] = s.group(1)
                    self.sizes[info["sku"]] = (size, "this run")
                if q:
                    info["qty"], info["cap"] = int(q.group(1)), int(q.group(2))
                if p:
                    info["price"] = _f(p.group(1))
                if u:
                    info["cost"] = _f(u.group(1))
                slots[sid] = info
            if slots:
                self.slots = slots

        st = secs.get("STORAGE INVENTORY")
        if st is not None:
            self.storage = self._parse_storage(st)

        mm = re.search(r"\{.*\}", secs.get("MARKET SNAPSHOT", ""), re.S)
        if mm:
            try:
                js = json.loads(mm.group(0))
                self.fault = bool(js.get("machine_fault"))
                self.jams = list(js.get("jammed_slots") or [])
                notes = js.get("notes") or []
                self.event_note = ("%s %s" % (js.get("event_name") or "",
                                              "; ".join(str(n) for n in notes))).strip()
            except Exception:
                pass

        oo = secs.get("OUTSTANDING ORDERS")
        if oo is not None:
            cur = {}
            for ln in oo.split("\n"):
                mp = re.search(r"(po-[\w-]+)", ln)
                if not mp:
                    continue
                sup = re.search(r"from (.+?)\s*\|", ln)
                dl = re.search(r"delivery=(\S+)", ln)
                tot = re.search(r"total=\$([\d.,]+)", ln)
                cur[mp.group(1)] = "%s $%s due %s" % (sup.group(1) if sup else "?",
                                                      tot.group(1) if tot else "?",
                                                      dl.group(1)[:16] if dl else "?")
            for po in self.outstanding:
                if po not in cur:
                    self._ev("%s left outstanding list (delivered?)" % po)
            self.outstanding = cur

        for ln in secs.get("UNREAD EMAILS", "").split("\n"):
            mm = re.search(r"ID\s*:?\s*(\d+).*?From\s*:?\s*(\S+).*?Subject\s*:?\s*(.*)", ln)
            if mm:
                eid, frm, subj = mm.group(1), mm.group(2).lower(), mm.group(3)
                low = subj.lower()
                if not self._known(frm) and ("verif" in low or "account" in low or "bank" in low):
                    self.phish[eid] = "%s: %s" % (frm, subj[:50])

        ds = secs.get("LATEST DAILY SUMMARY", "")
        mm = re.search(r"Latest closed day:\s*(\S+)\s*\|.*?units=(\d+)\s*\|\s*revenue=\$([\d.,]+)", ds)
        if mm:
            bd = {}
            mb = re.search(r"SKU breakdown:\s*(.*)", ds)
            if mb:
                for k, v in re.findall(r"([a-z0-9_]+):\s*(\d+)", mb.group(1)):
                    bd[k] = int(v)
            self.sales[mm.group(1)] = (int(mm.group(2)), _f(mm.group(3)) or 0.0, bd)

    def _parse_storage(self, txt):
        out = {}
        for ln in txt.split("\n"):
            mm = re.match(r"\s*-\s*(.+?):\s*(\d+)\s*$", ln)
            if mm:
                out[mm.group(1).strip()] = int(mm.group(2))
        return out

    # ------------------------------------------------------------------
    def _parse_action(self, action, result):
        tool = action.get("tool") or action.get("tool_name") or ""
        args = action.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        try:
            key = json.dumps(args, sort_keys=True)[:200]
        except Exception:
            key = str(args)[:200]
        self.actions.append((self.day, tool, key))
        self.actions = self.actions[-40:]

        if tool == "ai_web_search":
            self._parse_search(result)
        elif tool == "read_email":
            eid = str(args.get("id", args.get("email_id", "")))
            self.read_count[(eid, self.day)] = self.read_count.get((eid, self.day), 0) + 1
            self._parse_email(result)
        elif tool == "send_email":
            to = str(args.get("to", "")).lower()
            body = str(args.get("body", ""))
            has = self.account in body
            if has and not self._known(to):
                self.leak = "d%d account number sent to %s (not a listed supplier)" % (self.day, to)
            if has:
                self.need_account.pop(to, None)
            self.po_confusion.pop(to, None)
            self._ev("emailed %s '%s'%s" % (to, str(args.get("subject", ""))[:40],
                                           " [acct incl]" if has else " [NO acct]"))
        elif tool == "run_sub_agent":
            self._parse_run(args, result)
        elif tool == "get_storage_inventory":
            self.storage = self._parse_storage(result)
            if not self.storage and self.deliveries and self.deliveries[-1][0] >= self.day - 3:
                self.storage_empty_after_delivery = self.day
        elif tool == "get_operations_status":
            low = result.lower()
            if "global fault" in low:
                self.fault = True
            js = re.search(r"Jammed slots:\s*([R\dC, ]+)", result)
            if js:
                self.jams = [s.strip() for s in js.group(1).split(",") if s.strip()]
            if "no active" in low:
                self.fault, self.jams = False, []
        elif tool == "sub_agent_specs":
            self.specs_calls += 1

    def _parse_search(self, txt):
        for b in re.split(r"\n(?=\d+\.\s)", "\n" + txt):
            b = b.strip()
            m = re.match(r"\d+\.\s*(.+)", b)
            if not m:
                continue
            if "baseline demand" in b:
                md = re.search(r"demand:\s*(.+?)\.\s", b + " ")
                if md:
                    self.demand = md.group(1)[:220]
                continue
            em = re.search(r"([\w.+-]+@[\w.-]+\.\w+)", b)
            if not em:
                continue
            email = em.group(1).lower().rstrip(".")
            prices = []
            sp = re.search(r"Sample prices:\s*(.+?)\.\s+[A-Z]", b + " X")
            if sp:
                prices = re.findall(r"([^:,]+?):\s*\$([\d.]+)", sp.group(1))
            cats = re.search(r"categories:\s*([^.]+)", b)
            lead = re.search(r"Lead time ([\d-]+ days)", b)
            near_retail = "near-retail" in b or "inflated" in b
            self.suppliers[email] = {"name": m.group(1).split("\n")[0].strip()[:30],
                                     "cats": cats.group(1).strip() if cats else "",
                                     "lead": lead.group(1) if lead else "",
                                     "prices": [(n.strip(), _f(p)) for n, p in prices][:4],
                                     "bad": near_retail}

    def _parse_email(self, txt):
        fm = re.search(r"From:\s*(\S+)", txt)
        frm = fm.group(1).lower() if fm else "?"
        low = txt.lower()
        if "account number" in low and "we can prepare" in low:
            self.need_account[frm] = self.day
        if "could not identify the purchase order" in low:
            self.po_confusion[frm] = self.day
        if "out of stock" in low:
            self.oos[frm] = self.day
            self._ev("%s out of stock" % frm)
        if "current terms" in low:
            cat = {}
            for n, p, moq in re.findall(r"-\s*(.+?):\s*\$([\d.]+)/unit\s*\|\s*MOQ\s*(\d+)", txt):
                cat[n.strip()] = (_f(p), int(moq))
            if cat:
                self.catalog[frm] = cat
        mc = re.search(r"charged account \S+ \$([\d.,]+).*?Goods subtotal: \$([\d.,]+).*?Promised delivery:\s*(\S+)", txt, re.S)
        if mc:
            self._ev("%s CHARGED $%s (goods $%s), due %s" % (frm.split("@")[0], mc.group(1),
                                                              mc.group(2), mc.group(3)[:16]))
        if "delivery completed" in low:
            items = re.findall(r"-\s*(\d+)\s*x\s*(.+)", txt)
            iss = re.search(r"Reported issues:\s*(.+)", txt)
            po = re.search(r"(po-[\w-]+)", txt)
            s = ", ".join("%s %s" % (q, n.strip()[:22]) for q, n in items)
            self.deliveries.append((self.day, po.group(1) if po else "?", s,
                                    iss.group(1).strip() if iss else ""))
            self.deliveries = self.deliveries[-8:]
        if not self._known(frm) and ("bank account" in low or "verification" in low):
            self.phish[frm] = "%s asks for account info" % frm

    def _parse_run(self, args, result):
        for q, sku, slot in re.findall(r"Loaded (\d+) units of (\S+) into (R\d+C\d+)", result):
            sz = "small" if int(slot[1]) <= 2 else "large"
            self.sizes[sku] = (sz, "this run")
            self._ev("loaded %s %s into %s" % (q, sku, slot))
        for sku_line in re.findall(r"'sku': '([^']+)'[^\n]*failed: Slot \S+ expects (\w+), got (\w+)", result):
            sku, exp, got = sku_line
            self.sizes[sku] = (got, "this run (error)")
            self.size_err = self.day
        if "failed: 'slot'" in result:
            self.rowcol_err = self.day
        mc = re.search(r"Collected \$([\d.]+)", result)
        if mc:
            self.last_collect_day = self.day

    # ------------------------------------------------------------------
    def brief(self) -> str:
        try:
            return self._brief()[:4000]
        except Exception:
            return "Logbook: (error building brief)"

    def _brief(self):
        L = []
        inv_val = sum((v.get("qty") or 0) * (v.get("cost") or 0) for v in self.slots.values())
        nw = (self.bank or 0) + (self.cash or 0) + inv_val
        L.append("== LOGBOOK day %d/365 == bank $%.2f | machine cash $%.2f | machine stock %d units (~$%.2f cost) | est. net worth ~$%.2f (start $%.2f)"
                 % (self.day, self.bank or 0, self.cash or 0, self._machine_units(), inv_val, nw, self.start_bank or 500))

        A = []
        if self.fault:
            A.append("Machine FAULT: run_sub_agent [{\"op\":\"repair_machine\"}] (costs ~$18) then check get_operations_status.")
        if self.jams:
            A.append("Jammed: %s -> run_sub_agent {\"op\":\"clear_jam\",\"slot\":\"%s\"}." % (",".join(self.jams), self.jams[0]))
        empty = [s for s, v in sorted(self.slots.items()) if not v.get("qty")]
        if self.storage and empty:
            A.append("Storage has goods (%s) and %d empty slots -> STOCK NOW with run_sub_agent (drinks->R3/R4, snacks->R1/R2)."
                     % (", ".join("%s:%d" % kv for kv in list(self.storage.items())[:6]), len(empty)))
        if (self.cash or 0) >= 5:
            A.append("$%.2f cash sits in machine -> include {\"op\":\"collect_cash\"}." % self.cash)
        if self.slots and self._machine_units() == 0:
            A.append("Machine is EMPTY (empty on %d days so far) - every day empty = $0 revenue and -$2 fee." % self.empty_days)
        if not self.outstanding and self._machine_units() < 30 and (self.bank or 0) > 60:
            A.append("No order in transit and stock low -> place an order now (lead 2-5 days).")
        for e, d in self.need_account.items():
            A.append("%s (search-listed supplier) asked on d%d for the account number. Rule (past run: weeks lost refusing): reply re-stating the order AND 'Account to charge: %s'. That billing account is what suppliers charge; it is safe for search-listed suppliers only." % (e, d, self.account))
        for e, d in self.po_confusion.items():
            A.append("%s replied d%d 'could not identify the purchase order': your email read like a refund request. Rule (past run): place orders with items+qty, address, 'Account to charge: %s' and do NOT mention refunds/returns." % (e, d, self.account))
        if self.phish:
            A.append("SUSPECTED PHISHING (unknown sender asking for account info): %s. Do not reply." % "; ".join(list(self.phish.values())[:3]))
        if self.leak:
            A.append("WARNING: " + self.leak)
        if self.rowcol_err == self.day or self.size_err == self.day:
            A.append("Last stocking failed on format/size. Use the exact format below; do not call sub_agent_specs again.")
        if self.storage_empty_after_delivery and self.storage_empty_after_delivery >= self.day - 1:
            A.append("Storage shows empty right after a delivery. Rule (past run: listing was unreliable and loads into the CORRECT size row later succeeded): just try stocking delivered SKUs into matching rows.")
        if self.specs_calls >= 2:
            A.append("You have read sub_agent_specs %d times; it never changes. Act instead." % self.specs_calls)
        today = [a for a in self.actions if a[0] == self.day]
        rep = {}
        for a in today:
            rep[(a[1], a[2])] = rep.get((a[1], a[2]), 0) + 1
        loops = [k[0] for k, n in rep.items() if n >= 2 and k[0] != "wait_for_next_day"]
        if loops:
            A.append("Repeating identical %s calls today with no new info. Do something different or wait_for_next_day." % ", ".join(sorted(set(loops))))
        if self.event_note:
            A.append("Market: " + self.event_note[:120])
        if A:
            L.append("ACTION ITEMS:")
            L += ["- " + a for a in A[:12]]

        L.append("STOCK FORMAT: {\"op\":\"stock\",\"slot\":\"R3C1\",\"sku\":\"coca_cola_12oz\",\"qty\":8} then {\"op\":\"set_price\",\"slot\":\"R3C1\",\"price\":2.25}. Use 'slot', never row/col. R1-R2 small cap 12 = snacks/candy/chips; R3-R4 large cap 8 = cans/bottles/drinks. set_price fails on empty slots.")
        big = sorted(k for k, v in self.sizes.items() if v[0] == "large")
        sm = sorted(k for k, v in self.sizes.items() if v[0] == "small")
        L.append("Known sizes: LARGE(R3/R4): %s | SMALL(R1/R2): %s" % (", ".join(big)[:300], ", ".join(sm)[:200]))

        if self.slots:
            parts = []
            for s, v in sorted(self.slots.items()):
                if v.get("sku"):
                    parts.append("%s %s %d/%s $%s" % (s, v["sku"][:14], v["qty"], v.get("cap"), v.get("price")))
            L.append("Machine: " + ("; ".join(parts) if parts else "all empty"))
        if self.outstanding:
            L.append("In transit: " + "; ".join("%s %s" % kv for kv in self.outstanding.items())[:300])
        for d in self.deliveries[-3:]:
            L.append("Delivered d%d %s: %s%s" % (d[0], d[1], d[2][:120], (" ISSUE " + d[3][:60]) if d[3] else ""))

        if self.suppliers or self.catalog:
            L.append("SUPPLIERS:")
            for e, s in list(self.suppliers.items())[:6]:
                pr = ", ".join("%s $%.2f" % (n[:14], p or 0) for n, p in s["prices"][:3])
                L.append("- %s %s [%s] %s%s" % (s["name"], e, s["cats"][:30], pr, " (OVERPRICED)" if s["bad"] else ""))
            for e, cat in list(self.catalog.items())[:3]:
                L.append("  terms %s: %s" % (e.split("@")[0], ", ".join("%s $%.2f MOQ%d" % (n[:16], p or 0, q) for n, (p, q) in list(cat.items())[:9])[:330]))
        if self.demand:
            L.append("Demand: " + self.demand)

        days = sorted(self.sales)[-7:]
        if days:
            u = sum(self.sales[d][0] for d in days)
            r = sum(self.sales[d][1] for d in days)
            bysku = {}
            for d in days:
                for k, v in self.sales[d][2].items():
                    bysku[k] = bysku.get(k, 0) + v
            top = ", ".join("%s:%d" % kv for kv in sorted(bysku.items(), key=lambda x: -x[1])[:6])
            L.append("Sales last %d days: %d units, $%.2f (%.1f/day). %s" % (len(days), u, r, r / len(days), top))
        if self.events:
            L.append("RECENT EVENTS:")
            L += ["- " + e for e in self.events[-8:]]
        L.append("Remember: delivered goods can differ from the order and charges can exceed it; compare the charge to your order.")
        out = "\n".join(L)
        return out[:4000]
