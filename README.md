![Agent Logbook. Agents forget. The Logbook doesn’t. A record kept by code, outside the model, shown at every decision.](assets/banner.png)

# Agent Logbook

An agent only knows what it is sent at each call. The Logbook is a record kept by code, outside the model, and shown to the agent at every decision.

![How the Logbook works. A supplier refuses an order because it lacks the account number. Eight actions later, the refusal has left the agent's view. Without a Logbook, the agent sends the same order again and the run stops. With the Logbook, the refusal stays in every prompt and the order goes through.](assets/how-it-works.gif)

## Results

Four public benchmarks, each scored with its own metrics.

| Benchmark | What the agent sees | Effect of the Logbook |
|---|---|---|
| [AhaBench](benchmarks/ahabench): a year running a vending business | its last 8 actions | runs still in business at day 60: 0/15 → **12/15** |
| [Continual Learning Bench](benchmarks/clbench): 6 tasks, from poker to code fixes | the whole run | leaderboard score: 19.6% → **27.4%**, 1st of the 12 published systems (Claude Sonnet 4.6) |
| [τ²-bench](benchmarks/tau2-bench): customer service conversations | the whole conversation | none |
| [Vending Bench](benchmarks/vending-bench): 30 days of a vending business | its last 8 to 9 days, plus its own notes | none |

![Runs still in business on AhaBench, Claude Haiku 5.5, 15 seeds per setup: with no memory and with its own notes, every run is out by day 53; with the Logbook, 80% are still in business at day 290 and 60% complete the year.](benchmarks/ahabench/results/survival.png)

![Mean net profit by day on AhaBench, seeds 0 to 4: Haiku 5.5 and Sonnet 5.5 without memory stay below zero; with the Logbook, Haiku ends near +$630 and Sonnet near +$1,580.](benchmarks/ahabench/results/sonnet_vs_haiku.png)

![Continual Learning Bench, all 6 tasks, mean normalized reward: ICL with a Logbook, Claude Sonnet 4.6, 27.4%; then the 12 published systems, led by ICL with Claude Sonnet 4.6 at 19.6%, ICL with GPT-5.4 at 18.9% and Claude Code with Sonnet 4.6 at 18.5%.](benchmarks/clbench/results/board.png)

- **It helps when the agent needs a total over many steps**: a refusal that left its view 8 actions ago, a transmitter seen in earlier scans but silent in this one. When one prompt already holds what the agent needs, it gains nothing, and it is not free: on the poker task the model wrote twice as much reasoning with it, at twice the cost, and on cohort studies one run applied the rule set the Logbook listed as best and collapsed.
- **A small model with a Logbook can beat a bigger one without.** On AhaBench, Claude Haiku 5.5 with the Logbook beat Claude Sonnet 5.5 without it on 4 of 5 seeds, at about 1/26 of the cost.
- **What the record says matters.** A raw list of error messages did not change the agent's behavior. The same facts in the task's terms ("order refused, missing: account number") did.
- **A model can write it.** From a description of the agent and one failed run, Claude Opus 5.5 wrote Logbooks that kept Haiku in business at day 60 in 15/15 runs (no memory: 5/15). From the task descriptions alone, it wrote the six Logbooks used on Continual Learning Bench.

## How it works

![How the Logbook sits next to an agent: during the run, the world shows the agent a situation, the agent acts, what happened is counted by the memory, a program, and its memory block is in every message; before the run, a memory author, an LLM, writes the memory's code from the agent's spec, and the agent's own skill says how to read it.](assets/architecture-light.png)

During the run, at every step:

1. The world shows the agent a situation: a prompt, the result of its last action.
2. The memory, a program, reads it and counts what happened, in the task's terms: "orders to this supplier: sent 3, refused 2, reason: account number missing".
3. The agent gets its message with the memory block on top, decides, and acts.
4. The world answers. Back to 1.

The agent's own context can stay short: its last 8 actions on AhaBench, the current episode only in the `logbook_only` variant. The memory is what carries over, and it costs nothing: no model call, a few milliseconds.

Before the run:

5. You describe the agent: its goal, its tools, one example of what it receives.
6. The memory author, an LLM, writes the memory's code from that, once. It can rewrite it during the run, from what the run has shown.
7. Optionally, the agent's own instructions get a skill of four lines: how to read the memory. Counts are counts, not rules.

## Use it

Describe your agent in a spec, and a model writes its Logbook ([author/](author)):

```bash
python author/author.py my_agent/spec.json --past-runs --name my_logbook
```

Then call it in your agent loop:

```python
from adapter import LogbookAdapter                 # author/adapter.py
book = LogbookAdapter("written/my_logbook.py")
text = book.step(action, result, seen)             # before each decision; add text to the agent's context
```

The Logbooks written by hand for each benchmark are in [logbook/](logbook).

## Limits

- **It does not help everywhere.** No effect on τ²-bench or Vending Bench. On Continual Learning Bench, most of the gain comes from 2 of the 6 tasks (radio spectrum, code fixes), and poker got worse.
- **The author prompt was refined on AhaBench.** There, on fresh seeds, its Logbooks kept 15/15 runs in business. Unchanged, on Continual Learning Bench, they took the leaderboard score from 19.6% to 27.4%.
- **On Continual Learning Bench, the comparison is with runs published on other days.** A same-day rerun of the baseline, on 3 of the 6 tasks, confirmed the gain on radio spectrum only.
- **Few runs**: 5 to 15 per setup. Each benchmark folder gives its protocol, numbers, limits and the commands to reproduce.

## Repository

```text
author/       a model writes the Logbook for your agent
logbook/      the Logbooks written by hand
benchmarks/   ahabench, clbench, tau2-bench, vending-bench: protocol, results, every run
```

Runs use Claude models through the [Anthropic](https://www.anthropic.com) API. License: MIT ([LICENSE](LICENSE)).
