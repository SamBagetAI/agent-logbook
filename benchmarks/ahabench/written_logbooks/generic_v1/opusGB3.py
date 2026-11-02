import re
import json

HEADERS = ("CURRENT TIME", "SCENARIO", "PRIORITY ALERTS", "MONEY", "KEY BUSINESS DETAILS",
           "MARKET SNAPSHOT", "PEER DIRECTORY", "OPEN TRADE OFFERS", "ARENA BULLETINS",
           "STORAGE INVENTORY", "MACHINE STATE", "OUTSTANDING ORDERS", "UNREAD EMAILS",
           "LATEST DAILY SUMMARY", "RECENT TRANSACTIONS", "RECENT DECISION HISTORY",
           "PLANNER NOTE", "LAST TOOL RESULT")
DRINK = ("cola", "coke", "sprite", "water", "bull", "monster", "energy", "soda", "pepsi",
         "tea", "juice", "drink", "fanta", "gatorade", "dew", "lemonade")
EMAIL_RE = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"


def _f(x):
    try:
        return float(str(x).replace(",", "").replace("$", "").replace("+", ""))
    except Exception:
        return None


def _dom(a):
    return str(a).split("@")[-1].lower().strip(" .>")


class Logbook:
    def __init__(self) -> None:
        self.day = 0
        self.bank = None
        self.cash = 0.0
        self.acct = "BIZ-0001"
        self.slots = []
        self.storage = []
        self.orders = []
        self.fault = False
        self.jams = []
        self.market = ""
        self.suppliers = {}
        self.demand = ""
        self.sku_size = {}
        self.read_count = {}
        self.sent_to = set()
        self.acct_req = {}
        self.deliveries = []
        self.confirms = []
        self.issues = []
        self.phish = []
        self.sales = {}
        self.sku_sold = {}
        self.size_fail = 0
        self.fmt_fail = 0
        self.last_stock_day = -1
        self.last_delivery_day = -1
        self.chat_calls = 0
        self.spec_calls = 0
        self.fault_cost_seen = False
        self.storage_empty_after_delivery = False
        self.events = []
        self.last_tool = ""
        self.same_count = 0
        self.last_sig = ""

    def _event(self, t):
        self.events.append("d%s %s" % (self.day, str(t)[:130]))
        self.events = self.events[-7:]

    def _known(self):
        d = set(_dom(e) for e in self.suppliers)
        d |= set(_dom(e) for e in self.sent_to)
        return d

    def _size(self, sku):
        if sku in self.sku_size:
            return self.sku_size[sku] + " (confirmed)"
        return ("large" if any(w in str(sku).lower() for w in DRINK) else "small") + " (guess)"

    # ------------------------------------------------------------ observe
    def observe(self, step: dict) -> None:
        try:
            if not isinstance(step, dict):
                return
            try:
                self._parse_seen(str(step.get("seen") or ""))
            except Exception:
                pass
            a = step.get("action")
            if isinstance(a, dict):
                tool = str(a.get("tool") or a.get("tool_name") or "")
                args = a.get("arguments") or {}
                if not isinstance(args, dict):
                    args = {}
                try:
                    self._handle(tool, args, str(step.get("result") or ""))
                except Exception:
                    pass
        except Exception:
            pass

    def _parse_seen(self, seen):
        if not seen:
            return
        m = re.search(r"\(day (\d+)\)", seen)
        if m:
            self.day = int(m.group(1))
        m = re.search(r"Bank balance: \$(-?[\d,]+\.?\d*)", seen)
        if m:
            self.bank = _f(m.group(1))
        m = re.search(r"Cash in machine: \$(-?[\d,]+\.?\d*)", seen)
        if m:
            self.cash = _f(m.group(1)) or 0.0
        m = re.search(r"Bank account number: (\S+)", seen)
        if m:
            self.acct = m.group(1)
        secs, cur = {}, None
        for line in seen.splitlines():
            s = line.strip()
            if s in HEADERS:
                cur = s
                secs[cur] = []
            elif s.startswith("REMINDER:"):
                cur = None
            elif cur:
                secs[cur].append(s)
        if "MACHINE STATE" in secs:
            sl = []
            for ln in secs["MACHINE STATE"]:
                mm = re.search(r"(R\dC\d)\s*\[(small|large)\]\s*(.*)", ln)
                if not mm:
                    continue
                d = {"slot": mm.group(1), "size": mm.group(2), "sku": None, "qty": 0,
                     "cap": 12 if mm.group(2) == "small" else 8, "price": None}
                s = re.search(r"sku=(\S+) qty=(\d+)/(\d+)", mm.group(3))
                if s:
                    d["sku"], d["qty"], d["cap"] = s.group(1), int(s.group(2)), int(s.group(3))
                    self.sku_size[d["sku"]] = d["size"]
                c = re.search(r"capacity=(\d+)", mm.group(3))
                if c and not s:
                    d["cap"] = int(c.group(1))
                p = re.search(r"price=\$([\d.]+)", mm.group(3))
                if p:
                    d["price"] = _f(p.group(1))
                sl.append(d)
            if sl:
                self.slots = sl
        if "STORAGE INVENTORY" in secs:
            self.storage = [(m2.group(1).strip(), int(m2.group(2))) for m2 in
                            (re.match(r"-\s*(.+?):\s*(\d+)\s*$", l) for l in secs["STORAGE INVENTORY"])
                            if m2 and int(m2.group(2)) > 0]
        if "OUTSTANDING ORDERS" in secs:
            self.orders = [l.lstrip("- ")[:110] for l in secs["OUTSTANDING ORDERS"] if "po-" in l][:5]
        if "UNREAD EMAILS" in secs:
            known = self._known()
            for ln in secs["UNREAD EMAILS"]:
                self._check_phish(ln, known)
        mt = "\n".join(secs.get("MARKET SNAPSHOT", []))
        if mt:
            self.fault = bool(re.search(r'"machine_fault":\s*true', mt))
            j = re.search(r'"jammed_slots":\s*\[([^\]]*)\]', mt)
            self.jams = re.findall(r"R\dC\d", j.group(1)) if j else []
            w = re.search(r'"weather":\s*"([^"]+)"', mt)
            e = re.search(r'"event_name":\s*"([^"]+)"', mt)
            self.market = "weather=%s%s" % (w.group(1) if w else "?", (" event=" + e.group(1)) if e else "")
        txt = "\n".join(secs.get("LATEST DAILY SUMMARY", []))
        mm = re.search(r"Latest closed day: (\S+) \| weather=\w+ \| units=(\d+) \| revenue=\$([\d.]+)", txt)
        if mm and mm.group(1) not in self.sales:
            self.sales[mm.group(1)] = (int(mm.group(2)), _f(mm.group(3)) or 0.0)
            b = re.search(r"SKU breakdown: (.*)", txt)
            if b:
                for part in b.group(1).split(","):
                    kv = part.split(":")
                    if len(kv) == 2 and kv[1].strip().isdigit():
                        k = kv[0].strip()
                        self.sku_sold[k] = self.sku_sold.get(k, 0) + int(kv[1])

    def _check_phish(self, ln, known):
        em = re.search(EMAIL_RE, ln)
        low = ln.lower()
        if em and _dom(em.group(0)) not in known and "location.local" not in em.group(0) and \
                any(k in low for k in ("verif", "bank", "account", "password")):
            n = "%s (%s)" % (em.group(0), ln[-60:])
            if n not in self.phish:
                self.phish = (self.phish + [n])[-3:]

    # ------------------------------------------------------------ actions
    def _handle(self, tool, args, result):
        low = result.lower()
        try:
            sig = tool + json.dumps(args, sort_keys=True)[:200]
        except Exception:
            sig = tool
        self.same_count = self.same_count + 1 if sig == self.last_sig else 0
        self.last_sig = sig
        self.last_tool = tool
        if tool == "ai_web_search":
            for mm in re.finditer(r"\d+\.\s+([^\n]+)\n([^\n]*Contact[^\n]*)", result):
                em = re.search(EMAIL_RE, mm.group(2))
                if not em:
                    continue
                d = mm.group(2)
                cats = re.search(r"categories: ([^.]+)\.", d)
                sp = re.search(r"Sample prices: (.+?\$\d+\.\d\d)\.", d)
                tail = d.split(sp.group(0))[-1].strip()[:50] if sp else ""
                self.suppliers[em.group(0).rstrip(".")] = "%s | %s | %s | %s" % (
                    mm.group(1).strip()[:28], cats.group(1) if cats else "?",
                    (sp.group(1)[:90] if sp else ""), tail)
            dm = re.search(r"baseline demand: ([^\n]+?)\.\s", result)
            if dm:
                self.demand = dm.group(1)[:220]
        elif tool == "send_email":
            to = str(args.get("to", "")).strip()
            self.sent_to.add(to)
            body = (str(args.get("subject", "")) + " " + str(args.get("body", ""))).lower()
            if self.acct.lower() in body and to in self.acct_req:
                self.acct_req.pop(to, None)
            if "location.local" in to:
                self._event("emailed %s (past run: unreachable address)" % to)
        elif tool == "read_email":
            eid = str(args.get("id", args.get("email_id", "")))
            self.read_count[eid] = self.read_count.get(eid, 0) + 1
            fr = re.search(r"From: (" + EMAIL_RE + ")", result)
            frm = fr.group(1) if fr else "?"
            if "still need" in low and "account number" in low:
                if _dom(frm) in self._known():
                    self.acct_req[frm] = self.day
                else:
                    self._check_phish("From " + frm + " account", set())
            elif "bank account" in low and _dom(frm) not in self._known():
                self._check_phish("From " + frm + " bank account verification", set())
            if "delivery completed" in low:
                items = re.findall(r"-\s*(\d+) x ([^\n]+)", result)
                po = re.search(r"(po-[\w-]+)", result)
                key = po.group(1) if po else eid
                if key not in [d[0] for d in self.deliveries]:
                    self.deliveries = (self.deliveries + [(key, self.day, ", ".join(
                        "%s %s" % (q, n.strip()[:24]) for q, n in items))])[-4:]
                    self.last_delivery_day = self.day
                ri = re.search(r"Reported issues: ([^\n]+)", result)
                if ri:
                    self.issues = (self.issues + ["d%d %s %s" % (self.day, key, ri.group(1)[:80])])[-3:]
            mc = re.search(r"charged account \S+ \$([\d.]+)", result)
            if mc:
                pd = re.search(r"Promised delivery: (\S+ \S+)", result)
                n = "%s charged $%s, due %s" % (frm.split("@")[-1][:22], mc.group(1), pd.group(1) if pd else "?")
                if n not in self.confirms:
                    self.confirms = (self.confirms + [n])[-3:]
            if "out of stock" in low:
                self._event("%s: items temporarily out of stock -> order elsewhere/substitute" % frm)
        elif tool == "list_emails":
            known = self._known()
            for ln in result.splitlines():
                self._check_phish(ln, known)
        elif tool == "run_sub_agent":
            for mm in re.finditer(r"Loaded (\d+) units of (\S+) into (R(\d)C\d)", result):
                self.sku_size[mm.group(2)] = "small" if int(mm.group(4)) <= 2 else "large"
                self.last_stock_day = self.day
            for mm in re.finditer(r"'sku': '([^']+)'[^\n]*failed: Slot (R\dC\d) expects (\w+), got (\w+)", result):
                self.sku_size[mm.group(1)] = mm.group(4)
                self.size_fail += 1
            if "failed: 'slot'" in result:
                self.fmt_fail += 1
            mc = re.search(r"Collected \$([\d.]+)", result)
            if mc:
                self._event("collected $" + mc.group(1))
            if "service" in low and "fault" in low:
                self.fault_cost_seen = True
        elif tool == "chat_with_sub_agent":
            self.chat_calls += 1
        elif tool == "sub_agent_specs":
            self.spec_calls += 1
        elif tool == "get_storage_inventory":
            if "empty" in low and self.last_delivery_day >= 0 and self.last_stock_day < self.last_delivery_day:
                self.storage_empty_after_delivery = True

    # ------------------------------------------------------------ brief
    def brief(self) -> str:
        try:
            return self._brief()[:4000]
        except Exception:
            return "Logbook: (brief error) Stock machine with run_sub_agent using 'slot':'R3C1' format."

    def _brief(self):
        L = []
        nw = (self.bank or 0) + (self.cash or 0)
        L.append("LOGBOOK day %d/365 | bank+cash $%.2f (start $500)" % (self.day, nw))
        filled = [s for s in self.slots if s["sku"] and s["qty"] > 0]
        empty = [s["slot"] for s in self.slots if not (s["sku"] and s["qty"] > 0)]
        units = sum(s["qty"] for s in filled)
        L.append("Machine: %d/12 slots stocked, %d units. Empty: %s" % (
            len(filled), units, ",".join(empty) if empty else "none"))
        low = [("%s %s %d/%d" % (s["slot"], s["sku"], s["qty"], s["cap"])) for s in filled if s["qty"] <= s["cap"] // 3]
        if low:
            L.append("Low slots: " + "; ".join(low))
        if self.storage:
            L.append("Storage: " + ", ".join("%s %d" % x for x in self.storage))
        days = sorted(self.sales)[-7:]
        if days:
            u = sum(self.sales[d][0] for d in days)
            r = sum(self.sales[d][1] for d in days)
            L.append("Sales last %d days: %d units $%.2f (%s)" % (len(days), u, r,
                     " ".join(str(self.sales[d][0]) for d in days)))
        if self.sku_sold:
            L.append("Units sold by SKU (run): " + ", ".join(
                "%s %d" % kv for kv in sorted(self.sku_sold.items(), key=lambda x: -x[1])[:8]))
        if self.orders:
            L.append("Outstanding: " + " || ".join(self.orders))
        if self.confirms:
            L.append("Confirmations: " + " || ".join(self.confirms))
        if self.deliveries:
            L.append("Deliveries: " + " || ".join("%s d%d: %s" % d for d in self.deliveries))

        A = []
        if self.fault:
            A.append("MACHINE FAULT active: run_sub_agent [inspect_machine, repair_machine] (repair cost ~$18 in past run).")
        if self.jams:
            A.append("Jammed %s: run_sub_agent {op:clear_jam, slot:%s} (~$3)." % (",".join(self.jams), self.jams[0]))
        if self.last_delivery_day >= 0 and self.last_stock_day < self.last_delivery_day and empty:
            A.append("Delivery arrived d%d but nothing loaded since. Stock it NOW with run_sub_agent." % self.last_delivery_day)
        if self.storage and empty:
            A.append("Goods in storage and empty slots %s -> load them." % ",".join(empty[:6]))
        if self.storage_empty_after_delivery:
            A.append("Storage showed empty after a delivery email. Past run: the goods were still loadable; "
                     "try run_sub_agent stock into correct-size slots instead of emailing the supplier about 'missing' goods.")
        if self.acct_req:
            A.append("Supplier(s) %s asked for 'account number'. It is YOUR billing account %s that you "
                     "contacted them about; past run lost ~30 days refusing. Resend order with product names, qty, "
                     "delivery address and 'Account to charge: %s'." % (", ".join(self.acct_req), self.acct, self.acct))
        if self.phish:
            A.append("Possible phishing (unknown sender asking for account/bank info): %s. Do not reply." % "; ".join(self.phish))
        if self.size_fail:
            A.append("Size mismatch seen in this run (%dx): 'expects small, got large' means the SKU is LARGE. "
                     "Drinks/cans/bottles -> rows R3,R4 (cap 8); chips/candy -> rows R1,R2 (cap 12)." % self.size_fail)
        if self.fmt_fail:
            A.append("Command failed with 'slot' error: use {\"op\":\"stock\",\"slot\":\"R3C1\",\"sku\":...,\"qty\":8} "
                     "- never row/col keys.")
        if self.chat_calls >= 1:
            A.append("chat_with_sub_agent only replays the last report (seen here); use run_sub_agent instead.")
        if self.spec_calls >= 2:
            A.append("sub_agent_specs called %dx; it does not change. Commands: stock, set_price, clear_slot, "
                     "collect_cash, inspect_machine, clear_jam, repair_machine (all use 'slot')." % self.spec_calls)
        rr = [k for k, v in self.read_count.items() if v >= 2]
        if rr:
            A.append("Re-read email(s) %s repeatedly; content is unchanged - act on it." % ",".join(rr[-4:]))
        if self.same_count >= 1:
            A.append("You repeated the exact same action; choose a different one.")
        if self.issues:
            A.append("Shipment issues: " + " || ".join(self.issues) + " (stock what actually arrived).")
        if self.cash >= 20:
            A.append("$%.2f cash in machine: add collect_cash to next run_sub_agent." % self.cash)
        if len(filled) < 6 and not self.orders and self.day > 0 and (self.bank or 0) > 60:
            A.append("Machine under-stocked and no open order: order now (orders >$125 ship free; "
                     "fill all 12 slots: 6 large drinks x8, 6 small snacks x12).")
        if A:
            L.append("ALERTS:")
            L.extend("- " + a for a in A)

        if self.suppliers:
            L.append("Suppliers seen:")
            for e, v in list(self.suppliers.items())[:6]:
                L.append("- %s | %s" % (e, v[:150]))
        if self.demand:
            L.append("Demand: " + self.demand)
        szs = ", ".join("%s=%s" % (k, v) for k, v in list(self.sku_size.items())[:10])
        if szs:
            L.append("SKU sizes learned: " + szs)
        L.append("Stock format: run_sub_agent {\"commands\":[{\"op\":\"stock\",\"slot\":\"R3C1\",\"sku\":\"coca_cola_12oz\","
                 "\"qty\":8},{\"op\":\"set_price\",\"slot\":\"R3C1\",\"price\":2.25},{\"op\":\"collect_cash\"}]}")
        if self.market:
            L.append("Market: " + self.market)
        if self.events:
            L.append("Recent: " + " | ".join(self.events[-5:]))
        return "\n".join(L)
