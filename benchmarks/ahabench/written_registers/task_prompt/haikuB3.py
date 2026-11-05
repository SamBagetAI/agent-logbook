import json
import re


class Register:
    """Keeps a factual running record of this run and reports it in the planner note."""

    MAX_ERRORS = 40
    MAX_RECENT = 12

    def __init__(self) -> None:
        self.day = None
        self.day_label = None
        self.bank = None
        self.cash_in_machine = None
        self.bank_hist = []          # list of [day, bank]
        self.slots = {}              # slot -> {"size", "sku", "qty", "cap", "price"}
        self.slot_sizes = {}         # slot -> size, learned from error messages
        self.storage = []            # lines from storage inventory
        self.orders = []             # lines from outstanding orders
        self.summary = None          # latest closed day summary line
        self.errors = {}             # normalized error text -> count
        self.error_order = []        # recent distinct error texts
        self.recent = []             # recent actions, short strings
        self.action_counts = {}      # tool name -> count
        self.repeat_action = 0       # consecutive identical actions
        self.last_action_key = None
        self.stall_count = 0         # consecutive identical inbox/tool results
        self.last_result_key = None
        self.units_loaded = 0
        self.loads = 0
        self.cash_collected = 0.0
        self.sub_failed_cmds = 0
        self.empty_days = 0          # closed days with an empty machine
        self.last_empty_check_day = None
        self.waits_since_stock = 0
        self.sends = {}              # recipient -> number of emails sent
        self.account_requests = 0    # results asking for an account number
        self.out_of_stock_msgs = 0
        self.fault_msgs = 0
        self.last_result_text = ""
        self.observations = 0

    # ----------------------------------------------------------------- helpers
    def _add_error(self, text):
        key = re.sub(r"\s+", " ", text.strip())[:160]
        if not key:
            return
        if key not in self.errors:
            if len(self.errors) >= self.MAX_ERRORS:
                return
            self.errors[key] = 0
            self.error_order.append(key)
        self.errors[key] += 1

    def _record_action(self, last_action):
        if not last_action:
            return
        tool = str(last_action.get("tool_name", "?"))
        args = last_action.get("arguments", {}) or {}
        try:
            akey = tool + json.dumps(args, sort_keys=True, default=str)
        except Exception:
            akey = tool + str(args)
        if akey == self.last_action_key:
            self.repeat_action += 1
        else:
            self.repeat_action = 0
        self.last_action_key = akey
        self.action_counts[tool] = self.action_counts.get(tool, 0) + 1

        short = tool
        if tool == "send_email":
            to = str(args.get("to", "?"))
            self.sends[to] = self.sends.get(to, 0) + 1
            short += " to " + to + " | " + str(args.get("subject", ""))[:60]
        elif tool in ("run_sub_agent", "chat_with_sub_agent"):
            if "commands" in args:
                ops = [str(c.get("op", "?")) for c in (args.get("commands") or []) if isinstance(c, dict)]
                short += " ops=" + ",".join(ops[:6])
            else:
                short += " " + str(args.get("message", ""))[:60]
        elif tool == "read_email":
            short += " id=" + str(args.get("id", args.get("email_id", "?")))
        self.recent.append(f"d{self.day or '?'} {short}")
        self.recent = self.recent[-self.MAX_RECENT:]

        if tool == "wait_for_next_day":
            self.waits_since_stock += 1
        if tool == "run_sub_agent" and "commands" in args:
            ops = [c.get("op") for c in (args.get("commands") or []) if isinstance(c, dict)]
            if "stock" in ops:
                self.waits_since_stock = 0

    def _parse_result(self, result):
        r = result or ""
        for m in re.finditer(r"Slot (R\d+C\d+) expects (small|large)", r):
            self.slot_sizes[m.group(1)] = m.group(2)
        for m in re.finditer(r"Command (\{[^}]*\}) failed: (.*)", r):
            self.sub_failed_cmds += 1
            msg = m.group(2).strip()
            if msg == "'slot'":
                self._add_error("command used row/col keys, not slot")
            else:
                self._add_error(re.sub(r"'[^']*'", "'_'", msg))
        for m in re.finditer(r"Loaded (\d+) units of (\S+) into (R\d+C\d+)", r):
            self.units_loaded += int(m.group(1))
            self.loads += 1
        for m in re.finditer(r"Collected \$([\d.,]+) in cash", r):
            try:
                self.cash_collected += float(m.group(1).replace(",", ""))
            except ValueError:
                pass
        low = r.lower()
        if "account number" in low:
            self.account_requests += 1
        if "out of stock" in low:
            self.out_of_stock_msgs += 1
        if "global fault" in low:
            self.fault_msgs += 1

    # ----------------------------------------------------------------- observe
    def observe(self, prompt: str, last_action) -> None:
        try:
            self.observations += 1
            p = prompt or ""

            m = re.search(r"CURRENT TIME\s*\n\s*(\S+)\s*\((day \d+)\)", p)
            if m:
                self.day_label = m.group(1)
                self.day = int(re.search(r"\d+", m.group(2)).group(0))

            m = re.search(r"Bank balance:\s*\$([\-\d.,]+)", p)
            if m:
                self.bank = float(m.group(1).replace(",", ""))
            m = re.search(r"Cash in machine:\s*\$([\-\d.,]+)", p)
            if m:
                self.cash_in_machine = float(m.group(1).replace(",", ""))

            if self.bank is not None and self.day is not None:
                if not self.bank_hist or self.bank_hist[-1][0] != self.day:
                    self.bank_hist.append([self.day, self.bank])
                    self.bank_hist = self.bank_hist[-12:]

            # machine state
            for mm in re.finditer(r"-\s*(R\d+C\d+)\s*\[(small|large)\]\s*(.*)", p):
                slot, size, rest = mm.group(1), mm.group(2), mm.group(3)
                entry = {"size": size, "sku": None, "qty": 0, "cap": None, "price": None}
                sk = re.search(r"sku=(\S+)", rest)
                if sk:
                    entry["sku"] = sk.group(1)
                q = re.search(r"qty=(\d+)/(\d+)", rest)
                if q:
                    entry["qty"] = int(q.group(1))
                    entry["cap"] = int(q.group(2))
                pr = re.search(r"price=\$([\d.]+)", rest)
                if pr:
                    entry["price"] = pr.group(1)
                self.slots[slot] = entry

            # storage
            sm = re.search(r"STORAGE INVENTORY\s*\n(.*?)\n\s*MACHINE STATE", p, re.S)
            if sm:
                block = sm.group(1).strip()
                if not block or block.lower().startswith("empty"):
                    self.storage = []
                else:
                    self.storage = [ln.strip() for ln in block.splitlines() if ln.strip()][:8]

            # orders
            om = re.search(r"OUTSTANDING ORDERS\s*\n(.*?)\n\s*UNREAD EMAILS", p, re.S)
            if om:
                block = om.group(1).strip()
                if not block or block.lower().startswith("none"):
                    self.orders = []
                else:
                    self.orders = [ln.strip("- ").strip() for ln in block.splitlines() if ln.strip()][:6]

            # daily summary
            sy = re.search(r"LATEST DAILY SUMMARY\s*\n\s*(.*)", p)
            if sy:
                self.summary = sy.group(1).strip()[:200]

            # last tool result
            lr = re.search(r"LAST TOOL RESULT\s*\n(.*?)(\n\s*REMINDER:|\Z)", p, re.S)
            result = lr.group(1).strip() if lr else ""
            self.last_result_text = result[:300]

            rkey = result[:200]
            if rkey and rkey == self.last_result_key:
                self.stall_count += 1
            else:
                self.stall_count = 0
            self.last_result_key = rkey

            self._parse_result(result)
            self._record_action(last_action)

            # empty-machine streak, counted once per new day
            filled = sum(1 for s in self.slots.values() if s["sku"])
            if self.day is not None and self.day != self.last_empty_check_day:
                self.last_empty_check_day = self.day
                if self.slots and filled == 0:
                    self.empty_days += 1
                else:
                    self.empty_days = 0
        except Exception:
            pass

    # ------------------------------------------------------------------- brief
    def brief(self) -> str:
        try:
            out = []
            filled = [(k, v) for k, v in sorted(self.slots.items()) if v["sku"]]
            empty_n = len(self.slots) - len(filled)

            head = f"REGISTER (decision {self.observations})"
            out.append(head)
            if self.day is not None:
                worth = None
                if self.bank is not None:
                    worth = self.bank + (self.cash_in_machine or 0.0)
                line = f"Day {self.day} | bank ${self.bank:.2f}" if self.bank is not None else f"Day {self.day}"
                line += f" | cash in machine ${self.cash_in_machine:.2f}" if self.cash_in_machine is not None else ""
                if worth is not None:
                    line += f" | bank+cash ${worth:.2f} (start $500; stock at cost not included)"
                out.append(line)
            if len(self.bank_hist) > 1:
                out.append("Bank by day: " + ", ".join(f"d{d}:{b:.0f}" for d, b in self.bank_hist[-6:]))

            out.append(f"Machine: {len(filled)} slots filled, {empty_n} empty.")
            for k, v in filled[:12]:
                out.append(f"  {k} {v['sku']} {v['qty']}/{v['cap']} @{v['price']}")
            if self.empty_days >= 2:
                out.append(f"WARNING: machine has been empty for {self.empty_days} closed days.")

            if self.storage:
                out.append("Storage: " + "; ".join(self.storage)[:300])
                if empty_n > 0:
                    out.append("NOTE: storage holds goods while machine has empty slots.")
            else:
                out.append("Storage: empty.")
            if self.orders:
                out.append("Open orders: " + " | ".join(self.orders)[:300])
            if self.summary:
                out.append("Last closed day: " + self.summary)

            out.append(
                f"Totals so far: units loaded {self.units_loaded} in {self.loads} loads; "
                f"cash collected ${self.cash_collected:.2f}; failed sub-agent commands {self.sub_failed_cmds}."
            )
            if self.slot_sizes:
                sizes = ", ".join(f"{k}={v}" for k, v in sorted(self.slot_sizes.items())[:12])
                out.append("Slot sizes seen in errors: " + sizes)

            if self.errors:
                out.append("Recent distinct errors (count):")
                for e in self.error_order[-4:]:
                    out.append(f"  x{self.errors[e]} {e}")

            if self.waits_since_stock >= 4:
                out.append(f"WARNING: {self.waits_since_stock} day-advances since last stock load.")
            if self.repeat_action >= 2:
                out.append(f"WARNING: same action repeated {self.repeat_action + 1} times in a row.")
            if self.stall_count >= 2:
                out.append(f"WARNING: same tool result seen {self.stall_count + 1} times in a row; the approach is not working.")
            if self.account_requests >= 3:
                out.append(f"Counterparties have asked for an account number {self.account_requests} times; no quote has been fulfilled from that thread yet.")
            if self.out_of_stock_msgs:
                out.append(f"Supplier reported out-of-stock {self.out_of_stock_msgs} time(s).")
            if self.fault_msgs:
                out.append(f"Machine global-fault messages seen {self.fault_msgs} time(s).")

            busy = sorted(self.sends.items(), key=lambda kv: -kv[1])[:4]
            if busy:
                out.append("Emails sent per recipient: " + ", ".join(f"{k}:{v}" for k, v in busy))

            if self.recent:
                out.append("Recent actions:")
                for r in self.recent[-6:]:
                    out.append("  " + r[:120])

            text = "\n".join(out)
            if len(text) > 3900:
                text = text[:3897] + "..."
            return text
        except Exception:
            return "REGISTER: unavailable this step."
