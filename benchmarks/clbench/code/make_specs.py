"""Build the author's spec for each CL-Bench task: brief, response schemas, view, and one example
instance answered by a fixed placeholder policy (poker and spectrum: seed 7, which the evaluation does not use;
cohort studies: the first instance of the frozen dataset, of which the author sees the columns only).

  CLBENCH_REPO=/path/to/continual-learning-bench python make_specs.py      # writes ../written_logbooks/specs/
"""
import json
import os
import sys
from pathlib import Path

REPO = Path(os.environ.get("CLBENCH_REPO", "continual-learning-bench")).resolve()
sys.path.insert(0, str(REPO))
from src.interface import Response, format_task_agent_brief  # noqa: E402
from src.registry import get_task_class  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "written_logbooks" / "specs"
VIEW = ("At each step the agent receives the task's prompt for that step (the first step of each instance repeats the brief). "
        "It answers with one JSON object that matches the response schema named in <tools>, and then receives feedback on that answer. "
        "The agent keeps the whole run in its context: every prompt, every answer and every piece of feedback, across all instances, "
        "in order. In these runs nothing is dropped (the context holds about 1,000,000 tokens). The Logbook's text is added to the "
        "current prompt only. In step[\"action\"], \"tool\" is the name of the response schema and \"arguments\" is the agent's JSON answer; "
        "step[\"result\"] is the feedback text.")


def placeholder(task, q):
    name = q.response_schema.__name__
    if name == "PokerAction":
        legal = (q.metadata or {}).get("legal_actions") or []
        act = "CHECK" if any("CHECK" in str(a) for a in legal) else "CALL"
        return q.response_schema(thinking="placeholder", action=act, amount=0)
    if name == "ScanReport":
        return q.response_schema(transmitters=[])
    if name == "ToolCallResponse":
        seen = getattr(task, "_placeholder_steps", 0)
        task._placeholder_steps = seen + 1
        tool = "get_database_metadata" if seen == 0 else "submit_cohort_report"
        return q.response_schema.model_validate({"thought": "placeholder", "tool_call": {"tool": tool}})
    if name.startswith("CohortSubmission"):
        fields = q.response_schema.model_fields
        return q.response_schema.model_validate({k: 0.5 for k in fields})
    raise ValueError(name)


def build(task_name, ctor):
    task = get_task_class(task_name)(**ctor)
    q = task.reset()
    brief = task.get_agent_brief()
    lines, schemas, guard = [], {}, 0
    lines.append("One instance on a seed the evaluation does not use, answered by a fixed placeholder policy "
                 "(its answers say nothing about good play). Shown so you can see the format of prompts and feedback.\n")
    while q is not None and guard < 40:
        guard += 1
        schemas[q.response_schema.__name__] = q.response_schema
        act = placeholder(task, q)
        lines.append(f"--- PROMPT (instance {q.instance_index}) ---\n{q.prompt}\n")
        lines.append(f"--- ANSWER ({q.response_schema.__name__}) ---\n{act.model_dump_json()}\n")
        res = task.step(Response(action=act))
        lines.append(f"--- FEEDBACK ---\n{res.observation.content}\n")
        if res.observation.instance_complete or res.done:
            break
        q = res.next_query
    d = OUT / task_name
    d.mkdir(parents=True, exist_ok=True)
    (d / "instructions.txt").write_text(format_task_agent_brief(brief) + "\n\nEach step's prompt gives the rest of the task's rules.\n")
    tools = [{"name": n, "description": "The agent's answer at a step whose prompt asks for this schema.",
              "parameters": s.model_json_schema()} for n, s in schemas.items()]
    (d / "tools.json").write_text(json.dumps(tools, indent=1))
    (d / "example_step.txt").write_text("\n".join(lines))
    goal = f"{brief.objective} Unit: {brief.instance_unit} {brief.reward_definition} {brief.completion_definition}"
    spec = {"goal": goal, "instructions_file": "instructions.txt", "tools_file": "tools.json", "view": VIEW,
            "example_step_file": "example_step.txt"}
    (d / "spec.json").write_text(json.dumps(spec, indent=1))
    print(task_name, "| schemas:", list(schemas), "| example chars:", len((d / "example_step.txt").read_text()))


build("exploitable_poker", {"variant": "calling_station", "seed": 7, "num_instances": 1})
build("blind_spectrum_monitoring", {"seed": 7, "num_instances": 1})
build("cohort_studies", {"seed": 7, "num_instances": 1})
