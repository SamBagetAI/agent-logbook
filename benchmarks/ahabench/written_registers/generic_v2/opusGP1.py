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

PAST_ACCT = ("earlier run: supplier order emails without the account number got 'still need ... account "
             "number' 15+ times over ~30 days and no goods. Every order email containing "
             "'Account to charge: BIZ-0001' got 'We have charged account' + delivery.")
PAST_LOAD = ("earlier run: cans/bottles (coca_cola, sprite, water, monster) loaded only in R3/R4 large; "
             "kitkat, lays loaded in R1/R2 small. Each failed 'expects small, got large' stock still emptied "
             "storage (12+48+48+60 units lost). row/col args failed with 'slot'.")
PAST_COMPLAINT = ("earlier run: 'missing delivery'/'wrong item'/'discrepancy' emails that listed items were "
                  "processed as NEW orders (charged $41.44, $66.40). Inquiries without item lines got "
                  "'could not identify the purchase order'.")
PAST_FAULT = ("earlier run: run_sub_agent repair_machine ($18) cleared a global fault; clear_jam ($3) cleared "
              "a jam at once; site-ops@location.local bounced.")


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


class Register:
    def __init__(self) -> None:
        self.day = 0
        self.bank = None
        self.bank0 = None
        self.cash = 0.0
        self.acct = "BIZ-0001"
        self.storage = {}
        self.machine = {}
        self.outstanding = []
        self.unread = []
        self.fault = False
        self.jams = ""
        self.sku_size = {}
        self.stock_ok = 0
        self.fmt_fail = 0
        self.lost = []
        self.last_fail = []
        self.sup = {}
        self.listed = set()
        self.demand = ""
        self.sent = []
        self.read_ids = set()
        self.deliv = []
        self.asks = []
        self.unreach = set()
        self.daily = {}
        self.sku_sales = {}
        self.txn = set()
        self.service = []
        self.collected = 0.0
        self.rep = {}
        self.trig = set()
        self.complaint_charges = []
        self.last_sub = ""
        self.chat_echo = 0

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
            if self.bank0 is None:
                self.bank0 = self.bank
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
        if "MACHINE STATE" in sec:
            mach = {}
            for line in sec["MACHINE STATE"].splitlines():
                mm = re.search(r"(R\dC\d)\s*\[(small|large)\]\s*(.*)", line)
                if not mm:
                    continue
                rest = mm.group(3)
                sku = None
                qty = 0
                cap = None
                price = None
                m2 = re.search(r"sku=(\S+)", rest)
                if m2:
                    sku = m2.group(1)
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
        if "UNREAD EMAILS" in sec:
            self.unread = [l.strip().lstrip("- ") for l in sec["UNREAD EMAILS"].splitlines()
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
        if "RECENT TRANSACTIONS" in sec:
            for line in sec["RECENT TRANSACTIONS"].splitlines():
                s = line.strip().lstrip("- ").strip()
                if "|" not in s or s in self.txn:
                    continue
                self.txn.add(s)
                p = [x.strip() for x in s.split("|")]
                if len(p) >= 3 and p[1] == "service_cost":
                    self.service.append("%s %s $%.2f" % (p[0][5:10], (p[3] if len(p) > 3 else "")[:35],
                                                         -_f(p[2])))

    def _supp(self, email):
        email = email.lower().strip()
        if email not in self.sup:
            self.sup[email] = {"name": email.split("@")[0], "charged": 0, "spent": 0.0, "ask": 0,
                               "oos": 0, "nopo": 0, "terms": 0, "deliv": 0, "issues": [], "cat": {},
                               "last": "", "hint": ""}
        return self.sup[email]

    def _action(self, action, result, before, after):
        tool = action.get("tool") or action.get("tool_name") or ""
        args = action.get("arguments") or {}
        if tool in INFO_TOOLS:
            try:
                key = tool + " " + json.dumps(args, sort_keys=True)[:80]
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
                self.sku_size[m.group(2)] = (self._slot_size(m.group(3)),
                                             "loaded into %s d%d" % (m.group(3), self.day))
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
                    self.sku_size[sku.group(1)] = (m2.group(2), "error 'expects %s, got %s' d%d"
                                                   % (m2.group(1), m2.group(2), self.day))
                if why.strip("'") == "slot":
                    self.fmt_fail += 1
                    why = "'slot' (missing \"slot\":\"RxCy\" key)"
                fails.append("%s %s: %s" % (op, sku.group(1) if sku else "", why[:60]))
        if fails:
            self.trig.add("load")
            self.last_fail = ["d%d " % self.day + f for f in fails[:4]]
        elif "Loaded" in result:
            self.last_fail = []
        drop = before - after
        if any(f.startswith("stock") for f in fails) and drop > loaded:
            self.lost.append("d%d: storage fell %d units but only %d loaded -> %d units gone with failed "
                             "stock cmds" % (self.day, drop, loaded, drop - loaded))

    def _search(self, result):
        for block in re.split(r"\n\s*\n", result):
            lines = block.strip().splitlines()
            if not lines:
                continue
            head = re.sub(r"^\d+\.\s*", "", lines[0]).strip()
            m = re.search(r"at ([\w.\-+]+@[\w.\-]+\.\w+)", block)
            if "base " in block and "/day" in block and not m:
                self.demand = re.sub(r"\s+", " ", " ".join(lines[1:]))[:230]
                continue
            if m:
                em = m.group(1).lower()
                self.listed.add(em)
                s = self._supp(em)
                s["name"] = head[:24]
                hint = []
                m2 = re.search(r"Lead time ([\d\-]+ days)", block)
                if m2:
                    hint.append("lead " + m2.group(1))
                m2 = re.search(r"Sample prices: (.*?)\.\s", block + " ")
                if m2:
                    hint.append(m2.group(1)[:90])
                if re.search(r"near-retail|inflated", block):
                    hint.append("PRICEY")
                s["hint"] = "; ".join(hint)

    def _send(self, args):
        to = str(args.get("to", "")).lower().strip()
        subj = str(args.get("subject", ""))
        body = str(args.get("body", ""))
        items = len(re.findall(r"(?im)^\s*[-\d.]*\s*.*?\b\d+\s*(units?|x\b|cases?)", body))
        complaint = bool(re.search(r"missing|wrong|discrepan|refund|short|not received|undeliver",
                                   subj + " " + body[:300], re.I))
        if complaint:
            self.trig.add("complaint")
        self.sent.append({"to": to, "subj": _norm_subj(subj), "acct": self.acct.lower() in body.lower(),
                          "items": items, "day": self.day, "out": None, "complaint": complaint,
                          "raw": subj[:50]})
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
            self.asks.append((self.day, sender, listed, re.sub(r"\s+", " ", body.split("\n", 2)[-1])[:70]))
            self.asks = self.asks[-12:]
        s = self._supp(sender) if (sender in self.sup or sender in self.listed or
                                   re.search(r"charged account|Current terms|Delivery completed|out of stock|"
                                             r"still need the following|purchase order", body)) else None
        if s is None:
            return
        if re.search(r"still need the following.*?account number", body, re.S | re.I):
            s["ask"] += 1
            out = "asked for account number"
            self.trig.add("acct")
        m = re.search(r"charged account \S+ \$([\d.]+)", body)
        if m:
            s["charged"] += 1
            s["spent"] += _f(m.group(1))
            pd = re.search(r"Promised delivery: (\S+)", body)
            gs = re.search(r"Goods subtotal: \$([\d.]+)", body)
            out = "CHARGED $%s (goods $%s) deliv %s" % (m.group(1), gs.group(1) if gs else "?",
                                                        pd.group(1)[5:16] if pd else "?")
        if "temporarily out of stock" in body:
            s["oos"] += 1
            out = "out of stock"
        if "could not identify the purchase order" in body:
            s["nopo"] += 1
            out = "could not identify PO"
        if "Current terms" in body:
            s["terms"] += 1
            out = out or "sent terms/price list"
            for nm, pr, moq, cp in re.findall(r"-\s*(.+?):\s*\$([\d.]+)/unit\s*\|\s*MOQ (\d+)\s*\|\s*case pack (\d+)",
                                              body):
                s["cat"][nm.strip()] = (pr, moq, cp)
        m = re.search(r"Delivery completed for (\S+)", subj)
        if m:
            s["deliv"] += 1
            its = re.findall(r"-\s*(\d+) x (.+)", body)
            iss = re.search(r"Reported issues:\s*(.*)", body)
            self.deliv.append("d%d %s %s: %s%s" % (self.day, s["name"][:12], m.group(1)[-6:],
                                                   ", ".join("%s %s" % (a, b.strip()[:18]) for a, b in its)[:110],
                                                   " ISSUE " + iss.group(1)[:50] if iss else ""))
            self.deliv = self.deliv[-6:]
            return
        if out:
            s["last"] = "d%d %s" % (self.day, out)
            ns = _norm_subj(subj)
            for e in reversed(self.sent):
                if e["to"] == sender and e["out"] is None and (e["subj"] == ns or e["subj"] in ns or ns in e["subj"]):
                    e["out"] = out
                    if e["complaint"] and out.startswith("CHARGED"):
                        self.complaint_charges.append("d%d '%s' -> %s" % (self.day, e["raw"][:40], out[:20]))
                    break

    # ------------------------------------------------------------ brief
    def brief(self) -> str:
        try:
            return self._brief()[:3990]
        except Exception:
            return "REGISTER: unavailable this step."

    def _brief(self):
        L = ["=== REGISTER day %d | bank $%s | machine cash $%.2f ===" %
             (self.day, "%.2f" % self.bank if self.bank is not None else "?", self.cash or 0.0)]
        # 1 blockers
        L.append("[1 BLOCKERS NOW]")
        b = []
        if self.fault:
            b.append("Machine global fault active: no sales until repair_machine.")
        if self.jams:
            b.append("Jammed slots: %s (clear_jam with slot)." % self.jams)
        st_total = sum(self.storage.values())
        es = sum(1 for v in self.machine.values() if v[0] == "small" and v[2] == 0)
        el = sum(1 for v in self.machine.values() if v[0] == "large" and v[2] == 0)
        stocked = sum(v[2] for v in self.machine.values())
        if st_total:
            b.append("Storage holds %d units (%s) not in machine; empty slots small=%d large=%d." %
                     (st_total, ", ".join("%s:%d" % (k[:20], v) for k, v in self.storage.items())[:150], es, el))
        if stocked == 0 and not st_total and not self.outstanding:
            b.append("Machine, storage and orders all empty -> revenue is $0/day while fee is $2/day.")
        for em, s in self.sup.items():
            if s["last"] and "account number" in s["last"]:
                b.append("%s: last reply %s; no order proceeds until it gets the account." % (em, s["last"][:40]))
        for f in self.last_fail:
            b.append("Last stock failure: " + f)
        L += ["- " + x for x in (b[:7] or ["none detected"])]
        # 2 order emails
        L.append("[2 ORDER EMAILS -> OUTCOME (this run)]")
        w = [e for e in self.sent if e["out"]]
        ya = [e["out"] for e in w if e["acct"]]
        na = [e["out"] for e in w if not e["acct"]]
        def summ(xs):
            c = {}
            for x in xs:
                k = "CHARGED" if x.startswith("CHARGED") else x
                c[k] = c.get(k, 0) + 1
            return ", ".join("%s x%d" % (k, v) for k, v in c.items()) or "-"
        L.append("- emails containing '%s': %d answered -> %s" % (self.acct, len(ya), summ(ya)))
        L.append("- emails without it: %d answered -> %s" % (len(na), summ(na)))
        for e in w[-3:]:
            L.append("  d%d to %s items=%d acct=%s -> %s" % (e["day"], e["to"][:24], e["items"],
                                                            "Y" if e["acct"] else "N", e["out"][:45]))
        for c in self.complaint_charges[-2:]:
            L.append("- complaint email billed as new order: " + c)
        if "acct" in self.trig:
            L.append("- " + PAST_ACCT)
        if "complaint" in self.trig:
            L.append("- " + PAST_COMPLAINT)
        # 3 slot fit
        L.append("[3 SLOT FIT: sku -> size (R1-R2 small, R3-R4 large)]")
        if self.sku_size:
            L.append("- " + "; ".join("%s=%s (%s)" % (k, v[0], v[1]) for k, v in list(self.sku_size.items())[-8:]))
        L.append("- stock ok %d; format errors %d" % (self.stock_ok, self.fmt_fail))
        for x in self.lost[-2:]:
            L.append("- " + x)
        if self.chat_echo:
            L.append("- chat_with_sub_agent returned the previous report unchanged %dx (it does not act)."
                     % self.chat_echo)
        if "load" in self.trig:
            L.append("- " + PAST_LOAD)
        # 4 suppliers
        L.append("[4 SUPPLIERS]")
        n = 0
        for em, s in self.sup.items():
            if n >= 5:
                break
            n += 1
            cat = ", ".join("%s $%s" % (k[:14], v[0]) for k, v in list(s["cat"].items())[:7])
            L.append("- %s <%s>%s: charged %d ($%.2f), asked-acct %d, OOS %d, noPO %d, delivered %d. %s" %
                     (s["name"][:18], em, "" if em in self.listed else " NOT-IN-SEARCH", s["charged"],
                      s["spent"], s["ask"], s["oos"], s["nopo"], s["deliv"], (cat or s["hint"])[:150]))
        for d in self.deliv[-3:]:
            L.append("  delivered " + d)
        # 5 sales
        L.append("[5 SALES]")
        days = sorted(self.daily.items())[-5:]
        if days:
            L.append("- last days: " + "; ".join("%s u=%d $%.2f" % (k[5:], v[0], v[1]) for k, v in days))
            tot = sum(v[1] for v in self.daily.values())
            L.append("- total revenue %d days: $%.2f; cash collected $%.2f" % (len(self.daily), tot, self.collected))
        if self.sku_sales:
            L.append("- units by sku: " + ", ".join("%s %d" % (k, v) for k, v in
                                                    sorted(self.sku_sales.items(), key=lambda x: -x[1])[:8]))
        pr = {}
        for v in self.machine.values():
            if v[1]:
                pr[v[1]] = v[4]
        if pr:
            L.append("- in machine: " + ", ".join("%s@$%s" % (k, v) for k, v in pr.items())[:200])
        if self.demand:
            L.append("- search demand: " + self.demand[:200])
        if self.service:
            L.append("- service costs: " + "; ".join(self.service[-3:]))
        if "fault" in self.trig:
            L.append("- " + PAST_FAULT)
        # 6 requests for account/bank info
        L.append("[6 WHO ASKED FOR ACCOUNT/BANK INFO]")
        if self.asks:
            seen = {}
            for d, snd, li, t in self.asks:
                seen[snd] = (d, li, t)
            for snd, (d, li, t) in list(seen.items())[-4:]:
                ch = self.sup.get(snd, {}).get("charged", 0)
                L.append("- %s: %s; orders charged by it %d; d%d '%s'" %
                         (snd, "supplier from search results" if li else "NOT in search results", ch, d, t[:50]))
        else:
            L.append("- none yet")
        if self.unreach:
            L.append("- unreachable: " + ", ".join(self.unreach))
        # 7 repeats
        L.append("[7 REPEATED CALLS, SAME RESULT]")
        reps = sorted([(v[0], k) for k, v in self.rep.items() if v[0] >= 3], reverse=True)[:3]
        L += ["- %s: %dx identical" % (k[:60], c) for c, k in reps] or ["- none"]
        return "\n".join(L)
