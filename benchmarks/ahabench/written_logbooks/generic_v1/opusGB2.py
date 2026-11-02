import re
import json

HEADERS = ["CURRENT TIME", "SCENARIO", "PRIORITY ALERTS", "MONEY", "KEY BUSINESS DETAILS",
           "MARKET SNAPSHOT", "PEER DIRECTORY", "OPEN TRADE OFFERS", "ARENA BULLETINS",
           "STORAGE INVENTORY", "MACHINE STATE", "OUTSTANDING ORDERS", "UNREAD EMAILS",
           "LATEST DAILY SUMMARY", "RECENT TRANSACTIONS", "RECENT DECISION HISTORY",
           "PLANNER NOTE", "LAST TOOL RESULT", "REMINDER"]

PHISH_W = ["verif", "bank account", "password", "wire", "credential", "suspend", "login", "reply with the bank"]
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _f(x, d=None):
    try:
        return float(str(x).replace(",", "").replace("$", "").strip())
    except Exception:
        return d


def _sections(text):
    secs = {}
    cur = None
    buf = []
    for line in str(text).splitlines():
        s = line.strip()
        h = None
        for H in HEADERS:
            if s == H or (H == "REMINDER" and s.startswith("REMINDER")):
                h = H
                break
        if h:
            if cur:
                secs[cur] = "\n".join(buf)
            cur = h
            buf = []
            continue
        if cur:
            buf.append(s)
    if cur:
        secs[cur] = "\n".join(buf)
    return secs


def _domain(addr):
    try:
        return addr.split("@", 1)[1].lower().strip(".>")
    except Exception:
        return ""


class Logbook:
    def __init__(self) -> None:
        self.day = 0
        self.time = ""
        self.bank = None
        self.cash = None
        self.agent_email = ""
        self.account = ""
        self.suppliers = {}      # email -> {name, note}
        self.terms = {}          # email -> list of "sku $x MOQ"
        self.demand = ""
        self.sizes = {}          # sku -> small/large learned this run
        self.charges = []        # (day, supplier, amount, shipping, promised)
        self.deliveries = []     # (day, po, items, issues)
        self.sent = []           # (day, to, subject)
        self.awaiting = {}       # email -> day sent
        self.need_account = {}   # email -> day
        self.out_of_stock = []
        self.phish = set()
        self.cash_collected = 0.0
        self.service = 0.0
        self.fmt_errors = 0
        self.size_errors = 0
        self.stock_ok = 0
        self.recent = []
        self.daily = {}          # date -> (units, revenue, skus)
        self.machine = {}
        self.storage = {}
        self.outstanding = []
        self.unread = []
        self.fault = False
        self.jams = []
        self.event = ""
        self.weather = ""
        self.emailed_unreachable = False
        self.chat_uses = 0
        self.specs_uses = 0
        self.last_stock_day = -1
        self.price_changes = []

    # ---------------- observe ----------------
    def observe(self, step: dict) -> None:
        try:
            act = step.get("action") if isinstance(step, dict) else None
            res = str(step.get("result") or "") if isinstance(step, dict) else ""
            seen = str(step.get("seen") or "") if isinstance(step, dict) else ""
        except Exception:
            return
        try:
            self._parse_seen(seen)
        except Exception:
            pass
        try:
            if act:
                self._parse_action(act, res)
        except Exception:
            pass

    def _parse_seen(self, seen):
        m = re.search(r"\(day (\d+)\)", seen)
        if m:
            self.day = int(m.group(1))
        m = re.search(r"(\d{4}-\d\d-\d\dT[\d:]+)", seen)
        if m:
            self.time = m.group(1)
        m = re.search(r"Bank balance:\s*\$(-?[\d,.]+)", seen)
        if m:
            self.bank = _f(m.group(1), self.bank)
        m = re.search(r"Cash in machine:\s*\$(-?[\d,.]+)", seen)
        if m:
            self.cash = _f(m.group(1), self.cash)
        m = re.search(r"Agent email:\s*(\S+)", seen)
        if m:
            self.agent_email = m.group(1)
        m = re.search(r"Bank account number:\s*(\S+)", seen)
        if m:
            self.account = m.group(1)
        secs = _sections(seen)
        ms = secs.get("MACHINE STATE")
        if ms is not None:
            mach = {}
            for line in ms.splitlines():
                mm = re.match(r"-?\s*(R\dC\d)\s*\[(\w+)\]\s*(.*)", line.strip())
                if not mm:
                    continue
                slot, size, rest = mm.group(1), mm.group(2), mm.group(3)
                d = {"size": size, "sku": None, "qty": 0, "cap": None, "price": None, "cost": None}
                s = re.search(r"sku=(\S+)", rest)
                if s:
                    d["sku"] = s.group(1)
                    self.sizes[s.group(1)] = size
                q = re.search(r"qty=(\d+)/(\d+)", rest)
                if q:
                    d["qty"] = int(q.group(1))
                    d["cap"] = int(q.group(2))
                c = re.search(r"capacity=(\d+)", rest)
                if c:
                    d["cap"] = int(c.group(1))
                p = re.search(r"price=\$([\d.]+)", rest)
                if p:
                    d["price"] = _f(p.group(1))
                u = re.search(r"unit_cost_avg=([\d.]+)", rest)
                if u:
                    d["cost"] = _f(u.group(1))
                mach[slot] = d
            if mach:
                self.machine = mach
        st = secs.get("STORAGE INVENTORY")
        if st is not None:
            inv = {}
            if "empty" not in st.lower()[:30]:
                for line in st.splitlines():
                    mm = re.match(r"-?\s*(.+?):\s*(\d+)\s*$", line.strip())
                    if mm:
                        inv[mm.group(1)] = int(mm.group(2))
            self.storage = inv
        oo = secs.get("OUTSTANDING ORDERS")
        if oo is not None:
            self.outstanding = [l[:150] for l in oo.splitlines() if l.strip().startswith("-")][:6]
        ue = secs.get("UNREAD EMAILS")
        if ue is not None:
            self.unread = [l[:160] for l in ue.splitlines() if l.strip() and l.strip() != "None."][:8]
            for l in self.unread:
                em = EMAIL_RE.search(l)
                if em and any(w in l.lower() for w in PHISH_W) and not self._known(em.group(0)):
                    self.phish.add(em.group(0))
        ds = secs.get("LATEST DAILY SUMMARY")
        if ds:
            mm = re.search(r"Latest closed day:\s*(\S+).*?units=(\d+).*?revenue=\$([\d.]+)", ds)
            if mm:
                sk = ""
                s2 = re.search(r"SKU breakdown:\s*(.*)", ds)
                if s2:
                    sk = s2.group(1)[:120]
                self.daily[mm.group(1)] = (int(mm.group(2)), _f(mm.group(3), 0.0), sk)
        mk = secs.get("MARKET SNAPSHOT")
        if mk:
            try:
                j = json.loads(mk[mk.index("{"):mk.rindex("}") + 1])
                self.fault = bool(j.get("machine_fault"))
                self.jams = list(j.get("jammed_slots") or [])
                self.event = str(j.get("event_name") or "")
                self.weather = str(j.get("weather") or "")
            except Exception:
                pass

    def _known(self, addr):
        a = addr.lower().strip(".")
        if a in self.suppliers:
            return True
        d = _domain(a)
        return any(_domain(s) == d for s in self.suppliers)

    def _parse_action(self, act, res):
        tool = act.get("tool") or act.get("tool_name") or ""
        args = act.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        try:
            key = tool + json.dumps(args, sort_keys=True)[:300]
        except Exception:
            key = tool
        self.recent.append((self.day, tool, key))
        self.recent = self.recent[-14:]

        if tool == "ai_web_search":
            for block in re.split(r"\n\s*\n", res):
                lines = block.strip().splitlines()
                if not lines:
                    continue
                head = re.sub(r"^\d+\.\s*", "", lines[0]).strip()
                body = " ".join(lines[1:])
                if "baseline demand" in body:
                    mm = re.search(r"Top products by estimated baseline demand:\s*([^.]*(?:\.\d[^.]*)*)", body)
                    self.demand = (mm.group(1) if mm else body)[:260]
                    continue
                em = EMAIL_RE.search(body)
                if em:
                    addr = em.group(0).strip(".").lower()
                    note = ""
                    sp = re.search(r"Sample prices:\s*(.*?)\.\s+[A-Z]", body)
                    if sp:
                        note = sp.group(1)[:110]
                    lt = re.search(r"Lead time ([\d-]+ days)", body)
                    cats = re.search(r"Carries categories:\s*([^.]*)", body)
                    flag = ""
                    if "near-retail" in body or "inflated" in body:
                        flag = " [OVERPRICED]"
                    self.suppliers[addr] = {"name": head[:40] + flag,
                                            "note": ((cats.group(1) if cats else "") + "; " +
                                                     (lt.group(1) if lt else "") + "; " + note)[:200]}
        elif tool == "read_email":
            self._parse_email(res)
        elif tool == "send_email":
            to = str(args.get("to", "")).lower()
            subj = str(args.get("subject", ""))[:60]
            body = str(args.get("body", ""))
            self.sent.append((self.day, to, subj))
            self.sent = self.sent[-8:]
            self.awaiting[to] = self.day
            if to in self.need_account and self.account and self.account in body:
                self.need_account.pop(to, None)
            if "location.local" in to or "mailer" in to:
                self.emailed_unreachable = True
            if self.account and self.account in body and not self._known(to):
                self.phish.add("YOU SENT account to unverified " + to)
        elif tool == "run_sub_agent":
            for line in res.splitlines():
                l = line.strip()
                mm = re.match(r"Loaded (\d+) units of (\S+) into (R\dC\d)", l)
                if mm:
                    self.stock_ok += 1
                    self.last_stock_day = self.day
                    sz = self.machine.get(mm.group(3), {}).get("size")
                    if not sz:
                        sz = "small" if mm.group(3)[1] in "12" else "large"
                    self.sizes[mm.group(2)] = sz
                if "failed" in l:
                    sk = re.search(r"'sku': '([^']+)'", l)
                    if "expects small, got large" in l:
                        self.size_errors += 1
                        if sk:
                            self.sizes[sk.group(1)] = "large"
                    elif "expects large, got small" in l:
                        self.size_errors += 1
                        if sk:
                            self.sizes[sk.group(1)] = "small"
                    elif "failed: 'slot'" in l:
                        self.fmt_errors += 1
                mm = re.search(r"Collected \$([\d.]+) in cash", l)
                if mm:
                    self.cash_collected += _f(mm.group(1), 0.0)
                mm = re.match(r"Set (R\dC\d) price to \$([\d.]+)", l)
                if mm:
                    self.price_changes.append((self.day, mm.group(1), mm.group(2)))
                    self.price_changes = self.price_changes[-6:]
                if "global fault is active" in l:
                    self.fault = True
                if "no active machine incidents" in l:
                    self.fault = False
                    self.jams = []
        elif tool == "get_operations_status":
            low = res.lower()
            self.fault = "global fault" in low
            mm = re.search(r"Jammed slots:\s*([R\dC, ]+)", res)
            self.jams = [s.strip() for s in mm.group(1).split(",") if s.strip()] if mm else []
        elif tool == "chat_with_sub_agent":
            self.chat_uses += 1
        elif tool == "sub_agent_specs":
            self.specs_uses += 1

    def _parse_email(self, res):
        fm = re.search(r"From:\s*(\S+)", res)
        sender = fm.group(1).lower() if fm else ""
        low = res.lower()
        if sender:
            self.awaiting.pop(sender, None)
        if "we have charged account" in low:
            amt = re.search(r"charged account \S+ \$([\d.,]+)", res)
            sh = re.search(r"Shipping:\s*\$([\d.]+)", res)
            pr = re.search(r"Promised delivery:\s*(\S+ \S+)", res)
            sub = re.search(r"Goods subtotal:\s*\$([\d.]+)", res)
            self.charges.append((self.day, sender.split("@")[-1][:22],
                                 amt.group(1) if amt else "?", sh.group(1) if sh else "?",
                                 pr.group(1) if pr else "?", sub.group(1) if sub else "?"))
            self.charges = self.charges[-6:]
            self.need_account.pop(sender, None)
        if "delivery completed" in low:
            po = re.search(r"(po-[\w-]+)", res)
            items = re.findall(r"-\s*(\d+)\s*x\s*([^\n]+)", res)
            iss = re.search(r"Reported issues:\s*(.*)", res)
            itxt = ", ".join("%s %s" % (q, n.strip()[:24]) for q, n in items)[:160]
            entry = (self.day, po.group(1) if po else "?", itxt, iss.group(1)[:80] if iss else "")
            if not any(d[1] == entry[1] for d in self.deliveries):
                self.deliveries.append(entry)
                self.deliveries = self.deliveries[-6:]
        if "need the following" in low and "account number" in low:
            if self._known(sender) or "wholesale" in sender or "refresh" in sender or "vend" in sender:
                self.need_account[sender] = self.day
        if "out of stock" in low:
            self.out_of_stock.append((self.day, sender))
            self.out_of_stock = self.out_of_stock[-3:]
        if "current terms" in low:
            rows = re.findall(r"-\s*([^:\n]+):\s*\$([\d.]+)/unit\s*\|\s*MOQ\s*(\d+)\s*\|\s*case pack\s*(\d+)", res)
            if rows:
                self.terms[sender] = ["%s $%s cp%s" % (n.strip()[:22], p, c) for n, p, m, c in rows][:10]
        if sender and not self._known(sender) and any(w in low for w in PHISH_W):
            if "location.local" not in sender:
                self.phish.add(sender)

    # ---------------- brief ----------------
    def brief(self) -> str:
        try:
            return self._brief()[:3990]
        except Exception:
            return "Logbook: day %s (brief error)" % self.day

    def _brief(self):
        L = []
        inv_val = 0.0
        empty = []
        low = []
        units = 0
        for s, d in sorted(self.machine.items()):
            if d.get("sku") and d.get("qty", 0) > 0:
                units += d["qty"]
                inv_val += d["qty"] * (d.get("cost") or 0)
                if d.get("cap") and d["qty"] <= max(2, d["cap"] // 4):
                    low.append("%s %s %d" % (s, d["sku"], d["qty"]))
            else:
                empty.append(s)
        nw = (self.bank or 0) + (self.cash or 0) + inv_val
        L.append("== LOGBOOK day %d/365 (%d left) | est net worth $%.2f (bank %.2f + machine cash %.2f + slot stock at cost %.2f; storage extra) ==" %
                 (self.day, max(0, 365 - self.day), nw, self.bank or 0, self.cash or 0, inv_val))
        if self.machine:
            L.append("Machine: %d units loaded, %d/12 slots empty%s" % (units, len(empty),
                     ("; LOW: " + "; ".join(low[:5])) if low else ""))
        days = sorted(self.daily.keys())[-7:]
        if days:
            tu = sum(self.daily[d][0] for d in days)
            tr = sum(self.daily[d][1] for d in days)
            L.append("Sales last %d closed days: %d units, $%.2f (%s)" % (
                len(days), tu, tr, " ".join("%s:%d" % (d[5:], self.daily[d][0]) for d in days)))
            sk = self.daily[days[-1]][2]
            if sk:
                L.append("  last day SKUs: " + sk)
        L.append("Cash collected so far: $%.2f | service costs seen via ops." % self.cash_collected)

        # Stocking format (rule from past run; triggered when goods exist or errors happened)
        stock_need = bool(self.storage) or bool(self.deliveries) or self.size_errors or self.fmt_errors
        if stock_need:
            trig = []
            if self.storage:
                trig.append("storage has %s" % ", ".join("%s:%d" % (k[:20], v) for k, v in list(self.storage.items())[:5]))
            if self.deliveries:
                trig.append("delivery %s on day %d" % (self.deliveries[-1][1], self.deliveries[-1][0]))
            if self.size_errors:
                trig.append("%d size errors this run" % self.size_errors)
            if self.fmt_errors:
                trig.append("%d 'slot' format errors this run" % self.fmt_errors)
            L.append("STOCKING RULE (trigger: %s):" % "; ".join(trig))
            L.append("  Format: {\"op\":\"stock\",\"slot\":\"R3C1\",\"sku\":\"coca_cola_12oz\",\"qty\":8} then {\"op\":\"set_price\",\"slot\":\"R3C1\",\"price\":2.25}. NOT row/col.")
            L.append("  Drinks (cans/bottles: coke, sprite, water, red bull, monster) are LARGE -> rows R3/R4 (cap 8). Snacks (chips, candy) are SMALL -> R1/R2 (cap 12). 'expects small, got large' = move that sku to R3/R4.")
            L.append("  Past run: storage showed 'empty' right after a delivery email yet stocking worked; try run_sub_agent stock directly instead of investigating.")
        if self.sizes:
            L.append("Sizes confirmed this run: " + ", ".join("%s=%s" % (k, v[0].upper()) for k, v in sorted(self.sizes.items()))[:300])

        if self.suppliers:
            L.append("Suppliers found (use exact emails):")
            for e, d in list(self.suppliers.items())[:6]:
                L.append("  %s <%s> %s" % (d["name"], e, d["note"][:120]))
        for e, t in list(self.terms.items())[:3]:
            L.append("Terms %s: %s" % (e.split("@")[-1], "; ".join(t))[:260])
        if self.demand:
            L.append("Demand (search): " + self.demand)

        if self.charges:
            L.append("Charges confirmed (day, supplier, total, ship, subtotal, promised):")
            for c in self.charges[-4:]:
                L.append("  d%d %s $%s ship $%s goods $%s -> %s" % (c[0], c[1], c[2], c[3], c[5], c[4]))
            L.append("  Note: past run suppliers often shipped/charged MORE units than requested; check subtotal vs. what you asked.")
            if any(c[3] not in ("0.00", "?") for c in self.charges[-3:]):
                L.append("  Trigger: $4 shipping charged -> orders over $125 ship free; consolidate into fewer, larger orders.")
        if self.deliveries:
            L.append("Deliveries: " + " | ".join("d%d %s: %s%s" % (d[0], d[1], d[2], (" ISSUE " + d[3]) if d[3] else "") for d in self.deliveries[-3:]))
            if self.deliveries[-1][0] >= self.last_stock_day and self.last_stock_day <= self.deliveries[-1][0]:
                if empty:
                    L.append("  ACTION: goods delivered since last stocking and %d slots empty -> stock now." % len(empty))
        if self.outstanding:
            L.append("Outstanding: " + " | ".join(o.lstrip("- ")[:110] for o in self.outstanding[:3]))
        if self.awaiting:
            L.append("Awaiting replies from: " + ", ".join("%s (sent d%d)" % (k, v) for k, v in list(self.awaiting.items())[-4:]))

        # Rules triggered
        if self.need_account:
            who = ", ".join(self.need_account.keys())
            L.append("RULE (trigger: %s asked for 'account number'): this is the known supplier asking which account to charge. Reply once with items, qty, delivery address and 'Account to charge: %s'. Past run refused for ~3 weeks and the machine sat empty." % (who, self.account or "BIZ-0001"))
        if self.phish:
            L.append("PHISHING/UNVERIFIED (do not send account or details): " + ", ".join(sorted(self.phish))[:200])
        if self.out_of_stock:
            o = self.out_of_stock[-1]
            L.append("RULE (trigger: %s out of stock d%d): order same SKUs from another supplier now; don't wait." % (o[1], o[0]))
        if self.fault:
            L.append("RULE (trigger: machine fault active): run_sub_agent [{\"op\":\"repair_machine\"}] (~$18) fixes it; emailing site-ops is useless.")
        if self.jams:
            L.append("RULE (trigger: jammed %s): run_sub_agent {\"op\":\"clear_jam\",\"slot\":\"%s\"} (~$3)." % (",".join(self.jams), self.jams[0]))
        if self.emailed_unreachable:
            L.append("Note: site-ops@location.local is unreachable (bounced in past run); don't email it.")

        # Loop detection
        today = [r for r in self.recent if r[0] == self.day]
        keys = [r[2] for r in self.recent[-10:]]
        dup = [k for k in set(keys) if keys.count(k) >= 2 and not k.startswith("wait_for_next_day")]
        if dup:
            L.append("LOOP WARNING: repeated identical actions recently: " + "; ".join(d[:70] for d in dup[:3]) + ". The result will not change; act differently.")
        if self.chat_uses >= 1:
            L.append("Note: chat_with_sub_agent only echoes the last run report; it cannot investigate.")
        if self.specs_uses >= 2:
            L.append("Note: sub_agent_specs already read; format is above.")
        if self.machine and len(empty) >= 6 and not self.outstanding and not self.storage and self.day >= 2:
            L.append("IDLE ALERT: %d slots empty, nothing in storage, no open orders -> place a sizable order now (prefer Red Bull, Coke, water, Snickers, Doritos; target >$125 for free shipping)." % len(empty))
        if self.machine and self.cash and self.cash >= 20:
            L.append("Cash $%.2f sitting in machine: include {\"op\":\"collect_cash\"} in next run_sub_agent." % self.cash)
        if self.event:
            L.append("Market: event=%s weather=%s" % (self.event, self.weather))
        return "\n".join(L)
