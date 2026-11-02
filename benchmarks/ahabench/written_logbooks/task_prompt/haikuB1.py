import json
import re

_DAY_RE = re.compile(r"\(day (\d+)\)")
_BANK_RE = re.compile(r"Bank balance: \$([\d,\.\-]+)")
_CASH_RE = re.compile(r"Cash in machine: \$([\d,\.\-]+)")
_REMAIN_RE = re.compile(r"Messages remaining: (\d+)")
_EVENT_RE = re.compile(r'"event_name": (null|"[^"]*")')
_FAULT_RE = re.compile(r'"machine_fault": (true|false)')
_WEATHER_RE = re.compile(r'"weather": "([^"]*)"')
_SLOT_RE = re.compile(r"^- (R\d+C\d+) \[(small|large)\] (.*)$", re.M)
_FAIL_WORDS = ("failed", "error", "invalid", "unknown", "rejected")
_MAX_HISTORY = 300
_START_BANK = 500.0


def _money(s):
    try:
        return float(s.replace(",", ""))
    except Exception:
        return None


def _section(text, start, ends):
    s = text.find(start)
    if s < 0:
        return ""
    s += len(start)
    e = len(text)
    for m in ends:
        k = text.find(m, s)
        if 0 <= k < e:
            e = k
    return text[s:e]


class Logbook:
    def __init__(self):
        self.errors = 0
        self.cur_day = None
        self.day_seen = None
        self.actions_on_day = 0
        self.start_remaining = None
        self.history = []
        self.tool_counts = {}
        self.fail_sigs = {}
        self.recent_fails = []
        self.email_reads = {}
        self.last_load_day = None
        self.loads = 0
        self.streak_key = None
        self.streak_n = 0
        self.snapshots = []
        self.empty_machine_days = 0
        self.state = {}
        self.last_result = ""

    # ----- observation -------------------------------------------------
    def observe(self, prompt, last_action):
        try:
            self._observe(prompt or "", last_action)
        except Exception:
            self.errors += 1

    def _observe(self, text, last_action):
        m = _DAY_RE.search(text)
        day = int(m.group(1)) if m else self.day_seen
        self.day_seen = day

        rm = _REMAIN_RE.search(text)
        remaining = int(rm.group(1)) if rm else None
        if self.start_remaining is None and remaining is not None:
            self.start_remaining = remaining

        result = self._last_result(text)
        self.last_result = result

        if isinstance(last_action, dict):
            self._record_action(last_action, result, day)

        self._parse_state(text, remaining)

        if day is not None and day != self.cur_day:
            self.cur_day = day
            self.actions_on_day = 0
            self._snapshot(day)

    def _last_result(self, text):
        i = text.rfind("LAST TOOL RESULT")
        if i < 0:
            return ""
        seg = text[i + len("LAST TOOL RESULT"):]
        j = seg.find("REMINDER:")
        if j >= 0:
            seg = seg[:j]
        return seg.strip()[:1500]

    def _record_action(self, action, result, day):
        tool = str(action.get("tool_name", "?"))
        args = action.get("arguments", {})
        try:
            akey = json.dumps(args, sort_keys=True, default=str)[:300]
        except Exception:
            akey = str(args)[:300]
        key = tool + " " + akey

        low = result.lower()
        fail = any(w in low for w in _FAIL_WORDS)

        self.history.append({"day": day, "tool": tool, "key": key,
                             "fail": fail, "result": result[:120]})
        if len(self.history) > _MAX_HISTORY:
            del self.history[: len(self.history) - _MAX_HISTORY]

        self.tool_counts[tool] = self.tool_counts.get(tool, 0) + 1
        self.actions_on_day += 1

        if key == self.streak_key:
            self.streak_n += 1
        else:
            self.streak_key = key
            self.streak_n = 1

        if tool == "run_sub_agent" and "Loaded " in result:
            self.loads += 1
            self.last_load_day = day

        if tool == "read_email":
            eid = args.get("email_id", args.get("id"))
            if eid is not None:
                k = str(eid)
                self.email_reads[k] = self.email_reads.get(k, 0) + 1

        if fail:
            for line in result.splitlines():
                ll = line.lower()
                if any(w in ll for w in _FAIL_WORDS):
                    sig = re.sub(r"\d+", "#", line.strip())[:140]
                    self.fail_sigs[sig] = self.fail_sigs.get(sig, 0) + 1
                    self.recent_fails.append(sig)
            if len(self.recent_fails) > 6:
                del self.recent_fails[: len(self.recent_fails) - 6]

    def _parse_state(self, text, remaining):
        st = self.state
        bm = _BANK_RE.search(text)
        cm = _CASH_RE.search(text)
        st["bank"] = _money(bm.group(1)) if bm else st.get("bank")
        st["cash"] = _money(cm.group(1)) if cm else st.get("cash")
        st["remaining"] = remaining if remaining is not None else st.get("remaining")

        em = _EVENT_RE.search(text)
        fm = _FAULT_RE.search(text)
        wm = _WEATHER_RE.search(text)
        st["event"] = em.group(1) if em else "null"
        st["fault"] = fm.group(1) if fm else "unknown"
        st["weather"] = wm.group(1) if wm else "unknown"

        sto = _section(text, "STORAGE INVENTORY", ["MACHINE STATE"]).strip()
        st["storage_empty"] = (sto == "" or sto.startswith("Empty"))
        st["storage_text"] = " | ".join(
            l.strip() for l in sto.splitlines() if l.strip())[:200]

        mach = _section(text, "MACHINE STATE", ["OUTSTANDING ORDERS"])
        slots = _SLOT_RE.findall(mach)
        empty, stocked_desc = [], []
        for sid, size, rest in slots:
            if "empty" in rest:
                empty.append(sid)
            else:
                stocked_desc.append(f"{sid}:{rest.strip()[:60]}")
        st["slots_total"] = len(slots)
        st["empty"] = empty
        st["stocked"] = stocked_desc

        orders_sec = _section(text, "OUTSTANDING ORDERS", ["UNREAD EMAILS"])
        st["orders"] = [l.strip()[:160] for l in orders_sec.splitlines()
                        if l.strip().startswith("- ")]

        unread_sec = _section(text, "UNREAD EMAILS", ["LATEST DAILY SUMMARY"])
        st["unread"] = 0 if "None" in unread_sec else len(
            [l for l in unread_sec.splitlines() if l.strip()])

        summ = _section(text, "LATEST DAILY SUMMARY", ["RECENT TRANSACTIONS"])
        lines = [l.strip() for l in summ.splitlines() if l.strip()]
        st["summary"] = lines[0][:200] if lines else "none"

        alerts = _section(text, "PRIORITY ALERTS", ["MONEY"])
        al = [l.strip()[:160] for l in alerts.splitlines()
              if l.strip().startswith("- ") and "No obvious" not in l]
        st["alerts"] = al[:3]

        trade = _section(text, "OPEN TRADE OFFERS", ["ARENA BULLETINS"]).strip()
        st["trade"] = "" if (trade == "" or trade.startswith("None")) else trade[:120]

    def _snapshot(self, day):
        st = self.state
        stocked = st.get("slots_total", 0) - len(st.get("empty", []))
        if stocked == 0:
            self.empty_machine_days += 1
        else:
            self.empty_machine_days = 0
        snap = {
            "day": day,
            "bank": st.get("bank"),
            "cash": st.get("cash"),
            "stocked": stocked,
            "empty_n": len(st.get("empty", [])),
            "storage_empty": st.get("storage_empty", True),
            "orders": len(st.get("orders", [])),
        }
        self.snapshots.append(snap)
        if len(self.snapshots) > 400:
            del self.snapshots[: len(self.snapshots) - 400]

    # ----- brief -------------------------------------------------------
    def brief(self):
        try:
            return self._brief()[:3900]
        except Exception:
            return "LOGBOOK: unavailable this step."

    def _brief(self):
        st = self.state
        out = []
        day = self.day_seen
        bank = st.get("bank")
        cash = st.get("cash")
        if bank is None:
            return "LOGBOOK: no state parsed yet."
        cash = cash or 0.0
        nw = bank + cash
        out.append(f"LOGBOOK day {day} | bank ${bank:.2f} | cash in machine ${cash:.2f} "
                   f"| bank+cash ${nw:.2f} (start ${_START_BANK:.2f}; unsold stock not counted)")
        if self.start_remaining is not None and st.get("remaining") is not None:
            out.append(f"Messages used so far: {self.start_remaining - st['remaining']}")

        total = st.get("slots_total", 0)
        stocked = total - len(st.get("empty", []))
        out.append(f"Machine: {stocked}/{total} slots stocked; "
                   f"empty: {', '.join(st.get('empty', [])) or 'none'}")
        if st.get("storage_empty", True):
            out.append("Storage: empty")
        else:
            out.append(f"Storage: HAS ITEMS -> {st.get('storage_text', '')}")
        out.append(f"Open orders: {len(st.get('orders', []))}; unread emails: {st.get('unread', 0)}")
        for o in st.get("orders", [])[:3]:
            out.append(f"  order: {o}")
        out.append(f"Market: weather={st.get('weather')} event={st.get('event')} "
                   f"machine_fault={st.get('fault')}")
        out.append(f"Last daily summary: {st.get('summary')}")
        for a in st.get("alerts", []):
            out.append(f"Alert: {a}")
        if st.get("trade"):
            out.append(f"Trade offers present: {st['trade']}")

        # bank trend
        if self.snapshots:
            tail = self.snapshots[-6:]
            parts = []
            prev = None
            for s in tail:
                b = s.get("bank")
                if b is None:
                    continue
                d = "" if prev is None else f"({b - prev:+.2f})"
                parts.append(f"d{s['day']}:${b:.2f}{d}")
                prev = b
            if parts:
                out.append("Bank trend: " + "  ".join(parts))

        # warnings derived from this run's own state
        warn = []
        if not st.get("storage_empty", True) and st.get("empty"):
            warn.append(f"Goods sit in storage while {len(st['empty'])} slots are empty; "
                        "stocking them is the next step unless the last sub-agent result says otherwise.")
        if stocked == 0 and st.get("storage_empty", True) and len(st.get("orders", [])) == 0:
            warn.append("Machine is empty, storage is empty, no open orders: no income is coming.")
        if self.empty_machine_days >= 3:
            warn.append(f"Machine has had zero stock for {self.empty_machine_days} consecutive day checks.")
        if cash > 0:
            warn.append(f"${cash:.2f} cash is waiting in the machine; collect it.")
        if self.streak_n >= 3:
            warn.append(f"The same action ({self.streak_key[:120]}) was repeated {self.streak_n} times "
                        "in a row. Repeating it gives the same result; change approach or advance time.")
        if self.actions_on_day >= 12:
            warn.append(f"{self.actions_on_day} actions taken on this day without advancing time.")
        rep = [(k, v) for k, v in self.email_reads.items() if v >= 3]
        if rep:
            ids = ", ".join(f"{k}(x{v})" for k, v in sorted(rep, key=lambda x: -x[1])[:4])
            warn.append(f"Emails re-read several times with no new content: {ids}.")
        if self.loads == 0 and self.day_seen and self.day_seen > 3:
            warn.append("No successful stock load recorded in this run yet.")
        elif self.last_load_day is not None and self.day_seen:
            gap = self.day_seen - self.last_load_day
            if gap >= 7:
                warn.append(f"No successful stock load for {gap} days.")
        if warn:
            out.append("WARNINGS:")
            out.extend("- " + w for w in warn[:6])

        # sub-agent / tool failures
        if self.fail_sigs:
            top = sorted(self.fail_sigs.items(), key=lambda x: -x[1])[:3]
            out.append("Recurring failure messages (count):")
            for sig, n in top:
                out.append(f"- x{n}: {sig}")
        if self.recent_fails:
            out.append(f"Last failure: {self.recent_fails[-1][:140]}")

        # recent activity mix
        last8 = self.history[-8:]
        if last8:
            counts = {}
            for h in last8:
                counts[h["tool"]] = counts.get(h["tool"], 0) + 1
            mix = ", ".join(f"{k} x{v}" for k, v in sorted(counts.items(), key=lambda x: -x[1]))
            out.append(f"Last {len(last8)} actions: {mix}")
            last = self.history[-1]
            out.append(f"Previous action: {last['tool']} -> {last['result'][:100]}")

        return "\n".join(out)
