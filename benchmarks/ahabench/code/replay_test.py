"""Replay logged prompts through the Register (no API) and print the brief."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "register" / "ahabench"))  # the Register code
from vending_register import VendingRegister
from register_provider import first_json
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
book = VendingRegister()
last = None
for r in rows:
    if r["attempt"] == 1:
        book._observe(r["prompt"], last)
    a = first_json(r["reply"])
    if isinstance(a, dict):
        last = a
print(len(rows), "calls, last ts", rows[-1]["ts"])
b = book.brief()
print(b)
print("chars", len(b))
