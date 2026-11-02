import json
import re

_HEADERS = [
    "CURRENT TIME", "SCENARIO", "PRIORITY ALERTS", "MONEY", "KEY BUSINESS DETAILS",
    "MARKET SNAPSHOT", "PEER DIRECTORY", "OPEN TRADE OFFERS", "ARENA BULLETINS",
    "STORAGE INVENTORY", "MACHINE STATE", "OUTSTANDING ORDERS", "UNREAD EMAILS",
    "LATEST DAILY SUMMARY", "RECENT TRANSACTIONS", "RECENT DECISION HISTORY",
    "PLANNER NOTE", "LAST TOOL RESULT", "REMINDER",
]
_HEADER_SET = set(_HEADERS)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_ERR_RE = re.compile(r"error|invalid|fail|rejected|not allowed|unknown tool|insufficient", re.I)


def _sections(text):
    out = {}
    cur = None
    buf = []
    for line in text.splitlines():
        s = line.strip()
        if s in _HEADER_SET:
            if cur is not None:
                out[cur] = "\n".join(buf).strip()
            cur = s
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf).strip()
    return out


def _money(text):
    try:
        return float(text.replace(",", ""))
    except Exception:
        return None


class Logbook:
    def __init__(self) -> None:
        self.day = None
        self.bank = None
        self.cash = None
        self.msgs = None
        self.hist = []          # (day, bank, cash), one entry per day
        self.actions = []       # (day, tool, args_str)
        self.counts = {}
        self.streak = 0
        self.streak_key = None
        self.sub_runs = 0
        self.last_sub_day = None
        self.last_stock_day = None
        self.last_collect_day = None
        self.waits = 0
        self.slots_total = 0
        self.slots_filled = 0
        self.jam_lines = 0
        self.storage_has = False
        self.storage_note = ""
        self.orders_pending = False
        self.order_seen_day = None
        self.orders_note = ""
        self.unread = 0
        self.alerts = ""
        self.last_result = ""
        self.result_error = False
        self.addresses = {}     # email -> first day seen in a tool result
        self.errors = 0

    def observe(self, prompt: str, last_action: dict | None) -> None:
        try:
            self._observe(prompt or "", last_action)
        except Exception:
            self.errors += 1

    def _observe(self, prompt, last_action):
        secs = _sections(prompt)

        # 1. Record the previous action (taken on self.day, before updating).
        if isinstance(last_action, dict):
            tool = str(last_action.get("tool_name", "?"))
            args = last_action.get("arguments", {}) or {}
            try:
                astr = json.dumps(args, sort_keys=True)
            except Exception:
                astr = str(args)
            aday = self.day if self.day is not None else 0
            self.actions.append((aday, tool, astr[:150]))
            if len(self.actions) > 200:
                self.actions = self.actions[-200:]
            self.counts[tool] = self.counts.get(tool, 0) + 1
            key = (tool, astr)
            if key == self.streak_key:
                self.streak += 1
            else:
                self.streak_key = key
                self.streak = 1
            if tool == "run_sub_agent":
                self.sub_runs += 1
                self.last_sub_day = aday
                cmds = args.get("commands") if isinstance(args, dict) else None
                ops = []
                if isinstance(cmds, list):
                    ops = [c.get("op") for c in cmds if isinstance(c, dict)]
                if "stock" in ops:
                    self.last_stock_day = aday
                if "collect_cash" in ops:
                    self.last_collect_day = aday
            if tool == "wait_for_next_day":
                self.waits += 1

        # 2. Read the current state.
        m = re.search(r"\(day (\d+)\)", secs.get("CURRENT TIME", ""))
        if m:
            self.day = int(m.group(1))

        money = secs.get("MONEY", "")
        mb = re.search(r"Bank balance:\s*\$?(-?[\d,]+(?:\.\d+)?)", money)
        if mb:
            self.bank = _money(mb.group(1))
        mc = re.search(r"Cash in machine:\s*\$?(-?[\d,]+(?:\.\d+)?)", money)
        if mc:
            self.cash = _money(mc.group(1))
        mm = re.search(r"Messages used:\s*(\d+)", money)
        if mm:
            self.msgs = int(mm.group(1))

        machine = secs.get("MACHINE STATE", "")
        total = filled = jams = 0
        for line in machine.splitlines():
            if re.search(r"R\d\s*C\d", line):
                total += 1
                if not re.search(r"\bempty\b", line, re.I):
                    filled += 1
                if re.search(r"jam", line, re.I):
                    jams += 1
        if total:
            self.slots_total = total
            self.slots_filled = filled
            self.jam_lines = jams

        st = secs.get("STORAGE INVENTORY", "").strip()
        self.storage_has = bool(st) and not st.lower().startswith("empty")
        self.storage_note = " | ".join(st.splitlines()[:3])[:200] if self.storage_has else ""

        od = secs.get("OUTSTANDING ORDERS", "").strip()
        pending = bool(od) and not od.lower().startswith("none")
        if pending and not self.orders_pending:
            self.order_seen_day = self.day
        if not pending:
            self.order_seen_day = None
        self.orders_pending = pending
        self.orders_note = " | ".join(od.splitlines()[:3])[:200] if pending else ""

        ue = secs.get("UNREAD EMAILS", "").strip()
        if not ue or ue.lower().startswith("none"):
            self.unread = 0
        else:
            n = sum(1 for l in ue.splitlines() if l.strip().startswith("-"))
            self.unread = n if n else 1

        pa = secs.get("PRIORITY ALERTS", "").strip()
        self.alerts = "" if (not pa or pa.startswith("No obvious")) else pa[:200]

        lr = secs.get("LAST TOOL RESULT", "")
        lr = lr.split("REMINDER")[0].strip()
        self.last_result = lr[:220]
        self.result_error = bool(_ERR_RE.search(lr))
        if lr and self.day is not None:
            for addr in _EMAIL_RE.findall(lr):
                self.addresses.setdefault(addr, self.day)
            if len(self.addresses) > 40:
                self.addresses = dict(list(self.addresses.items())[-40:])

        if self.day is not None and self.bank is not None:
            if not self.hist or self.hist[-1][0] != self.day:
                self.hist.append((self.day, self.bank, self.cash or 0.0))
            else:
                self.hist[-1] = (self.day, self.bank, self.cash or 0.0)
            if len(self.hist) > 400:
                self.hist = self.hist[-400:]

    def brief(self) -> str:
        try:
            return self._brief()[:3900]
        except Exception:
            return "LOGBOOK: unavailable due to internal error."

    def _brief(self):
        L = []
        L.append("LOGBOOK (built from this run's prompts and your past actions)")
        L.append(f"Day {self.day} | bank ${self.bank} | cash in machine ${self.cash} | messages used {self.msgs}")
        if len(self.hist) >= 2:
            tail = self.hist[-8:]
            trend = ", ".join(f"d{d}:${b:.2f}" for d, b, _ in tail)
            L.append(f"Bank trend (recent days): {trend}")
        L.append(f"Machine: {self.slots_filled}/{self.slots_total} slots filled; jammed lines seen now: {self.jam_lines}")
        L.append(f"Storage: {'has stock' if self.storage_has else 'empty'}" +
                 (f" ({self.storage_note})" if self.storage_note else ""))
        L.append(f"Open orders: {'yes ' + self.orders_note if self.orders_pending else 'none'}")
        L.append(f"Unread emails: {self.unread}")
        L.append(f"Sub-agent runs: {self.sub_runs} | last stock op day: {self.last_stock_day} | "
                 f"last collect op day: {self.last_collect_day} | waits: {self.waits}")
        if self.counts:
            L.append("Tool use counts: " + ", ".join(f"{k}={v}" for k, v in sorted(self.counts.items())))

        checks = []
        d = self.day
        if self.alerts:
            checks.append(f"Priority alert shown: {self.alerts}")
        if self.storage_has and self.slots_total > self.slots_filled:
            checks.append("Storage holds stock while machine slots are empty.")
        if self.slots_total and self.slots_filled == 0 and d is not None and d >= 2:
            checks.append("Machine currently has no stock loaded.")
        if d is not None and d >= 2 and self.last_stock_day is None:
            checks.append("No stock op has been issued to run_sub_agent yet.")
        if d is not None and self.last_stock_day is not None and d - self.last_stock_day >= 7:
            checks.append(f"No restock op for {d - self.last_stock_day} days (last on day {self.last_stock_day}).")
        if self.cash and self.cash > 0:
            if self.last_collect_day is None:
                checks.append(f"Cash ${self.cash:.2f} in machine; no collect op issued yet.")
            elif d is not None and d - self.last_collect_day >= 3:
                checks.append(f"Cash ${self.cash:.2f} in machine; last collect was day {self.last_collect_day}.")
        if len(self.hist) >= 8 and self.hist[-1][1] < self.hist[-8][1]:
            checks.append("Bank balance is lower than 7 recorded days ago.")
        if self.streak >= 3 and self.streak_key:
            checks.append(f"Same action repeated {self.streak} times in a row: {self.streak_key[0]}.")
        if self.unread > 0:
            checks.append("Unread emails exist; read them before acting on them.")
        if self.orders_pending and self.order_seen_day is not None and d is not None and d - self.order_seen_day >= 7:
            checks.append(f"An order has been open since day {self.order_seen_day}; verify its status.")
        if self.result_error:
            checks.append("Last tool result looked like an error or rejection; check it before retrying.")
        if self.jam_lines:
            checks.append("Machine shows jammed slot(s) in MACHINE STATE.")
        if checks:
            L.append("CHECKS:")
            L += ["- " + c for c in checks]
        else:
            L.append("CHECKS: none triggered.")

        if self.actions:
            L.append("RECENT ACTIONS (your own record, oldest first, last 8):")
            for aday, tool, astr in self.actions[-8:]:
                L.append(f"- d{aday} {tool} {astr}")

        if self.last_result:
            L.append("LAST RESULT (first 220 chars): " + self.last_result.replace("\n", " "))

        if self.addresses:
            recent = list(self.addresses.items())[-8:]
            L.append("Addresses seen in tool results: " + ", ".join(f"{a} (d{dd})" for a, dd in recent))

        return "\n".join(L)
