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

Every decision is kept in book.rows, in order: {"time", "seen", "brief", "action", "result"}. In
the middle of a run, book.rewrite(path) switches to another Logbook: it replays every decision so
far into it, so its record starts at the first one, and its text is shown from the next decision
on (rewrite.py has the author write that Logbook from the run so far).

With trace=..., every decision is also written to a JSONL file in the same shape, "seen" included
with trace_seen=True. trial_report.py turns a trace into the report the author reads before
rewriting its Logbook. Without a Logbook (path=None) the adapter only records: with
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


def _step(prev: dict | None, seen: str) -> dict:
    act = prev["action"] if prev else None
    return {"action": copy.deepcopy(act), "result": "" if act is None else (prev.get("result") or ""), "seen": seen}


def steps_from_rows(rows: list[dict]) -> list[dict]:
    """The decisions as observe() receives them: at each one, the previous action, its result, and what the agent saw."""
    return [_step(rows[i - 1] if i else None, row["seen"]) for i, row in enumerate(rows)]


class LogbookAdapter:
    def __init__(self, path: str | Path | None, cap: int = 4200, seconds: float = 2.0,
                 trace: str | Path | None = None, trace_seen: bool = False):
        self.book = load_logbook(path) if path else None
        self.path = str(path) if path else None
        self.cap, self.seconds = cap, seconds
        self.trace, self.trace_seen = (Path(trace) if trace else None), trace_seen
        self.errors = 0
        self.text = ""
        self.rows: list[dict] = []     # one per decision, in order
        self.rewrites: list[dict] = []
        self._open = False             # the last row still waits for its action and result

    def step(self, action: dict | None, result: str, seen: str, time: str | None = None) -> str:
        """Before each decision: the previous action (None at the start), the text it returned, and
        what the agent receives now. Returns the text to show the agent."""
        self._finish(action, result)
        if self.book is not None:
            step = {"action": copy.deepcopy(action), "result": "" if action is None else (result or ""), "seen": seen}
            self.errors += self._observe(self.book, step)
            self.text, errors = self._brief(self.book)
            self.errors += errors
        self.rows.append({"time": time, "seen": seen, "brief": self.text, "action": None, "result": None})
        self._open = True
        return self.text

    def close(self, action: dict | None = None, result: str | None = None) -> None:
        """At the end of the run: the last decision's action and result."""
        self._finish(action, result)

    def rewrite(self, path: str | Path) -> dict:
        """In the middle of the run: switch to the Logbook at path. It replays every decision so far, so its
        record starts at the first one; book.text holds its text, shown from the next decision on. Give it a
        file of its own: a module already loaded is not read again. If it does not load, the current Logbook
        stays. Returns {"ok", "replayed", "errors"}."""
        try:
            book = load_logbook(path)
        except Exception:  # noqa: BLE001
            self.errors += 1
            return {"ok": False, "replayed": 0, "errors": 1}
        errors = sum(self._observe(book, step) for step in steps_from_rows(self.rows))
        text, brief_errors = self._brief(book)
        errors += brief_errors
        self.book, self.path, self.text = book, str(path), text
        self.errors += errors
        self.rewrites.append({"path": str(path), "after": len(self.rows), "errors": errors})
        return {"ok": True, "replayed": len(self.rows), "errors": errors}

    def _observe(self, book, step: dict) -> int:
        try:
            _guarded(book.observe, step, seconds=self.seconds)
            return 0
        except Exception:  # noqa: BLE001 - a Logbook bug must never stop the run
            return 1

    def _brief(self, book) -> tuple[str, int]:
        try:
            return str(_guarded(book.brief, seconds=self.seconds))[:self.cap], 0
        except Exception:  # noqa: BLE001 - keep the last text that worked
            return self.text, 1

    def _finish(self, action, result) -> None:
        if not self._open:
            return
        row = self.rows[-1]
        row["action"], row["result"] = copy.deepcopy(action), result
        self._open = False
        if self.trace is not None:
            self.trace.parent.mkdir(parents=True, exist_ok=True)
            line = {k: v for k, v in row.items() if k != "seen" or self.trace_seen}
            with self.trace.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
