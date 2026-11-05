"""Airline adapter for the Register: rules as data, taken from tau2's airline policy.

The airline API checks almost nothing (the policy says so: "The API does not check
these for the agent"), so these rules are the only thing standing between a
persuasive customer and a wrong write. Facts come only from tool results the
agent received and from the conversation, never from hidden task data.
"""

from __future__ import annotations

import re
from typing import Optional

from register_agent import ACTION_WORDS, RetailRegister, _parse
from tau2.data_model.message import ToolCall, ToolMessage, UserMessage

NOW_DATE = "2024-05-15"  # policy: "The current time is 2024-05-15 15:00:00 EST."
CUTOFF_24H = "2024-05-14T15:00:00"

WRITES = {
    "book_reservation", "cancel_reservation", "update_reservation_baggages",
    "update_reservation_flights", "update_reservation_passengers", "send_certificate",
}
RES_WRITES = WRITES - {"book_reservation", "send_certificate"}

FREE_BAGS = {  # per passenger, by the booking user's membership and the cabin
    "regular": {"basic_economy": 0, "economy": 1, "business": 2},
    "silver": {"basic_economy": 1, "economy": 2, "business": 3},
    "gold": {"basic_economy": 2, "economy": 3, "business": 4},
}
INSURED_REASON = re.compile(
    r"health|sick|\bill\b|illness|medical|hospital|injur|surgery|doctor|emergenc|"
    r"weather|storm|hurricane|snow|flood|tornado", re.I)
ASKED_COMPENSATION = re.compile(
    r"compensat|certificate|voucher|refund|reimburs|credit|gesture|make up for|"
    r"make it up|something for the (trouble|inconvenience)", re.I)

ACTION_WORDS.update({
    "book_reservation": r"book|reserv",
    "cancel_reservation": r"cancel",
    "update_reservation_baggages": r"bag",
    "update_reservation_flights": r"flight|chang|modif|switch|upgrad|downgrad|cabin|class",
    "update_reservation_passengers": r"passenger|name|chang|updat",
    "send_certificate": r"certificate|compensat|voucher|credit",
})

RULES = [
    {"id": "one_call_per_turn", "tools": WRITES,
     "policy": "You should only make one tool call at a time."},
    {"id": "user_identified", "tools": WRITES,
     "policy": "The agent must first obtain the user id from the user."},
    {"id": "reservation_checked", "tools": RES_WRITES,
     "policy": "First, the agent must obtain the user id and reservation id."},
    {"id": "same_user", "tools": WRITES,
     "policy": "All payment methods must already be in user profile for safety reasons."},
    {"id": "cancel_not_flown", "tools": {"cancel_reservation"},
     "policy": "If any portion of the flight has already been flown, the agent cannot help."},
    {"id": "cancel_eligible", "tools": {"cancel_reservation"},
     "policy": "Cancel only if booked within 24 hrs, cancelled by airline, business, or "
               "insured with a covered reason."},
    {"id": "basic_economy_locked", "tools": {"update_reservation_flights"},
     "policy": "Basic economy flights cannot be modified."},
    {"id": "same_route", "tools": {"update_reservation_flights"},
     "policy": "Reservations can be modified without changing the origin, destination, "
               "and trip type."},
    {"id": "cabin_not_flown", "tools": {"update_reservation_flights"},
     "policy": "Cabin cannot be changed if any flight in the reservation has already been flown."},
    {"id": "change_payment", "tools": {"update_reservation_flights"},
     "policy": "If the flights are changed, the user needs to provide a single gift card "
               "or credit card for payment or refund method."},
    {"id": "bags_add_only", "tools": {"update_reservation_baggages"},
     "policy": "The user can add but not remove checked bags."},
    {"id": "bags_allowance", "tools": {"update_reservation_baggages", "book_reservation"},
     "policy": "Free checked bags depend on membership and cabin; each extra bag is 50 dollars."},
    {"id": "booking_limits", "tools": {"book_reservation"},
     "policy": "At most five passengers; at most one travel certificate, one credit card "
               "and three gift cards."},
    {"id": "compensation_asked", "tools": {"send_certificate"},
     "policy": "Do not proactively offer a compensation unless the user explicitly asks for one."},
    {"id": "compensation_eligible", "tools": {"send_certificate"},
     "policy": "Only compensate if the user is a silver/gold member or has travel "
               "insurance or flies business."},
    {"id": "compensation_amount", "tools": {"send_certificate"},
     "policy": "$100 x passengers for cancelled flights, $50 x passengers for delayed flights."},
    {"id": "explicit_yes", "tools": WRITES,
     "policy": "Before taking any actions that update the booking database, list the "
               "action details and obtain explicit user confirmation (yes) to proceed."},
]


def _user_text(history) -> str:
    return "\n".join(m.content for m in history if isinstance(m, UserMessage) and m.content)


class AirlineRegister(RetailRegister):
    WRITES = WRITES

    def __init__(self):
        super().__init__()
        self.users: dict[str, dict] = {}
        self.res: dict[str, dict] = {}
        self.flight_od: dict[str, tuple] = {}  # flight number -> (origin, destination)
        self.status: dict[tuple, str] = {}  # (flight number, date) -> status

    # ---- observe ----
    def observe(self, call: ToolCall, result: ToolMessage, turn: int) -> None:
        data = _parse(result.content)
        name, args = call.name, call.arguments or {}
        if not result.error:
            if name == "get_user_details" and isinstance(data, dict) and data.get("user_id"):
                self.users[data["user_id"]] = data
                if self.user is None:
                    self.user = data["user_id"]
                for pid, pm in (data.get("payment_methods") or {}).items():
                    lab = pm.get("source", pid)
                    if "amount" in pm:
                        lab += f" (balance {pm['amount']})"
                    self.payments[pid] = lab
            elif name == "get_flight_status" and isinstance(data, str):
                self.status[(args.get("flight_number"), args.get("date"))] = data
            elif name in ("search_direct_flight", "search_onestop_flight") and isinstance(data, list):
                for item in data:
                    for f in (item if isinstance(item, list) else [item]):
                        if isinstance(f, dict) and f.get("flight_number"):
                            self.flight_od[f["flight_number"]] = (f.get("origin"), f.get("destination"))
            if isinstance(data, dict) and data.get("reservation_id"):
                self.res[data["reservation_id"]] = data
                for f in data.get("flights") or []:
                    self.flight_od[f.get("flight_number")] = (f.get("origin"), f.get("destination"))
        if name in WRITES:
            key = self.pending_yes.pop(call.id, None)
            if result.error and key is not None:
                self.used_yes.discard(key)
            self.touches.append({
                "tool": name, "args": args, "turn": turn,
                "status": "failed" if result.error else "done",
                "result": (result.content or "")[:200] if result.error else "",
            })

    # ---- helpers ----
    def _flown(self, r: dict) -> bool:
        for f in r.get("flights") or []:
            if str(f.get("date")) < NOW_DATE:
                return True
            if self.status.get((f.get("flight_number"), f.get("date"))) in ("flying", "landed"):
                return True
        return False

    def _free(self, uid: Optional[str], cabin: Optional[str], npax: int) -> Optional[int]:
        u = self.users.get(uid or "")
        if not u or cabin not in FREE_BAGS.get(u.get("membership"), {}):
            return None
        return FREE_BAGS[u["membership"]][cabin] * npax

    # ---- check ----
    def check(self, call: ToolCall, history: list, n_calls: int) -> list[tuple[str, str]]:
        name, a = call.name, call.arguments or {}
        rid = a.get("reservation_id")
        r = self.res.get(rid) if rid else None
        users_said = _user_text(history)
        self._yes_key = None
        out = []
        for rule in RULES:
            if name not in rule["tools"]:
                continue
            k, why = rule["id"], None
            if k == "one_call_per_turn" and n_calls > 1:
                why = "this write was sent together with other tool calls; send it alone."
            elif k == "user_identified":
                if self.user is None:
                    why = "no user profile looked up yet; ask the user for their user id and call get_user_details."
                elif self.user.lower() not in users_said.lower():
                    why = f"the user id {self.user} was not given by the user; the user must provide it."
            elif k == "reservation_checked" and r is None:
                why = f"reservation {rid} has not been looked up; call get_reservation_details first."
            elif k == "same_user" and self.user is not None:
                owner = r.get("user_id") if r else a.get("user_id")
                if owner is not None and owner != self.user:
                    why = f"this belongs to user {owner}, but the user in this conversation is {self.user}."
                elif r is not None and r.get("status") == "cancelled":
                    why = f"reservation {rid} is already cancelled."
            elif k == "cancel_not_flown" and r is not None and self._flown(r):
                why = ("a segment of this reservation has already been flown; it cannot be "
                       "cancelled here, transfer to a human agent.")
            elif k == "cancel_eligible" and r is not None and not self._flown(r):
                within = str(r.get("created_at", "")) >= CUTOFF_24H
                business = r.get("cabin") == "business"
                statuses = [self.status.get((f.get("flight_number"), f.get("date"))) for f in r.get("flights") or []]
                airline = any(s == "cancelled" for s in statuses)
                insured = r.get("insurance") == "yes" and bool(INSURED_REASON.search(users_said))
                if not (within or business or airline or insured):
                    unknown = sum(1 for s in statuses if s is None)
                    why = (f"not eligible: booked {r.get('created_at')} (over 24 h ago), cabin "
                           f"{r.get('cabin')}, insurance {r.get('insurance')}"
                           + (", and the user gave no health or weather reason" if r.get("insurance") == "yes" else "")
                           + (f"; {unknown} flight status(es) unchecked, check get_flight_status "
                              "in case the airline cancelled" if unknown else "; no flight cancelled by the airline")
                           + ". Deny the cancellation unless one of these changes.")
            elif k == "basic_economy_locked" and r is not None and r.get("cabin") == "basic_economy":
                old = sorted((f.get("flight_number"), f.get("date")) for f in r.get("flights") or [])
                new = sorted((f.get("flight_number"), f.get("date")) for f in a.get("flights") or [])
                if old != new:
                    why = "this is a basic economy reservation: its flights cannot be changed (only the cabin can)."
            elif k == "same_route" and r is not None:
                legs = [self.flight_od.get(f.get("flight_number")) for f in a.get("flights") or []]
                if legs and all(legs):
                    o, d = r.get("origin"), r.get("destination")
                    if r.get("flight_type") == "one_way":
                        ok = legs[0][0] == o and legs[-1][1] == d
                    else:
                        ok = legs[0][0] == o and legs[-1][1] == o and any(x[1] == d for x in legs)
                    if not ok:
                        why = f"the new itinerary changes the route; it must stay {o} -> {d} ({r.get('flight_type')})."
            elif k == "cabin_not_flown" and r is not None and a.get("cabin") != r.get("cabin") and self._flown(r):
                why = "a flight of this reservation has been flown, so the cabin cannot change."
            elif k == "change_payment" and r is not None:
                old = sorted((f.get("flight_number"), f.get("date")) for f in r.get("flights") or [])
                new = sorted((f.get("flight_number"), f.get("date")) for f in a.get("flights") or [])
                src = (self.users.get(self.user or "", {}).get("payment_methods") or {}).get(a.get("payment_id"), {}).get("source")
                if old != new and src is not None and src not in ("credit_card", "gift_card"):
                    why = "flight changes must be paid or refunded with a single gift card or credit card from the profile."
            elif k == "bags_add_only" and r is not None and int(a.get("total_baggages", 0)) < int(r.get("total_baggages", 0)):
                why = f"checked bags can only be added: the reservation already has {r.get('total_baggages')}."
            elif k == "bags_allowance":
                if name == "book_reservation":
                    free = self._free(a.get("user_id"), a.get("cabin"), len(a.get("passengers") or []))
                else:
                    free = self._free(r.get("user_id"), r.get("cabin"), len(r.get("passengers") or [])) if r else None
                if free is not None:
                    expect = max(0, int(a.get("total_baggages", 0)) - free)
                    if int(a.get("nonfree_baggages", -1)) != expect:
                        why = (f"with {a.get('total_baggages')} bags and {free} free for this membership, cabin and "
                               f"passenger count, nonfree_baggages must be {expect}.")
            elif k == "booking_limits":
                pms = a.get("payment_methods") or []
                prof = (self.users.get(a.get("user_id") or "", {}).get("payment_methods") or {})
                src = [(prof.get(p.get("payment_id")) or {}).get("source") for p in pms if isinstance(p, dict)]
                if len(a.get("passengers") or []) > 5:
                    why = "a reservation can have at most five passengers."
                elif src.count("certificate") > 1 or src.count("credit_card") > 1 or src.count("gift_card") > 3:
                    why = "at most one travel certificate, one credit card and three gift cards per reservation."
            elif k == "compensation_asked" and not ASKED_COMPENSATION.search(users_said):
                why = "the user has not asked for compensation; do not offer it proactively."
            elif k == "compensation_eligible":
                u = self.users.get(a.get("user_id") or "", {})
                theirs = [x for x in self.res.values() if x.get("user_id") == a.get("user_id")]
                if not theirs:
                    why = "confirm the facts first: look up the reservation the complaint is about."
                elif u and u.get("membership") == "regular" and not any(
                        x.get("insurance") == "yes" or x.get("cabin") == "business" for x in theirs):
                    why = "regular member, no travel insurance, (basic) economy: no compensation."
            elif k == "compensation_amount":
                theirs = [x for x in self.res.values() if x.get("user_id") == a.get("user_id")]
                allowed = sorted({m * len(x.get("passengers") or []) for x in theirs for m in (50, 100)})
                if theirs and int(a.get("amount", 0)) not in allowed:
                    why = (f"the amount must be $100 x passengers (cancelled flight) or $50 x passengers "
                           f"(delayed flight): {allowed} for the reservations looked up.")
            elif k == "explicit_yes":
                why = self._confirmation_problem(call, history)
            if why:
                out.append((k, why))
        if not out and self._yes_key is not None:
            self.used_yes.add(self._yes_key)
            self.pending_yes[call.id] = self._yes_key
        return out

    def _target(self, call: ToolCall) -> list[str]:
        a = call.arguments or {}
        if call.name in RES_WRITES:
            out = [str(a.get("reservation_id"))] if a.get("reservation_id") else []
            out += [str(f.get("flight_number")) for f in a.get("flights") or [] if isinstance(f, dict)]
            return out
        if call.name == "book_reservation":
            fl = [str(f.get("flight_number")) for f in a.get("flights") or [] if isinstance(f, dict)]
            return fl + [str(a.get("destination") or "")]
        if call.name == "send_certificate":
            return [str(a.get("amount"))]
        return []

    def render(self, call: ToolCall) -> str:
        a = call.arguments or {}
        fl = lambda fs: ", ".join(f"{f.get('flight_number')} on {f.get('date')}" for f in fs or [] if isinstance(f, dict))  # noqa: E731
        return {
            "book_reservation": f"book {a.get('flight_type')} {a.get('origin')}->{a.get('destination')} in {a.get('cabin')} "
                                f"on {fl(a.get('flights'))} for {len(a.get('passengers') or [])} passenger(s), "
                                f"{a.get('total_baggages')} bags ({a.get('nonfree_baggages')} paid), insurance {a.get('insurance')}",
            "cancel_reservation": f"cancel reservation {a.get('reservation_id')}",
            "update_reservation_baggages": f"set reservation {a.get('reservation_id')} to {a.get('total_baggages')} bags "
                                           f"({a.get('nonfree_baggages')} paid) using {a.get('payment_id')}",
            "update_reservation_flights": f"change reservation {a.get('reservation_id')} to {a.get('cabin')} on {fl(a.get('flights'))} "
                                          f"using {a.get('payment_id')}",
            "update_reservation_passengers": f"update the passengers of reservation {a.get('reservation_id')}",
            "send_certificate": f"send a ${a.get('amount')} certificate to {a.get('user_id')}",
        }.get(call.name, call.name)

    def brief(self) -> str:
        lines = [f"User: {self.user or 'not identified yet'}"
                 + (f" ({self.users[self.user].get('membership')})" if self.user in self.users else "")]
        for rid, r in self.res.items():
            fl = ", ".join(f"{f.get('flight_number')} {f.get('date')}" for f in r.get("flights") or [])
            lines.append(f"- reservation {rid}: {r.get('flight_type')} {r.get('origin')}->{r.get('destination')}, "
                         f"{r.get('cabin')}, {len(r.get('passengers') or [])} pax, bags {r.get('total_baggages')}, "
                         f"insurance {r.get('insurance')}, status {r.get('status') or 'active'}; flights {fl}")
        done = [t for t in self.touches if t["status"] == "done"]
        lines.append("Actions DONE (do not repeat):" if done else "Actions done: none")
        for t in done:
            lines.append("- " + self.render(ToolCall(id="x", name=t["tool"], arguments=t["args"])))
        return "\n".join(lines)
