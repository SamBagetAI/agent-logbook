"""Logbook layer for Prosus Vending Bench.

It sits between the agent and the 22 business tools. It only reads what the
agent itself receives (tool results), never simulator internals.

  record  every action and every fact the agent saw: complaints and what is still
          owed, orders and deliveries, the depot, each slot, the offers seen
  brief   a short "where am I" block appended to get_status and to the morning
          report of wait_for_next_day, so it survives the rolling window
  check   refuses three actions the briefing calls irreversible mistakes:
          a refund that is not the full amount still owed, a payment whose
          reference matches nothing seen, the same order placed twice in a day
"""
from __future__ import annotations

import json
import re

DAY = re.compile(r"\[Day (\d+)/\d+")
MORNING = re.compile(r"--- Day (\d+),")
CMP_STATUS = re.compile(r"^\s*(CMP-\d+)\s+—\s+(.+?)\s+—\s+€([\d,]+\.\d\d)", re.M)
CMP_EMAIL_REF = re.compile(r"Refund reference:\s*(CMP-\d+)")
CMP_EMAIL_AMT = re.compile(r"Amount owed:\s*€([\d,]+\.\d\d)")
EMAIL_FROM = re.compile(r"^From:\s+(.+?)\s+<", re.M)
PO_STATUS = re.compile(r"^\s*(PO-\d+)\s+—\s+(.+?)\s+—\s+(\w+)\s+—\s+€([\d,]+\.\d\d)(?:\s+—\s+due day (\d+))?", re.M)
DELIVERED = re.compile(r"Delivery (PO-\d+) arrived in storage")
STORAGE_ROW = re.compile(r"^([a-z0-9][a-z0-9-]+)\s{2,}.+?\s(small|large)\s+(\d+)\s+€", re.M)
SLOT_ROW = re.compile(r"^([A-Z-]+:(?:SNACK|DRINKS):[A-D]\d)\s+(small|large)\s+(.+?)\s{2,}(\d+)\s+(\d+)\s+(€[\d.,]+|-)", re.M)
LOADED = re.compile(r"Loaded (\d+) x (.+?) into slot (\S+) \((\d+)/(\d+)\)")
PRICED = re.compile(r"Slot (\S+) \((.+?)\) is now €([\d.,]+)")
SENT_FULL = re.compile(r"Sent €([\d,]+\.\d\d) for (CMP-\d+)\. The customer has confirmed")
SENT_PART = re.compile(r"Sent €([\d,]+\.\d\d) towards (CMP-\d+), but .+? is still €([\d,]+\.\d\d) short")


def money(s: str) -> float:
    return float(s.replace(",", "").replace("€", ""))


class Logbook:
    def __init__(self):
        self.day = 1
        self.complaints: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        self.depot: dict[str, int] = {}
        self.depot_day = None
        self.slots: dict[str, dict] = {}
        self.slots_day = None
        self.offers: dict[str, tuple] = {}  # product -> (unit price, supplier, valid through)
        self.actions: list[tuple] = []
        self.refusals = 0
        self.refused: list[dict] = []

    # ------------------------------------------------------------------ check
    def check(self, name: str, a: dict):
        why = None
        if name == "send_payment":
            ref = str(a.get("reference", "")).strip().upper()
            try:
                amount = round(float(a.get("amount", 0)), 2)
            except (TypeError, ValueError):
                amount = None
            if ref.startswith("CMP-"):
                c = self.complaints.get(ref)
                if c is None:
                    why = (f"no complaint {ref} in what you have read. Unmatched payments are lost: "
                           "read the customer's email for the exact reference and amount first.")
                elif c["status"] == "refunded":
                    why = f"{ref} was already refunded in full on day {c.get('closed_day')}; a second payment is lost."
                else:
                    owed = round(c["amount"] - c["paid"], 2)
                    if amount is not None and abs(amount - owed) > 0.01:
                        why = (f"{c['customer']} is owed €{owed:.2f} on {ref} (asked €{c['amount']:.2f}, "
                               f"paid so far €{c['paid']:.2f}). Refunds must cover the full amount; "
                               f"send exactly {owed:.2f}.")
            elif ref.startswith("PO-"):
                o = self.orders.get(ref)
                if o is None or o.get("status") != "quoted":
                    why = (f"{ref} is not an unpaid quote ({(o or {}).get('status', 'unknown')}). Goods are paid "
                           "automatically when ordered; a second payment is lost.")
            else:
                why = ("this reference matches no complaint (CMP-...) or quote you have seen. "
                       "Unmatched payments are lost.")
        elif name == "order_goods":
            key = (a.get("supplier_id"), json.dumps(a.get("items") or {}, sort_keys=True))
            same = [oid for oid, o in self.orders.items() if o.get("key") == key and o.get("day") == self.day]
            if same:
                why = (f"you already placed this exact order today ({', '.join(same)}). There is no "
                       "cancellation; change the quantities if you really want more.")
        if why:
            self.refusals += 1
            self.refused.append({"day": self.day, "tool": name, "args": a, "why": why})
            return "LOGBOOK REFUSED, nothing was executed: " + why + "\n\n" + self.brief()
        return None

    # ------------------------------------------------------------------ record
    def after(self, name: str, a: dict, content: str) -> str:
        try:
            self._record(name, a, content)
        except Exception:  # noqa: BLE001 - the layer must never break a run
            pass
        if name in ("get_status", "wait_for_next_day"):
            return content + "\n\n" + self.brief()
        return content

    def _record(self, name, a, content):
        m = DAY.search(content)
        if m:
            self.day = int(m.group(1))
        m = MORNING.search(content)
        if m:
            self.day = int(m.group(1))
        self.actions.append((self.day, name, {k: a[k] for k in list(a)[:3]}))
        if name == "get_status":
            open_now = set()
            for ref, cust, amt in CMP_STATUS.findall(content):
                c = self.complaints.setdefault(ref, {"customer": cust, "amount": money(amt), "paid": 0.0,
                                                     "status": "open", "seen_day": self.day})
                c["status"] = "open"
                open_now.add(ref)
            for oid, sup, status, total, due in PO_STATUS.findall(content):
                o = self.orders.setdefault(oid, {"supplier": sup, "total": money(total), "day": None})
                o["status"] = status
                if due:
                    o["due"] = int(due)
        elif name == "read_email":
            ref, amt = CMP_EMAIL_REF.search(content), CMP_EMAIL_AMT.search(content)
            if ref and amt:
                who = EMAIL_FROM.search(content)
                self.complaints.setdefault(ref.group(1), {
                    "customer": who.group(1) if who else "customer", "amount": money(amt.group(1)),
                    "paid": 0.0, "status": "open", "seen_day": self.day})
        elif name == "send_payment":
            m = SENT_FULL.search(content)
            if m and m.group(2) in self.complaints:
                c = self.complaints[m.group(2)]
                c["paid"] = round(c["paid"] + money(m.group(1)), 2)
                c["status"], c["closed_day"] = "refunded", self.day
            m = SENT_PART.search(content)
            if m and m.group(2) in self.complaints:
                c = self.complaints[m.group(2)]
                c["paid"] = round(c["paid"] + money(m.group(1)), 2)
        elif name == "order_goods":
            r = json.loads(content.split("\n\n[Day")[0]) if content.strip().startswith("{") else {}
            if r.get("order_id"):
                self.orders[r["order_id"]] = {
                    "supplier": a.get("supplier_id"), "total": r.get("paid"), "due": r.get("delivery_day"),
                    "status": "paid", "day": self.day,
                    "lines": {ln["product_id"]: ln["quantity"] for ln in r.get("lines") or []},
                    "key": (a.get("supplier_id"), json.dumps(a.get("items") or {}, sort_keys=True))}
        elif name == "check_offers":
            r = json.loads(content.split("\n\n[Day")[0]) if content.strip().startswith("{") else {}
            for pid, off in (r.get("offers") or {}).items():
                p = off.get("unit_price")
                if p is not None and (pid not in self.offers or p < self.offers[pid][0] or
                                      self.offers[pid][2] < self.day):
                    self.offers[pid] = (p, r.get("supplier_id"), r.get("valid_through_day"))
        elif name == "get_storage_inventory":
            self.depot = {pid: int(q) for pid, _size, q in STORAGE_ROW.findall(content)}
            if "Storage is empty" in content:
                self.depot = {}
            self.depot_day = self.day
        elif name == "get_machine_inventory":
            for slot, _t, prod, qty, cap, price in SLOT_ROW.findall(content):
                self.slots[slot] = {"product": None if prod.strip() == "(empty)" else prod.strip(),
                                    "qty": int(qty), "cap": int(cap),
                                    "price": None if price == "-" else money(price), "day": self.day}
            self.slots_day = self.day
        elif name in ("restock_machine", "swap_item"):
            m = LOADED.search(content)
            if m:
                n, prod, slot, now, cap = m.groups()
                s = self.slots.setdefault(slot, {"price": None})
                if name == "swap_item" or s.get("product") != prod:
                    s["price"] = None if "no price set" in content else s.get("price")
                s.update(product=prod, qty=int(now), cap=int(cap), day=self.day)
                if "no price set" in content:
                    s["price"] = None
                pid = a.get("product_id")
                if pid in self.depot:
                    self.depot[pid] = max(0, self.depot[pid] - int(n))
        elif name == "set_price":
            m = PRICED.search(content)
            if m:
                s = self.slots.setdefault(m.group(1), {})
                s["price"] = money(m.group(3))
        elif name == "clear_slot":
            slot = a.get("slot")
            if slot and "cleared" in content.lower() or "back to" in content.lower():
                self.slots[slot] = {"product": None, "qty": 0, "price": None, "day": self.day}
        if name == "wait_for_next_day":
            for oid in DELIVERED.findall(content):
                o = self.orders.setdefault(oid, {})
                o["status"] = "delivered"
                for pid, q in (o.get("lines") or {}).items():
                    self.depot[pid] = self.depot.get(pid, 0) + q

    # ------------------------------------------------------------------ brief
    def brief(self) -> str:
        L = ["LOGBOOK (recorded automatically from your tool results; survives context trimming)"]
        owed = [(r, c) for r, c in self.complaints.items() if c["status"] != "refunded"]
        if owed:
            L.append("Unresolved complaints (refund the full amount still owed, exact reference):")
            for r, c in owed:
                rest = round(c["amount"] - c["paid"], 2)
                L.append(f"- {r} {c['customer']}: €{rest:.2f} owed"
                         + (f" (asked €{c['amount']:.2f}, paid €{c['paid']:.2f})" if c["paid"] else ""))
        done = [r for r, c in self.complaints.items() if c["status"] == "refunded"]
        if done:
            L.append("Refunded already: " + ", ".join(done))
        pending = [(o_id, o) for o_id, o in self.orders.items() if o.get("status") in ("paid", "shipped", "quoted")]
        if pending:
            L.append("Orders not delivered yet: " + "; ".join(
                f"{o_id} {o.get('supplier')} due day {o.get('due')}" for o_id, o in pending))
        if self.depot_day is not None or self.depot:
            units = sum(self.depot.values())
            top = ", ".join(f"{p} {q}" for p, q in sorted(self.depot.items(), key=lambda x: -x[1])[:6] if q)
            L.append(f"Depot estimate: {units} units" + (f" ({top})" if top else "")
                     + f" [listed day {self.depot_day}, then deliveries and restocks applied]")
        if self.slots:
            stocked = [s for s, v in self.slots.items() if v.get("product") and v.get("qty")]
            unpriced = [s for s in stocked if not self.slots[s].get("price")]
            by_machine = {}
            for s, v in self.slots.items():
                mach = ":".join(s.split(":")[:2])
                k = by_machine.setdefault(mach, [0, 0])
                k[1] += 1
                k[0] += 1 if v.get("product") and v.get("qty") else 0
            # v2: slot stock levels are not shown. Sales empty slots overnight without the
            # agent seeing it, so a "last seen" count goes stale and misleads (seen in v1).
            if unpriced:
                L.append("Stocked but NO PRICE (sells nothing): " + ", ".join(unpriced[:10]))
        valid = {p: v for p, v in self.offers.items() if v[2] is None or v[2] >= self.day}
        if valid:
            cheap = sorted(valid.items(), key=lambda x: x[0])[:12]
            L.append("Lowest unit prices seen, still valid: " + "; ".join(
                f"{p} €{v[0]:.2f} {v[1]} (to day {v[2]})" for p, v in cheap))
        today = [n for d, n, _ in self.actions if d == self.day]
        if today:
            from collections import Counter
            L.append(f"Day {self.day} so far: " + ", ".join(f"{n} x{c}" for n, c in Counter(today).items()))
        return "\n".join(L)

    def dump(self) -> dict:
        return {"complaints": self.complaints, "orders": {k: {kk: vv for kk, vv in v.items() if kk != "key"}
                                                          for k, v in self.orders.items()},
                "depot": self.depot, "slots": self.slots, "offers": self.offers,
                "refusals": self.refused}
