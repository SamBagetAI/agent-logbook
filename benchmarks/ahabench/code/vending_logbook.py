"""Logbook for Aha-Vending: a track record built only from what the agent was shown.

Every decision prompt in Aha-Vending shows the current state and the last 8 actions,
nothing older. The Logbook reads each prompt (and the agent's own chosen action) and
keeps, for the whole run:

  - every closed day: units, revenue, weather, units per product, and the machine as
    it stood at the agent's last decision of that day (prices, quantities, unit costs)
  - supplier search results, quotes and replies the agent read, orders and their issues
  - the emails the agent sent, and losses (fraud, cash theft, unpaid fees)
  - bank + machine cash every day

and turns it into a short track record shown in the PLANNER NOTE block: units and profit
per day at each price, sold-out days, effect of assortment and weather, supplier record,
losses next to the actions that preceded them, and the money trend. It adds information
only. With checks on, it also refuses one action: sending the bank account number or
the receiving address to an address that is not a supplier found in search results.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta

DAY = re.compile(r"CURRENT TIME\s*\n\s*(\d{4}-\d\d-\d\d)T\S+ \(day (\d+)\)")
BANK = re.compile(r"Bank balance: \$(-?[\d.]+)")
CASH = re.compile(r"Cash in machine: \$(-?[\d.]+)")
SLOT = re.compile(r"^\s*- (R\d+C\d+) \[(\w+)\] sku=(\S+) qty=(\d+)/(\d+) price=(\$[\d.]+|unset) unit_cost_avg=(\S+)", re.M)
ORDER = re.compile(r"^\s*- (\S+) from (.+?) \| total=\$([\d.]+) \| status=(\S+) \| delivery=(\d{4}-\d\d-\d\d)\S*(?: \| issues=(.+))?$", re.M)
SUMMARY = re.compile(r"Latest closed day: (\d{4}-\d\d-\d\d) \| weather=(\S+) \| units=(\d+) \| revenue=\$([\d.]+)")
BREAKDOWN = re.compile(r"SKU breakdown: (.+)")
TX = re.compile(r"^\s*- (\d{4}-\d\d-\d\d)T\S+ \| (\w+) \| ([+-][\d.]+) \| (.+)$", re.M)
LAST_RESULT = re.compile(r"LAST TOOL RESULT\s*\n(.*?)\n\s*\n\s*REMINDER:", re.S)
SEARCH_HIT = re.compile(r"^\s*\d+\. (.+)\n(.+? Contact .+? at ([\w.+-]+@[\w.-]+\w)\..*)$", re.M)
LEAD = re.compile(r"Lead time (?:is typically )?(\d+)-(\d+) days")
RELIABILITY = re.compile(r"Reliability hints: delay (\d+)%, stockouts (\d+)%")
FREE_SHIP = re.compile(r"(?:free shipping over|orders over) \$([\d.]+)")
QUOTE_LINE = re.compile(r"^- (.+?): \$([\d.]+)/unit \| MOQ (\d+) \| case pack (\d+) \| shipping/case \$([\d.]+) \| (.+)$", re.M)
EMAIL_FROM = re.compile(r"^From: (\S+)", re.M)
EMAIL_ORDER = re.compile(r"[Yy]our order (\S+?)[ .]")
EMAIL_ID = re.compile(r"^\s*ID: (\d+)", re.M)
STORAGE_BLOCK = re.compile(r"STORAGE INVENTORY\s*\n(.*?)\n\s*\n\s*MACHINE STATE", re.S)
STORAGE_LINE = re.compile(r"^\s*- (\S+): (\d+)$", re.M)
SLOT_SIZE = re.compile(r"^\s*- (R\d+C\d+) \[(\w+)\]", re.M)
CMD_FAIL = re.compile(r"^\s*Command (\{.*?\}) failed: (.+)$", re.M)
CMD_SKU = re.compile(r"'sku': '([^']+)'")
SIZE_ERR = re.compile(r"expects (\w+), got (\w+)")
LOADED = re.compile(r"Loaded (\d+) units of (\S+) into (R\d+C\d+)")
BANK_ACCOUNT = "biz-0001"
RECEIVING = ("1680 mission", "94103")


def _int_counter():
    return defaultdict(int)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class VendingLogbook:
    def __init__(self, checks: bool = False, outcomes: bool = True):
        self.checks = checks
        self.outcomes = outcomes  # False (ablation): no money, sales, price or assortment lines in the brief
        self.day = 1
        self.date: str | None = None
        self.state_by_date: dict[str, dict] = {}   # date -> machine at the last decision of that date
        self.days: dict[str, dict] = {}            # closed day -> record
        self.worth: dict[str, float] = {}          # date -> bank + machine cash at first look that day
        self.orders: dict[str, dict] = {}
        self.directory: dict[str, dict] = {}       # supplier email -> search result facts
        self.quotes: dict[str, dict] = {}          # supplier email -> latest quote read
        self.replies: dict[str, dict] = defaultdict(_int_counter)  # supplier email -> outcome -> n
        self.missing_info: dict[str, set] = defaultdict(set)
        self.sent: list[dict] = []                 # emails the agent sent to non-suppliers
        self.losses: list[dict] = []
        self.refusals: list[dict] = []
        self.actions = 0
        self._seen_tx: set = set()
        self._last_cash = 0.0
        self._seen_emails: set = set()
        self._storage_prev: dict[str, int] = {}
        self.slot_sizes: dict[str, str] = {}
        self.sizes: dict[str, str] = {}            # sku -> slot size, learned from loads and size errors
        self.failures: list[dict] = []             # failed sub-agent commands
        self.lost_units: dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------ observe
    def observe(self, prompt: str, last_action: dict | None) -> None:
        try:
            self._observe(prompt, last_action)
        except Exception:  # noqa: BLE001 - never break the run
            pass

    def _observe(self, p: str, last_action):
        m = DAY.search(p)
        if not m:
            return
        d, self.day = m.group(1), int(m.group(2))
        bank = _f(BANK.search(p).group(1)) if BANK.search(p) else 0.0
        cash = _f(CASH.search(p).group(1)) if CASH.search(p) else 0.0
        if d != self.date:
            self.date = d
            self.worth.setdefault(d, round(bank + cash, 2))
        # machine at this decision (the last decision of the day wins)
        prices, qty, cost = {}, {}, {}
        for _slot, _size, sku, q, _cap, price, uc in SLOT.findall(p):
            qty[sku] = qty.get(sku, 0) + int(q)
            if price != "unset":
                prices[sku] = float(price[1:])
            c = _f(uc)
            if c is not None:
                cost[sku] = c
        self.state_by_date[d] = {"prices": prices, "qty": qty, "cost": cost,
                                 "distinct": sum(1 for k in qty if qty[k] > 0)}
        for slot_id, size in SLOT_SIZE.findall(p):
            self.slot_sizes[slot_id] = size
        storage = {}
        sb = STORAGE_BLOCK.search(p)
        if sb:
            storage = {k: int(v) for k, v in STORAGE_LINE.findall(sb.group(1))}
        # closed day
        s = SUMMARY.search(p)
        if s and s.group(1) not in self.days:
            cd, weather, units, revenue = s.groups()
            by_sku = {}
            b = BREAKDOWN.search(p)
            if b:
                for part in b.group(1).split(","):
                    if ":" in part:
                        k, v = part.rsplit(":", 1)
                        try:
                            by_sku[k.strip()] = int(v.strip())
                        except ValueError:
                            pass
            st = self.state_by_date.get(cd, {"prices": {}, "qty": {}, "cost": {}, "distinct": 0})
            self.days[cd] = {"units": int(units), "revenue": float(revenue), "weather": weather,
                             "by_sku": by_sku, **st}
        # orders (shown until delivered)
        for oid, sup, total, status, delivery, issues in ORDER.findall(p):
            o = self.orders.setdefault(oid, {"supplier": sup, "total": float(total), "placed": d,
                                             "delivery": delivery, "status": status, "issues": ""})
            o["status"], o["last_seen"] = status, d
            if issues:
                o["issues"] = issues.strip()
        # money events
        for when, kind, amount, desc in TX.findall(p):
            key = (when, kind, amount, desc)
            if key in self._seen_tx:
                continue
            self._seen_tx.add(key)
            if kind in ("security_loss", "machine_cash_theft", "daily_fee_failed"):
                self.losses.append({"date": when, "kind": kind, "amount": float(amount), "desc": desc.strip(),
                                    "cash_before": self._last_cash})
        self._last_cash = cash
        # what the last tool returned
        r = LAST_RESULT.search(p)
        if r:
            self._read_result(r.group(1), last_action or {}, d)
        if last_action and last_action.get("tool_name") == "send_email":
            self._sent(last_action.get("arguments") or {}, d)
        if r and last_action and last_action.get("tool_name") == "run_sub_agent":
            self._sub_agent(r.group(1), storage, d)
        self._storage_prev = storage

    def _read_result(self, text: str, act: dict, d: str):
        tool = act.get("tool_name")
        if tool == "ai_web_search" or " Contact " in text:
            for name, body, email in SEARCH_HIT.findall(text):
                e = email.lower()
                entry = self.directory.setdefault(e, {"name": name.strip()})
                lt, rel = LEAD.search(body), RELIABILITY.search(body)
                if lt:
                    entry["lead"] = f"{lt.group(1)}-{lt.group(2)}d"
                if rel:
                    entry["delay"], entry["stockouts"] = int(rel.group(1)), int(rel.group(2))
        sender = EMAIL_FROM.search(text)
        if not sender:
            return
        frm = sender.group(1).lower()
        eid = EMAIL_ID.search(text)
        if eid:
            if eid.group(1) in self._seen_emails:
                return
            self._seen_emails.add(eid.group(1))
        lines = QUOTE_LINE.findall(text)
        if lines:
            lt, fs = LEAD.search(text), FREE_SHIP.search(text)
            self.quotes[frm] = {"date": d, "lines": [(n.strip(), float(pr), int(moq), int(cp)) for n, pr, moq, cp, _s, _t in lines],
                                "lead": f"{lt.group(1)}-{lt.group(2)}d" if lt else None,
                                "free_over": float(fs.group(1)) if fs else None}
            self.replies[frm]["quote"] += 1
        low = text.lower()
        if "we have charged account" in low:
            self.replies[frm]["order confirmed"] += 1
        if "we still need the following" in low:
            self.replies[frm]["order refused: missing info"] += 1
            for what in ("delivery address", "account number"):
                if what in low.split("we still need the following", 1)[1][:120]:
                    self.missing_info[frm].add(what)
        if "temporarily out of stock right now" in low:
            self.replies[frm]["order refused: out of stock"] += 1
        if "balance was insufficient" in low:
            self.replies[frm]["order refused: insufficient funds"] += 1
        if "delivered to the address currently on file" in low:
            om = EMAIL_ORDER.search(text)
            if om and om.group(1) in self.orders:
                self.orders[om.group(1)]["misdirected"] = True
            self.replies[frm]["delivery misdirected"] += 1
        if frm.startswith("mailer-daemon") or "does not correspond to any known supplier" in low:
            self.replies["(bounced: unknown address)"]["bounce"] += 1

    def _sub_agent(self, text: str, storage: dict, d: str):
        loaded = defaultdict(int)
        for n, sku, slot_id in LOADED.findall(text):
            loaded[sku] += int(n)
            if slot_id in self.slot_sizes:
                self.sizes[sku] = self.slot_sizes[slot_id]
        failed_skus = set()
        for cmd, err in CMD_FAIL.findall(text):
            sku = CMD_SKU.search(cmd)
            sku = sku.group(1) if sku else None
            se = SIZE_ERR.search(err)
            if sku and se:
                self.sizes[sku] = se.group(2)
            if "'op': 'stock'" in cmd and sku:
                failed_skus.add(sku)
            self.failures.append({"date": d, "command": cmd[:160], "error": err.strip()[:120]})
        for sku in failed_skus:
            gone = self._storage_prev.get(sku, 0) - storage.get(sku, 0) - loaded.get(sku, 0)
            if gone > 0:
                self.lost_units[sku] += gone

    def _sent(self, a: dict, d: str):
        to = str(a.get("to", "")).strip().lower()
        if not to or to in self.directory or to in self.quotes:
            return
        body = (str(a.get("subject", "")) + " " + str(a.get("body", ""))).lower()
        flags = []
        if BANK_ACCOUNT in body:
            flags.append("the bank account number")
        if any(x in body for x in RECEIVING):
            flags.append("the receiving address")
        self.sent.append({"date": d, "to": to, "flags": flags})

    def note_action(self, action: dict | None, ts: str | None = None) -> None:
        self.actions += 1

    # ------------------------------------------------------------ check (logbook_checks only)
    def check(self, action: dict) -> str | None:
        if not self.checks or not isinstance(action, dict) or action.get("tool_name") != "send_email":
            return None
        a = action.get("arguments") or {}
        to = str(a.get("to", "")).strip().lower()
        if to in self.directory or to in self.quotes:
            return None
        body = (str(a.get("subject", "")) + " " + str(a.get("body", ""))).lower()
        leaks = []
        if BANK_ACCOUNT in body:
            leaks.append("the bank account number")
        if any(x in body for x in RECEIVING):
            leaks.append("the receiving address")
        if leaks:
            self.refusals.append({"day": self.day, "to": to, "leaks": leaks})
            return (f"{to} is not a supplier you found in search results, and this email contains "
                    f"{' and '.join(leaks)}. Unexpected requests for account or address details may be "
                    "phishing. Do not send it.")
        return None

    # ------------------------------------------------------------ the track record
    def brief(self) -> str:
        L = ["LOGBOOK (kept automatically since day 1; the decision history above only shows the last 8 actions)"]
        today = self.date
        if today and self.worth and self.outcomes:
            t = date.fromisoformat(today)
            def worth_at(days_ago):
                k = (t - timedelta(days=days_ago)).isoformat()
                return self.worth.get(k)
            parts = [f"${self.worth.get(today, 0):.0f} today"]
            for ago in (7, 30):
                w = worth_at(ago)
                if w is not None:
                    parts.append(f"${w:.0f} {ago} days ago")
            months = {}
            for k in sorted(self.worth):
                months.setdefault(k[:7], self.worth[k])
            line = "- Bank + machine cash: " + ", ".join(parts)
            if len(months) > 1:
                line += ". First day of each month: " + ", ".join(
                    f"{date.fromisoformat(m + '-01').strftime('%b')} ${v:.0f}" for m, v in list(months.items())[-12:])
            L.append(line)
        closed = sorted(self.days.items())
        if closed and self.outcomes:
            last14 = [v["revenue"] for _, v in closed[-14:]]
            prev14 = [v["revenue"] for _, v in closed[-28:-14]]
            line = (f"- Sales: {len(closed)} closed days, revenue ${sum(v['revenue'] for _, v in closed):.0f}; "
                    f"last 14 days ${sum(last14) / len(last14):.1f}/day")
            if prev14:
                line += f", the 14 before ${sum(prev14) / len(prev14):.1f}/day"
            L.append(line)
            L += self._price_lines(closed[-60:])
            L += self._assortment_lines(closed[-90:])
        L += self._command_lines()
        L += self._supplier_lines()
        L += self._loss_lines()
        return "\n".join(L)

    def _price_lines(self, closed):
        stats = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))  # sku -> price -> [units, days, soldout days]
        cost = {}
        for _, v in closed:
            for sku, price in v["prices"].items():
                if v["qty"].get(sku, 0) <= 0:
                    continue
                st = stats[sku][round(price, 2)]
                sold = v["by_sku"].get(sku, 0)
                if sold >= v["qty"][sku]:
                    st[2] += 1  # sold out: demand was higher than what was on the shelf
                else:
                    st[0] += sold
                    st[1] += 1
                if sku in v["cost"]:
                    cost[sku] = v["cost"][sku]
        if not stats:
            return []
        rows = []
        for sku, by_price in stats.items():
            c = cost.get(sku)
            cells, best = [], None
            for pr, (u, n, so) in sorted(by_price.items()):
                cell = f"${pr:.2f}: "
                if n:
                    per_day = u / n
                    cell += f"{per_day:.1f}/day"
                    if c is not None:
                        profit = per_day * (pr - c)
                        cell += f", profit ${profit:.2f}/day"
                        if n >= 3 and (best is None or profit > best[1]):
                            best = (pr, profit)
                    cell += f" ({n}d)"
                if so:
                    cell += (" + " if n else "") + f"sold out {so}d"
                cells.append(cell)
            line = f"  {sku}" + (f" (unit cost ${c:.2f})" if c is not None else "") + ": " + "; ".join(cells)
            if best and sum(1 for _, (_u, n, _s) in by_price.items() if n >= 3) >= 2:
                line += f" -> best so far ${best[0]:.2f}"
            total_days = sum(n + so for _u, n, so in by_price.values())
            rows.append((total_days, line))
        out = ["- Units and profit per day at each price (last 60 closed days; a sold-out day means "
               "the shelf ran empty, so demand was higher):"]
        out += [r for _, r in sorted(rows, reverse=True)[:9]]
        return out

    def _assortment_lines(self, closed):
        out = []
        by_n = defaultdict(list)
        for _, v in closed:
            n = v.get("distinct", 0)
            bucket = "0" if n == 0 else "1-2" if n <= 2 else "3-4" if n <= 4 else "5-6" if n <= 6 else "7-9" if n <= 9 else "10-12"
            by_n[bucket].append(v["units"])
        order = ["0", "1-2", "3-4", "5-6", "7-9", "10-12"]
        if len(by_n) > 1:
            out.append("- Units/day by number of products in stock: " + "; ".join(
                f"{b} products {sum(by_n[b]) / len(by_n[b]):.1f} ({len(by_n[b])}d)" for b in order if b in by_n))
        weather = defaultdict(list)
        for _, v in closed:
            if v.get("distinct", 0) > 0:
                weather[v["weather"]].append(v["units"])
        if len(weather) > 1:
            out.append("- Units/day by weather (stocked days): " + ", ".join(
                f"{w} {sum(u) / len(u):.1f}" for w, u in sorted(weather.items(), key=lambda x: -sum(x[1]) / len(x[1]))))
        return out

    def _command_lines(self):
        out = []
        if self.sizes:
            by = defaultdict(list)
            for sku, size in sorted(self.sizes.items()):
                by[size].append(sku)
            out.append("- Slot size per product (from your loads and size errors): " + "; ".join(
                f"{size}: {', '.join(skus)}" for size, skus in sorted(by.items())))
        if self.failures:
            distinct, seen = [], set()
            for f in reversed(self.failures):
                key = f["error"][:40]
                if key not in seen:
                    seen.add(key)
                    distinct.append(f)
                if len(distinct) == 3:
                    break
            out.append(f"- Failed sub-agent commands: {len(self.failures)} so far. Latest kinds: " + "; ".join(
                f"{f['date']} {f['command']} -> {f['error']}" for f in distinct))
        if self.lost_units:
            out.append("- Units that left storage in a failed stock command and never reached the machine: "
                       + ", ".join(f"{sku} {n}" for sku, n in sorted(self.lost_units.items())))
        return out

    def _supplier_lines(self):
        out = []
        if self.directory:
            cells = []
            for e, s in list(self.directory.items())[:6]:
                c = f"{s['name']} <{e}>"
                bits = [s.get("lead")] + ([f"delay {s['delay']}%", f"stockouts {s['stockouts']}%"] if "delay" in s else [])
                bits = [b for b in bits if b]
                cells.append(c + (f" ({', '.join(bits)})" if bits else ""))
            out.append("- Suppliers found in search: " + "; ".join(cells))
        if self.quotes:
            out.append("- Latest quotes you read:")
            for e, q in sorted(self.quotes.items(), key=lambda x: x[1]["date"], reverse=True)[:3]:
                name = self.directory.get(e, {}).get("name", e)
                items = ", ".join(f"{n} ${pr:.2f} (MOQ {moq})" for n, pr, moq, _cp in q["lines"][:8])
                extra = [x for x in (q["lead"] and f"lead {q['lead']}", q["free_over"] and f"free shipping over ${q['free_over']:.0f}") if x]
                out.append(f"  {name} ({q['date']}): {items}" + (f"; {', '.join(extra)}" if extra else ""))
        if self.orders or self.replies:
            sup = defaultdict(lambda: {"n": 0, "delay": [], "short": 0, "wrong": 0, "mis": 0})
            for o in self.orders.values():
                s = sup[o["supplier"]]
                s["n"] += 1
                for issue in [i.strip() for i in (o.get("issues") or "").split(",") if i.strip()]:
                    if issue.startswith("delay_"):
                        try:
                            s["delay"].append(int(issue[6:].rstrip("d")))
                        except ValueError:
                            pass
                    elif issue.startswith("short_ship"):
                        s["short"] += 1
                    elif issue.startswith("wrong_ship"):
                        s["wrong"] += 1
                if o.get("misdirected"):
                    s["mis"] += 1
            if sup:
                out.append("- Orders by supplier: " + "; ".join(
                    f"{name}: {s['n']} orders"
                    + (f", {len(s['delay'])} late (avg {sum(s['delay']) / len(s['delay']):.0f}d)" if s["delay"] else ", none late")
                    + (f", {s['short']} short-shipped" if s["short"] else "")
                    + (f", {s['wrong']} wrong item" if s["wrong"] else "")
                    + (f", {s['mis']} delivered to a wrong address" if s["mis"] else "")
                    for name, s in sorted(sup.items(), key=lambda x: -x[1]["n"])[:5]))
            bad = []
            for e, outcomes in self.replies.items():
                refusals = {k: v for k, v in outcomes.items() if k.startswith("order refused") or k in ("bounce", "delivery misdirected")}
                if refusals:
                    name = self.directory.get(e, {}).get("name", e)
                    txt = ", ".join(f"{k} x{v}" for k, v in refusals.items())
                    if self.missing_info.get(e):
                        txt += f" (missing: {', '.join(sorted(self.missing_info[e]))})"
                    bad.append(f"{name}: {txt}")
            if bad:
                out.append("- Replies that did not go through: " + "; ".join(bad[:5]))
        return out

    def _loss_lines(self):
        out = []
        if self.sent:
            flagged = [s for s in self.sent if s["flags"]]
            show = (flagged or self.sent)[-4:]
            out.append("- Emails you sent to addresses that are not suppliers found in search: " + "; ".join(
                f"{s['date']} {s['to']}" + (f" (contained {' and '.join(s['flags'])})" if s["flags"] else "") for s in show))
        if self.losses:
            fees = [x for x in self.losses if x["kind"] == "daily_fee_failed"]
            other = [x for x in self.losses if x["kind"] != "daily_fee_failed"]
            cells = []
            for x in other[-5:]:
                c = f"{x['date']} {x['kind']} {x['amount']:+.2f} ({x['desc'][:50]})"
                if x["kind"] == "machine_cash_theft":
                    c += f", machine cash before: ${x['cash_before']:.0f}"
                cells.append(c)
            if fees:
                cells.append(f"{len(fees)} daily fees not paid (bank too low), last {fees[-1]['date']}")
            out.append("- Losses: " + "; ".join(cells))
        return out

    def dump(self) -> dict:
        return {"days": self.days, "orders": self.orders, "directory": self.directory, "quotes": self.quotes,
                "replies": {k: dict(v) for k, v in self.replies.items()}, "sent": self.sent,
                "losses": self.losses, "worth": self.worth, "refusals": self.refusals}
