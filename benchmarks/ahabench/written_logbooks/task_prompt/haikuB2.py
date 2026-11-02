import json
import re

_HEADS = {
    "CURRENT TIME", "SCENARIO", "PRIORITY ALERTS", "MONEY", "KEY BUSINESS DETAILS",
    "MARKET SNAPSHOT", "PEER DIRECTORY", "OPEN TRADE OFFERS", "ARENA BULLETINS",
    "STORAGE INVENTORY", "MACHINE STATE", "OUTSTANDING ORDERS", "UNREAD EMAILS",
    "LATEST DAILY SUMMARY", "RECENT TRANSACTIONS", "RECENT DECISION HISTORY",
    "PLANNER NOTE", "LAST TOOL RESULT",
}

_RE_DAY = re.compile(r"\(day (\d+)\)")
_RE_BANK = re.compile(r"Bank balance:\s*\$(-?[\d,]+(?:\.\d+)?)")
_RE_CASH = re.compile(r"Cash in machine:\s*\$(-?[\d,]+(?:\.\d+)?)")
_RE_SUM = re.compile(
    r"Latest closed day:\s*(\S+)\s*\|.*?units=(\d+)\s*\|\s*revenue=\$(-?[\d,]+(?:\.\d+)?)"
)
_RE_TX = re.compile(r"^-\s*(\S+)\s*\|\s*([a-z_]+)\s*\|\s*([+-]?[\d,]+(?:\.\d+)?)\s*\|")
_RE_SLOT = re.compile(r"^-\s*R\d+C\d+\s*\[(small|large)\]\s*(.*)$")
_RE_LOAD = re.compile(r"Loaded (\d+) units")
_RE_COLL = re.compile(r"Collected \$(\d+(?:\.\d+)?) in cash")


def _num(s):
    try:
        return float(str(s).replace(",", ""))
    except Exception:
        return None


def _block(text, name):
    out = []
    grab = False
    for ln in text.splitlines():
        s = ln.strip()
        if not grab:
            if s == name:
                grab = True
            continue
        if s in _HEADS or s.startswith("REMINDER:"):
            break
        if s:
            out.append(s)
    return out


class Logbook:
    def __init__(self) -> None:
        self.n = 0
        self.day = 0
        self.last_day = None
        self.start_bank = None
        self.bank = None
        self.cash = None
        self.stocked = 0
        self.total_slots = 0
        self.empty_streak = 0
        self.storage = "?"
        self.orders = []
        self.unread = ""
        self.sum_days = {}
        self.last_sum = None
        self.flows = {}
        self.tx_seen = set()
        self.tools = {}
        self.recent = []
        self.last_key = None
        self.same_run = 0
        self.errs = {}
        self.last_err = ""
        self.units_loaded = 0
        self.cash_total = 0.0
        self.emails = {}
        self.prev_res = None
        self.same_res = 0
        self.last_ops = ""

    # ---------- public API ----------
    def observe(self, prompt: str, last_action: dict | None) -> None:
        try:
            self.n += 1
            text = prompt if isinstance(prompt, str) else str(prompt)
            res = "\n".join(_block(text, "LAST TOOL RESULT"))
            if res == self.prev_res:
                self.same_res += 1
            else:
                self.same_res = 1
                self.prev_res = res
            self._note_action(last_action, res)
            self._note_state(text)
        except Exception:
            pass

    def brief(self) -> str:
        try:
            s = self._render()
        except Exception:
            s = "LOGBOOK unavailable this step."
        return s[:3900]

    # ---------- internals ----------
    def _note_action(self, act, res):
        if not isinstance(act, dict):
            return
        tool = str(act.get("tool_name", "?"))
        args = act.get("arguments")
        if not isinstance(args, dict):
            args = {}
        try:
            akey = tool + " " + json.dumps(args, sort_keys=True)
        except Exception:
            akey = tool + " " + str(args)
        akey = akey[:140]
        if akey == self.last_key:
            self.same_run += 1
        else:
            self.last_key = akey
            self.same_run = 1
        self.tools[tool] = self.tools.get(tool, 0) + 1
        self.recent.append("d%d %s" % (self.day, akey[:100]))
        self.recent = self.recent[-6:]

        if tool == "send_email":
            to = str(args.get("to", "?"))[:60]
            self.emails[to] = self.emails.get(to, 0) + 1

        for ln in res.splitlines():
            if "failed:" in ln:
                k = re.sub(r"\d+", "#", ln.strip())[:150]
                if k in self.errs or len(self.errs) < 40:
                    self.errs[k] = self.errs.get(k, 0) + 1
                self.last_err = ln.strip()[:160]
            for m in _RE_LOAD.finditer(ln):
                self.units_loaded += int(m.group(1))
            for m in _RE_COLL.finditer(ln):
                v = _num(m.group(1))
                if v:
                    self.cash_total += v

        if tool == "get_operations_status":
            self.last_ops = " ".join(res.split())[:120]

    def _note_state(self, text):
        m = _RE_DAY.search(text)
        if m:
            self.day = int(m.group(1))

        mb = _RE_BANK.search(text)
        if mb:
            self.bank = _num(mb.group(1))
            if self.start_bank is None:
                self.start_bank = self.bank
        mc = _RE_CASH.search(text)
        if mc:
            self.cash = _num(mc.group(1))

        total = 0
        stocked = 0
        for ln in _block(text, "MACHINE STATE"):
            mm = _RE_SLOT.match(ln)
            if mm:
                total += 1
                if not mm.group(2).lower().startswith("empty"):
                    stocked += 1
        if total:
            self.total_slots = total
            self.stocked = stocked

        if self.day != self.last_day:
            self.last_day = self.day
            if stocked == 0:
                self.empty_streak += 1
            else:
                self.empty_streak = 0

        sto = _block(text, "STORAGE INVENTORY")
        if not sto or (len(sto) == 1 and "empty" in sto[0].lower()):
            self.storage = "empty"
        else:
            self.storage = "; ".join(sto)[:150]

        self.orders = [x[1:].strip() for x in _block(text, "OUTSTANDING ORDERS")
                       if x.startswith("-")][:5]

        ub = _block(text, "UNREAD EMAILS")
        if ub and not ub[0].lower().startswith("none"):
            self.unread = "; ".join(ub)[:150]
        else:
            self.unread = ""

        for ln in _block(text, "LATEST DAILY SUMMARY"):
            ms = _RE_SUM.search(ln)
            if ms:
                day_s = ms.group(1)
                units = int(ms.group(2))
                rev = _num(ms.group(3)) or 0.0
                if day_s not in self.sum_days and len(self.sum_days) > 400:
                    self.sum_days = {}
                self.sum_days[day_s] = (units, rev)
                self.last_sum = (day_s, units, rev)

        for ln in _block(text, "RECENT TRANSACTIONS"):
            mt = _RE_TX.match(ln)
            if not mt:
                continue
            key = ln[:160]
            if key in self.tx_seen:
                continue
            if len(self.tx_seen) > 5000:
                self.tx_seen = set()
            self.tx_seen.add(key)
            typ = mt.group(2)
            amt = _num(mt.group(3))
            if amt is not None:
                self.flows[typ] = self.flows.get(typ, 0.0) + amt

    def _render(self):
        L = []
        bank = self.bank
        if bank is not None:
            delta = bank - self.start_bank if self.start_bank is not None else 0.0
            cash = ("%.2f" % self.cash) if self.cash is not None else "?"
            L.append("LEDGER: day %d | bank $%.2f (%+.2f since start) | cash in machine $%s"
                     % (self.day, bank, delta, cash))
        L.append("MACHINE: %d/%d slots stocked | empty for %d consecutive day(s)"
                 % (self.stocked, self.total_slots, self.empty_streak))
        L.append("STORAGE: %s" % self.storage)
        if self.sum_days:
            tu = sum(v[0] for v in self.sum_days.values())
            tr = sum(v[1] for v in self.sum_days.values())
            ls = self.last_sum
            L.append("SALES: %d days reported, units %d, revenue $%.2f | last day %s units=%d rev=$%.2f"
                     % (len(self.sum_days), tu, tr, ls[0], ls[1], ls[2]))
        else:
            L.append("SALES: no daily summary yet")
        L.append("SUB-AGENT: units loaded %d | cash collected $%.2f"
                 % (self.units_loaded, self.cash_total))
        if self.flows:
            parts = ["%s %+.2f" % (k, v) for k, v in sorted(self.flows.items())][:8]
            L.append("TRANSACTION TOTALS (seen): " + " | ".join(parts))
        if self.orders:
            L.append("OPEN ORDERS: " + " || ".join(self.orders)[:300])
        else:
            L.append("OPEN ORDERS: none")
        if self.unread:
            L.append("UNREAD: " + self.unread)
        if self.last_ops:
            L.append("OPS: " + self.last_ops)

        W = []
        if self.same_run >= 3:
            W.append("Same action repeated %d times in a row. Change approach." % self.same_run)
        if self.same_res >= 3:
            W.append("Last tool result identical %d times in a row. Waiting on it will not change it."
                     % self.same_res)
        top = sorted(self.errs.items(), key=lambda kv: -kv[1])[:3]
        for k, c in top:
            W.append("Command error x%d: %s" % (c, k[:110]))
        keys = " ".join(self.errs.keys())
        if "'slot'" in keys:
            W.append("Commands using row/col keys failed with KeyError 'slot'. Use "
                     "{\"op\":\"stock\",\"slot\":\"R1C1\",...} format.")
        if "expects small" in keys or "expects large" in keys:
            W.append("Slot size must match the machine: small slots are R1-R2, large are R3-R4.")
        if "is empty" in keys:
            W.append("set_price on an empty slot fails. Stock the slot first, then set price.")
        if self.empty_streak >= 2 and self.storage != "empty":
            W.append("Storage holds stock while the machine is empty. Load it with run_sub_agent.")
        if self.empty_streak >= 3 and self.storage == "empty":
            W.append("Machine empty %d days and storage empty. Check open orders and replies first."
                     % self.empty_streak)
        for to, c in self.emails.items():
            if c >= 4:
                W.append("Emailed %s %d times. Check for an existing reply before writing again."
                         % (to, c))
        if self.tools.get("sub_agent_specs", 0) >= 3:
            W.append("sub_agent_specs returns the same template each time. It does not explain errors.")

        out = ["LOGBOOK (this run only)"] + L
        if W:
            out.append("WARNINGS:")
            out.extend("- " + w for w in W[:8])
        if self.recent:
            out.append("RECENT ACTIONS:")
            out.extend("- " + r for r in self.recent[-4:])
        return "\n".join(out)
