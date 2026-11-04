"""ICL plus a Logbook: the ICL baseline, unchanged, with a Logbook's text added to the current prompt.

The Logbook is Python code written before the runs by a model, from the task's brief, its response
schemas and one example instance (https://github.com/SamBagetAI/agent-logbook, author/). At every
turn it reads the system's previous answer, the feedback on it and the new prompt, updates a record
kept for the whole rollout, and returns a short text. That text is added in front of the current
prompt for that API call only: the ICL history stays exactly what the ICL baseline keeps.

One Logbook per task, chosen from the instance id (or, when it has no task prefix, the response schema). reset() starts a fresh Logbook, so the
stateless baseline gets a fresh one for every instance.

Optional, off by default: a rewrite in the middle of the rollout (rewrite_after_instances=K). At the first turn of
the (K+1)-th instance the system is asked about (an instance that ends before the system acts, e.g. a poker hand
where the opponent folds first, is not counted), the author model reads the Logbook and the rollout so far, as the
system saw it, and rewrites it; the new Logbook replays every turn so far and takes over (agent-logbook author/rewrite.py). The author's call is
recorded as a usage event, so it counts in the rollout's cost. The stateless baseline, reset at every instance, never
reaches K and is unchanged.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, ClassVar

from ...interface import Observation, Query, Response
from ...registry import register_system
from ...usage import build_usage_event
from ..icl.system import ICLSystem
from ..utils import ProviderTurnClient
from .adapter import LogbookAdapter

# instance id prefix -> task name (the Logbook file is <logbook_dir>/<task name>.py)
_PREFIX_TO_TASK = {"poker": "exploitable_poker"}
# tasks whose instance ids carry no task prefix ("17", "jazzband__tablib-534"), told apart by their response schema
_SCHEMA_TO_TASK = {"DatabaseAction": "database_exploration", "BashCommandResponse": "codebase_adaptation"}


def task_of(query: Query) -> str:
    """The task a query belongs to: from its instance id prefix, else from its response schema."""
    iid = str(query.instance_id or "")
    prefix = iid.split(":", 1)[0]
    if prefix in _PREFIX_TO_TASK:
        return _PREFIX_TO_TASK[prefix]
    if ":" in iid:
        return prefix
    return _SCHEMA_TO_TASK.get(query.response_schema.__name__, prefix)
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

    # LOGBOOK_SKIP_BASELINE=1 skips the stateless baseline the harness runs after every rollout (development runs:
    # the baselines of the published runs serve as reference). Leaderboard runs keep it.
    supports_baseline: ClassVar[bool] = os.environ.get("LOGBOOK_SKIP_BASELINE", "") != "1"

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
        rewrite_after_instances: int = 0,
        author_dir: str = "",
        specs_dir: str = "",
        author_model: str = "claude-haiku-5-5",
        author_effort: str = "low",
        author_max_tokens: int = 32000,
        rewrite_dir: str = "",
        usage_note_file: str = "",
    ):
        """
        Args:
            model: LiteLLM model identifier (an Anthropic model: the Logbook text is added on that path).
            logbook_dir: Folder holding one written Logbook per task, named <task name>.py.
            cap: Longest Logbook text shown, in characters.
            max_tokens, system_prompt, name, reserve_tokens, provider_mode, anthropic_max_tokens: as for icl.
            rewrite_after_instances: 0 (default): the Logbook is never rewritten. K: at the first turn of the
                (K+1)-th instance, the author rewrites it from the rollout so far.
            author_dir: agent-logbook's author/ folder (author.py, rewrite.py). Needed when K > 0.
            specs_dir: the folder of specs the Logbooks were written from, one <task name>/spec.json each.
            author_model, author_effort, author_max_tokens: the author's model, effort and answer limit.
            rewrite_dir: where the rewritten Logbooks are written (default: a temporary folder per rollout).
            usage_note_file: a text file telling the model how to read the Logbook, appended to the system prompt
                (stable across calls, so cached). Default: no note; the model gets the Logbook's header line only.
        """
        if usage_note_file:
            note = Path(usage_note_file).expanduser().read_text().strip()
            system_prompt = (system_prompt.rstrip() + "\n\n" + note) if system_prompt else note
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
        self.rewrite_after_instances = int(rewrite_after_instances)
        self.author_dir = Path(author_dir).expanduser() if author_dir else None
        self.specs_dir = Path(specs_dir).expanduser() if specs_dir else None
        self.author_model, self.author_effort = author_model, author_effort
        self.author_max_tokens = int(author_max_tokens)
        self.rewrite_dir = Path(rewrite_dir).expanduser() if rewrite_dir else None
        if self.rewrite_after_instances and not (self.author_dir and self.specs_dir):
            raise ValueError("rewrite_after_instances needs author_dir and specs_dir")
        self._seen_instances: list[Any] = []   # instance ids, in order of first appearance
        self._rewrite: dict[str, Any] | None = None

    def _ensure_book(self, query: Query) -> LogbookAdapter:
        if self._book is None:
            task = task_of(query)
            path = self.logbook_dir / f"{task}.py"
            if not path.is_file():
                raise FileNotFoundError(f"No written Logbook for task {task!r}: expected {path}")
            self._book, self._book_task = LogbookAdapter(path, cap=self.cap), task
        return self._book

    def _rewrite_book(self, book: LogbookAdapter, query: Query) -> str | None:
        """The author rewrites the Logbook from the rollout so far; the new one replays it and takes over.
        Returns its text, or None when the author failed (the current Logbook stays). Never raises."""
        if str(self.author_dir) not in sys.path:
            sys.path.insert(0, str(self.author_dir))
        from rewrite import run_author, write_inputs  # agent-logbook author/rewrite.py

        out = self.rewrite_dir or Path(tempfile.mkdtemp(prefix="logbook_rewrite_"))
        name = f"{self._book_task}_{uuid.uuid4().hex[:8]}"
        info: dict[str, Any] = {"at_instance": len(self._seen_instances), "turns": len(book.rows), "name": name,
                                "dir": str(out), "author": self.author_model, "effort": self.author_effort}
        try:
            report, steps = write_inputs(book.rows, out / f"{name}_input")
            path = run_author(self.specs_dir / self._book_task / "spec.json", Path(book.path), report, steps, name, out,
                              self.author_model, "", False, None, self.author_max_tokens, self.author_effort, None)
            log = json.loads((out / f"{name}.json").read_text())
        except Exception as exc:  # noqa: BLE001 - the rollout goes on with the current Logbook
            info["error"] = repr(exc)[:500]
            self._rewrite = info
            return None
        rounds = log.get("rounds", [])
        usage = {k: sum(int(r.get("usage", {}).get(k) or 0) for r in rounds) for k in ("input_tokens", "output_tokens")}
        event = build_usage_event(model=self.author_model, provider="anthropic", call_type="logbook_author",
                                  input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                                  metadata={"rounds": len(rounds), "final_ok": log.get("final_ok")})
        if event.cost_usd is None and log.get("cost_usd") is not None:
            event.cost_usd, event.pricing_source, event.pricing_error = float(log["cost_usd"]), "author.py PRICE", None
        self.record_usage_event(event)
        info.update({"rounds": len(rounds), "final_ok": log.get("final_ok"), "cost_usd": log.get("cost_usd"), **usage})
        if path is None:
            self._rewrite = info
            return None
        done = book.rewrite(path)
        info.update({"path": str(path), "replayed": done["replayed"], "replay_errors": done["errors"],
                     "code": path.read_text()})
        self._rewrite = info
        return book.text if done["ok"] else None

    def respond(self, query: Query) -> Response:
        book = self._ensure_book(query)
        if query.instance_id not in self._seen_instances:
            self._seen_instances.append(query.instance_id)
        text = book.step(self._last_action, self._last_result, query.prompt or "")
        if (self.rewrite_after_instances and self._rewrite is None
                and len(self._seen_instances) == self.rewrite_after_instances + 1):
            text = self._rewrite_book(book, query) or text
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
                     "logbook_chars": len(text), "logbook_errors": book.errors,
                     "logbook_rewritten": bool(self._rewrite and self._rewrite.get("path"))})
        return Response(action=response.action, metadata=meta)

    def observe(self, observation: Observation, next_query: Query | None = None) -> None:
        super().observe(observation, next_query)
        self._last_result = observation.content or ""

    def reset(self) -> None:
        super().reset()
        self._book, self._book_task = None, None
        self._last_action, self._last_result, self._last_text = None, "", ""
        self._seen_instances, self._rewrite = [], None

    def get_run_artifacts(self) -> dict[str, Any]:
        artifacts = super().get_run_artifacts()
        artifacts.update({"artifact_type": "logbook_icl", "logbook_task": self._book_task,
                          "logbook_errors": self._book.errors if self._book else 0,
                          "logbook_last_text": self._last_text, "logbook_rewrite": self._rewrite})
        return artifacts

