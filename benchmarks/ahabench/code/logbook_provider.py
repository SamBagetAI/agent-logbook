"""Anthropic provider for Aha-Vending, with three memory setups.

Plugged in through AhaBench's own extension point (provider = "python"). The
benchmark, its simulator, its agent loop and its prompts stay untouched; this
class only sits between the agent and the model.

  none      AhaBench default: each decision sees the current state and the last 8
            actions, nothing older
  notes     the agent may add a "note" to any action; its last 25 notes are shown
            in the PLANNER NOTE block of every later decision
  logbook   the Logbook records what the agent was shown, for the whole run, and
            shows a short track record in the PLANNER NOTE block (sales by price,
            stockouts, supplier quotes and issues, incidents, money trend). It never
            blocks anything: it only adds information.
  logbook_checks  same, plus one refusal: sending the bank account number or the
            receiving address to an address that is not a known supplier
  logbook_noout  ablation: the Logbook without its money, sales, price and assortment
            lines (refusals, suppliers, quotes, orders, failed commands and losses kept)
  generic   a Logbook that knows nothing about the environment: every failure or
            refusal the agent received (grouped and counted) and the last call that
            worked for each tool (generic_logbook.py)
  generic2  the same, plus every sender of a repeated refusal and where each
            address came from (returned by the agent's own tools, or wrote first)

Every arm keeps AhaBench's default planner line. Usage is written to
$AHA_USAGE_LOG after every call; every call is appended to $AHA_TRACE_LOG.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

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


class AnthropicLogbookProvider:
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
                       "cost_usd": 0.0, "refusals": 0, "notes": 0, "api_errors": 0}
        self.seed = None
        self.last_ts = None
        self._reset()

    def _reset(self):
        self.notes: list[str] = []
        self.last_action = None
        if self.arm in ("logbook", "logbook_checks", "logbook_noout"):
            from vending_logbook import VendingLogbook
            self.book = VendingLogbook(checks=self.arm == "logbook_checks", outcomes=self.arm != "logbook_noout")
        elif self.arm in ("generic", "generic2"):
            from generic_logbook import GenericLogbook
            self.book = GenericLogbook(v2=self.arm == "generic2")
        else:
            self.book = None

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
                text, usage = self._call(system, user + f"\n\nLOGBOOK REFUSED your choice {json.dumps(act)[:300]}: {why} "
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
