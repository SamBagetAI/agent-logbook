"""Anthropic provider for Aha-Vending, with three memory setups.

Plugged in through AhaBench's own extension point (provider = "python"). The
benchmark, its simulator, its agent loop and its prompts stay untouched; this
class only sits between the agent and the model.

  none      AhaBench default: each decision sees the current state and the last 8
            actions, nothing older
  notes     the agent may add a "note" to any action; its last 25 notes are shown
            in the PLANNER NOTE block of every later decision
  register   the Register records what the agent was shown, for the whole run, and
            shows a short track record in the PLANNER NOTE block (sales by price,
            stockouts, supplier quotes and issues, incidents, money trend). It never
            blocks anything: it only adds information.
  register_checks  same, plus one refusal: sending the bank account number or the
            receiving address to an address that is not a known supplier
  register_noout  ablation: the Register without its money, sales, price and assortment
            lines (refusals, suppliers, quotes, orders, failed commands and losses kept)
  generic   a Register that knows nothing about the environment: every failure or
            refusal the agent received (grouped and counted) and the last call that
            worked for each tool (generic_register.py)
  generic2  the same, plus every sender of a repeated refusal and where each
            address came from (returned by the agent's own tools, or wrote first)
  auto      a Register written by a model for this task, loaded from $AHA_REGISTER_FILE
            (interface: observe(prompt, last_action), then brief())
  auto2     a Register written by a model from a generic spec of the agent (see author/
            at the root of the repo), loaded from $AHA_REGISTER_FILE (interface:
            observe(step), then brief(); step = the previous action, its result, and
            the prompt the agent receives now)

Every arm keeps AhaBench's default planner line. Usage is written to
$AHA_USAGE_LOG after every call; every call is appended to $AHA_TRACE_LOG.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import signal
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "register" / "ahabench"))  # the Register code

API = "https://api.anthropic.com/v1/messages"
KEY_ENV = "ANTHROPIC_API_KEY"  # or point ANTHROPIC_KEY_FILE at a file that holds the key


def _api_key() -> str:
    if os.environ.get(KEY_ENV):
        return os.environ[KEY_ENV].strip()
    path = os.environ.get("ANTHROPIC_KEY_FILE")
    if path and Path(path).exists():
        return Path(path).read_text().strip()
    raise RuntimeError("Set ANTHROPIC_API_KEY (or ANTHROPIC_KEY_FILE) before running.")


PRICE = {  # $ per million tokens: input, output (cache read 0.1x, cache write 1.25x)
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}
DEFAULT_PLANNER = ("- Keep the machine stocked, protect bank liquidity, and use explicit supplier "
                   "evidence instead of assumptions.")
PLANNER = re.compile(r"(PLANNER NOTE\n)(.*?)(\n\s*\n\s*LAST TOOL RESULT)", re.S)
LAST_RESULT = re.compile(r"LAST TOOL RESULT\s*\n(.*?)\n\s*\n\s*REMINDER:", re.S)  # where the harness prints the last tool output
NOTES_INSTRUCTION = """

Long-term memory: only the last 8 actions are shown to you, nothing older. To remember
anything for later (prices that sold, supplier behaviour, mistakes, plans), add an optional
"note" string to your JSON object, next to "tool_name" and "arguments". Your last 25 notes
are shown back to you in the PLANNER NOTE block of every future decision."""


def first_json(text: str):
    """The first JSON object in a reply, the way the benchmark parses it."""
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:  # noqa: BLE001
                        break
        start = text.find("{", start + 1)
    return None


def put_planner(user_prompt: str, block: str) -> str:
    m = PLANNER.search(user_prompt)
    if not m:
        return user_prompt + "\n\nPLANNER NOTE\n" + block
    return user_prompt[:m.start(2)] + block + user_prompt[m.end(2):]


AUTO_CAP = 4200  # characters shown from a written Register; the hand-written one never exceeded 4,171


class _Timeout(Exception):
    pass


def _guarded(fn, *args, seconds: float = 2.0):
    """Call fn with a time limit (main thread only), so a slow Register cannot stall a run."""
    def _alarm(signum, frame):
        raise _Timeout()
    try:
        old = signal.signal(signal.SIGALRM, _alarm)
    except ValueError:  # not the main thread
        return fn(*args)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn(*args)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _load_auto_register():
    """The Register class of the module at $AHA_REGISTER_FILE (a Register written by a model)."""
    path = Path(os.environ["AHA_REGISTER_FILE"]).resolve()
    name = "autolog_" + hashlib.sha1(str(path).encode()).hexdigest()[:10]
    if name not in sys.modules:  # a stable module name, so checkpoints unpickle
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name].Register()


class AnthropicRegisterProvider:
    def __init__(self, config=None):
        opts = dict(getattr(config, "provider_options", {}) or {})
        self.model = getattr(config, "model", None) or opts.get("model") or "claude-haiku-5-5"
        self.arm = os.environ.get("AHA_ARM") or opts.get("arm") or "none"
        self.usage_path = os.environ.get("AHA_USAGE_LOG")
        self.trace_path = os.environ.get("AHA_TRACE_LOG")
        self.http = httpx.Client(timeout=180, headers={
            "x-api-key": _api_key(), "anthropic-version": "2023-06-01",
            "content-type": "application/json"})
        self.totals = {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                       "cost_usd": 0.0, "refusals": 0, "notes": 0, "api_errors": 0, "register_errors": 0}
        self.seed = None
        self.last_ts = None
        self._reset()

    def _reset(self):
        self.notes: list[str] = []
        self.last_action = None
        if self.arm in ("register", "register_checks", "register_noout"):
            from vending_register import VendingRegister
            self.book = VendingRegister(checks=self.arm == "register_checks", outcomes=self.arm != "register_noout")
        elif self.arm in ("generic", "generic2"):
            from generic_register import GenericRegister
            self.book = GenericRegister(v2=self.arm == "generic2")
        elif self.arm in ("auto", "auto2"):  # a Register written by a model ($AHA_REGISTER_FILE)
            self.book = _load_auto_register()
        else:
            self.book = None
        self._last_brief = ""

    # ------------------------------------------------------------------ model call
    def _call(self, system: str, user: str, max_tokens: int) -> tuple[str, dict]:
        body = {"model": self.model, "max_tokens": int(max_tokens or 2000),
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": user}]}
        last = None
        for attempt in range(8):
            try:
                r = self.http.post(API, json=body)
                if r.status_code in (429, 500, 502, 503, 529):
                    last = f"HTTP {r.status_code}"
                    self.totals["api_errors"] += 1
                    time.sleep(min(60, 3 * 2 ** attempt))
                    continue
                r.raise_for_status()
                data = r.json()
                usage = data.get("usage") or {}
                self._account(usage)
                text = "\n".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
                return text, usage
            except httpx.HTTPError as exc:
                last = str(exc)
                self.totals["api_errors"] += 1
                time.sleep(min(60, 3 * 2 ** attempt))
        raise RuntimeError(f"Anthropic call failed: {last}")

    def _account(self, u: dict):
        pin, pout = PRICE.get(self.model, (0.0, 0.0))
        fresh = u.get("input_tokens", 0) or 0
        cr = u.get("cache_read_input_tokens", 0) or 0
        cw = u.get("cache_creation_input_tokens", 0) or 0
        out = u.get("output_tokens", 0) or 0
        t = self.totals
        t["calls"] += 1
        t["input"] += fresh + cr + cw
        t["output"] += out
        t["cache_read"] += cr
        t["cache_write"] += cw
        t["cost_usd"] += (fresh * pin + cr * pin * 0.1 + cw * pin * 1.25 + out * pout) / 1e6
        if self.usage_path:
            tmp = self.usage_path + ".tmp"
            Path(tmp).write_text(json.dumps({**t, "arm": self.arm, "model": self.model, "seed": self.seed,
                                             "last_timestamp": self.last_ts}))
            os.replace(tmp, self.usage_path)

    def _log(self, row: dict):
        if not self.trace_path:
            return
        with open(self.trace_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------ the agent's request
    def complete(self, request) -> str:
        md = getattr(request, "metadata", {}) or {}
        if md.get("seed") != self.seed:
            self.seed = md.get("seed")
            self._reset()
        self.last_ts = md.get("timestamp")
        attempt = int(md.get("action_attempt", 1))
        system, user = request.system_prompt, request.user_prompt
        max_tokens = getattr(request, "max_output_tokens", 2000)
        row = {"step": md.get("step_index"), "attempt": attempt, "ts": self.last_ts, "arm": self.arm}
        refused = []

        if self.arm == "notes":
            system += NOTES_INSTRUCTION
            shown = "\n".join(f"- {n}" for n in self.notes[-25:]) or "No notes yet."
            user = put_planner(user, DEFAULT_PLANNER + "\nYOUR NOTES (newest last):\n" + shown)
            text, usage = self._call(system, user, max_tokens)
            act = first_json(text)
            if isinstance(act, dict) and isinstance(act.get("note"), str) and act["note"].strip():
                self.notes.append(f"{(self.last_ts or '')[:10]}: {act['note'].strip()[:300]}")
                self.totals["notes"] += 1
        elif self.arm in ("generic", "generic2"):
            if attempt == 1 and self.last_action:
                m = LAST_RESULT.search(user)
                self.book.record(self.last_action.get("tool_name", "?"), self.last_action.get("arguments"),
                                 m.group(1) if m else "", self.last_ts)
            user = put_planner(user, DEFAULT_PLANNER + "\n" + self.book.brief())
            text, usage = self._call(system, user, max_tokens)
            act = first_json(text)
            if isinstance(act, dict) and act.get("tool_name"):
                self.last_action = act
        elif self.arm in ("auto", "auto2"):
            if attempt == 1:  # a retry of the same decision sees nothing new
                try:
                    if self.arm == "auto2":  # generic interface: the previous action, its result, what is seen now
                        la, lr = self.last_action, LAST_RESULT.search(user)
                        step = {"action": {"tool": la.get("tool_name"), "arguments": copy.deepcopy(la.get("arguments"))} if la else None,
                                "result": lr.group(1).strip() if (la and lr) else "", "seen": user}
                        _guarded(self.book.observe, step)
                    else:
                        _guarded(self.book.observe, user, copy.deepcopy(self.last_action))
                except BaseException:  # noqa: BLE001 - a Register bug must never break the run
                    self.totals["register_errors"] += 1
            try:
                self._last_brief = str(_guarded(self.book.brief))[:AUTO_CAP]
            except BaseException:  # noqa: BLE001 - keep the last text that worked
                self.totals["register_errors"] += 1
            user = put_planner(user, DEFAULT_PLANNER + ("\n" + self._last_brief if self._last_brief else ""))
            text, usage = self._call(system, user, max_tokens)
            act = first_json(text)
            if isinstance(act, dict) and act.get("tool_name"):
                self.last_action = act
        elif self.book is not None:
            if attempt == 1:
                self.book.observe(user, self.last_action)
            user = put_planner(user, DEFAULT_PLANNER + "\n" + self.book.brief())
            text, usage = self._call(system, user, max_tokens)
            for _ in range(2):
                act = first_json(text)
                why = self.book.check(act) if isinstance(act, dict) else None
                if not why:
                    break
                self.totals["refusals"] += 1
                refused.append({"action": act, "why": why})
                text, usage = self._call(system, user + f"\n\nREGISTER REFUSED your choice {json.dumps(act)[:300]}: {why} "
                                         "Choose another action. Return one JSON object.", max_tokens)
            act = first_json(text)
            if isinstance(act, dict) and act.get("tool_name"):
                self.last_action = act
                self.book.note_action(act, self.last_ts)
        else:
            text, usage = self._call(system, user, max_tokens)

        row.update({"prompt": user, "reply": text, "usage": usage})
        if refused:
            row["refused"] = refused
        if self.arm == "notes":
            row["n_notes"] = len(self.notes)
        self._log(row)
        return text
