"""Generic Logbook: knows nothing about the environment.

It sees, after every action, the tool name, the arguments and the text the tool returned,
which is all any tool-using agent sees. From that alone it keeps, for the whole run:

  - every failure or refusal the agent received: result lines that match a generic
    failure vocabulary (failed, error, invalid, insufficient, missing, still need, ...),
    grouped by tool and by message pattern, with how many times and when
  - for each tool, the last call that worked (no failure in its result)

Messages: a message the agent reads counts only if it is a reply to something the agent
sent (subject starting with "Re:"). Unsolicited messages are never recorded, because
they can be adversarial (phishing) and must not be repeated back to the agent.

It shows that record in the prompt. It adds information only and never blocks an action.

v2 (GenericLogbook(v2=True)) adds two generic facts, still with no knowledge of the task:
  - who said the same thing: every sender of a repeated refusal, not only the first
  - where each address comes from: first returned by one of the agent's own tools
    (for example a search), or first seen as the sender of a message nobody asked for
"""
from __future__ import annotations

import hashlib
import json
import re

FAILURE = re.compile(
    r"\b(fail(?:s|ed|ure)?|error|invalid|unable|cannot|can't|could not|not found|not enough|insufficient|"
    r"missing|refused|rejected|denied|declined|unavailable|out of stock|undeliverable|not reachable|"
    r"still need|incorrect|expired|exceeded|blocked)\b", re.I)
SUBJECT = re.compile(r"^\s*Subject:\s*(.*)$", re.M)
SENDER = re.compile(r"^\s*From:\s*(\S+)", re.M)
HEADER = re.compile(r"^(ID|From|To|Sent|Date|Subject):", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\w")


def _pattern(line: str) -> str:
    """The shape of a message, so repeats group together: ids, numbers and quoted values removed."""
    s = EMAIL.sub("<address>", line.lower())
    s = re.sub(r"\{.*?\}", "{...}", s)
    s = re.sub(r"'[^']*'|\"[^\"]*\"", "'...'", s)
    s = re.sub(r"\b[\w-]*\d[\w-]*\b", "#", s)
    return re.sub(r"\s+", " ", s).strip()[:160]


class GenericLogbook:
    def __init__(self, v2: bool = False):
        self.v2 = v2
        self.found: dict[str, tuple] = {}        # v2: address -> (tool, ts) first returned by the agent's own tools
        self.unsolicited: dict[str, str] = {}    # v2: address -> ts, sender of an unsolicited message
        self.failures: dict[tuple, dict] = {}   # (tool, pattern) -> {n, first, last, example, sender}
        self.last_ok: dict[str, dict] = {}      # tool -> {args, ts}
        self.steps = 0
        self._seen_messages: set[str] = set()

    def record(self, tool: str, args: dict | None, result: str | None, ts: str | None = None) -> None:
        try:
            self._record(tool, args or {}, result or "", ts or "")
        except Exception:  # noqa: BLE001 - never break the run
            pass

    def _record(self, tool: str, args: dict, result: str, ts: str):
        self.steps += 1
        sender = None
        is_message = bool(SUBJECT.search(result) and SENDER.search(result))
        if not is_message:
            for addr in EMAIL.findall(result):                         # v2 provenance: returned by its own tool
                self.found.setdefault(addr.lower(), (tool, ts))
        if is_message:                                                 # the result is a message
            if not SUBJECT.search(result).group(1).lower().startswith("re:"):
                frm = SENDER.search(result).group(1).lower()
                if frm not in self.found:
                    self.unsolicited.setdefault(frm, ts)               # v2 provenance: wrote first
                return                                                # unsolicited: never recorded
            sender = SENDER.search(result).group(1)
            digest = hashlib.sha1(result.encode()).hexdigest()
            if digest in self._seen_messages:                          # re-reading the same message
                return
            self._seen_messages.add(digest)
        lines = [l.strip() for l in re.split(r"\n|(?<=[.!?])\s+(?=[A-Z])", result) if l.strip()]
        if sender is not None:
            lines = [l for l in lines if not HEADER.match(l)]
        bad = [l for l in lines if FAILURE.search(l)]
        for l in bad:
            key = (tool, _pattern(l))
            f = self.failures.setdefault(key, {"n": 0, "first": ts, "last": ts, "example": "", "sender": sender,
                                               "senders": []})
            f["n"] += 1
            if sender and sender not in f["senders"]:
                f["senders"].append(sender)
            f["last"], f["example"] = ts, l[:220]
        if not bad and sender is None:
            self.last_ok[tool] = {"args": json.dumps(args, ensure_ascii=False)[:200], "ts": ts}

    def brief(self) -> str:
        L = ["LOGBOOK (kept automatically since the start; the decision history above only shows the last 8 actions)"]
        if self.failures:
            L.append(f"- Failures and refusals you received ({sum(f['n'] for f in self.failures.values())} so far; the most repeated and the latest, newest first):")
            frequent = sorted(self.failures.items(), key=lambda x: (x[1]["n"], x[1]["last"]), reverse=True)[:5]
            recent = sorted(self.failures.items(), key=lambda x: x[1]["last"], reverse=True)[:3]
            shown = {k: v for k, v in frequent + recent}
            for (tool, _p), f in sorted(shown.items(), key=lambda x: x[1]["last"], reverse=True):
                who = f" from {f['sender']}" if f["sender"] else ""
                if self.v2 and len(f.get("senders") or []) > 1:
                    s = f["senders"]
                    who = f" from {len(s)} different senders: {', '.join(s[:4])}" + (" ..." if len(s) > 4 else "")
                when = f"last {f['last'][:10]}" + (f", first {f['first'][:10]}" if f["n"] > 1 else "")
                L.append(f"  x{f['n']} [{tool}{who}] ({when}): {f['example']}")
        if self.v2 and self.found:
            by_tool: dict[str, list] = {}
            for addr, (tool, _ts) in self.found.items():
                by_tool.setdefault(tool, []).append(addr)
            L.append("- Addresses your own tools returned: " + "; ".join(
                f"{', '.join(a[:8])} (via {t})" for t, a in by_tool.items()))
        if self.v2 and self.unsolicited:
            L.append("- Addresses that wrote to you first and that your tools never returned: "
                     + ", ".join(list(self.unsolicited)[:6]))
        if self.last_ok:
            L.append("- Last call that worked, per tool:")
            for tool, ok in sorted(self.last_ok.items(), key=lambda x: x[1]["ts"], reverse=True)[:6]:
                L.append(f"  {tool} ({ok['ts'][:10]}): {ok['args']}")
        return "\n".join(L)

    def dump(self) -> dict:
        return {"failures": [{"tool": k[0], "pattern": k[1], **v} for k, v in self.failures.items()],
                "last_ok": self.last_ok, "steps": self.steps, "found": self.found, "unsolicited": self.unsolicited}
