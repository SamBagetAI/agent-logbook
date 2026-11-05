"""tau2 retail with a restart: the agent is killed once, right after its first
successful write, and a fresh instance takes over the same conversation.

What survives the restart, by arm:
  crash_transcript : the chat transcript only (user and agent text messages)
  crash_notes      : the transcript + notes the agent kept with a save_notes tool
  crash_register    : the transcript + the Register brief (recorded automatically),
                     and the Register keeps checking every write

Everything else is tau2's own code: tasks, environment, user simulator, scoring.
The tool result of the write that triggered the restart is lost for the
transcript and notes arms, as it would be in a real crash; the environment did
execute it, so it counts in the final database.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from tau2.agent.llm_agent import LLMAgent, LLMAgentState
from tau2.data_model.message import (
    AssistantMessage,
    MultiToolMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from tau2.environment.tool import as_tool
from tau2.utils.llm_utils import generate

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "register" / "tau2-bench"))  # the Register code
from register_agent import WRITES, RegisterAgent  # noqa: E402

PRE = {
    "transcript": "This service may restart you at any time without warning. After a "
                  "restart, only the chat transcript survives; your tool calls and their "
                  "results are lost.",
    "notes": "This service may restart you at any time without warning. After a "
             "restart, only the chat transcript and your notes survive; your tool calls "
             "and their results are lost. Keep your notes current with the save_notes "
             "tool (each call replaces the previous notes).",
    "register": "This service may restart you at any time without warning. After a "
               "restart, only the chat transcript and the Register survive; your tool calls "
               "and their results are lost. The Register records your actions "
               "automatically; you do not need to do anything for it.",
}
POST = ("RESTART: you are a new instance taking over this conversation. The previous "
        "instance stopped without warning. Its tool calls and their results are lost.")


def save_notes(notes: str) -> str:
    """Replace your saved notes. Notes survive a restart; your tool calls and their results do not.

    Args:
        notes: The full text of your notes. This replaces the previous notes.

    Returns:
        A confirmation that the notes were saved.
    """
    return "Notes saved."


def transcript(messages) -> list:
    """The chat as a platform would keep it: user and agent text, no tool traffic."""
    out = []
    for m in messages:
        if isinstance(m, UserMessage) and m.content and not m.tool_calls:
            out.append(UserMessage(role="user", content=m.content))
        elif isinstance(m, AssistantMessage) and m.has_text_content() and not m.tool_calls:
            out.append(AssistantMessage(role="assistant", content=m.content))
    return out


def first_write_done(agent, message, state) -> Optional[str]:
    """Name of the write whose successful result is arriving now, if any."""
    tms = message.tool_messages if isinstance(message, MultiToolMessage) else (
        [message] if isinstance(message, ToolMessage) else [])
    if not tms:
        return None
    calls = {}
    for m in reversed(state.messages):
        if isinstance(m, AssistantMessage) and m.tool_calls:
            calls = {c.id: c for c in m.tool_calls}
            break
    for tm in tms:
        c = calls.get(tm.id)
        if c is not None and c.name in WRITES and not tm.error:
            return c.name
    return None


class _CrashMixin:
    ARM = "transcript"

    def _init_crash(self):
        self.crashed = False
        self.crash_info: Optional[dict] = None

    @property
    def system_prompt(self) -> str:
        return super().system_prompt + f"\n<restarts>\n{PRE[self.ARM]}\n</restarts>"

    def _recovery(self) -> str:
        return ""

    def _restart(self, state: LLMAgentState, extra: str) -> LLMAgentState:
        content = self.system_prompt + f"\n<restart>\n{POST}\n{extra}\n</restart>"
        return LLMAgentState(
            system_messages=[SystemMessage(role="system", content=content)],
            messages=transcript(state.messages),
        )

    def _tag(self, msg: AssistantMessage) -> AssistantMessage:
        if self.crash_info and not self.crash_info.get("tagged"):
            self.crash_info["tagged"] = True
            msg.raw_data = dict(msg.raw_data or {})
            ev = list(msg.raw_data.get("register_events") or [])
            ev.append({"event": "crash", "tool": self.crash_info["after"], "args": {},
                       "recovery_chars": self.crash_info["recovery_chars"],
                       "recovery": self.crash_info["recovery"]})
            msg.raw_data["register_events"] = ev
        return msg


class CrashTranscriptAgent(_CrashMixin, LLMAgent):
    ARM = "transcript"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._init_crash()

    def generate_next_message(self, message, state):
        if not self.crashed:
            after = first_write_done(self, message, state)
            if after:
                self.crashed = True
                rec = self._recovery()
                state = self._restart(state, rec)
                self.crash_info = {"after": after, "recovery_chars": len(rec), "recovery": rec[:1500]}
                msg = generate(model=self.llm, tools=self.tools,
                               messages=state.system_messages + state.messages,
                               call_name="agent_response", **self.llm_args)
                state.messages.append(msg)
                return self._tag(msg), state
        return super().generate_next_message(message, state)


class CrashNotesAgent(_CrashMixin, LLMAgent):
    ARM = "notes"
    MAX_NOTE_ROUNDS = 5

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._init_crash()
        self.notes = ""
        self.notes_saves = 0
        self.tools = list(self.tools) + [as_tool(save_notes)]
        self._env_tools = [t for t in self.tools if t.name != "save_notes"]

    def _recovery(self) -> str:
        return "Notes saved by the previous instance:\n" + (self.notes or "(no notes were saved)")

    def generate_next_message(self, message, state):
        if not self.crashed:
            after = first_write_done(self, message, state)
            if after:
                self.crashed = True
                rec = self._recovery()
                state = self._restart(state, rec)
                self.crash_info = {"after": after, "recovery_chars": len(rec), "recovery": rec[:1500]}
                msg, state = self._respond(state)
                return self._tag(msg), state
        if isinstance(message, MultiToolMessage):
            state.messages.extend(message.tool_messages)
        else:
            state.messages.append(message)
        return self._respond(state)

    def _respond(self, state):
        extra = 0.0
        saved = []
        for rnd in range(self.MAX_NOTE_ROUNDS + 1):
            tools = self.tools if rnd < self.MAX_NOTE_ROUNDS else self._env_tools
            msg = generate(model=self.llm, tools=tools,
                           messages=state.system_messages + state.messages,
                           call_name="agent_response", **self.llm_args)
            notes_calls = [c for c in (msg.tool_calls or []) if c.name == "save_notes"]
            if not notes_calls:
                break
            for c in notes_calls:
                self.notes = str((c.arguments or {}).get("notes", ""))
                self.notes_saves += 1
                saved.append({"event": "notes_saved", "tool": "save_notes", "args": {},
                              "chars": len(self.notes)})
            others = [c for c in msg.tool_calls if c.name != "save_notes"]
            state.messages.append(msg)
            for c in notes_calls:
                state.messages.append(ToolMessage(id=c.id, role="tool", requestor="assistant",
                                                  content="Notes saved."))
            if others:
                # the environment only sees the real tool calls
                out = msg.model_copy(update={"tool_calls": others,
                                             "raw_data": {"register_events": saved}})
                if out.cost is not None:
                    out.cost += extra
                return out, state
            extra += msg.cost or 0.0
        if extra and msg.cost is not None:
            msg.cost += extra
        if saved:
            msg.raw_data = dict(msg.raw_data or {})
            msg.raw_data["register_events"] = list(msg.raw_data.get("register_events") or []) + saved
        state.messages.append(msg)
        return msg, state


class CrashRegisterAgent(_CrashMixin, RegisterAgent):
    ARM = "register"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._init_crash()

    def _recovery(self) -> str:
        return "Register brief:\n" + self.book.brief()

    def generate_next_message(self, message, state):
        if not self.crashed:
            after = first_write_done(self, message, state)
            if after:
                self.turn += 1
                self._observe(message, state)  # the Register is durable: it keeps the result
                self.crashed = True
                rec = self._recovery()
                state = self._restart(state, rec)
                self.crash_info = {"after": after, "recovery_chars": len(rec), "recovery": rec[:1500]}
                msg, state = self._respond(state)
                return self._tag(msg), state
        return super().generate_next_message(message, state)


def _factory(cls):
    def create(tools, domain_policy, **kwargs):
        extra = {}
        if cls is CrashRegisterAgent:
            extra["task_id"] = getattr(kwargs.get("task"), "id", None)
        return cls(tools=tools, domain_policy=domain_policy, llm=kwargs.get("llm"),
                   llm_args=kwargs.get("llm_args"),
                   additional_stop_tool_names=kwargs.get("additional_stop_tool_names", frozenset()),
                   **extra)
    return create


def register(registry) -> None:
    for name, cls in (("crash_transcript", CrashTranscriptAgent),
                      ("crash_notes", CrashNotesAgent),
                      ("crash_register", CrashRegisterAgent)):
        if registry.get_agent_factory(name) is None:
            registry.register_agent_factory(_factory(cls), name)
