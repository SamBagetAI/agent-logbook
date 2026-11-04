# The Register author

The Registers in [logbook/](../logbook) were each written by hand for one benchmark. Here a model writes the Register for any agent. You describe the agent in a spec: its goal, its instructions, its tools, what it keeps in view, one example step, and optionally a past run. Claude Opus 5.5 then writes the Register as Python code. Nothing in the prompt is specific to a benchmark: everything specific comes from the spec.

## Result on AhaBench

| Claude Haiku 5.5 on Aha-Vending, fresh seeds 15 to 29 | In business at day 60 | Net profit at day 60, median |
|---|---|---|
| No memory | 5/15 | -$133 |
| Register written by hand | 14/15 | -$40 |
| Register written by Opus 5.5 with this prompt, from the spec and one failed run | **15/15** | +$56 |
| The same Registers, rewritten once after a trial run | 15/15 | +$89 |

3 written Registers per row, each run on 5 seeds, runs stopped after day 60. On profit at day 60, the Registers written with this prompt were ahead of the hand-written one on 15 of 15 seeds. The earlier prompt versions, the 24 Registers written in the tests and the full protocol are in [benchmarks/ahabench](../benchmarks/ahabench#can-a-model-write-the-logbook).

## How to use it

```text
spec.json ──> author.py ──> my_logbook.py ──> adapter.py, in your agent loop
                  ^                                  │
                  └── trial_report.py <── trace ─────┘   (optional: try it, then let the author rewrite it)
                  └── rewrite.py <── the run so far ──┘   (optional: rewrite it in the middle of the run)
```

### 1. Describe the agent

A JSON file. Paths are relative to the spec. [`examples/ahabench/spec.json`](examples/ahabench/spec.json) is the one used in the tests.

| Field | What to put | AhaBench |
|---|---|---|
| `goal` | what the agent must achieve, and how success is measured | "Run a vending-machine business for 365 simulated days and end with the highest possible net worth [...]" |
| `instructions_file` | the agent's instructions, as it receives them (its system prompt) | [`instructions.txt`](examples/ahabench/instructions.txt) |
| `tools_file` | its tools: name, description and parameters, as JSON | [`tools.json`](examples/ahabench/tools.json), 12 tools |
| `view` | what the agent keeps in view from one step to the next. What leaves it is lost to the agent: that is what the Register is for | "[...] its last 8 actions with each result cut to 260 characters, and the full result of its previous action. Nothing else is kept between steps." |
| `harness_files` | optional: the agent's side of its loop, as files. The code that builds each prompt and cuts the history, and anything the agent may load during a run: skills, memory, notes. A list of `{"file", "what"}`, `what` being one line on the file's role. Never the environment's code | not used in the tests. It would be [`logbook_provider.py`](../benchmarks/ahabench/code/logbook_provider.py), which builds every prompt of the AhaBench runs |
| `example_step_file` | one step exactly as the agent receives it, which is what the Register will get as `step["seen"]` | [`example_step.txt`](examples/ahabench/example_step.txt), the day-1 prompt |
| `past_runs_intro` | optional: one sentence on the past run (which agent, without a Register, how it ended) | "[...] After 60 days its net worth was $118.96; it started with $500.00." |
| `past_runs_file` | optional: the past run as the author reads it | [`past_run.txt`](examples/ahabench/past_run.txt), 60 simulated days |
| `past_runs_steps_file` | optional: the same steps as the Register receives them, to test it on (JSON, can be gzipped) | [`past_run_steps.json.gz`](examples/ahabench/past_run_steps.json.gz), 321 steps |

The past run matters: on AhaBench, the Registers written without one kept 1 or 2 runs of 15 in business at day 60, fewer than no memory (5/15). To make one, record a run of your agent without a Register, one JSON line per decision, `{"time", "seen", "action", "result"}` (the adapter below does it with `path=None, trace_seen=True`), then:

```bash
python past_run.py past_run.jsonl my_agent/    # writes my_agent/past_run.txt and my_agent/past_run_steps.json.gz
```

`view` is a sentence a human writes, and nothing checks it against the loop. With `harness_files` the author reads the loop itself and sees what `view` may not say: a skill the agent loads on demand, a memory it keeps on its own, the exact cut of its history. The Register will receive all of that in `step["seen"]`, so the author has to know it exists. Give the agent's side only: the environment's code would tell the author what the world will do, and the Register may use only what the agent is shown.

### 2. Write the Register

```bash
pip install httpx
export ANTHROPIC_API_KEY=...            # or ANTHROPIC_KEY_FILE=/path/to/key
python author.py my_agent/spec.json --past-runs --name my_logbook
```

This writes `written/my_logbook.py` and `written/my_logbook.json` (every round: the author's full answer, its validation, tokens and cost; with the spec and, for a rewrite, the `_input/` folder, every call can be read again without being made again). [`validate.py`](validate.py) runs each draft on the steps the author saw, in a separate process: no exception, a text within the limit, every call under a second, a picklable object. On a problem the author gets the error and fixes it, at most twice. No human edits.

With AhaBench's spec and past run (about 115,000 input tokens), a Register cost $0.77 to $1.54 with Opus 5.5. All 24 Registers written in the tests passed validation at their first complete draft; the extra rounds came from first drafts cut by the 16,000-token answer limit (`--max-tokens`).

| Option | Default | What it does |
|---|---|---|
| `--author` | `claude-opus-5-5` | the model that writes the Register. Haiku 5.5 as author failed on AhaBench (2/15) |
| `--past-runs` | off | show the author the spec's past run |
| `--prompt-version` | 2 | 1: without the design principles (6/15 on AhaBench, against 15/15); 3: version 2 plus a count on every fact, not yet run on any benchmark |
| `--max-chars` | 4000 | the longest text the Register may show |
| `--max-tokens`, `--effort`, `--thinking` | 16,000, API defaults | the author's answer limit, thinking included, its effort and its thinking. Claude Haiku 5.5 at its default effort spent all 16,000 tokens thinking, three times, and returned no code: give it `--effort low --max-tokens 32000` |
| `--revise-from`, `--trial-report`, `--trial-outcome` | | rewrite a Register after a trial run (step 4) |

### 3. Plug it into the agent loop

The written Register implements:

```python
class Register:
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
    text = book.step(action, result, seen, time=clock)  # updates the Register, returns its text
    action = agent.decide(seen, register=text)           # put the text where the agent reads its context
    result = env.run(action)                            # the text the action returned
book.close(action, result)
```

- `action` in the shape the author was told: `{"tool": "send_email", "arguments": {...}}`.
- `seen` must match the spec's `view` and `example_step_file`: the whole prompt if your loop rebuilds it at every step (as AhaBench does), the new messages if it is a conversation.
- Where the text goes is up to the loop. On AhaBench it replaces the PLANNER NOTE block of each prompt ([`logbook_provider.py`](../benchmarks/ahabench/code/logbook_provider.py), arm `auto2`).

### 4. Optional: try it, then let the author rewrite it

Run the agent with the Register on a seed or task you will not evaluate on, with `trace=...`. Then:

```bash
python trial_report.py trial/trace.jsonl trial/report.txt
python author.py my_agent/spec.json --past-runs --revise-from my_logbook --trial-report trial/report.txt \
    --trial-outcome 'The trial ran for 60 days. Its net worth at the end was $616.84; it started with $500.00.' \
    --name my_logbook_2
```

For each decision of the trial, the report shows what was new in the Register's text, the action the agent chose and its result. The author reads it after its own Register, looks for where the agent ignored or misread it and what it lacked, and rewrites it. On AhaBench this gained nothing measurable at day 60 (15/15 before and after, profit ahead on 8 of 15 seeds).

### 5. Optional: rewrite it in the middle of the run

The author writes the Register before the run, from the spec. The run then shows what the spec could not: a counterpart that changes, a field that matters, a text the agent ignores. With [`rewrite.py`](rewrite.py), after some decisions, the author reads its own Register and the run so far, as the agent saw it, and rewrites it. The new Register replays every decision so far, so its record is complete, and takes over at the next decision.

In the loop, after K decisions:

```python
from rewrite import rewrite_mid_run
new_path = rewrite_mid_run(book, "my_agent/spec.json", name="my_logbook_2")   # book: the LogbookAdapter
```

One author call, one to three minutes, during which the loop waits; `None` when the author failed, and the current Register stays. The author gets, under `<run_so_far>`, every decision so far: the lines that were new in what the agent received, the lines that were new in the Register's text, the action and its result, then the full prompt of the last decision. It is told to count what these decisions show, not to hard-code it, since the Register must work as well on the decisions that follow. Its drafts are validated on those decisions. When to call it is the loop's choice; a fixed point, 10 to 20% into the run, is the simplest.

Offline, on a trace recorded with `trace_seen=True`:

```bash
python rewrite.py my_agent/spec.json --logbook written/my_logbook.py --trace trial/trace.jsonl --at 80 --name my_logbook_2
```

This cuts the trace after 80 decisions, has the author rewrite the Register from them, then replays the whole trace through the new Register, the decisions after the cut included, and prints its text at the cut and at the end.

**Tried once, offline, on the poker task of Continual Learning Bench.** The published poker Register pools every count over "the opponent", while the task changes opponents four times per rollout; its author could not know it, since the spec's example was one hand against one opponent. On rollout 0 of the published Register run, cut at the start of hand 31 (91 decisions: 20 hands against Tom, then 10 against Alex, with the prompt's `NOTICE: You are now playing against Alex`), Claude Haiku 5.5 as the author, at `--effort low`, rewrote it in one round for $0.0125. The new Register keys every section by the opponent's name (`per opp: Tom 20 hands net +2470; Alex 8 hands net -55`, then `Alex RESPONSE TO YOUR BETS/RAISES`, `Alex TENDENCIES`, `Alex SHOWDOWNS`), and replayed on the whole rollout, 313 decisions: 0 errors, texts of at most 2,117 characters, the same 103 hands recorded as the original. At its default effort, Haiku had spent all 16,000 output tokens thinking, three times, and returned no code ([`benchmarks/clbench/rewrite/failed/`](../benchmarks/clbench/rewrite/failed)). Files: [`benchmarks/clbench/rewrite/`](../benchmarks/clbench/rewrite). Whether the rewritten Register helps the agent is not measured: no rollout was run with it.

## What the author is told

The system prompt, version 2, with a past run:

```text
You design a Register for an AI agent.

The agent works in a loop. At each step it receives what its environment shows it, then it acts, usually by calling a tool, and the action returns a result. The agent keeps only part of this in view from one step to the next (see <view>). What leaves its view is lost to it.

A Register is code that runs next to the agent, outside the model. Before every decision, it reads what happened since the previous one, updates a record that it keeps for the whole run, and returns a short text that is shown to the agent.

Your job: from the agent's goal, instructions and tools, decide what this Register should record and show so that the agent reaches its goal. Then write it.

How to design it:
- Record outcomes. For each thing the agent tries toward its goal, record whether it worked and, if not, the exact reason it was given. Group them by the counterpart or object involved.
- Show cause and effect from this run: which attempts worked, which failed, and what differed between them.
- Prefer facts to instructions. The agent acts more readily on evidence from its own run than on advice.
- When the agent's instructions make it wary of something, show the evidence from this run that separates the safe cases from the unsafe ones.
- Put what blocks the goal first. Keep the same sections, in the same order, at every step. Short beats complete.

Interface (exactly):

    class Register:
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
- The Register object must be picklable: no lambdas or local functions stored on it.

Answer with the complete module in a single ```python block, and nothing else.
```

The first message is the spec, in tags: `<goal>`, `<instructions>`, `<tools>`, `<view>`, `<harness>` when the spec gives harness files (each file under a line saying its role), `<example_step>`, then `<past_runs>` (the intro sentence and the past run), and "Write the Register." Without a past run, the second rule reads: "Show only what this run has produced. Do not hard-code facts or advice about the environment." Version 1 has no "How to design it" section, and lets the author turn past runs into rules shown when the current run reaches the same situation. Version 3 adds one principle after "Record outcomes": "Give every fact its count: how many attempts, and how many of each outcome ('sent 10: accepted 8, refused 2'), so the agent can weigh it", and ends "Prefer facts to instructions" with "Show the evidence; never tell the agent what to do." It has not been run on any benchmark.

The five design principles were written after looking at why version 1 failed on AhaBench. Its Registers did show the right rule ("Reply once with items, qty, delivery address and 'Account to charge: BIZ-0001'"), and Haiku mostly kept withholding the account number (it was in 25% of its supplier emails after a refusal). Version 2's Registers show evidence from the current run instead, for example "emails containing 'BIZ-0001': 10 answered -> CHARGED x8 [...] emails without it: 24 answered -> asked for account number x13", and Haiku acted on it.

## Files

| File | What it does |
|---|---|
| [`author.py`](author.py) | builds the prompt from a spec, has the model write the Register, validates it, asks for fixes; also rewrites one after a trial |
| [`validate.py`](validate.py) | runs a written Register on the steps its author saw, or on a steps file, and reports errors, length and speed |
| [`adapter.py`](adapter.py) | calls a written Register from any agent loop, with time limits and error isolation; keeps every decision, writes the trace, and switches to a rewritten Register in the middle of a run, with a replay |
| [`trial_report.py`](trial_report.py) | turns a trace into the report the author reads before rewriting; with `--seen`, adds what the agent received |
| [`rewrite.py`](rewrite.py) | has the author rewrite a Register in the middle of a run, from the run so far; offline, does it on a trace and replays the rest of the run through the result |
| [`past_run.py`](past_run.py) | turns a recorded run into the spec's past-run files |
| [`examples/ahabench/`](examples/ahabench) | the spec of the AhaBench agent used in the tests. Its past run is the first 60 days of a run without a Register (seed 200), in the format `past_run.py` writes |

## Limits

- **One benchmark, one agent, one author.** AhaBench Aha-Vending, Claude Haiku 5.5 as the agent, Claude Opus 5.5 as the author, 3 Registers per setup, runs stopped after day 60.
- **The design principles come from AhaBench.** They were written after looking at AhaBench failures, then tested on fresh AhaBench seeds. Whether they hold on another task needs a second task.
- **`rewrite.py` was tried on one task.** Offline on one published poker rollout, then live in one development rollout on other seeds ([`benchmarks/clbench/rewrite/dev_seed7/`](../benchmarks/clbench/rewrite/dev_seed7)): the mechanism runs end to end, Claude Haiku 5.5 as the author keys the record by opponent, the agent reads it, and the rollout still ends below its stateless baseline, as most poker rollouts do. Its effect on a score is not measured.
- **`harness_files` has not been used in any test.** Every Register written so far saw the loop through `view` only, a sentence a human wrote.
- **Version 2 was only tested with a past run.** Without one, version 1 and the task-specific prompt failed (2/15 and 1/15).
- **Validation checks that the code runs, not that it helps.** Every Register written in the tests passed it, including the 9 written without a past run, which kept fewer runs in business than no memory.
- **A written Register can depend on Python's hash order.** 4 of the 24 written in the tests list items in the order of a Python set, which changes from one process to the next, so the same run can show the same facts in another order. Nothing in the prompt or in `validate.py` prevents it.
