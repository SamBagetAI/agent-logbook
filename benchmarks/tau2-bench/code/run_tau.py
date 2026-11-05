"""Run tau2-bench retail with the standard agent or the Register agent.

The environment, tasks, user simulator and DB evaluation are tau2's own code.
Only two things differ from an official run, and both are the same for every arm:
  1. Claude models everywhere (agent, user simulator, NL-assertion judge),
     because this workspace cannot reach OpenAI.
  2. Prompt caching is switched on (it changes cost, not outputs).

Usage:
  python run_tau.py --arm base|register --agent-model claude-haiku-5-5 \
      --split test --trials 4 --concurrency 8 --name pilot-haiku-base
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "register" / "tau2-bench"))  # the Register code

KEY_FILE = os.environ.get("ANTHROPIC_KEY_FILE")  # or set ANTHROPIC_API_KEY directly
if KEY_FILE and not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = Path(KEY_FILE).read_text().strip()

from loguru import logger  # noqa: E402

import tau2.evaluator.evaluator_nl_assertions as nl_mod  # noqa: E402
from tau2.data_model.message import SystemMessage, UserMessage  # noqa: E402
from tau2.data_model.semantic_communication import CommunicateScoring  # noqa: E402
from tau2.data_model.simulation import NLAssertionCheck, TextRunConfig  # noqa: E402
from tau2.evaluator.evaluator import EvaluationType  # noqa: E402
from tau2.registry import registry  # noqa: E402
from tau2.runner.batch import _load_run_tasks, _run_save_paths, run_tasks  # noqa: E402
from tau2.utils.llm_utils import extract_json_from_llm_response, generate  # noqa: E402

import crash_agent  # noqa: E402
import register_agent  # noqa: E402

CACHE = {
    "cache_control_injection_points": [
        {"location": "message", "role": "system"},
        {"location": "message", "index": -1},
    ]
}
JUDGE_MODEL = "anthropic/claude-sonnet-5-5"


# --- NL-assertion judge: tau2's prompt verbatim, Claude as the judge, robust JSON ---
_SYSTEM = """
        TASK
        - You will be given a list of expected outcomes and a conversation that was collected during a test case run.
        - The conversation is between an agent and a customer.
        - Your job is to evaluate whether the agent satisfies each of the expected outcomes.
        - Grade each expected outcome individually.

        FORMAT
        - Your response should be a JSON object with the following fields:
        - `reasoning`: a short explanation for your classification
        - `metExpectation`: `true` if the agent satisfies the expected outcomes, `false` otherwise
        - `expectedOutcome`: repeat the expectation from the input that you are grading

        Example response structure:
        {
            "results": [
                {
                    "expectedOutcome": "<one of the expected outcomes from the input>",
                    "reasoning": "<reasoning trace>",
                    "metExpectation": <false or true>,
                }
            ]
        }
        """


def _evaluate_nl_assertions(cls, trajectory, nl_assertions):
    trajectory_str = "\n".join(f"{m.role}: {m.content}" for m in trajectory)
    user_prompt = f"""
        conversation:
        {trajectory_str}

        expectedOutcomes:
        {nl_assertions}
        """
    messages = [
        SystemMessage(role="system", content=_SYSTEM),
        UserMessage(role="user", content=user_prompt),
    ]
    last_err = None
    for _ in range(3):
        try:
            reply = generate(model=JUDGE_MODEL, messages=messages,
                             call_name="nl_assertions_eval")
            data = json.loads(extract_json_from_llm_response(reply.content or ""))
            results = data.get("results", [])
            if len(results) != len(nl_assertions):
                raise ValueError("judge returned a different number of results")
            return [
                NLAssertionCheck(nl_assertion=r["expectedOutcome"],
                                 met=bool(r["metExpectation"]),
                                 justification=r["reasoning"])
                for r in results
            ]
        except Exception as e:  # retry, then fail closed
            last_err = e
    return [NLAssertionCheck(nl_assertion=a, met=False,
                             justification=f"JUDGE_ERROR: {last_err}")
            for a in nl_assertions]


nl_mod.NLAssertionsEvaluator.evaluate_nl_assertions = classmethod(_evaluate_nl_assertions)


# --- User simulator: strip pseudo tool-call markup Claude sometimes types as text ---
import re  # noqa: E402

import tau2.user.user_simulator as user_mod  # noqa: E402

_JUNK = re.compile(r"\s*<(invoke|function_calls|antml:invoke)[^>]*>.*?</\1>\s*", re.S)
_orig_user_generate = user_mod.generate
USER_JUNK_STRIPPED = {"n": 0}


def _user_generate(*args, **kwargs):
    msg = _orig_user_generate(*args, **kwargs)
    msg.raw_data = None  # the full provider response is not needed and costs memory
    if msg.content and "<" in msg.content:
        clean = _JUNK.sub("\n", msg.content).strip()
        if clean and clean != msg.content.strip():
            msg.content = clean
            USER_JUNK_STRIPPED["n"] += 1
    return msg


user_mod.generate = _user_generate


# --- Same for the agents: keep cost and usage, drop the raw provider response ---
import tau2.agent.llm_agent as llm_agent_mod  # noqa: E402


def _lean(gen):
    def f(*args, **kwargs):
        msg = gen(*args, **kwargs)
        msg.raw_data = None
        return msg
    return f


llm_agent_mod.generate = _lean(llm_agent_mod.generate)
register_agent.generate = _lean(register_agent.generate)
crash_agent.generate = _lean(crash_agent.generate)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--arm", required=True,
                   choices=["base", "register", "crash_transcript", "crash_notes", "crash_register"])
    p.add_argument("--domain", default="retail", choices=["retail", "airline"])
    p.add_argument("--agent-model", default="claude-haiku-5-5")
    p.add_argument("--user-model", default="claude-sonnet-5-5")
    p.add_argument("--split", default="test")
    p.add_argument("--task-ids", nargs="*", default=None)
    p.add_argument("--trials", type=int, default=1)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--seed", type=int, default=300)
    p.add_argument("--name", required=True)
    a = p.parse_args()

    register_agent.register(registry)
    crash_agent.register(registry)
    agent = {"base": "llm_agent", "register": "register_agent"}.get(a.arm, a.arm)
    cfg = TextRunConfig(
        domain=a.domain,
        agent=agent,
        llm_agent=f"anthropic/{a.agent_model}",
        llm_args_agent=dict(CACHE),
        user="user_simulator",
        llm_user=f"anthropic/{a.user_model}",
        llm_args_user=dict(CACHE),
        task_split_name=None if a.task_ids else a.split,
        task_ids=a.task_ids,
        num_trials=a.trials,
        max_concurrency=a.concurrency,
        seed=a.seed,
        save_to=a.name,
        communicate_scoring=CommunicateScoring(scorer="substring"),
        hallucination_retries=0,
        auto_resume=True,
        log_level="ERROR",
    )
    cfg.validate()
    tasks = _load_run_tasks(cfg)
    _, save_dir, save_path, fmt = _run_save_paths(cfg)
    t0 = time.time()
    run_tasks(cfg, tasks, save_path=save_path, save_dir=save_dir,
              evaluation_type=EvaluationType.ALL, console_display=False,
              results_format=fmt)
    print(f"DONE {a.name} in {time.time() - t0:.0f}s -> {save_path}")


if __name__ == "__main__":
    logger.remove()
    main()
