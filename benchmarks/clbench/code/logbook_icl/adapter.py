"""Plug a written Logbook into any agent loop.

    from adapter import LogbookAdapter

    book = LogbookAdapter("written/my_logbook.py", trace="trial/trace.jsonl")  # trace is optional
    action, result = None, ""
    while not done:
        seen = ...                                           # what the agent receives at this step
        text = book.step(action, result, seen, time=clock)  # updates the Logbook, returns its text
        action = agent.decide(seen, logbook=text)           # show the text the way your loop shows context
        result = env.run(action)                            # the text the action returned
    book.close(action, result)

Pass actions in the shape the author was told: {"tool": "send_email", "arguments": {...}}.

Guards, the same as in the AhaBench runs: observe and brief get 2 seconds each; an error or a
timeout never stops the run (it is counted in book.errors, and the last text that worked is shown
again); the text is cut at `cap` characters (4,200 by default, a little above the author's limit).

With trace=..., every decision is written to a JSONL file: {"time", "brief", "action", "result"},
plus "seen" with trace_seen=True. trial_report.py turns a trace into the report the author reads
before rewriting its Logbook. Without a Logbook (path=None) the adapter only records: with
trace_seen=True, that is a past run for past_run.py.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import signal
import sys
from pathlib import Path


class _Timeout(Exception):
    pass


def _guarded(fn, *args, seconds: float = 2.0):
    """Call fn with a time limit. The limit needs the main thread and SIGALRM (Linux, macOS)."""
    def _alarm(signum, frame):
        raise _Timeout()
    try:
        old = signal.signal(signal.SIGALRM, _alarm)
    except (ValueError, AttributeError):  # not the main thread, or no SIGALRM: no time limit
        return fn(*args)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn(*args)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def load_logbook(path: str | Path):
    """A new Logbook from the module at path. The module gets a stable name, so a pickled Logbook loads back."""
    path = Path(path).resolve()
    name = "written_logbook_" + hashlib.sha1(str(path).encode()).hexdigest()[:10]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name].Logbook()


class LogbookAdapter:
    def __init__(self, path: str | Path | None, cap: int = 4200, seconds: float = 2.0,
                 trace: str | Path | None = None, trace_seen: bool = False):
        self.book = load_logbook(path) if path else None
        self.cap, self.seconds = cap, seconds
        self.trace, self.trace_seen = (Path(trace) if trace else None), trace_seen
        self.errors = 0
        self.text = ""
        self._pending = None  # the previous decision's trace line, waiting for its action and result

    def step(self, action: dict | None, result: str, seen: str, time: str | None = None) -> str:
        """Before each decision: the previous action (None at the start), the text it returned, and
        what the agent receives now. Returns the text to show the agent."""
        self._write(action, result)
        if self.book is not None:
            step = {"action": copy.deepcopy(action), "result": "" if action is None else (result or ""), "seen": seen}
            try:
                _guarded(self.book.observe, step, seconds=self.seconds)
            except Exception:  # noqa: BLE001 - a Logbook bug must never stop the run
                self.errors += 1
            try:
                self.text = str(_guarded(self.book.brief, seconds=self.seconds))[:self.cap]
            except Exception:  # noqa: BLE001 - keep the last text that worked
                self.errors += 1
        if self.trace is not None:
            self._pending = {"time": time, **({"seen": seen} if self.trace_seen else {}), "brief": self.text}
        return self.text

    def close(self, action: dict | None = None, result: str | None = None) -> None:
        """At the end of the run: writes the last decision to the trace."""
        self._write(action, result)

    def _write(self, action, result) -> None:
        if self.trace is None or self._pending is None:
            return
        self.trace.parent.mkdir(parents=True, exist_ok=True)
        with self.trace.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**self._pending, "action": action, "result": result}, ensure_ascii=False) + "\n")
        self._pending = None
