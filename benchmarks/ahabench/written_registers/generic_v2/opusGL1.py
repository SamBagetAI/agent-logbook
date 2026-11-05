import re
import json

HEADERS = ["CURRENT TIME", "SCENARIO", "PRIORITY ALERTS", "MONEY", "KEY BUSINESS DETAILS",
           "MARKET SNAPSHOT", "PEER DIRECTORY", "OPEN TRADE OFFERS", "ARENA BULLETINS",
           "STORAGE INVENTORY", "MACHINE STATE", "OUTSTANDING ORDERS", "UNREAD EMAILS",
           "LATEST DAILY SUMMARY", "RECENT TRANSACTIONS", "RECENT DECISION HISTORY",
           "PLANNER NOTE", "LAST TOOL RESULT"]

INFO_TOOLS = ("read_email", "sub_agent_specs", "get_storage_inventory", "get_operations_status",
              "list_emails", "ai_web_search", "chat_with_sub_agent", "get_market_snapshot",
              "get_balance_and_transactions")

PAST_ACCT = ("earlier run: order emails without the account number got 'still need ... account number' "
             "15+ times, no goods; every one with 'Account to charge: BIZ-0001' was charged + delivered.")
PAST_LOAD = ("earlier run: cans/bottles loaded only in R3/R4 large; snacks only in R1/R2 small; "
             "row/col args always failed with 'slot'.")
PAST_COMPLAINT = ("earlier run: 'missing/wrong item' emails that listed items were billed as NEW orders "
                  "($41.44, $66.40).")
PAST_FAULT = "earlier run: repair_machine ($18) cleared a global fault; clear_jam ($3) cleared a jam."


def _sections(text):
    out = {}
    cur = None
    for line in text.splitlines():
        s = line.strip()
        if s in HEADERS:
            cur = s
            out[cur] = []
            continue
        if s.startswith("REMINDER:"):
            cur = None
            continue
        if cur:
            out[cur].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def _norm_subj(s):
    s = (s or "").strip()
    while True:
        n = re.sub(r"(?i)^\s*(re|fwd?|fw)\s*:\s*", "", s, count=1)
        if n == s:
            break
        s = n
    return s.lower().strip()


def _f(x):
    try:
        return float(str(x).replace(",", "").replace("+", ""))
    except Exception:
        return 0.0


def _key(name):
    w = re.findall(r"[a-z]+", (name or "").lower())
    return w[0] if w else ""


def _short(sku):
    return re.sub(r"_(\d+_)?\d+(oz)?$", "", sku or "")[:14]


class Register:
    def __init__(self) -> None:
        self.day = 0
        self.bank = None
        self.cash = 0.0
        self.acct = "BIZ-0001"
        self.storage = {}
        self.machine = {}
        self.outstanding = []
        self.fault = False
        self.jams = ""
        self.sku_size = {}
        self.stock_ok = 0
        self.slot_ok = 0
        self.fmt_fail = 0
        self.size_fail = 0
        self.example = ""
        self.lost = []
        self.last_fail = []
        self.sup = {}
        self.listed = set()
        self.demand = ""
        self.demand_list = []
        self.sent = []
        self.read_ids = set()
        self.deliv = []
        self.asks = []
        self.unreach = set()
        self.daily = {}
        self.day_bd = []
        self.sku_sales = {}
        self.price_hist = {}
        self.carried = set()
        self.txn = set()
        self.service = []
        self.collected = 0.0
        self.rep = {}
        self.trig = set()
        self.complaint_charges = []
        self.last_sub = ""
        self.chat_echo = 0
        self.ship_paid = 0.0
        self.n_charged = 0
        self.max_goods = 0.0
        self.free_thr = ""

    # ------------------------------------------------------------ observe
    def observe(self, step: dict) -> None:
        try:
            if not isinstance(step, dict):
                return
            action = step.get("action")
            result = str(step.get("result") or "")
            seen = str(step.get("seen") or "")
            before = sum(self.storage.values())
            try:
                self._parse_seen(seen)
            except Exception:
                pass
            after = sum(self.storage.values())
            if action:
                try:
                    self._action(action, result, before, after)
                except Exception:
                    pass
            if after > 0:
                self.trig.add("load")
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
            self.cash = _f(m.group(1))
        m = re.search(r"Bank account number: (\S+)", seen)
        if m:
            self.acct = m.group(1)
        sec = _sections(seen)
        if "STORAGE INVENTORY" in sec:
            st = {}
            for line in sec["STORAGE INVENTORY"].splitlines():
                mm = re.match(r"^\s*-?\s*(.+?):\s*(\d+)\s*$", line)
                if mm:
                    st[mm.group(1).strip()] = int(mm.group(2))
            self.storage = st
            for k in st:
                self.carried.add(_key(k))
        if "MACHINE STATE" in sec:
            mach = {}
            for line in sec["MACHINE STATE"].splitlines():
                mm = re.search(r"(R\dC\d)\s*\[(small|large)\]\s*(.*)", line)
                if not mm:
                    continue
                rest = mm.group(3)
                sku, qty, cap, price = None, 0, None, None
                m2 = re.search(r"sku=(\S+)", rest)
                if m2:
                    sku = m2.group(1)
                    self.carried.add(_key(sku))
                    self.sku_size.setdefault(sku, (mm.group(2), "in %s" % mm.group(1)))
                m2 = re.search(r"qty=(\d+)/(\d+)", rest)
                if m2:
                    qty, cap = int(m2.group(1)), int(m2.group(2))
                m2 = re.search(r"capacity=(\d+)", rest)
                if m2:
                    cap = int(m2.group(1))
                m2 = re.search(r"price=\$([\d.]+)", rest)
                if m2:
                    price = _f(m2.group(1))
                mach[mm.group(1)] = (mm.group(2), sku, qty, cap, price)
            if mach:
                self.machine = mach
        if "OUTSTANDING ORDERS" in sec:
            self.outstanding = [l.strip().lstrip("- ") for l in sec["OUTSTANDING ORDERS"].splitlines()
                                if l.strip() and l.strip() != "None."]
        if "MARKET SNAPSHOT" in sec:
            t = sec["MARKET SNAPSHOT"]
            self.fault = bool(re.search(r'"machine_fault":\s*true', t))
            mm = re.search(r'"jammed_slots":\s*\[(.*?)\]', t, re.S)
            self.jams = mm.group(1).replace('"', '').replace("\n", "").replace(" ", "") if mm else ""
            if self.fault or self.jams:
                self.trig.add("fault")
        if "LATEST DAILY SUMMARY" in sec:
            t = sec["LATEST DAILY SUMMARY"]
            mm = re.search(r"Latest closed day: (\S+).*?units=(\d+).*?revenue=\$([\d.]+)", t)
            if mm and mm.group(1) not in self.daily:
                bd = {}
                m2 = re.search(r"SKU breakdown:(.*)", t)
                if m2:
                    for a, b in re.findall(r"([A-Za-z0-9_]+):\s*(\d+)", m2.group(1)):
                        bd[a] = int(b)
                        self.sku_sales[a] = self.sku_sales.get(a, 0) + int(b)
                self.daily[mm.group(1)] = (int(mm.group(2)), _f(mm.group(3)))
                self.day_bd.append(bd)
                self.day_bd = self.day_bd[-14:]
                prices = {}
                for v in self.machine.values():
                    if v[1] and v[4]:
                        prices[v[1]] = v[4]
                for sku in set(list(bd.keys()) + list(prices.keys())):
                    p = prices.get(sku)
                    if p is None:
                        continue
                    h = self.price_hist.setdefault(sku, {})
                    e = h.setdefault("%.2f" % p, [0, 0])
                    e[0] += bd.get(sku, 0)
                    e[1] += 1
        if "RECENT TRANSACTIONS" in sec:
            for line in sec["RECENT TRANSACTIONS"].splitlines():
                s = line.strip().lstrip("- ").strip()
                if "|" not in s or s in self.txn:
                    continue
                self.txn.add(s)
                p = [x.strip() for x in s.split("|")]
                if len(p) >= 3 and p[1] == "service_cost":
                    self.service.append("%s %s $%.2f" % (p[0][5:10], (p[3] if len(p) > 3 else "")[:22],
                                                         -_f(p[2])))

    def _supp(self, email):
        email = email.lower().strip()
        if email not in self.sup:
            self.sup[email] = {"name": email.split("@")[0], "charged": 0, "spent": 0.0, "ask": 0,
                               "oos": 0, "nopo": 0, "deliv": 0, "wrong": 0, "cat": {}, "last": "",
                               "hint": ""}
        return self.sup[email]

    def _action(self, action, result, before, after):
        tool = action.get("tool") or action.get("tool_name") or ""
        args = action.get("arguments") or {}
        if tool in INFO_TOOLS:
            try:
                key = tool + " " + json.dumps(args, sort_keys=True)[:60]
            except Exception:
                key = tool
            h = hash(result)
            r = self.rep.get(key)
            if r and r[1] == h:
                self.rep[key] = [r[0] + 1, h, self.day]
            else:
                self.rep[key] = [1, h, self.day]
        if tool == "run_sub_agent":
            self._sub(args, result, before, after)
        elif tool == "chat_with_sub_agent":
            if result.strip() and result.strip() == self.last_sub.strip():
                self.chat_echo += 1
        elif tool == "ai_web_search":
            self._search(result)
        elif tool == "send_email":
            self._send(args)
        elif tool == "read_email":
            self._read(result)
        elif tool == "get_operations_status":
            if re.search(r"fault|jam", result, re.I) and "No active" not in result:
                self.trig.add("fault")

    def _slot_size(self, slot):
        if slot in self.machine:
            return self.machine[slot][0]
        m = re.match(r"R(\d)", slot or "")
        if m:
            return "small" if int(m.group(1)) <= 2 else "large"
        return "?"

    def _sub(self, args, result, before, after):
        self.last_sub = result
        loaded = 0
        fails = []
        for line in result.splitlines():
            m = re.search(r"Loaded (\d+) units of (\S+) into (R\dC\d)", line)
            if m:
                loaded += int(m.group(1))
                self.stock_ok += 1
                self.slot_ok += 1
                self.sku_size[m.group(2)] = (self._slot_size(m.group(3)),
                                             "loaded %s d%d" % (m.group(3), self.day))
                if not self.example:
                    self.example = '{"op":"stock","slot":"%s","sku":"%s","qty":%s}' % (
                        m.group(3), m.group(2), m.group(1))
                continue
            m = re.search(r"Collected \$([\d.]+)", line)
            if m:
                self.collected += _f(m.group(1))
                continue
            m = re.search(r"Command (\{.*\}) failed: (.*)", line)
            if m:
                cmd, why = m.group(1), m.group(2).strip()
                op = re.search(r"'op': '(\w+)'", cmd)
                op = op.group(1) if op else "?"
                sku = re.search(r"'sku': '([^']+)'", cmd)
                m2 = re.search(r"expects (small|large), got (small|large)", why)
                if m2 and sku:
                    self.size_fail += 1
                    self.sku_size[sku.group(1)] = (m2.group(2), "rejected by %s slot d%d"
                                                   % (m2.group(1), self.day))
                if why.strip("'") == "slot":
                    self.fmt_fail += 1
                    why = "'slot' (used row/col)"
                if op == "set_price" and "is empty" in why:
                    continue
                fails.append("%s %s: %s" % (op, sku.group(1) if sku else "", why[:45]))
        if fails:
            self.trig.add("load")
            self.last_fail = ["d%d " % self.day + f for f in fails[:2]]
        elif "Loaded" in result or "Set " in result:
            self.last_fail = []
        drop = before - after
        if fails and drop > loaded:
            self.lost.append("d%d: storage fell %d, only %d loaded" % (self.day, drop, loaded))

    def _search(self, result):
        for block in re.split(r"\n\s*\n", result):
            lines = block.strip().splitlines()
            if not lines:
                continue
            head = re.sub(r"^\d+\.\s*", "", lines[0]).strip()
            m = re.search(r"at ([\w.\-+]+@[\w.\-]+\.\w+)", block)
            if "base " in block and "/day" in block and not m:
                self.demand_list = re.findall(r"([A-Z][\w'&.\- ]+?) \(base ([\d.]+)/day\)", block)
                self.demand = ", ".join("%s %s" % (n.split()[0] + (" " + n.split()[1] if len(n.split()) > 1 and
                                                                    n.split()[0] in ("Red", "Diet", "Bottled")
                                                                    else ""), r)
                                        for n, r in self.demand_list)[:170]
                continue
            if m:
                em = m.group(1).lower()
                self.listed.add(em)
                s = self._supp(em)
                s["name"] = head[:18]
                hint = []
                m2 = re.search(r"Lead time ([\d\-]+) days", block)
                if m2:
                    hint.append("lead " + m2.group(1) + "d")
                m2 = re.search(r"free shipping over \$([\d.]+)", block)
                if m2:
                    self.free_thr = m2.group(1)
                m2 = re.search(r"categories: ([\w, ]+)\.", block)
                if m2:
                    hint.append(m2.group(1)[:40])
                if re.search(r"near-retail|inflated", block):
                    hint.append("PRICEY")
                s["hint"] = "; ".join(hint)

    def _send(self, args):
        to = str(args.get("to", "")).lower().strip()
        subj = str(args.get("subject", ""))
        body = str(args.get("body", ""))
        complaint = bool(re.search(r"missing|wrong|discrepan|refund|short|not received|undeliver",
                                   subj + " " + body[:300], re.I))
        if complaint:
            self.trig.add("complaint")
        self.sent.append({"to": to, "subj": _norm_subj(subj), "acct": self.acct.lower() in body.lower(),
                          "day": self.day, "out": None, "complaint": complaint, "raw": subj[:40]})
        self.sent = self.sent[-60:]

    def _read(self, result):
        m = re.search(r"ID:\s*(\d+)", result)
        eid = m.group(1) if m else None
        if eid and eid in self.read_ids:
            return
        if eid:
            self.read_ids.add(eid)
        m = re.search(r"From:\s*(\S+)", result)
        sender = m.group(1).lower() if m else ""
        m = re.search(r"Subject:\s*(.*)", result)
        subj = m.group(1).strip() if m else ""
        body = result.split("Subject:", 1)[-1]
        out = None
        if "not reachable" in body or "Undeliverable" in subj:
            mm = re.search(r"address (\S+) is not reachable", body)
            if mm:
                self.unreach.add(mm.group(1))
            return
        if re.search(r"bank account|account number|password|routing", body, re.I):
            listed = sender in self.listed
            self.asks.append((self.day, sender, listed))
            self.asks = self.asks[-12:]
        is_sup = (sender in self.sup or sender in self.listed or
                  re.search(r"charged account|Current terms|Delivery completed|out of stock|"
                            r"still need the following|purchase order", body))
        if not is_sup:
            return
        s = self._supp(sender)
        m = re.search(r"still need the following[^:]*:\s*([^\n.]+)", body)
        if m:
            fields = m.group(1).strip()
            if "account" in fields:
                s["ask"] += 1
                self.trig.add("acct")
            out = "asked for: " + fields[:40]
        m = re.search(r"charged account \S+ \$([\d.]+)", body)
        if m:
            s["charged"] += 1
            s["spent"] += _f(m.group(1))
            self.n_charged += 1
            pd = re.search(r"Promised delivery: (\S+)", body)
            gs = re.search(r"Goods subtotal: \$([\d.]+)", body)
            sh = re.search(r"Shipping: \$([\d.]+)", body)
            if sh:
                self.ship_paid += _f(sh.group(1))
            if gs:
                self.max_goods = max(self.max_goods, _f(gs.group(1)))
            out = "CHARGED $%s deliv %s" % (m.group(1).rstrip("."), pd.group(1)[5:10] if pd else "?")
            ex = re.search(r"excluded as temporarily out of stock:\s*([^\n]+)", body)
            if ex:
                s["oos"] += 1
                out += " (OOS dropped: %s)" % ex.group(1).strip().rstrip(".")[:30]
        elif "temporarily out of stock" in body:
            s["oos"] += 1
            out = "out of stock"
        if "could not identify the purchase order" in body:
            s["nopo"] += 1
            out = "could not identify PO"
        if "Current terms" in body:
            out = out or "sent price list"
            for nm, pr, moq, cp in re.findall(
                    r"-\s*(.+?):\s*\$([\d.]+)/unit\s*\|\s*MOQ (\d+)\s*\|\s*case pack (\d+)", body):
                s["cat"][nm.strip()] = (pr, moq, cp)
        m = re.match(r"Delivery completed for (\S+)", subj)
        if m:
            s["deliv"] += 1
            its = re.findall(r"-\s*(\d+) x (.+)", body)
            for _, nm in its:
                self.carried.add(_key(nm))
            iss = re.search(r"Reported issues:\s*(.*)", body)
            if iss and "wrong_ship" in iss.group(1):
                s["wrong"] += 1
            self.deliv.append("d%d %s: %s%s" % (self.day, s["name"][:10],
                                                ", ".join("%s %s" % (a, b.strip()[:12]) for a, b in its)[:70],
                                                " ISSUE " + iss.group(1)[:40] if iss else ""))
            self.deliv = self.deliv[-4:]
            return
        if out:
            s["last"] = "d%d %s" % (self.day, out)
            ns = _norm_subj(subj)
            for e in reversed(self.sent):
                if e["to"] == sender and e["out"] is None and (e["subj"] == ns or e["subj"] in ns or ns in e["subj"]):
                    e["out"] = out
                    if e["complaint"] and out.startswith("CHARGED"):
                        self.complaint_charges.append("d%d '%s' -> %s" % (self.day, e["raw"][:30], out[:16]))
                    break

    # ------------------------------------------------------------ brief
    def brief(self) -> str:
        try:
            return self._brief()
        except Exception:
            return "REGISTER: unavailable this step."

    def _rates(self):
        days = self.day_bd[-7:]
        n = max(1, len(days))
        r = {}
        for bd in days:
            for k, v in bd.items():
                r[k] = r.get(k, 0) + v
        return {k: v / float(n) for k, v in r.items()}, len(days)

    def _brief(self):
        S = []
        head = "=== REGISTER day %d | bank $%s | machine cash $%.2f ===" % (
            self.day, "%.2f" % self.bank if self.bank is not None else "?", self.cash or 0.0)
        empty = {"small": [], "large": []}
        mq = {}
        for slot, v in sorted(self.machine.items()):
            if v[2] == 0:
                empty.setdefault(v[0], []).append(slot)
            if v[1]:
                mq[v[1]] = mq.get(v[1], 0) + v[2]
        rates, nd = self._rates()

        # 1 blockers
        b = []
        if self.fault:
            b.append("Machine global fault active: no sales until repair_machine.")
        if self.jams:
            b.append("Jammed slots: %s (clear_jam with slot)." % self.jams)
        for sku, q in self.storage.items():
            sz = self.sku_size.get(sku, ("?",))[0]
            if sz in ("small", "large"):
                es = empty.get(sz, [])
                b.append("Storage %s x%d (fits %s only): empty %s slots now: %s" %
                         (sku, q, sz.upper(), sz, ",".join(es) if es else "NONE (top up partial %s slots)" % sz))
            else:
                b.append("Storage %s x%d (size unknown yet)" % (sku, q))
        sold_out = [k for k, r in rates.items() if r > 0 and mq.get(k, 0) == 0 and self.storage.get(k, 0) == 0]
        if sold_out:
            b.append("Sold out (0 in machine & storage): %s; open orders: %d" %
                     (", ".join(sold_out)[:90], len(self.outstanding)))
        if self.machine and not self.storage and (empty["small"] or empty["large"]):
            b.append("Empty slots earning $0: small %s | large %s" %
                     (",".join(empty["small"]) or "-", ",".join(empty["large"]) or "-"))
        if not mq and not self.storage and not self.outstanding:
            b.append("Machine, storage, orders all empty -> $0/day revenue vs $2/day fee.")
        for em, s in self.sup.items():
            if s["last"] and "asked for" in s["last"]:
                b.append("%s last reply: %s" % (em, s["last"][:60]))
        for f in self.last_fail:
            b.append("Last command failure: " + f)
        S.append(("[1 BLOCKERS NOW]", b[:8] or ["none detected"], 9))

        # 2 stocking
        st = []
        st.append('format: "slot":"RxCy" commands loaded %d times; row/col keys failed %d times (error \'slot\').'
                  % (self.slot_ok, self.fmt_fail))
        if self.example:
            st.append("worked this run: " + self.example)
        lg = [_short(k) for k, v in self.sku_size.items() if v[0] == "large"]
        sm = [_short(k) for k, v in self.sku_size.items() if v[0] == "small"]
        st.append("LARGE R3-R4 only: %s | SMALL R1-R2 only: %s | size rejections so far: %d" %
                  (", ".join(lg) or "?", ", ".join(sm) or "?", self.size_fail))
        cap = {"small": [0, 0], "large": [0, 0]}
        for v in self.machine.values():
            if v[0] in cap:
                cap[v[0]][0] += v[2]
                cap[v[0]][1] += v[3] or 0
        if self.machine:
            st.append("fill: small %d/%d units, large %d/%d units" %
                      (cap["small"][0], cap["small"][1], cap["large"][0], cap["large"][1]))
        for x in self.lost[-1:]:
            st.append(x)
        if self.chat_echo:
            st.append("chat_with_sub_agent echoed last report %dx (does not act/answer)." % self.chat_echo)
        if "load" in self.trig and not self.example:
            st.append(PAST_LOAD)
        S.append(("[2 STOCKING FACTS]", st, 8))

        # 3 sales
        sl = []
        if nd:
            tot7 = sum(v[1] for v in list(sorted(self.daily.items()))[-7:][i][1:] for i in []) if False else 0
            last = sorted(self.daily.items())[-7:]
            tot7 = sum(v[1] for k, v in last)
            sl.append("revenue last %d days $%.2f ($%.2f/day); all %d days $%.2f; cash collected $%.2f" %
                      (len(last), tot7, tot7 / max(1, len(last)), len(self.daily),
                       sum(v[1] for v in self.daily.values()), self.collected))
            rows = []
            for k in sorted(set(list(rates.keys()) + list(mq.keys()) + list(self.storage.keys())),
                            key=lambda x: -rates.get(x, 0)):
                r = rates.get(k, 0.0)
                have = mq.get(k, 0) + self.storage.get(k, 0)
                cov = ("%.0fd" % (have / r)) if r > 0 else "-"
                rows.append("%s %.1f/d have %d cover %s" % (_short(k), r, have, cov))
            if rows:
                sl.append("per sku (7d): " + "; ".join(rows)[:300])
        ph = []
        for sku, h in self.price_hist.items():
            parts = ["$%s:%.1f/d(%dd)" % (p, e[0] / float(max(1, e[1])), e[1]) for p, e in h.items()]
            ph.append("%s %s" % (_short(sku), " ".join(parts)))
        if ph:
            sl.append("units/day by price: " + "; ".join(ph)[:260])
        if self.demand:
            sl.append("search demand base/day: " + self.demand)
            never = [n for n, r in self.demand_list if _key(n) not in self.carried]
            if never:
                sl.append("top-demand items never stocked this run: " + ", ".join(never)[:100])
        S.append(("[3 SALES]", sl or ["no closed day yet"], 7))

        # 4 orders
        o = []
        w = [e for e in self.sent if e["out"]]

        def summ(xs):
            c = {}
            for x in xs:
                k = "CHARGED" if x.startswith("CHARGED") else ("asked for fields" if x.startswith("asked") else x)
                c[k] = c.get(k, 0) + 1
            return ", ".join("%s x%d" % (k, v) for k, v in c.items()) or "-"
        o.append("emails with '%s': %s | without: %s" % (
            self.acct, summ([e["out"] for e in w if e["acct"]]), summ([e["out"] for e in w if not e["acct"]])))
        for e in w[-2:]:
            o.append("d%d %s acct=%s -> %s" % (e["day"], e["to"].split("@")[0][:14],
                                               "Y" if e["acct"] else "N", e["out"][:60]))
        if self.n_charged:
            o.append("%d orders charged; shipping paid $%.2f; largest goods subtotal $%.2f; free shipping over $%s per search"
                     % (self.n_charged, self.ship_paid, self.max_goods, self.free_thr or "?"))
        o.append("open orders: %d %s" % (len(self.outstanding),
                                         ("| " + "; ".join(x[:60] for x in self.outstanding[:2])) if self.outstanding else ""))
        for c in self.complaint_charges[-1:]:
            o.append("complaint email billed as new order: " + c)
        if "acct" in self.trig and not any(e["acct"] and e["out"] and e["out"].startswith("CHARGED") for e in w):
            o.append(PAST_ACCT)
        if "complaint" in self.trig and not self.complaint_charges:
            o.append(PAST_COMPLAINT)
        S.append(("[4 ORDERS]", o, 6))

        # 5 suppliers
        sp = []
        for em, s in list(self.sup.items())[:5]:
            cat = ", ".join("%s $%s" % (k.split()[0][:8], v[0]) for k, v in list(s["cat"].items())[:6])
            sp.append("%s <%s>%s: charged %d ($%.0f) deliv %d wrong-ship %d OOS %d. %s" %
                      (s["name"][:14], em, "" if em in self.listed else " NOT-IN-SEARCH", s["charged"],
                       s["spent"], s["deliv"], s["wrong"], s["oos"], (cat or s["hint"])[:70]))
        for d in self.deliv[-2:]:
            sp.append("delivered " + d)
        S.append(("[5 SUPPLIERS]", sp or ["none found yet"], 2))

        # 6 incidents & account requests
        ic = []
        if self.service:
            ic.append("service costs: " + "; ".join(self.service[-3:]))
        if "fault" in self.trig and not self.service:
            ic.append(PAST_FAULT)
        if self.asks:
            seen = {}
            for d, snd, li in self.asks:
                seen[snd] = (d, li)
            for snd, (d, li) in list(seen.items())[-3:]:
                ch = self.sup.get(snd, {}).get("charged", 0)
                ic.append("%s asked for account d%d: %s; orders it charged %d" %
                          (snd, d, "supplier in search results" if li else "NOT in search results", ch))
        if self.unreach:
            ic.append("unreachable: " + ", ".join(self.unreach))
        S.append(("[6 INCIDENTS & ACCOUNT REQUESTS]", ic or ["none"], 3))

        # 7 repeats
        reps = sorted([(v[0], k) for k, v in self.rep.items() if v[0] >= 3], reverse=True)[:2]
        S.append(("[7 REPEATED CALLS, SAME RESULT]",
                  ["%s: %dx identical" % (k[:50], c) for c, k in reps] or ["none"], 1))

        # assemble with trimming
        secs = [[t, ["- " + x[:300] for x in ls], p] for t, ls, p in S]

        def total():
            return len(head) + sum(len(t) + 1 + sum(len(x) + 1 for x in ls) for t, ls, p in secs)
        guard = 0
        while total() > 3900 and guard < 80:
            guard += 1
            cand = [s for s in secs if len(s[1]) > 1]
            if not cand:
                break
            cand.sort(key=lambda s: s[2])
            cand[0][1].pop()
        out = [head]
        for t, ls, p in secs:
            out.append(t)
            out += ls
        return "\n".join(out)[:3990]
