import re
import json

_HDR = re.compile(r'^([A-Z][A-Z /&\-]+)$')
_DAY_RE = re.compile(r'\(day\s*(\d+)\)')
_BANK_RE = re.compile(r'Bank balance:\s*\$?\s*(-?[\d,]+(?:\.\d+)?)')
_CASH_RE = re.compile(r'Cash in machine:\s*\$?\s*(-?[\d,]+(?:\.\d+)?)')
_MSG_RE = re.compile(r'Messages used:\s*(\d+)')
_SLOT_RE = re.compile(r'R\d+C\d+')


def _num(s):
    try:
        return float(s.replace(",", ""))
    except Exception:
        return None


def _sections(prompt):
    secs = {}
    cur = None
    buf = []
    for line in prompt.splitlines():
        s = line.strip()
        if s.startswith("REMINDER:"):
            break
        m = _HDR.match(s)
        if m and s == m.group(1) and len(s) > 2:
            if cur is not None:
                secs[cur] = "\n".join(buf).strip()
            cur = s
            buf = []
        elif cur is not None:
            buf.append(line.rstrip())
    if cur is not None:
        secs[cur] = "\n".join(buf).strip()
    return secs


def _short(text, n):
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[: n - 3] + "..."


class Logbook:
    def __init__(self) -> None:
        self.day = None
        self.bank = None
        self.cash = None
        self.msgs = None
        self.timeline = []          # list of dicts, one per observed day (last 40)
        self.actions = []           # list of (day, tool, args_str)
        self.tool_counts = {}       # tool -> count
        self.last_sub_day = None
        self.last_email_day = None
        self.last_wait_day = None
        self.wait_streak = 0
        self.repeat_streak = 0
        self.last_key = None
        self.empty_slots = None
        self.total_slots = None
        self.storage_empty = None
        self.unread = ""
        self.alerts = ""
        self.summary = ""
        self.orders = ""
        self.last_result = ""
        self.machine_brief = ""
        self.first_bank = None
        self.errors = 0
        self.obs_count = 0

    def _update_state(self, prompt):
        secs = _sections(prompt)
        ct = secs.get("CURRENT TIME", "") or prompt[:300]
        m = _DAY_RE.search(ct) or _DAY_RE.search(prompt[:600])
        if m:
            self.day = int(m.group(1))
        money = secs.get("MONEY", "") or prompt
        m = _BANK_RE.search(money) or _BANK_RE.search(prompt)
        if m:
            self.bank = _num(m.group(1))
        m = _CASH_RE.search(money) or _CASH_RE.search(prompt)
        if m:
            self.cash = _num(m.group(1))
        m = _MSG_RE.search(money) or _MSG_RE.search(prompt)
        if m:
            self.msgs = int(m.group(1))
        if self.first_bank is None and self.bank is not None:
            self.first_bank = self.bank

        ms = secs.get("MACHINE STATE", "")
        slot_lines = [l for l in ms.splitlines() if _SLOT_RE.search(l)]
        if slot_lines:
            self.total_slots = len(slot_lines)
            self.empty_slots = sum(1 for l in slot_lines if "empty" in l.lower())
            self.machine_brief = _short(" | ".join(l.strip() for l in slot_lines), 500)
        st = secs.get("STORAGE INVENTORY", "")
        self.storage_empty = (st.strip() == "" or "empty" in st.lower()[:20])
        self.unread = _short(secs.get("UNREAD EMAILS", ""), 220)
        self.alerts = _short(secs.get("PRIORITY ALERTS", ""), 220)
        self.summary = _short(secs.get("LATEST DAILY SUMMARY", ""), 260)
        self.orders = _short(secs.get("OUTSTANDING ORDERS", ""), 260)
        self.last_result = _short(secs.get("LAST TOOL RESULT", ""), 380)

        if self.day is not None and (not self.timeline or self.timeline[-1]["day"] != self.day):
            self.timeline.append({
                "day": self.day,
                "bank": self.bank,
                "cash": self.cash,
                "empty": self.empty_slots,
                "storage_empty": self.storage_empty,
            })
        elif self.day is not None and self.timeline:
            last = self.timeline[-1]
            last.update({"bank": self.bank, "cash": self.cash,
                         "empty": self.empty_slots, "storage_empty": self.storage_empty})
        if len(self.timeline) > 40:
            self.timeline = self.timeline[-40:]

    def _record_action(self, last_action):
        if not isinstance(last_action, dict):
            return
        tool = str(last_action.get("tool_name", "?"))
        args = last_action.get("arguments", {})
        try:
            args_str = json.dumps(args, sort_keys=True)
        except Exception:
            args_str = str(args)
        d = self.day
        self.actions.append((d, tool, _short(args_str, 110)))
        if len(self.actions) > 60:
            self.actions = self.actions[-60:]
        self.tool_counts[tool] = self.tool_counts.get(tool, 0) + 1
        if tool == "run_sub_agent":
            self.last_sub_day = d
        if tool == "send_email":
            self.last_email_day = d
        if tool == "wait_for_next_day":
            self.last_wait_day = d
            self.wait_streak += 1
        else:
            self.wait_streak = 0
        key = (tool, args_str)
        if key == self.last_key:
            self.repeat_streak += 1
        else:
            self.repeat_streak = 1
        self.last_key = key

    def observe(self, prompt: str, last_action: dict | None) -> None:
        try:
            self.obs_count += 1
            self._record_action(last_action)
            self._update_state(str(prompt))
        except Exception:
            self.errors += 1

    def brief(self) -> str:
        try:
            lines = ["LOGBOOK (computed from this run's prompts and your past actions)"]
            lines.append(
                f"- Day {self.day} | bank ${self.bank} "
                f"(start ${self.first_bank}) | cash in machine ${self.cash} | "
                f"messages used {self.msgs}"
            )
            if self.bank is not None and self.first_bank is not None:
                lines.append(f"- Bank change since first prompt: {self.bank - self.first_bank:+.2f}")
            if self.total_slots is not None:
                lines.append(
                    f"- Machine: {self.empty_slots} of {self.total_slots} slots empty"
                )
            lines.append(f"- Storage empty: {self.storage_empty}")
            since = lambda d: "never" if d is None or self.day is None else f"{self.day - d} day(s) ago"
            lines.append(
                f"- Last stock run (run_sub_agent): {since(self.last_sub_day)}; "
                f"last email sent: {since(self.last_email_day)}; "
                f"wait streak: {self.wait_streak}"
            )
            if self.repeat_streak >= 3 and self.last_key:
                lines.append(
                    f"- Note: the same action ({self.last_key[0]}) has been repeated "
                    f"{self.repeat_streak} times in a row."
                )
            if self.timeline:
                lines.append("- Recent day records (day: bank / cash / empty slots):")
                for r in self.timeline[-6:]:
                    lines.append(
                        f"  d{r['day']}: ${r['bank']} / ${r['cash']} / {r['empty']}"
                    )
            if self.actions:
                lines.append("- Your last actions:")
                for d, tool, a in self.actions[-8:]:
                    lines.append(f"  d{d} {tool} {a}")
            if self.tool_counts:
                counts = ", ".join(f"{k}={v}" for k, v in sorted(self.tool_counts.items()))
                lines.append(f"- Tool totals: {counts}")
            if self.machine_brief:
                lines.append(f"- Machine as last shown: {self.machine_brief}")
            if self.orders and self.orders.lower() != "none.":
                lines.append(f"- Outstanding orders as last shown: {self.orders}")
            if self.unread and self.unread.lower() != "none.":
                lines.append(f"- Unread emails as last shown: {self.unread}")
            if self.alerts and "no obvious urgent" not in self.alerts.lower():
                lines.append(f"- Alerts as last shown: {self.alerts}")
            if self.summary and "no completed" not in self.summary.lower():
                lines.append(f"- Latest daily summary: {self.summary}")
            if self.last_result and self.last_result.lower() != "none yet.":
                lines.append(f"- Last tool result: {self.last_result}")
            text = "\n".join(lines)
            if len(text) > 3900:
                text = text[:3897] + "..."
            return text
        except Exception:
            return "LOGBOOK unavailable this step."
