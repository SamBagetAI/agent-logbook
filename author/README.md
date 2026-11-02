# The Logbook author

The Logbooks in [logbook/](../logbook) were each written by hand for one benchmark. Here a model writes the Logbook for any agent. You describe the agent in a spec: its goal, its instructions, its tools, what it keeps in view, one example step, and optionally a past run. Claude Opus 5.5 then writes the Logbook as Python code. Nothing in the prompt is specific to a benchmark: everything specific comes from the spec.

## Result on AhaBench

| Claude Haiku 5.5 on Aha-Vending, fresh seeds 15 to 29 | In business at day 60 | Net profit at day 60, median |
|---|---|---|
| No memory | 5/15 | -$133 |
| Logbook written by hand | 14/15 | -$40 |
| Logbook written by Opus 5.5 with this prompt, from the spec and one failed run | **15/15** | +$56 |
| The same Logbooks, rewritten once after a trial run | 15/15 | +$89 |

3 written Logbooks per row, each run on 5 seeds, runs stopped after day 60. On profit at day 60, the Logbooks written with this prompt were ahead of the hand-written one on 15 of 15 seeds. The earlier prompt versions, the 24 Logbooks written in the tests and the full protocol are in [benchmarks/ahabench](../benchmarks/ahabench#can-a-model-write-the-logbook).

## How to use it

```text
spec.json ──> author.py ──> my_logbook.py ──> adapter.py, in your agent loop
                  ^                                  │
                  └── trial_report.py <── trace ─────┘   (optional: try it, then let the author rewrite it)
```

### 1. Describe the agent

A JSON file. Paths are relative to the spec. [`examples/ahabench/spec.json`](examples/ahabench/spec.json) is the one used in the tests.

| Field | What to put | AhaBench |
|---|---|---|
| `goal` | what the agent must achieve, and how success is measured | "Run a vending-machine business for 365 simulated days and end with the highest possible net worth [...]" |
| `instructions_file` | the agent's instructions, as it receives them (its system prompt) | [`instructions.txt`](examples/ahabench/instructions.txt) |
| `tools_file` | its tools: name, description and parameters, as JSON | [`tools.json`](examples/ahabench/tools.json), 12 tools |
| `view` | what the agent keeps in view from one step to the next. What leaves it is lost to the agent: that is what the Logbook is for | "[...] its last 8 actions with each result cut to 260 characters, and the full result of its previous action. Nothing else is kept between steps." |
| `example_step_file` | one step exactly as the agent receives it, which is what the Logbook will get as `step["seen"]` | [`example_step.txt`](examples/ahabench/example_step.txt), the day-1 prompt |
| `past_runs_intro` | optional: one sentence on the past run (which agent, without a Logbook, how it ended) | "[...] After 60 days its net worth was $118.96; it started with $500.00." |
| `past_runs_file` | optional: the past run as the author reads it | [`past_run.txt`](examples/ahabench/past_run.txt), 60 simulated days |
| `past_runs_steps_file` | optional: the same steps as the Logbook receives them, to test it on (JSON, can be gzipped) | [`past_run_steps.json.gz`](examples/ahabench/past_run_steps.json.gz), 321 steps |

The past run matters: on AhaBench, the Logbooks written without one kept 1 or 2 runs of 15 in business at day 60, fewer than no memory (5/15). To make one, record a run of your agent without a Logbook, one JSON line per decision, `{"time", "seen", "action", "result"}` (the adapter below does it with `path=None, trace_seen=True`), then:

```bash
python past_run.py past_run.jsonl my_agent/    # writes my_agent/past_run.txt and my_agent/past_run_steps.json.gz
```

### 2. Write the Logbook

```bash
pip install httpx
export ANTHROPIC_API_KEY=...            # or ANTHROPIC_KEY_FILE=/path/to/key
python author.py my_agent/spec.json --past-runs --name my_logbook
```

This writes `written/my_logbook.py` and `written/my_logbook.json` (every round, its validation, tokens and cost). [`validate.py`](validate.py) runs each draft on the steps the author saw, in a separate process: no exception, a text within the limit, every call under a second, a picklable object. On a problem the author gets the error and fixes it, at most twice. No human edits.

With AhaBench's spec and past run (about 115,000 input tokens), a Logbook cost $0.77 to $1.54 with Opus 5.5. All 24 Logbooks written in the tests passed validation at their first complete draft; the extra rounds came from first drafts cut by the 16,000-token answer limit (`--max-tokens`).

| Option | Default | What it does |
|---|---|---|
| `--author` | `claude-opus-5-5` | the model that writes the Logbook. Haiku 5.5 as author failed on AhaBench (2/15) |
| `--past-runs` | off | show the author the spec's past run |
| `--prompt-version` | 2 | 1: without the design principles (6/15 on AhaBench, against 15/15) |
| `--max-chars` | 4000 | the longest text the Logbook may show |
| `--revise-from`, `--trial-report`, `--trial-outcome` | | rewrite a Logbook after a trial run (step 4) |

### 3. Plug it into the agent loop

The written Logbook implements:

```python
class Logbook:
    def observe(self, step: dict) -> None:  # before every decision; step = {"action", "result", "seen"}
        ...
    def brief(self) -> str:                 # right after: the text shown to the agent
        ...
```

[`adapter.py`](adapter.py) calls it from any loop, with the guards used in the AhaBench runs: 2 seconds per call, an error or a timeout never stops the run (the last text that worked is shown again), the text is cut at 4,200 characters.

```python
from adapter import LogbookAdapter

book = LogbookAdapter("written/my_logbook.py", trace="trial/trace.jsonl")   # trace is optional
action, result = None, ""
while not done:
    seen = ...                                           # what the agent receives at this step
    text = book.step(action, result, seen, time=clock)  # updates the Logbook, returns its text
    action = agent.decide(seen, logbook=text)           # put the text where the agent reads its context
    result = env.run(action)                            # the text the action returned
book.close(action, result)
```

- `action` in the shape the author was told: `{"tool": "send_email", "arguments": {...}}`.
- `seen` must match the spec's `view` and `example_step_file`: the whole prompt if your loop rebuilds it at every step (as AhaBench does), the new messages if it is a conversation.
- Where the text goes is up to the loop. On AhaBench it replaces the PLANNER NOTE block of each prompt ([`logbook_provider.py`](../benchmarks/ahabench/code/logbook_provider.py), arm `auto2`).

### 4. Optional: try it, then let the author rewrite it

Run the agent with the Logbook on a seed or task you will not evaluate on, with `trace=...`. Then:

```bash
python trial_report.py trial/trace.jsonl trial/report.txt
python author.py my_agent/spec.json --past-runs --revise-from my_logbook --trial-report trial/report.txt \
    --trial-outcome 'The trial ran for 60 days. Its net worth at the end was $616.84; it started with $500.00.' \
    --name my_logbook_2
```

For each decision of the trial, the report shows what was new in the Logbook's text, the action the agent chose and its result. The author reads it after its own Logbook, looks for where the agent ignored or misread it and what it lacked, and rewrites it. On AhaBench this gained nothing measurable at day 60 (15/15 before and after, profit ahead on 8 of 15 seeds).

## What the author is told

The system prompt, version 2, with a past run:

```text
You design a Logbook for an AI agent.

The agent works in a loop. At each step it receives what its environment shows it, then it acts, usually by calling a tool, and the action returns a result. The agent keeps only part of this in view from one step to the next (see <view>). What leaves its view is lost to it.

A Logbook is code that runs next to the agent, outside the model. Before every decision, it reads what happened since the previous one, updates a record that it keeps for the whole run, and returns a short text that is shown to the agent.

Your job: from the agent's goal, instructions and tools, decide what this Logbook should record and show so that the agent reaches its goal. Then write it.

How to design it:
- Record outcomes. For each thing the agent tries toward its goal, record whether it worked and, if not, the exact reason it was given. Group them by the counterpart or object involved.
- Show cause and effect from this run: which attempts worked, which failed, and what differed between them.
- Prefer facts to instructions. The agent acts more readily on evidence from its own run than on advice.
- When the agent's instructions make it wary of something, show the evidence from this run that separates the safe cases from the unsafe ones.
- Put what blocks the goal first. Keep the same sections, in the same order, at every step. Short beats complete.

Interface (exactly):

    class Logbook:
        def __init__(self) -> None: ...

        def observe(self, step: dict) -> None:
            # Called before every decision, with what happened since the previous one:
            #   step["action"]: the agent's previous action, e.g. {"tool": "send_email", "arguments": {...}},
            #                   or None at the start
            #   step["result"]: the text that action returned, or "" at the start
            #   step["seen"]:   everything the agent receives now, before its next decision

        def brief(self) -> str:
            # Called right after observe. The text shown to the agent, at most 4,000 characters.

Rules:
- Use only what the agent was shown and what it did. You have no other access to its environment.
- Show what this run has produced. You may also use <past_runs>: show what happened there as evidence ("in an earlier run: ..."), only when the current run reaches the same situation, next to what this run shows.
- Python 3 standard library only. No files, network, subprocesses, threads or randomness.
- observe and brief must be fast (well under 100 ms) and must never raise.
- The Logbook object must be picklable: no lambdas or local functions stored on it.

Answer with the complete module in a single ```python block, and nothing else.
```

The first message is the spec, in tags: `<goal>`, `<instructions>`, `<tools>`, `<view>`, `<example_step>`, then `<past_runs>` (the intro sentence and the past run), and "Write the Logbook." Without a past run, the second rule reads: "Show only what this run has produced. Do not hard-code facts or advice about the environment." Version 1 has no "How to design it" section, and lets the author turn past runs into rules shown when the current run reaches the same situation.

The five design principles were written after looking at why version 1 failed on AhaBench. Its Logbooks did show the right rule ("Reply once with items, qty, delivery address and 'Account to charge: BIZ-0001'"), and Haiku mostly kept withholding the account number (it was in 25% of its supplier emails after a refusal). Version 2's Logbooks show evidence from the current run instead, for example "emails containing 'BIZ-0001': 10 answered -> CHARGED x8 [...] emails without it: 24 answered -> asked for account number x13", and Haiku acted on it.

## Files

| File | What it does |
|---|---|
| [`author.py`](author.py) | builds the prompt from a spec, has the model write the Logbook, validates it, asks for fixes; also rewrites one after a trial |
| [`validate.py`](validate.py) | runs a written Logbook on the steps its author saw and reports errors, length and speed |
| [`adapter.py`](adapter.py) | calls a written Logbook from any agent loop, with time limits and error isolation; writes the trace |
| [`trial_report.py`](trial_report.py) | turns a trace into the report the author reads before rewriting |
| [`past_run.py`](past_run.py) | turns a recorded run into the spec's past-run files |
| [`examples/ahabench/`](examples/ahabench) | the spec of the AhaBench agent used in the tests. Its past run is the first 60 days of a run without a Logbook (seed 200), in the format `past_run.py` writes |

## Limits

- **One benchmark, one agent, one author.** AhaBench Aha-Vending, Claude Haiku 5.5 as the agent, Claude Opus 5.5 as the author, 3 Logbooks per setup, runs stopped after day 60.
- **The design principles come from AhaBench.** They were written after looking at AhaBench failures, then tested on fresh AhaBench seeds. Whether they hold on another task needs a second task.
- **Version 2 was only tested with a past run.** Without one, version 1 and the task-specific prompt failed (2/15 and 1/15).
- **Validation checks that the code runs, not that it helps.** Every Logbook written in the tests passed it, including the 9 written without a past run, which kept fewer runs in business than no memory.
- **A written Logbook can depend on Python's hash order.** 4 of the 24 written in the tests list items in the order of a Python set, which changes from one process to the next, so the same run can show the same facts in another order. Nothing in the prompt or in `validate.py` prevents it.
