import json
import re

_HEADERS = {
    "CURRENT TIME", "SCENARIO", "PRIORITY ALERTS", "MONEY", "KEY BUSINESS DETAILS",
    "MARKET SNAPSHOT", "PEER DIRECTORY", "OPEN TRADE OFFERS", "ARENA BULLETINS",
    "STORAGE INVENTORY", "MACHINE STATE", "OUTSTANDING ORDERS", "UNREAD EMAILS",
    "LATEST DAILY SUMMARY", "RECENT TRANSACTIONS", "RECENT DECISION HISTORY",
    "PLANNER NOTE", "LAST TOOL RESULT", "REMINDER",
}


def _sec(text, name):
    try:
        out = []
        found = False
        for ln in text.splitlines():
            s = ln.strip()
            if not found:
                if s == name:
                    found = True
                continue
            if s in _HEADERS:
                break
            if s:
                out.append(s)
        return "\n".join(out)
    except Exception:
        return ""


def _money(s):
    try:
        return float(s.replace(",", ""))
    except Exception:
        return None


class Register:
    def __init__(self) -> None:
        self.calls = 0
        self.day = None
        self.first_day = None
        self.balance = None
        self.start_balance = None
        self.balance_by_day = {}
        self.cash_in_machine = None
        self.msgs_remaining = None
        self.actions = []          # list of (day, tool, sig, summary)
        self.tool_counts = {}
        self.last_stock_day = None
        self.last_order_day = None
        self.last_search_day = None
        self.last_collect_day = None
        self.last_wait_streak = 0
        self.stock_ops_total = 0
        self.price_ops_total = 0
        self.collect_ops_total = 0
        self.machine_total = 0
        self.machine_empty = None
        self.machine_text = ""
        self.storage_text = ""
        self.outstanding_text = ""
        self.unread_text = ""
        self.alerts_text = ""
        self.last_result = ""
        self.summary_text = ""
        self.empty_since_day = None

    def _summarize(self, tool, args):
        try:
            if tool == "run_sub_agent" and isinstance(args, dict):
                cmds = args.get("commands") or []
                ops = {}
                for c in cmds:
                    if isinstance(c, dict):
                        op = str(c.get("op", "?"))
                        ops[op] = ops.get(op, 0) + 1
                        if op == "stock":
                            self.stock_ops_total += 1
                        elif op == "set_price":
                            self.price_ops_total += 1
                        elif op == "collect_cash":
                            self.collect_ops_total += 1
                            self.last_collect_day = self.day
                        if op == "stock":
                            self.last_stock_day = self.day
                return "sub_agent ops " + ",".join(f"{k}x{v}" for k, v in ops.items())
            s = json.dumps(args, sort_keys=True)
            return s[:120]
        except Exception:
            return "?"

    def _record_action(self, last_action):
        if not isinstance(last_action, dict):
            return
        tool = str(last_action.get("tool_name", "?"))
        args = last_action.get("arguments", {})
        try:
            sig = tool + json.dumps(args, sort_keys=True)
        except Exception:
            sig = tool + repr(args)
        summ = self._summarize(tool, args)
        if tool == "send_email":
            self.last_order_day = self.day
        elif tool == "ai_web_search":
            self.last_search_day = self.day
        self.tool_counts[tool] = self.tool_counts.get(tool, 0) + 1
        if tool == "wait_for_next_day":
            self.last_wait_streak += 1
        else:
            self.last_wait_streak = 0
        self.actions.append((self.day, tool, sig, summ))
        if len(self.actions) > 400:
            self.actions = self.actions[-400:]

    def observe(self, prompt: str, last_action: dict | None) -> None:
        try:
            self.calls += 1
            self._record_action(last_action)
            text = prompt if isinstance(prompt, str) else str(prompt)

            m = re.search(r"\(day (\d+)\)", text)
            if m:
                self.day = int(m.group(1))
                if self.first_day is None:
                    self.first_day = self.day

            m = re.search(r"Bank balance:\s*\$([\-\d,\.]+)", text)
            if m:
                self.balance = _money(m.group(1))
                if self.start_balance is None and self.balance is not None:
                    self.start_balance = self.balance
                if self.day is not None and self.balance is not None:
                    self.balance_by_day[self.day] = self.balance
            m = re.search(r"Cash in machine:\s*\$([\-\d,\.]+)", text)
            if m:
                self.cash_in_machine = _money(m.group(1))
            m = re.search(r"Messages remaining:\s*(\d+)", text)
            if m:
                self.msgs_remaining = int(m.group(1))

            self.machine_text = _sec(text, "MACHINE STATE")
            slots = [ln for ln in self.machine_text.splitlines() if re.search(r"R\d+C\d+", ln)]
            if slots:
                self.machine_total = len(slots)
                self.machine_empty = sum(1 for ln in slots if "empty" in ln.lower())
                if self.machine_empty == self.machine_total:
                    if self.empty_since_day is None:
                        self.empty_since_day = self.day
                else:
                    self.empty_since_day = None

            self.storage_text = _sec(text, "STORAGE INVENTORY")
            self.outstanding_text = _sec(text, "OUTSTANDING ORDERS")
            self.unread_text = _sec(text, "UNREAD EMAILS")
            self.alerts_text = _sec(text, "PRIORITY ALERTS")
            self.summary_text = _sec(text, "LATEST DAILY SUMMARY")
            self.last_result = _sec(text, "LAST TOOL RESULT")
        except Exception:
            pass

    def _days_since(self, d):
        if d is None or self.day is None:
            return None
        return self.day - d

    def brief(self) -> str:
        try:
            lines = []
            lines.append("REGISTER (kept outside the model; based only on prompts and past actions)")
            day = self.day if self.day is not None else "?"
            lines.append(f"- Day {day}, decisions so far: {self.calls}, messages remaining: {self.msgs_remaining}")
            if self.balance is not None:
                delta = ""
                if self.start_balance is not None:
                    delta = f" (change since first prompt: {self.balance - self.start_balance:+.2f})"
                lines.append(f"- Bank: ${self.balance:.2f}{delta}; cash in machine: ${self.cash_in_machine if self.cash_in_machine is not None else '?'}")
                recent = sorted(self.balance_by_day.items())[-5:]
                if len(recent) >= 2:
                    lines.append("- Bank trend (day:balance): " + ", ".join(f"d{d}:{b:.0f}" for d, b in recent))

            if self.machine_total:
                filled = self.machine_total - (self.machine_empty or 0)
                lines.append(f"- Machine: {filled}/{self.machine_total} slots filled, {self.machine_empty} empty")
            if self.last_stock_day is not None:
                lines.append(f"- Last stock op: day {self.last_stock_day} ({self._days_since(self.last_stock_day)} days ago); stock ops total {self.stock_ops_total}, price ops {self.price_ops_total}, collect ops {self.collect_ops_total}")
            else:
                lines.append("- No stock op has been issued in this run yet.")
            if self.last_collect_day is not None:
                lines.append(f"- Last cash collection: day {self.last_collect_day}")

            st = self.storage_text.replace("\n", " | ")
            lines.append(f"- Storage: {st[:300] if st else '(not shown)'}")
            oo = self.outstanding_text.replace("\n", " | ")
            if oo and oo.lower() != "none.":
                lines.append(f"- Outstanding orders: {oo[:300]}")
            if self.last_order_day is not None:
                lines.append(f"- Last email sent: day {self.last_order_day} ({self._days_since(self.last_order_day)} days ago)")
            ue = self.unread_text.replace("\n", " | ")
            if ue and ue.lower() != "none.":
                lines.append(f"- Unread emails: {ue[:200]}")
            al = self.alerts_text.replace("\n", " | ")
            if al and "no obvious urgent" not in al.lower():
                lines.append(f"- Alerts: {al[:250]}")

            flags = []
            if len(self.actions) >= 3 and len({a[2] for a in self.actions[-3:]}) == 1:
                flags.append("same action 3 times in a row; check whether state changed before repeating it")
            if self.last_wait_streak >= 6:
                flags.append(f"waited {self.last_wait_streak} days in a row; confirm deliveries and stock status")
            if self.machine_empty and self.machine_empty == self.machine_total and self.storage_text.strip().lower() not in ("", "empty.", "empty"):
                flags.append("machine fully empty while storage shows goods; a stock op is likely needed")
            if (self.cash_in_machine or 0) > 0 and self.last_collect_day is not None and self._days_since(self.last_collect_day) and self._days_since(self.last_collect_day) >= 4:
                flags.append("cash has not been collected for several days")
            if self.last_order_day is None and self.calls > 6:
                flags.append("no supplier order sent yet")
            if flags:
                lines.append("- Flags:")
                for f in flags:
                    lines.append(f"  * {f}")

            if self.tool_counts:
                tc = sorted(self.tool_counts.items(), key=lambda x: -x[1])
                lines.append("- Tool use: " + ", ".join(f"{k} {v}" for k, v in tc[:8]))

            recent = self.actions[-6:]
            if recent:
                lines.append("- Recent own actions:")
                for d, tool, _sig, summ in recent:
                    lines.append(f"  d{d} {tool}: {summ}")

            lr = self.last_result.replace("\n", " ")
            if lr:
                lines.append(f"- Last result (excerpt): {lr[:300]}")

            out = "\n".join(lines)
            if len(out) > 3900:
                out = out[:3890] + "\n...[truncated]"
            return out
        except Exception:
            return "REGISTER: unavailable due to internal error."
