"""Independent review with tau2's own LLM judge (the code behind `tau2 review`).

The judge reads each conversation with the policy, the scenario and the expected
actions, and flags agent errors. It never sees which arm produced the
conversation: the Logbook's refusals stay inside the agent and leave no trace.

  python review_tau.py eval-haiku-base eval-haiku-logbook --trial 0
"""

import argparse
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if os.environ.get("ANTHROPIC_KEY_FILE") and not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = Path(os.environ["ANTHROPIC_KEY_FILE"]).read_text().strip()

from tau2.data_model.simulation import Results  # noqa: E402
from tau2.evaluator.review_llm_judge import ConversationReviewer  # noqa: E402
from tau2.multilingual.runtime import recorded_review_user_info  # noqa: E402
from tau2.scripts.review_conversation import _recorded_review_task  # noqa: E402

SIM = Path(os.environ.get("TAU2_SIMULATIONS", "tau2-bench/data/simulations"))
OUT = Path(__file__).resolve().parent.parent / "results" / "reviews"
MODEL = "anthropic/claude-sonnet-5-5"


def review_one(results, tasks, s):
    task = _recorded_review_task(s, tasks[s.task_id], results)
    user_info = recorded_review_user_info(results.info, task, context=s.language_context)
    policy = results.info.environment_info.policy
    last = None
    for _ in range(3):
        try:
            rv = ConversationReviewer.review(user_info=user_info, task=task,
                                             full_trajectory=s.messages, policy=policy,
                                             review_model=MODEL)
            return {"task": s.task_id, "trial": s.trial,
                    "reward": s.reward_info.reward if s.reward_info else None,
                    "agent_error": rv.agent_error, "user_error": rv.user_error,
                    "critical_user_error": rv.critical_user_error,
                    "errors": [e.model_dump() for e in rv.errors], "summary": rv.summary,
                    "cost": rv.cost}
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
    return {"task": s.task_id, "trial": s.trial, "failed": last}


def run(name, trial):
    results = Results.load(SIM / name / "results.json")
    tasks = {t.id: t for t in results.tasks}
    sims = [s for s in results.simulations if (s.trial or 0) == trial]
    with ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(lambda s: review_one(results, tasks, s), sims))
    OUT.mkdir(exist_ok=True)
    (OUT / f"{name}.trial{trial}.json").write_text(json.dumps(rows, indent=1, default=str))
    ok = [r for r in rows if "failed" not in r]
    sev = Counter(e.get("severity") for r in ok for e in r["errors"] if e.get("source") == "agent")
    typ = Counter(e.get("error_type") for r in ok for e in r["errors"] if e.get("source") == "agent")
    print(json.dumps({
        "name": name, "reviewed": len(ok), "failed": len(rows) - len(ok),
        "convs_with_agent_error": sum(1 for r in ok if r["agent_error"]),
        "agent_errors": sum(1 for r in ok for e in r["errors"] if e.get("source") == "agent"),
        "by_severity": dict(sev), "by_type": dict(typ.most_common(8)),
        "convs_with_critical_user_error": sum(1 for r in ok if r["critical_user_error"]),
        "review_cost": round(sum(r.get("cost") or 0 for r in ok), 3),
    }, indent=1))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("names", nargs="+")
    p.add_argument("--trial", type=int, default=0)
    a = p.parse_args()
    for n in a.names:
        run(n, a.trial)
