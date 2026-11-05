import re
import json

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_MONEY_RE = re.compile(r"-?\$?\s*(-?[\d,]+(?:\.\d+)?)")
_TERM_WORDS = ("$", "moq", "minimum", "case", "pack", "ship", "discount", "refund",
               "return", "lead", "deliver", "per unit", "unit", "price", "wholesale",
               "stock", "delay", "backorder", "short", "invoice", "total", "eta")
_PHISH_WORDS = ("password", "account number", "routing", "verify your", "wire",
                "gift card", "login", "credentials", "urgent payment", "bank details",
                "confirm your account", "ssn", "pin")
_ERR_WORDS = ("error", "invalid", "unknown", "failed", "not found", "cannot",
              "insufficient", "rejected", "unsupported", "missing")


def _clip(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 3] + "..."


def _num(s):
    try:
        return float(str(s).replace(",", "").replace("$", ""))
    except Exception:
        return None


def _section(text, name):
    lines = text.split("\n")
    out = []
    grab = False
    for ln in lines:
        st = ln.strip()
        if not grab:
            if st == name:
                grab = True
            continue
        if st and len(st) > 3 and st.upper() == st and not any(c.islower() for c in st) \
                and not st.startswith("-") and not st.startswith("{") and not st.startswith("}") \
                and re.match(r"^[A-Z][A-Z /]+$", st):
            break
        out.append(ln)
    return "\n".join(out).strip()


class Register:
    def __init__(self) -> None:
        self.day = 0
        self.bank = None
        self.cash = None
        self.worth_by_day = {}
        self.suppliers = {}       # email -> context
        self.supplier_order = []
        self.searches = []
        self.emails_read = {}     # id -> dict
        self.suspicious = []
        self.orders = []          # (day, to, subject, body)
        self.specs = ""
        self.last_good_cmds = ""
        self.sub_errors = []
        self.errors = []
        self.prices = {}          # slot -> price
        self.slot_sku = {}
        self.prev_slots = {}      # slot -> (sku, qty)
        self.sold = {}            # sku -> units
        self.sold_by_day = {}     # day -> units
        self.last_collect_day = None
        self.last_stock_day = None
        self.empty_slots = 0
        self.slot_count = 0
        self.storage_lines = []
        self.outstanding = ""
        self.daily_summaries = []
        self.last_sig = None
        self.repeat = 0
        self.steps = 0

    # ---------------- observe ----------------
    def observe(self, step: dict) -> None:
        try:
            self._observe(step or {})
        except Exception:
            pass

    def _observe(self, step):
        self.steps += 1
        action = step.get("action")
        result = str(step.get("result") or "")
        seen = str(step.get("seen") or "")
        try:
            self._parse_seen(seen)
        except Exception:
            pass
        if action:
            try:
                self._parse_action(action, result)
            except Exception:
                pass

    def _parse_seen(self, seen):
        m = re.search(r"\(day (\d+)\)", seen)
        if m:
            self.day = int(m.group(1))
        m = re.search(r"Bank balance:\s*\$?\s*(-?[\d,]+(?:\.\d+)?)", seen)
        if m:
            self.bank = _num(m.group(1))
        m = re.search(r"Cash in machine:\s*\$?\s*(-?[\d,]+(?:\.\d+)?)", seen)
        if m:
            self.cash = _num(m.group(1))
        if self.bank is not None:
            self.worth_by_day[self.day] = self.bank + (self.cash or 0.0)

        st = _section(seen, "STORAGE INVENTORY")
        self.storage_lines = [l.strip() for l in st.split("\n")
                              if l.strip() and l.strip().lower().rstrip(".") != "empty"][:12]

        self.outstanding = _section(seen, "OUTSTANDING ORDERS")

        ms = _section(seen, "MACHINE STATE")
        empty = 0
        count = 0
        cur = {}
        for ln in ms.split("\n"):
            mm = re.search(r"(R\d+C\d+)", ln)
            if not mm:
                continue
            count += 1
            slot = mm.group(1)
            rest = ln[mm.end():]
            sk = re.search(r"\]\s*([A-Za-z0-9_\-]+)", rest)
            sku = sk.group(1) if sk else None
            if sku is None or sku.lower() == "empty" or " empty" in rest.lower()[:20]:
                empty += 1
                cur[slot] = (None, 0)
                continue
            q = re.search(r"qty[=:]\s*(\d+)", rest) or re.search(r"(\d+)\s*/\s*\d+", rest)
            qty = int(q.group(1)) if q else None
            if qty == 0:
                empty += 1
            cur[slot] = (sku, qty)
            pm = re.search(r"price[=:]\s*\$?\s*([\d.]+)", rest)
            if pm:
                p = _num(pm.group(1))
                if p is not None:
                    self.prices[slot] = p
        # sales estimate from qty drops
        for slot, (sku, qty) in cur.items():
            prev = self.prev_slots.get(slot)
            if prev and prev[0] and prev[1] is not None and qty is not None:
                if sku == prev[0] and qty < prev[1]:
                    d = prev[1] - qty
                    self.sold[sku] = self.sold.get(sku, 0) + d
                    self.sold_by_day[self.day] = self.sold_by_day.get(self.day, 0) + d
                elif sku is None and prev[1] > 0 and qty == 0:
                    pass
        if cur:
            self.prev_slots = cur
            for slot, (sku, _) in cur.items():
                if sku:
                    self.slot_sku[slot] = sku
        self.empty_slots = empty
        self.slot_count = count

        ds = _section(seen, "LATEST DAILY SUMMARY")
        if ds and "no completed" not in ds.lower():
            c = _clip(ds, 220)
            if not self.daily_summaries or self.daily_summaries[-1][1] != c:
                self.daily_summaries.append((self.day, c))
                self.daily_summaries = self.daily_summaries[-4:]

    def _parse_action(self, action, result):
        tool = str(action.get("tool") or action.get("tool_name") or "")
        args = action.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        try:
            sig = tool + json.dumps(args, sort_keys=True)[:300]
        except Exception:
            sig = tool
        if tool != "wait_for_next_day" and sig == self.last_sig:
            self.repeat += 1
        else:
            self.repeat = 0
        self.last_sig = sig
        low = result.lower()
        if tool != "read_email" and tool != "ai_web_search" and any(w in low[:300] for w in _ERR_WORDS):
            self.errors.append("d%s %s: %s" % (self.day, tool, _clip(result, 140)))
            self.errors = self.errors[-4:]

        if tool == "ai_web_search":
            q = _clip(args.get("query", ""), 60)
            if q and q not in self.searches:
                self.searches.append(q)
                self.searches = self.searches[-8:]
            for m in _EMAIL_RE.finditer(result):
                em = m.group(0).lower()
                ctx = result[max(0, m.start() - 160): m.end() + 120]
                if em not in self.suppliers:
                    self.suppliers[em] = _clip(ctx, 230)
                    self.supplier_order.append(em)
        elif tool == "read_email":
            eid = args.get("id")
            frm = re.search(r"(?im)^\s*from:\s*(.+)$", result)
            subj = re.search(r"(?im)^\s*subject:\s*(.+)$", result)
            sender = _clip(frm.group(1), 60) if frm else ""
            if not sender:
                ems = _EMAIL_RE.findall(result)
                sender = ems[0] if ems else "?"
            terms = []
            for ln in result.split("\n"):
                l2 = ln.lower()
                if any(w in l2 for w in _TERM_WORDS) and len(ln.strip()) > 3 \
                        and not l2.strip().startswith(("from:", "to:", "subject:")):
                    terms.append(_clip(ln, 120))
                if len(terms) >= 5:
                    break
            phish = [w for w in _PHISH_WORDS if w in low]
            rec = {"day": self.day, "from": sender,
                   "subj": _clip(subj.group(1), 60) if subj else "",
                   "terms": terms, "phish": phish}
            self.emails_read[str(eid)] = rec
            if len(self.emails_read) > 30:
                k = list(self.emails_read.keys())[0]
                del self.emails_read[k]
            if phish:
                self.suspicious.append("id %s from %s (%s)" % (eid, sender, ",".join(phish[:3])))
                self.suspicious = self.suspicious[-4:]
        elif tool == "send_email":
            self.orders.append((self.day, _clip(args.get("to", ""), 50),
                                _clip(args.get("subject", ""), 50),
                                _clip(args.get("body", ""), 110),
                                _clip(result, 70)))
            self.orders = self.orders[-10:]
        elif tool == "sub_agent_specs":
            self.specs = _clip(result, 700)
        elif tool == "run_sub_agent":
            cmds = args.get("commands") or []
            bad = any(w in low for w in _ERR_WORDS)
            ok_any = False
            for c in cmds if isinstance(cmds, list) else []:
                if not isinstance(c, dict):
                    continue
                op = str(c.get("op", ""))
                slot = c.get("slot")
                if slot is None and "row" in c:
                    slot = "R%sC%s" % (c.get("row"), c.get("col"))
                if op == "collect_cash":
                    self.last_collect_day = self.day
                elif op == "stock":
                    self.last_stock_day = self.day
                    if slot is not None and c.get("sku"):
                        self.slot_sku[str(slot)] = str(c.get("sku"))
                elif op == "set_price" and slot is not None:
                    p = _num(c.get("price"))
                    if p is not None:
                        self.prices[str(slot)] = p
                ok_any = True
            if bad:
                self.sub_errors.append("d%s: %s" % (self.day, _clip(result, 180)))
                self.sub_errors = self.sub_errors[-3:]
            elif ok_any:
                try:
                    self.last_good_cmds = _clip(json.dumps(cmds[:3]), 260)
                except Exception:
                    pass

    # ---------------- brief ----------------
    def brief(self) -> str:
        try:
            return self._brief()[:4000]
        except Exception:
            return "Register: (unavailable this step)"

    def _brief(self):
        L = []
        w = self.worth_by_day.get(self.day)
        head = "REGISTER day %s/365" % self.day
        if w is not None:
            head += " | bank+cash $%.2f" % w
            past = [d for d in self.worth_by_day if d <= self.day - 7]
            if past:
                d0 = max(past)
                head += " (7d change %+.2f)" % (w - self.worth_by_day[d0])
        L.append(head)

        A = []
        if self.storage_lines and self.empty_slots > 0:
            A.append("%d empty slot(s) while storage has goods -> run_sub_agent stock + set_price." % self.empty_slots)
        if (self.cash or 0) > 0:
            since = (self.day - self.last_collect_day) if self.last_collect_day is not None else None
            if since is None or since >= 3:
                A.append("$%.2f cash in machine, last collected %s -> collect_cash (theft risk)."
                         % (self.cash, "never" if since is None else "%dd ago" % since))
        if not self.orders and self.day >= 1 and not self.storage_lines:
            A.append("No order sent yet: search suppliers, email an order to an exact address.")
        if self.orders and not self.storage_lines:
            lastd = self.orders[-1][0]
            out_none = (not self.outstanding) or self.outstanding.lower().startswith("none")
            if out_none and self.day - lastd >= 2:
                A.append("Storage empty and no outstanding orders; last email sent day %d -> check inbox / reorder." % lastd)
        if self.storage_lines == [] and self.slot_count and self.empty_slots >= self.slot_count // 2 and self.orders:
            A.append("Half+ of machine empty and storage empty: restock supply chain.")
        if self.repeat >= 2:
            A.append("Same action repeated %d times with no change -> try something different." % (self.repeat + 1))
        if self.sub_errors:
            A.append("Last sub-agent errors: " + self.sub_errors[-1])
        recent = [self.sold_by_day.get(d, 0) for d in range(max(1, self.day - 3), self.day + 1)]
        if self.day >= 8 and self.sold and sum(recent) == 0:
            A.append("No unit sales detected in last 3 days: check jams/outages, prices, stock.")
        if self.bank is not None and self.bank < 30:
            A.append("Bank low ($%.2f): avoid big orders, collect cash." % self.bank)
        if A:
            L.append("ALERTS:")
            L += ["- " + a for a in A]

        if self.specs:
            L.append("SUB-AGENT SPECS (cached): " + self.specs)
        else:
            L.append("Sub-agent specs not read yet (call sub_agent_specs once).")
        if self.last_good_cmds:
            L.append("Last accepted run_sub_agent cmds: " + self.last_good_cmds)

        if self.sold or self.slot_sku:
            parts = []
            for slot in sorted(self.slot_sku):
                sku = self.slot_sku[slot]
                p = self.prices.get(slot)
                parts.append("%s:%s%s" % (slot, sku, "@$%.2f" % p if p is not None else ""))
            if parts:
                L.append("SLOTS/PRICES: " + _clip(", ".join(parts), 400))
            if self.sold:
                s = sorted(self.sold.items(), key=lambda x: -x[1])
                L.append("EST UNITS SOLD (run): " + _clip(", ".join("%s=%d" % x for x in s), 300))
            last7 = sum(self.sold_by_day.get(d, 0) for d in range(self.day - 6, self.day + 1))
            L.append("Units sold last 7d: %d" % last7)

        if self.supplier_order:
            L.append("SUPPLIERS FOUND (exact addresses):")
            for em in self.supplier_order[-6:]:
                L.append("- %s :: %s" % (em, _clip(self.suppliers.get(em, ""), 150)))
        if self.searches:
            L.append("Searches done: " + _clip(" | ".join(self.searches), 220))

        termed = [(k, v) for k, v in self.emails_read.items() if v.get("terms") and not v.get("phish")]
        if termed:
            L.append("EMAIL TERMS READ:")
            for k, v in termed[-5:]:
                L.append("- #%s d%s %s: %s" % (k, v["day"], _clip(v["from"], 40),
                                               _clip(" / ".join(v["terms"]), 200)))
        if self.orders:
            L.append("EMAILS SENT:")
            for d, to, sj, body, res in self.orders[-5:]:
                L.append("- d%d to %s [%s] %s" % (d, to, sj, body))
        if self.suspicious:
            L.append("SUSPICIOUS (do not share info): " + "; ".join(self.suspicious))
        if self.daily_summaries:
            L.append("Daily summaries: " + " || ".join("d%d %s" % x for x in self.daily_summaries[-2:]))
        if self.errors:
            L.append("Recent tool errors: " + " || ".join(self.errors[-2:]))

        out = "\n".join(L)
        if len(out) > 4000:
            out = out[:3990] + "..."
        return out
