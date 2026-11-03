"""ICL plus a Logbook: the ICL baseline, unchanged, with a Logbook's text added to the current prompt.

The Logbook is Python code written before the runs by a model, from the task's brief, its response
schemas and one example instance (https://github.com/SamBagetAI/agent-logbook, author/). At every
turn it reads the system's previous answer, the feedback on it and the new prompt, updates a record
kept for the whole rollout, and returns a short text. That text is added in front of the current
prompt for that API call only: the ICL history stays exactly what the ICL baseline keeps.

One Logbook per task, chosen from the instance id. reset() starts a fresh Logbook, so the
stateless baseline gets a fresh one for every instance.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from ...interface import Observation, Query, Response
from ...registry import register_system
from ..icl.system import ICLSystem
from ..utils import ProviderTurnClient
from .adapter import LogbookAdapter

# instance id prefix -> task name (the Logbook file is <logbook_dir>/<task name>.py)
_PREFIX_TO_TASK = {"poker": "exploitable_poker"}
_HEADER = "[Logbook: kept by code since the start of this run, from your prompts, answers and feedback]\n"
_FOOTER = "\n[End of Logbook]\n\n"


class _LogbookTurnClient(ProviderTurnClient):
    """Adds the Logbook's text to the last user message of the Anthropic payload only."""

    logbook_text: str = ""

    def _anthropic_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        if self.logbook_text and messages and messages[-1].get("role") == "user":
            last = deepcopy(messages[-1])
            content = last.get("content")
            if isinstance(content, list):
                last["content"] = [{"type": "text", "text": self.logbook_text}, *content]
            else:
                last["content"] = self.logbook_text + str(content or "")
            messages = [*messages[:-1], last]
        return super()._anthropic_payload(messages)


@register_system("logbook_icl")
class LogbookICLSystem(ICLSystem):
    """ICL plus a written Logbook: the full ICL history, and the Logbook's text in front of the current prompt."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        logbook_dir: str = "",
        cap: int = 4200,
        max_tokens: int | None = None,
        system_prompt: str = "",
        name: str = "logbook_icl",
        reserve_tokens: int = 500,
        provider_mode: str = "auto",
        anthropic_max_tokens: int | None = None,
    ):
        """
        Args:
            model: LiteLLM model identifier (an Anthropic model: the Logbook text is added on that path).
            logbook_dir: Folder holding one written Logbook per task, named <task name>.py.
            cap: Longest Logbook text shown, in characters.
            max_tokens, system_prompt, name, reserve_tokens, provider_mode, anthropic_max_tokens: as for icl.
        """
        super().__init__(model=model, max_tokens=max_tokens, system_prompt=system_prompt, name=name,
                         reserve_tokens=reserve_tokens, provider_mode=provider_mode,
                         anthropic_max_tokens=anthropic_max_tokens)
        self._provider_client = _LogbookTurnClient(model=model, system_prompt=system_prompt,
                                                   provider_mode=provider_mode,  # type: ignore[arg-type]
                                                   anthropic_max_tokens=anthropic_max_tokens)
        if self._provider_client.state.provider != "anthropic":
            raise ValueError("logbook_icl adds the Logbook on the Anthropic path; use a Claude model.")
        self.logbook_dir = Path(logbook_dir).expanduser()
        self.cap = cap
        self._book: LogbookAdapter | None = None
        self._book_task: str | None = None
        self._last_action: dict[str, Any] | None = None
        self._last_result = ""
        self._last_text = ""
        self._errors_before = 0

    def _ensure_book(self, query: Query) -> LogbookAdapter:
        if self._book is None:
            prefix = str(query.instance_id or "").split(":", 1)[0]
            task = _PREFIX_TO_TASK.get(prefix, prefix)
            path = self.logbook_dir / f"{task}.py"
            if not path.is_file():
                raise FileNotFoundError(f"No written Logbook for task {task!r}: expected {path}")
            self._book, self._book_task = LogbookAdapter(path, cap=self.cap), task
        return self._book

    def respond(self, query: Query) -> Response:
        book = self._ensure_book(query)
        text = book.step(self._last_action, self._last_result, query.prompt or "")
        self._last_text = text
        self._provider_client.logbook_text = (_HEADER + text + _FOOTER) if text else ""
        try:
            response = super().respond(query)
        finally:
            self._provider_client.logbook_text = ""
        self._last_action = {"tool": query.response_schema.__name__, "arguments": response.action.model_dump()}
        self._last_result = ""
        meta = dict(response.metadata or {})
        meta.update({"system_type": "logbook_icl", "logbook_task": self._book_task,
                     "logbook_chars": len(text), "logbook_errors": book.errors})
        return Response(action=response.action, metadata=meta)

    def observe(self, observation: Observation, next_query: Query | None = None) -> None:
        super().observe(observation, next_query)
        self._last_result = observation.content or ""

    def reset(self) -> None:
        super().reset()
        self._book, self._book_task = None, None
        self._last_action, self._last_result, self._last_text = None, "", ""

    def get_run_artifacts(self) -> dict[str, Any]:
        artifacts = super().get_run_artifacts()
        artifacts.update({"artifact_type": "logbook_icl", "logbook_task": self._book_task,
                          "logbook_errors": self._book.errors if self._book else 0,
                          "logbook_last_text": self._last_text})
        return artifacts
