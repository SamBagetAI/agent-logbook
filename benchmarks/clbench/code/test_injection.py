"""Offline check, no API call: the Logbook text reaches the API payload's last user message only,
and the history the system stores stays the ICL history.

  CLBENCH_REPO=/path/to/continual-learning-bench python test_injection.py   # logbook_icl copied into src/systems/
"""
import json, os, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("CLBENCH_REPO", "continual-learning-bench")).resolve()
sys.path.insert(0, str(REPO))
os.chdir(REPO)
os.environ.setdefault("ANTHROPIC_API_KEY", "offline-test")
from src.interface import Response
from src.registry import get_system_class, get_task_class
from src.systems.utils import provider_adapters as pa

payloads = []
def fake_request(self, payload):
    payloads.append(json.loads(json.dumps(payload)))
    return {"content": [{"type": "text", "text": json.dumps({"thinking": "x", "action": "CALL", "amount": 0})}],
            "usage": {"input_tokens": 10, "output_tokens": 5}, "stop_reason": "end_turn"}
pa.ProviderTurnClient._anthropic_request = fake_request

for name in ("icl", "logbook_icl"):
    payloads.clear()
    kw = {"model": "claude-sonnet-5-5"}
    if name == "logbook_icl":
        kw["logbook_dir"] = str(HERE.parent / "written_logbooks")
    sysm = get_system_class(name)(**kw)
    task = get_task_class("exploitable_poker")(schedule="quick_test")
    q = task.reset(); sysm.reset()
    for _ in range(6):
        r = sysm.respond(q)
        res = task.step(r)
        sysm.observe(res.observation, res.next_query)
        if res.done: break
        q = res.next_query
    last = payloads[-1]["messages"][-1]["content"]
    texts = [b.get("text", "")[:90].replace("\n", " | ") for b in last]
    hist = sysm._provider_client.state.native_messages
    stored = sum("Logbook" in b.get("text", "") for m in hist for b in m.get("content", []))
    print(name, "| calls", len(payloads), "| last user message blocks:", texts, "| Logbook copies stored in history:", stored)
