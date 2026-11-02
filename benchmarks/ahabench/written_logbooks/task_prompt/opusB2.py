import re
import ast
import json

HEADERS = [
    "CURRENT TIME", "SCENARIO", "PRIORITY ALERTS", "MONEY", "KEY BUSINESS DETAILS",
    "MARKET SNAPSHOT", "PEER DIRECTORY", "OPEN TRADE OFFERS", "ARENA BULLETINS",
    "STORAGE INVENTORY", "MACHINE STATE", "OUTSTANDING ORDERS", "UNREAD EMAILS",
    "LATEST DAILY SUMMARY", "RECENT TRANSACTIONS", "RECENT DECISION HISTORY",
    "PLANNER NOTE", "LAST TOOL RESULT",
]

COMPLAINT_WORDS = ("missing", "wrong", "discrepancy", "refund", "return", "not received", "undelivered")


def _f(x):
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return None


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower().replace("'", "")).strip("_")


class Logbook:
    def __init__(self) -> None:
        self.day = 0
        self.bank = None
        self.start_bank = None
        self.cash = None
        self.acct = None
        self.slot_size = {}
        self.sku_size = {}
        self.skus = set()
        self.fmt_bad = 0
        self.stock_template = None
        self.fail_reasons = {}
        self.ops = []
        self.suppliers = {}
        self.demand = ""
        self.catalog = {}
        self.need = {}
        self.sent = {}
        self.last_sent = {}
        self.charges = []
        self.complaint_charges = 0
        self.deliveries = []
        self.emails = {}
        self.email_order = []
        self.read_counts = {}
        self.sales = {}
        self.sku_sold = {}
        self.hist = []
        self.storage = {}
        self.storage_seen = False
        self.machine = {}
        self.outstanding = []
        self.unread = []
        self.jams = []
        self.fault = False
        self.event = None
        self.chat_echo = 0
        self.last_run_result = None
        self.storage_ghost = 0
        self.suspicious = []
        self.repair_cost_note = ""
        self.last_stock_day = None

    # ------------------------------------------------------------------
    def observe(self, prompt, last_action):
        try:
            self._observe(prompt or "", last_action)
        except Exception:
            pass

    def _sections(self, prompt):
        secs = {}
        cur = None
        for line in prompt.split("\n"):
            s = line.strip()
            if s in HEADERS:
                cur = s
                secs[cur] = []
                continue
            if cur == "LAST TOOL RESULT" and s.startswith("REMINDER:"):
                cur = None
                continue
            if cur:
                secs[cur].append(line)
        return {k: "\n".join(v).strip() for k, v in secs.items()}

    def _observe(self, prompt, last_action):
        secs = self._sections(prompt)
        m = re.search(r"\(day (\d+)\)", prompt)
        if m:
            self.day = int(m.group(1))
        money = secs.get("MONEY", "")
        m = re.search(r"Bank balance: \$([-\d,.]+)", money)
        if m:
            self.bank = _f(m.group(1))
            if self.start_bank is None:
                self.start_bank = self.bank
        m = re.search(r"Cash in machine: \$([-\d,.]+)", money)
        if m:
            self.cash = _f(m.group(1))
        m = re.search(r"Bank account number: (\S+)", prompt)
        if m:
            self.acct = m.group(1)

        machine = {}
        for line in secs.get("MACHINE STATE", "").split("\n"):
            s = line.strip().lstrip("-").strip()
            mm = re.match(r"(R\d+C\d+) \[(\w+)\]\s*(.*)", s)
            if not mm:
                continue
            slot, size, rest = mm.group(1), mm.group(2), mm.group(3)
            self.slot_size[slot] = size
            info = {"size": size, "sku": None, "qty": 0, "cap": None, "price": None}
            c = re.search(r"capacity=(\d+)", rest)
            if c:
                info["cap"] = int(c.group(1))
            k = re.search(r"sku=(\S+) qty=(\d+)/(\d+)", rest)
            if k:
                info["sku"] = k.group(1)
                info["qty"] = int(k.group(2))
                info["cap"] = int(k.group(3))
                self.skus.add(k.group(1))
                self.sku_size[k.group(1)] = size
            p = re.search(r"price=\$([\d.]+)", rest)
            if p:
                info["price"] = _f(p.group(1))
            machine[slot] = info
        if machine:
            self.machine = machine

        res = secs.get("LAST TOOL RESULT", "")
        if last_action:
            try:
                self._handle_action(last_action, res)
            except Exception:
                pass

        st = secs.get("STORAGE INVENTORY", None)
        if st is not None:
            storage = {}
            for line in st.split("\n"):
                s = line.strip()
                mm = re.match(r"-?\s*(.+?):\s*(\d+)\s*$", s)
                if mm:
                    storage[mm.group(1).strip()] = int(mm.group(2))
            self.storage = storage
            self.storage_seen = True

        self.outstanding = [l.strip().lstrip("-").strip() for l in secs.get("OUTSTANDING ORDERS", "").split("\n")
                            if l.strip() and l.strip() != "None."]
        self.unread = [l.strip().lstrip("-").strip() for l in secs.get("UNREAD EMAILS", "").split("\n")
                       if l.strip() and l.strip() != "None."]
        mk = secs.get("MARKET SNAPSHOT", "")
        mm = re.search(r'"jammed_slots": \[(.*?)\]', mk, re.S)
        self.jams = re.findall(r'"(R\d+C\d+)"', mm.group(1)) if mm else []
        self.fault = bool(re.search(r'"machine_fault": true', mk))
        mm = re.search(r'"event_name": "([^"]+)"', mk)
        self.event = mm.group(1) if mm else None

        tx = secs.get("RECENT TRANSACTIONS", "")
        for amt, desc in re.findall(r"service_cost \| (-?[\d.]+) \| (.*)", tx):
            self.repair_cost_note = "%s costs $%s" % (desc.strip()[:40], amt.lstrip("-"))

        ds = secs.get("LATEST DAILY SUMMARY", "")
        mm = re.search(r"Latest closed day: (\S+) \|.*?units=(\d+) \| revenue=\$([\d.]+)", ds)
        if mm:
            date = mm.group(1)
            if date not in self.sales:
                self.sales[date] = (int(mm.group(2)), _f(mm.group(3)) or 0.0)
                b = re.search(r"SKU breakdown: (.*)", ds)
                if b:
                    for sku, n in re.findall(r"(\w+): (\d+)", b.group(1)):
                        self.sku_sold[sku] = self.sku_sold.get(sku, 0) + int(n)
                        self.skus.add(sku)

    # ------------------------------------------------------------------
    def _handle_action(self, act, res):
        tool = act.get("tool_name", "")
        args = act.get("arguments", {}) or {}
        try:
            key = tool + " " + json.dumps(args, sort_keys=True)[:200]
        except Exception:
            key = tool
        self.hist.append((self.day, tool, key))
        if len(self.hist) > 60:
            self.hist = self.hist[-60:]

        if tool == "run_sub_agent":
            cmds = args.get("commands", []) if isinstance(args, dict) else []
            for line in res.split("\n"):
                s = line.strip()
                mm = re.match(r"Command (\{.*\}) failed: (.*)$", s)
                if mm:
                    try:
                        cmd = ast.literal_eval(mm.group(1))
                    except Exception:
                        cmd = {}
                    reason = mm.group(2).strip()
                    e = re.search(r"expects (\w+), got (\w+)", reason)
                    if e and isinstance(cmd, dict) and cmd.get("sku"):
                        self.sku_size[cmd["sku"]] = e.group(2)
                        self.skus.add(cmd["sku"])
                    if reason.startswith("'"):
                        self.fmt_bad += 1
                        reason = "missing key %s (use \"slot\":\"R3C1\" not row/col)" % reason
                    k = re.sub(r"R\d+C\d+", "<slot>", reason)[:80]
                    self.fail_reasons[k] = self.fail_reasons.get(k, 0) + 1
                    continue
                mm = re.match(r"Loaded (\d+) units of (\S+) into (R\d+C\d+)", s)
                if mm:
                    sku, slot = mm.group(2), mm.group(3)
                    self.skus.add(sku)
                    self.last_stock_day = self.day
                    if slot in self.slot_size:
                        self.sku_size[sku] = self.slot_size[slot]
                    if self.storage_seen and not self.storage:
                        self.storage_ghost += 1
                    for c in cmds:
                        if isinstance(c, dict) and c.get("op") == "stock" and c.get("slot") == slot:
                            try:
                                self.stock_template = json.dumps(c)
                            except Exception:
                                pass
            self.last_run_result = res
        elif tool == "chat_with_sub_agent":
            if res and self.last_run_result and res.strip() == self.last_run_result.strip():
                self.chat_echo += 1
        elif tool == "sub_agent_specs":
            ops = re.findall(r'"op": "(\w+)"', res)
            if ops:
                self.ops = list(dict.fromkeys(ops))
        elif tool == "read_email":
            self._parse_email(res)
        elif tool == "ai_web_search":
            self._parse_search(res)
        elif tool == "send_email":
            to = str(args.get("to", "")).lower().strip()
            body = str(args.get("body", ""))
            subj = str(args.get("subject", ""))
            c = self.sent.setdefault(to, [0, 0])
            c[0] += 1
            has = bool(self.acct and self.acct in body)
            if has:
                c[1] += 1
            self.last_sent[to] = (self.day, subj[:50], has)

    def _supplier_domains(self):
        return set(e.split("@")[-1] for e in self.suppliers)

    def _parse_email(self, res):
        mid = re.search(r"ID: (\d+)", res)
        if not mid:
            return
        eid = mid.group(1)
        fm = re.search(r"From: (\S+)", res)
        frm = fm.group(1).lower() if fm else "?"
        subj_m = re.search(r"Subject: (.*)", res)
        subj = subj_m.group(1).strip() if subj_m else ""
        body = res.split(subj, 1)[-1] if subj else res
        self.read_counts[eid] = self.read_counts.get(eid, 0) + 1
        new = eid not in self.emails
        dom = frm.split("@")[-1]
        ch = re.search(r"charged account \S+ \$([\d.]+)", body)
        dl = re.search(r"Delivery completed for (\S+)", subj)
        if ch:
            g = re.search(r"Goods subtotal: \$([\d.]+)", body)
            pd = re.search(r"Promised delivery: (\S+)", body)
            trig = self.last_sent.get(frm)
            gist = "CHARGED $%s (goods $%s) delivery %s" % (ch.group(1), g.group(1) if g else "?",
                                                          pd.group(1)[:16] if pd else "?")
            if new:
                tsub = trig[1] if trig else "?"
                self.charges.append((self.day, dom, ch.group(1), pd.group(1)[:10] if pd else "?", tsub))
                if any(w in tsub.lower() for w in COMPLAINT_WORDS):
                    self.complaint_charges += 1
        elif dl:
            items = re.findall(r"- (\d+) x (.+)", body)
            iss = re.search(r"Reported issues: (.*)", body)
            it = ", ".join("%s %s" % (n, nm.strip()) for n, nm in items)
            gist = "DELIVERED %s: %s%s" % (dl.group(1), it, (" ISSUES: " + iss.group(1)) if iss else "")
            if new:
                self.deliveries.append((self.day, dl.group(1), it[:110], iss.group(1)[:60] if iss else ""))
        elif "/unit | MOQ" in body:
            cat = self.catalog.setdefault(dom, {})
            for nm, pr, moq, cp in re.findall(r"- (.+?): \$(\d+\.\d+)/unit \| MOQ (\d+) \| case pack (\d+)", body):
                cat[nm.strip()] = (pr, moq, cp)
            gist = "TERMS/price list (%d items) - order not placed by this reply" % len(cat)
        else:
            nd = re.search(r"still need the following before we can proceed: (.*)", body)
            low = body.lower()
            if nd:
                if new:
                    self.need[dom] = self.need.get(dom, 0) + 1
                gist = "ORDER NOT PLACED - needs: " + nd.group(1).strip()[:60]
            elif "out of stock" in low:
                gist = "OUT OF STOCK - order not placed; retry later or substitute"
            elif "could not identify the purchase order" in low:
                gist = "Read as refund/return request (wants PO#); no order placed"
            else:
                gist = " ".join(body.split())[:140]
                if dom not in self._supplier_domains() and any(w in low for w in ("account", "password", "bank", "verify")):
                    gist = "SUSPICIOUS unknown sender wants account info - do not reply. " + gist[:70]
                    if new and frm not in self.suspicious:
                        self.suspicious.append(frm)
        self.emails[eid] = "#%s %s d%d: %s" % (eid, dom[:24], self.day, gist[:170])
        if eid in self.email_order:
            self.email_order.remove(eid)
        self.email_order.append(eid)
        if len(self.email_order) > 30:
            old = self.email_order.pop(0)
            self.emails.pop(old, None)

    def _parse_search(self, res):
        blocks = re.split(r"\n(?=\d+\. )", "\n" + res)
        for b in blocks:
            b = b.strip()
            if not b:
                continue
            if "baseline demand" in b:
                m = re.search(r"baseline demand: (.*?)\.\s", b + " ")
                self.demand = (m.group(1) if m else b)[:220]
                continue
            em = re.search(r"at (\S+@\S+?)\.?\s", b + " ")
            if not em:
                continue
            email = em.group(1).rstrip(".").lower()
            name = re.sub(r"^\d+\.\s*", "", b.split("\n")[0]).strip()
            lt = re.search(r"Lead time ([\d\-]+ days)", b)
            sh = re.search(r"Shipping base \$([\d.]+), free shipping over \$([\d.]+)", b)
            rel = re.search(r"delay (\d+%), stockouts (\d+%)", b)
            cats = re.search(r"categories: (.*?)\.", b)
            prices = re.findall(r"([A-Z][^:,]+?): \$(\d+\.\d+)", b.split("Sample prices:", 1)[-1]) if "Sample prices:" in b else []
            self.suppliers[email] = {
                "name": name[:30],
                "lt": lt.group(1) if lt else "?",
                "ship": ("$%s, free>$%s" % sh.groups()) if sh else "?",
                "rel": ("delay %s/stockout %s" % rel.groups()) if rel else "",
                "cats": cats.group(1)[:45] if cats else "",
                "p": ", ".join("%s $%s" % (n.strip()[:16], p) for n, p in prices[:3]),
            }

    # ------------------------------------------------------------------
    def _sku_for(self, name):
        s = _slug(name)
        s2 = re.sub(r"_can$", "", s)
        for c in (s, s2):
            if c in self.skus:
                return c
        toks = set(s.split("_"))
        best = None
        for k in self.skus:
            kt = set(k.split("_"))
            if kt and kt <= toks:
                if best is None or len(k) > len(best):
                    best = k
        return best or s2

    def brief(self):
        try:
            return self._brief()[:3990]
        except Exception:
            return "Logbook: (error building note) Keep machine stocked; use run_sub_agent with \"slot\":\"R3C1\" format."

    def _brief(self):
        L = []
        bank = self.bank if self.bank is not None else 0.0
        net = (bank - self.start_bank) if self.start_bank is not None else 0.0
        L.append("LOGBOOK day %d | bank $%.2f (%+.2f since start) | cash in machine $%.2f" %
                 (self.day, bank, net, self.cash or 0.0))
        if self.sales:
            dates = sorted(self.sales)[-7:]
            u = sum(self.sales[d][0] for d in dates)
            r = sum(self.sales[d][1] for d in dates)
            L.append("Sales last %d days: %d units, $%.2f revenue (fee $2/day)." % (len(dates), u, r))
        if self.sku_sold:
            top = sorted(self.sku_sold.items(), key=lambda x: -x[1])[:6]
            L.append("Units sold by SKU (run total): " + ", ".join("%s %d" % t for t in top))
        if (self.cash or 0) >= 15:
            L.append("-> Collect cash: run_sub_agent [{\"op\":\"collect_cash\"}].")

        # machine
        filled = []
        empty_s, empty_l = [], []
        for slot in sorted(self.machine):
            i = self.machine[slot]
            if i["sku"] and i["qty"] > 0:
                filled.append("%s %s %d/%s $%.2f" % (slot, i["sku"], i["qty"], i["cap"], i["price"] or 0))
            else:
                (empty_s if i["size"] == "small" else empty_l).append(slot)
        L.append("MACHINE filled: " + ("; ".join(filled) if filled else "NONE - earning nothing!"))
        if empty_s or empty_l:
            L.append("Empty small slots: %s | empty large slots: %s" % (",".join(empty_s) or "-", ",".join(empty_l) or "-"))

        # stocking know-how
        sm = sorted(k for k, v in self.sku_size.items() if v == "small")
        lg = sorted(k for k, v in self.sku_size.items() if v == "large")
        L.append("STOCK FORMAT (works): " + (self.stock_template or
                 '{"op":"stock","slot":"R3C1","sku":"<sku>","qty":N} then {"op":"set_price","slot":"R3C1","price":P}') +
                 ". row/col keys fail. Product size must match slot size.")
        if sm or lg:
            L.append("Learned SKU sizes -> SMALL slots(R1,R2): %s | LARGE slots(R3,R4): %s" %
                     (", ".join(sm) or "?", ", ".join(lg) or "?"))
        if self.fail_reasons:
            fr = sorted(self.fail_reasons.items(), key=lambda x: -x[1])[:3]
            L.append("Past stock failures: " + "; ".join("%s x%d" % t for t in fr) +
                     " ('expects small, got large' = item is LARGE, use R3/R4).")
        if self.storage:
            parts = []
            for nm, q in self.storage.items():
                sku = self._sku_for(nm)
                parts.append("%s=%d (sku %s, %s)" % (nm, q, sku, self.sku_size.get(sku, "size?")))
            L.append("STORAGE: " + "; ".join(parts))
            if empty_s or empty_l:
                L.append("-> ACTION: stock storage items into empty matching slots NOW.")
        if self.storage_ghost:
            L.append("Note: stocking succeeded %d time(s) while STORAGE said Empty - after a delivery email, just try stocking." % self.storage_ghost)
        if self.chat_echo:
            L.append("chat_with_sub_agent only echoed the last run_sub_agent output (%dx) - it does not answer; use run_sub_agent." % self.chat_echo)
        if self.repair_cost_note:
            L.append("Seen service cost: " + self.repair_cost_note)

        # incidents
        if self.jams or self.fault:
            L.append("INCIDENT: jams %s fault=%s -> run_sub_agent clear_jam{slot} / repair_machine." %
                     (",".join(self.jams) or "none", self.fault))
        if self.event:
            L.append("Market event today: " + self.event)

        # ordering lessons
        if self.sent or self.charges:
            sent_tot = sum(v[0] for v in self.sent.values())
            with_acct = sum(v[1] for v in self.sent.values())
            needs = sum(self.need.values())
            L.append("ORDERING: %d emails sent to suppliers (%d included account %s); %d charges received; %d replies said 'still need account number'." %
                     (sent_tot, with_acct, self.acct or "?", len(self.charges), needs))
            if needs:
                L.append("-> Suppliers only place orders when the email lists products, quantities, delivery address AND account number %s (your own billing acct, shown in KEY BUSINESS DETAILS; sending it to a listed supplier is how payment works)." % (self.acct or ""))
            if self.complaint_charges:
                L.append("WARNING: %d complaint/follow-up emails listing items were treated as NEW orders and charged. Don't list items in complaints." % self.complaint_charges)
        for c in self.charges[-4:]:
            L.append(" charge d%d %s $%s deliv %s (from mail '%s')" % (c[0], c[1][:20], c[2], c[3], c[4][:40]))
        for d in self.deliveries[-3:]:
            L.append(" delivered(read d%d) %s: %s%s" % (d[0], d[1], d[2], (" ISSUE " + d[3]) if d[3] else ""))
        if self.outstanding:
            L.append("Outstanding: " + " || ".join(o[:110] for o in self.outstanding[:3]))

        # catalog
        best = {}
        for dom, cat in self.catalog.items():
            for nm, (pr, moq, cp) in cat.items():
                p = _f(pr) or 99
                if nm not in best or p < best[nm][0]:
                    best[nm] = (p, dom.split(".")[0], moq)
        if best:
            L.append("Best quoted unit costs: " + "; ".join("%s $%.2f %s MOQ%s" % (n[:18], v[0], v[1][:14], v[2])
                                                          for n, v in sorted(best.items())[:12]))
        elif self.suppliers:
            for e, s in list(self.suppliers.items())[:5]:
                L.append(" sup %s <%s> lt %s ship %s; %s" % (s["name"], e, s["lt"], s["ship"], s["p"][:60]))
        if self.demand:
            L.append("Demand info: " + self.demand[:200])

        # emails
        if self.email_order:
            L.append("EMAILS read (latest):")
            for eid in self.email_order[-5:]:
                L.append(" " + self.emails.get(eid, "")[:190])
        rep = [e for e, n in self.read_counts.items() if n >= 3]
        if rep:
            L.append("You re-read emails %s 3+ times - content is summarized above; don't reread." % ",".join(rep[-5:]))
        if self.suspicious:
            L.append("Suspicious senders (ignore): " + ", ".join(self.suspicious[-3:]))

        # loops
        today = [h for h in self.hist if h[0] == self.day]
        cnt = {}
        for h in self.hist[-8:]:
            cnt[h[2]] = cnt.get(h[2], 0) + 1
        loops = [k for k, v in cnt.items() if v >= 3]
        if loops:
            L.append("LOOP: repeated '%s' %dx recently - change approach." % (loops[0][:70], cnt[loops[0]]))
        if len(today) >= 8:
            L.append("%d actions already today; if nothing to stock/order, wait_for_next_day." % len(today))
        return "\n".join(L)
