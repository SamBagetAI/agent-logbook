"""Logbook agent for tau2-bench retail.

Same LLM, same system prompt, same policy, same tools as tau2's standard
`llm_agent`. The only addition is a Logbook that sits between the model and the
environment:

  * it records facts and touches from what the agent itself has already seen
    (tool results and the conversation), never from hidden task data;
  * before any write reaches the environment it runs `check`: rules written as
    data, taken from the retail policy text;
  * a refused write is never executed. The model gets the refusal as a tool
    result inside its own context and writes a new message. The orchestrator,
    the environment and the evaluator never see the refused call.

Every refused generation is billed to the message that finally goes out, so the
cost comparison with the standard agent stays honest.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from tau2.agent.llm_agent import LLMAgent, LLMAgentState
from tau2.data_model.message import (
    AssistantMessage,
    MultiToolMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.utils.llm_utils import generate

# ---------------------------------------------------------------------------
# Retail adapter: which tools write, and the rules as data (from policy.md)
# ---------------------------------------------------------------------------

WRITES = {
    "cancel_pending_order",
    "exchange_delivered_order_items",
    "modify_pending_order_address",
    "modify_pending_order_items",
    "modify_pending_order_payment",
    "modify_user_address",
    "return_delivered_order_items",
}
ORDER_WRITES = WRITES - {"modify_user_address"}
# Cancel and item changes need status exactly 'pending'. Address and payment changes
# also accept 'pending (item modified)': the policy text is stricter, but the
# environment allows it on purpose and the reference trajectories rely on it
# (found on the train split, tasks 41 and 72).
NEEDS_PENDING = {"cancel_pending_order", "modify_pending_order_items"}
NEEDS_PENDING_ANY = {"modify_pending_order_address", "modify_pending_order_payment"}
NEEDS_DELIVERED = {"return_delivered_order_items", "exchange_delivered_order_items"}
ONCE_PER_ORDER = {"modify_pending_order_items", "exchange_delivered_order_items"}
AUTH_TOOLS = {"find_user_id_by_email", "find_user_id_by_name_zip"}
ITEM_WRITES = {"modify_pending_order_items", "exchange_delivered_order_items",
               "return_delivered_order_items"}
PAY_WORDS = {"paypal": r"paypal", "gift_card": r"gift card", "credit_card": r"credit card|visa|mastercard|amex|american express|discover"}

# Each rule: id, the tools it guards, and the policy sentence it comes from.
RULES = [
    {"id": "one_call_per_turn", "tools": WRITES,
     "policy": "You should at most make one tool call at a time."},
    {"id": "authenticated", "tools": WRITES,
     "policy": "Authenticate the user identity by locating their user id via email, "
               "or via name + zip code."},
    {"id": "order_checked", "tools": ORDER_WRITES,
     "policy": "You should check its status before taking the action."},
    {"id": "same_user", "tools": WRITES,
     "policy": "You can only help one user per conversation."},
    {"id": "status_pending", "tools": NEEDS_PENDING | NEEDS_PENDING_ANY,
     "policy": "An order can only be cancelled or modified if its status is 'pending'."},
    {"id": "status_delivered", "tools": NEEDS_DELIVERED,
     "policy": "An order can only be returned or exchanged if its status is 'delivered'."},
    {"id": "once_per_order", "tools": ONCE_PER_ORDER,
     "policy": "Exchange or modify order tools can only be called once per order."},
    {"id": "explicit_yes", "tools": WRITES,
     "policy": "Before taking any action that updates the database, list the action "
               "details and obtain explicit user confirmation (yes) to proceed."},
    # v2: what is executed must be what the user confirmed (added after the
    # independent review of the test split; evaluated on the train split).
    {"id": "matches_confirmation", "tools": ITEM_WRITES,
     "policy": "List the action details and obtain explicit user confirmation: the "
               "items and payment executed must be the ones the user confirmed."},
]

AFFIRM = re.compile(
    r"\b(yes|yeah|yep|yup|sure|correct|confirm|confirmed|go ahead|proceed|please do|"
    r"do it|that's right|that is right|absolutely|ok|okay|sounds good|affirmative|"
    r"definitely|of course)\b",
    re.I,
)
# Strong refusals anywhere in the reply. A leading "No" is not one: "No longer
# needed. Yes, cancel it." and "Nope, that's all. Yes, go ahead." are both a yes
# (found on the train split).
DENY = re.compile(
    r"\b(don't|do not|dont) (proceed|do it|go ahead)\b|\bnot yet\b|\bhold off\b",
    re.I,
)
# The agent's confirmation request must name the kind of action it is about to take.
ACTION_WORDS = {
    "cancel_pending_order": r"cancel",
    "modify_pending_order_address": r"address",
    "modify_pending_order_items": r"modif|chang|exchang|swap|replac|updat|switch",
    "modify_pending_order_payment": r"pay",
    "return_delivered_order_items": r"return",
    "exchange_delivered_order_items": r"exchang|swap|replac|switch",
    "modify_user_address": r"address",
}


def _oid(x) -> str:
    """Normalise an order id: '#W123', 'W123' and 'w123' are the same order."""
    return str(x or "").strip().lstrip("#").upper()


def _parse(content: Optional[str]):
    try:
        return json.loads(content) if content else None
    except Exception:
        return content


# ---------------------------------------------------------------------------
# The Logbook: facts + touches for one conversation
# ---------------------------------------------------------------------------


class RetailLogbook:
    WRITES = WRITES

    def __init__(self):
        self.user: Optional[str] = None  # authenticated user id
        self.orders: dict[str, dict] = {}  # oid -> {user, status, items{id: label}}
        self.labels: dict[str, str] = {}  # item id -> readable label
        self.payments: dict[str, str] = {}  # payment id -> readable label
        self.price: dict[str, float] = {}  # item id -> price seen in a tool result
        self.pay_source: dict[str, str] = {}  # payment id -> paypal / credit_card / gift_card
        self.touches: list[dict] = []  # every write: intended / done / failed / refused
        self.used_yes: set = set()  # (user message index, tool, target) already spent
        self.pending_yes: dict = {}  # call id -> key, released if the call fails
        self.events: list[dict] = []

    # ---- observe: facts come only from tool results the agent received ----
    def observe(self, call: ToolCall, result: ToolMessage, turn: int) -> None:
        data = _parse(result.content)
        name, args = call.name, call.arguments or {}
        if name in AUTH_TOOLS and not result.error and isinstance(data, str):
            if self.user is None:
                self.user = data
        elif name == "get_user_details" and isinstance(data, dict):
            for pid, pm in (data.get("payment_methods") or {}).items():
                label = pm.get("source", pid)
                if "balance" in pm:
                    label += f" (balance {pm['balance']})"
                self.payments[pid] = label
                if pm.get("source"):
                    self.pay_source[pid] = pm["source"]
        elif name == "get_product_details" and isinstance(data, dict):
            pname = data.get("name", "")
            for vid, v in (data.get("variants") or {}).items():
                opts = ", ".join(f"{k} {val}" for k, val in (v.get("options") or {}).items())
                self.labels[vid] = f"{pname} ({opts})"
                if isinstance(v.get("price"), (int, float)):
                    self.price[vid] = float(v["price"])
        if isinstance(data, dict) and "order_id" in data and not result.error:
            items = {}
            for it in data.get("items") or []:
                opts = ", ".join(f"{k} {v}" for k, v in (it.get("options") or {}).items())
                self.labels[it.get("item_id")] = f"{it.get('name')} ({opts})"
                items[it.get("item_id")] = self.labels[it.get("item_id")]
                if isinstance(it.get("price"), (int, float)):
                    self.price.setdefault(it.get("item_id"), float(it["price"]))
            self.orders[_oid(data["order_id"])] = {
                "user": data.get("user_id"),
                "status": data.get("status"),
                "items": items,
            }
        if name in WRITES:
            key = self.pending_yes.pop(call.id, None)
            if result.error and key is not None:
                self.used_yes.discard(key)  # a failed call does not spend the yes
            self.touches.append({
                "tool": name, "args": args, "turn": turn,
                "status": "failed" if result.error else "done",
                "result": (result.content or "")[:200] if result.error else "",
            })

    # ---- check: run the rules on one proposed write ----
    def check(self, call: ToolCall, history: list, n_calls: int) -> list[tuple[str, str]]:
        name, args = call.name, call.arguments or {}
        oid = _oid(args.get("order_id")) if name in ORDER_WRITES else None
        order = self.orders.get(oid) if oid else None
        self._yes_key = None
        out = []
        for rule in RULES:
            if name not in rule["tools"]:
                continue
            rid = rule["id"]
            why = None
            if rid == "one_call_per_turn" and n_calls > 1:
                why = "this write was sent together with other tool calls; send it alone."
            elif rid == "authenticated" and self.user is None:
                why = ("the user is not authenticated yet; find their user id by email "
                       "or by name + zip first.")
            elif rid == "order_checked" and order is None:
                why = f"order {args.get('order_id')} has not been looked up in this conversation; call get_order_details first."
            elif rid == "same_user" and self.user is not None:
                owner = order["user"] if order else (args.get("user_id") if name == "modify_user_address" else None)
                if owner is not None and owner != self.user:
                    why = f"this belongs to user {owner}, but the authenticated user is {self.user}."
            elif rid == "status_pending" and order is not None and not (
                order["status"] == "pending"
                or (name in NEEDS_PENDING_ANY and "pending" in str(order["status"]))
            ):
                why = f"order {args.get('order_id')} has status '{order['status']}', not 'pending'."
            elif rid == "status_delivered" and order is not None and order["status"] != "delivered":
                why = f"order {args.get('order_id')} has status '{order['status']}', not 'delivered'."
            elif rid == "once_per_order" and any(
                t["tool"] == name and t["status"] == "done" and _oid(t["args"].get("order_id")) == oid
                for t in self.touches
            ):
                why = f"{name} was already done on order {args.get('order_id')}; it can only be called once per order."
            elif rid == "explicit_yes":
                why = self._confirmation_problem(call, history)
            elif rid == "matches_confirmation":
                why = self._mismatch(call, history)
            if why:
                out.append((rid, why))
        if not out and self._yes_key is not None:
            self.used_yes.add(self._yes_key)
            self.pending_yes[call.id] = self._yes_key
        return out

    def _confirmation_problem(self, call: ToolCall, history: list) -> Optional[str]:
        """The last user message must be a yes to an agent message that named the target."""
        u_idx = next((i for i in range(len(history) - 1, -1, -1)
                      if isinstance(history[i], UserMessage) and history[i].content), None)
        details = self.render(call)
        ask = f"List these details to the user and ask for an explicit yes: {details}."
        if u_idx is None:
            return "no user confirmation yet. " + ask
        reply = history[u_idx].content or ""
        if DENY.search(reply) or not AFFIRM.search(reply):
            return "the user's last message is not an explicit yes. " + ask
        # The confirmation request may be the agent's last message or the one before
        # it (users often confirm two pending actions in one reply).
        asks = [history[i].content or "" for i in range(u_idx - 1, -1, -1)
                if isinstance(history[i], AssistantMessage) and history[i].has_text_content()][:2]
        target = self._target(call)
        stated = any(
            any(t.lower() in said.lower() for t in target)
            and re.search(ACTION_WORDS.get(call.name, r"."), said, re.I)
            for said in asks
        ) if target else False
        if not stated:
            return "your recent messages to the user did not state this action. " + ask
        key = (reply.strip().lower(), call.name, target[0])  # survives a restart
        if key in self.used_yes:
            return "that yes was already used for this exact action. " + ask
        self._yes_key = key  # spent only if every rule passes
        return None

    def _mismatch(self, call: ToolCall, history: list) -> Optional[str]:
        """The items (and payment) in the call must be the ones described to the user."""
        u_idx = next((i for i in range(len(history) - 1, -1, -1)
                      if isinstance(history[i], UserMessage) and history[i].content), None)
        if u_idx is None:
            return None  # explicit_yes already covers this case
        asks = [history[i].content or "" for i in range(u_idx - 1, -1, -1)
                if isinstance(history[i], AssistantMessage) and history[i].has_text_content()][:2]
        text = " ".join(asks)
        a = call.arguments or {}
        ids = a.get("new_item_ids") if call.name != "return_delivered_order_items" else a.get("item_ids")
        wrong = []
        for i in [str(x) for x in ids or []]:
            p = self.price.get(i)
            if i in text or p is None:
                continue
            if not any(f in text for f in {f"{p:.2f}", f"{p:,.2f}", str(p)}):
                wrong.append(f"{self.labels.get(i, 'item')} [{i}] at ${p:.2f}")
        pid = str(a.get("payment_method_id") or "")
        src = self.pay_source.get(pid)
        if src and pid not in text and not re.search(PAY_WORDS.get(src, "$^"), text, re.I):
            others = [k for k, rx in PAY_WORDS.items() if k != src and re.search(rx, text, re.I)]
            if others:
                wrong.append(f"payment {pid} ({src}), but you told the user {', '.join(others)}")
        if not wrong:
            return None
        return ("this is not what you confirmed with the user: " + "; ".join(wrong) +
                ". Check the variant and payment, or describe exactly these to the user and get a new yes.")

    def _target(self, call: ToolCall) -> list[str]:
        """Strings that identify what the action touches: the order id first, then item
        ids and product names (agents usually describe items by name, not by id)."""
        a = call.arguments or {}
        if call.name in ORDER_WRITES:
            o = _oid(a.get("order_id"))
            ids = [str(i) for i in (a.get("item_ids") or []) + (a.get("new_item_ids") or [])]
            if not ids and o in self.orders:  # cancel, address, payment: the order's items
                ids = list((self.orders[o].get("items") or {}).keys())
            names = {self.labels[i].split(" (")[0] for i in ids if i in self.labels}
            pm = a.get("payment_method_id")
            extra = [str(pm)] if pm else []
            return ([o] if o else []) + ids + sorted(n for n in names if len(n) > 3) + extra
        return [str(a.get(k)) for k in ("zip", "address1") if a.get(k)]

    def brief(self) -> str:
        """Where am I: what a fresh instance needs to continue without redoing anything."""
        lines = [f"Authenticated user: {self.user or 'none yet'}"]
        if self.orders:
            lines.append("Orders looked up (last known status):")
            for oid, o in self.orders.items():
                items = "; ".join(f"{lab} [{iid}]" for iid, lab in (o.get("items") or {}).items())
                lines.append(f"- #{oid}: {o['status']} (owner {o['user']}). Items: {items or 'n/a'}")
        if self.payments:
            lines.append("Payment methods: " + "; ".join(f"{lab} [{pid}]" for pid, lab in self.payments.items()))
        done = [t for t in self.touches if t["status"] == "done"]
        failed = [t for t in self.touches if t["status"] == "failed"]
        lines.append("Actions DONE (already executed, do not repeat):" if done else "Actions done: none")
        for t in done:
            lines.append(f"- {self.render(ToolCall(id='x', name=t['tool'], arguments=t['args']))}")
        for t in failed:
            lines.append(f"- FAILED: {self.render(ToolCall(id='x', name=t['tool'], arguments=t['args']))} -> {t.get('result', '')}")
        return "\n".join(lines)

    def render(self, call: ToolCall) -> str:
        a = call.arguments or {}
        lab = lambda ids: ", ".join(f"{self.labels.get(i, 'item')} [{i}]" for i in ids or [])  # noqa: E731
        pm = lambda p: f"{self.payments.get(p, p)} [{p}]"  # noqa: E731
        addr = ", ".join(str(a.get(k)) for k in ("address1", "address2", "city", "state", "zip", "country") if a.get(k))
        o = a.get("order_id")
        return {
            "cancel_pending_order": f"cancel order {o} (reason: {a.get('reason')})",
            "modify_pending_order_address": f"ship order {o} to {addr}",
            "modify_pending_order_items": f"in order {o}, change {lab(a.get('item_ids'))} to {lab(a.get('new_item_ids'))}; price difference via {pm(a.get('payment_method_id'))}",
            "modify_pending_order_payment": f"pay order {o} with {pm(a.get('payment_method_id'))}",
            "return_delivered_order_items": f"return {lab(a.get('item_ids'))} from order {o}; refund to {pm(a.get('payment_method_id'))}",
            "exchange_delivered_order_items": f"in order {o}, exchange {lab(a.get('item_ids'))} for {lab(a.get('new_item_ids'))}; price difference via {pm(a.get('payment_method_id'))}",
            "modify_user_address": f"set the default address of {a.get('user_id')} to {addr}",
        }.get(call.name, call.name)


def refusal_text(problems: list[tuple[str, str]]) -> str:
    lines = [f"- {rid}: {why}" for rid, why in problems]
    return ("LOGBOOK REFUSED. Nothing was executed.\n" + "\n".join(lines) +
            "\nFix this, then try again.")


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


class LogbookAgent(LLMAgent):
    MAX_REFUSALS = 3  # per turn; after that the call goes through and is flagged

    def __init__(self, *args, task_id: Optional[str] = None, **kwargs):
        super().__init__(*args, **kwargs)
        names = {t.name for t in self.tools}
        if "book_reservation" in names:  # airline domain
            from logbook_airline import AirlineLogbook
            self.book = AirlineLogbook()
        else:
            self.book = RetailLogbook()
        self.task_id = task_id
        self.turn = 0

    def _observe(self, message, state: LLMAgentState) -> None:
        tms = message.tool_messages if isinstance(message, MultiToolMessage) else (
            [message] if isinstance(message, ToolMessage) else [])
        if not tms:
            return
        calls = {}
        for m in reversed(state.messages):
            if isinstance(m, AssistantMessage) and m.tool_calls:
                calls = {c.id: c for c in m.tool_calls}
                break
        for tm in tms:
            if tm.id in calls:
                self.book.observe(calls[tm.id], tm, self.turn)

    def generate_next_message(self, message, state: LLMAgentState):
        if isinstance(message, UserMessage) and message.is_audio:
            raise ValueError("User message cannot be audio.")
        self.turn += 1
        self._observe(message, state)
        if isinstance(message, MultiToolMessage):
            state.messages.extend(message.tool_messages)
        else:
            state.messages.append(message)
        return self._respond(state)

    def _respond(self, state: LLMAgentState):
        """Generate the next message, routing every write through `check`."""
        extra_cost, extra_in, extra_out = 0.0, 0, 0
        events = []
        for attempt in range(self.MAX_REFUSALS + 1):
            msg = generate(model=self.llm, tools=self.tools,
                           messages=state.system_messages + state.messages,
                           call_name="agent_response", **self.llm_args)
            writes = [c for c in (msg.tool_calls or []) if c.name in self.book.WRITES]
            if not writes:
                break
            n = len(msg.tool_calls)
            problems = {c.id: self.book.check(c, state.messages, n) for c in writes}
            if not any(problems.values()):
                for c in writes:
                    events.append({"event": "allowed", "tool": c.name, "args": c.arguments})
                break
            if attempt == self.MAX_REFUSALS:
                for c in writes:
                    events.append({"event": "override", "tool": c.name, "args": c.arguments,
                                   "rules": [r for r, _ in problems.get(c.id) or []]})
                break
            last_user = next((m.content for m in reversed(state.messages)
                              if isinstance(m, UserMessage) and m.content), "")
            for c in writes:
                if problems.get(c.id):
                    events.append({"event": "refused", "tool": c.name, "args": c.arguments,
                                   "rules": [r for r, _ in problems[c.id]],
                                   "why": [w for _, w in problems[c.id]],
                                   "user_said": (last_user or "")[:300]})
                    self.book.touches.append({"tool": c.name, "args": c.arguments,
                                              "turn": self.turn, "status": "refused",
                                              "rules": [r for r, _ in problems[c.id]]})
            # keep the refused attempt inside the model's own context only
            extra_cost += msg.cost or 0.0
            if msg.usage:
                extra_in += msg.usage.get("prompt_tokens", 0) or 0
                extra_out += msg.usage.get("completion_tokens", 0) or 0
            msg.raw_data = None
            state.messages.append(msg)
            any_problem = [p for c in writes for p in (problems.get(c.id) or [])]
            for c in msg.tool_calls:
                text = refusal_text(problems.get(c.id) or any_problem)
                state.messages.append(ToolMessage(
                    id=c.id, role="tool", requestor="assistant", content=text,
                    error=True, execution_status="rejected"))

        if extra_cost and msg.cost is not None:
            msg.cost += extra_cost
        if (extra_in or extra_out) and msg.usage:
            msg.usage = {"prompt_tokens": (msg.usage.get("prompt_tokens") or 0) + extra_in,
                         "completion_tokens": (msg.usage.get("completion_tokens") or 0) + extra_out}
        if events:
            msg.raw_data = dict(msg.raw_data or {})
            msg.raw_data["logbook_events"] = events
        state.messages.append(msg)
        return msg, state


def create_logbook_agent(tools, domain_policy, **kwargs):
    task = kwargs.get("task")
    return LogbookAgent(
        tools=tools,
        domain_policy=domain_policy,
        llm=kwargs.get("llm"),
        llm_args=kwargs.get("llm_args"),
        additional_stop_tool_names=kwargs.get("additional_stop_tool_names", frozenset()),
        task_id=getattr(task, "id", None),
    )


def register(registry) -> None:
    if registry.get_agent_factory("logbook_agent") is None:
        registry.register_agent_factory(create_logbook_agent, "logbook_agent")
