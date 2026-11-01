"""Replay logged prompts through the generic Logbook (no API) and print the brief."""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from generic_logbook import GenericLogbook
from logbook_provider import first_json
LAST_RESULT = re.compile(r"LAST TOOL RESULT\s*\n(.*?)\n\s*\n\s*REMINDER:", re.S)
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
upto = int(sys.argv[2]) if len(sys.argv) > 2 else len(rows)
book = GenericLogbook(v2=len(sys.argv) > 3 and sys.argv[3] == "v2")
last = None
for r in rows[:upto]:
    if r["attempt"] == 1 and last:
        m = LAST_RESULT.search(r["prompt"])
        book._record(last.get("tool_name", "?"), last.get("arguments") or {}, m.group(1) if m else "", r["ts"])
    a = first_json(r["reply"])
    if isinstance(a, dict) and a.get("tool_name"):
        last = a
b = book.brief()
print(len(rows[:upto]), "calls replayed, last ts", rows[upto - 1]["ts"])
print(b)
print("chars", len(b))
