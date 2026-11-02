![Agent Logbook. Agents forget. The Logbook doesn’t. A record kept by code, outside the model, shown at every decision.](assets/banner.png)

# Agent Logbook

An agent only knows what it is sent at each call. The Logbook is a record kept by code, outside the model, of what the agent was shown and what happened, and given back to it at every decision.

We tested it on three public agent benchmarks, with each benchmark's own metrics, to answer one question: **does it help an agent learn from its own experience?**

## Short answer

1. **It helps when important feedback leaves the agent's view before the agent needs it.** In a year-long business simulation where the agent sees only its last 8 actions, Claude Haiku 5.5 went from 0 of 15 runs still in business at day 60 to 12 of 15. Haiku with the Logbook beat Claude Sonnet 5.5 without it, at about 1/26 of the cost.
2. **Where the agent still sees what it needs, it changes nothing.** No gain on τ²-bench (the whole conversation stays in view) or on Vending Bench (30 days, a large history window and the agent's own notes).
3. **What the record says matters.** The same refusals, kept as a raw list of error messages, did not change the agent's behavior. Kept in the task's terms ("order refused, missing: account number", for each supplier), they did. The agent's own notes did not help: Haiku wrote down the opposite lesson and kept it.
4. **A model can write the Logbook, if it sees the agent fail once.** From a description of the agent (its goal, instructions, tools, and what it keeps in view) and the first 60 days of a run without a Logbook, Claude Opus 5.5 wrote Logbooks that kept Haiku in business at day 60 in 15 of 15 runs on fresh seeds (no memory: 5/15, our hand-written Logbook: 14/15). Without the failed run (tested with earlier versions of the prompt), its Logbooks did worse than no memory (1/15 and 2/15). The author prompt is generic and in [author/](author), ready for another agent; it has only been tested on AhaBench.

| Benchmark | One episode | What the agent keeps in view | Logbook effect |
|---|---|---|---|
| [τ²-bench](benchmarks/tau2-bench) ([Sierra](https://sierra.ai)) | one customer conversation | the whole conversation | none |
| [Vending Bench](benchmarks/vending-bench) ([Prosus](https://www.prosus.com)) | 30 simulated days | its last 60,000 characters (about 8 to 9 days) plus its own notes | none |
| [AhaBench](benchmarks/ahabench), Aha-Vending | 365 simulated days | its last 8 actions (about 2 days) | large |

## Main result: AhaBench

[AhaBench](https://github.com/Marco-Cheng/AhaBench) ([paper](https://arxiv.org/abs/2609.05435)) asks whether agents turn experience into reusable insights. In Aha-Vending, the agent runs a vending business for a simulated year: it finds suppliers, orders by email, stocks the machine, sets prices and handles incidents (late deliveries, machine faults, cash theft, phishing). It starts with $500 and pays $2 a day. AhaBench stops a run after 20 days without a new order.

![Haiku and Sonnet, with and without the Logbook](benchmarks/ahabench/results/sonnet_vs_haiku.png)

| The paper's seeds 0 to 4 | Haiku 5.5 | Haiku 5.5 + Logbook | Sonnet 5.5 | Sonnet 5.5 + Logbook |
|---|---|---|---|---|
| Completed the year | 0/5 | 4/5 | 0/5 | 4/5 |
| Final net profit, median | -$77 | +$140 | -$50 | **+$1,840** |
| Profit per day, days 1-90 vs days 90-end (the paper's learning metric) | never reached day 90 | -$0.15 vs $2.91 | -$1.01 vs -$0.62 | $1.66 vs $5.17 |
| Cost, 5 runs | $0.37 | $3.29 | $84.16 | $143.93 |

With Haiku on 15 seeds:

![Runs still in business](benchmarks/ahabench/results/survival.png)

| Claude Haiku 5.5, 15 seeds | Still in business at day 60 | Completed the year | Final net profit, median |
|---|---|---|---|
| No memory (AhaBench default) | 0/15 | 0/15 | -$77 |
| Agent's own notes | 0/15 | 0/15 | -$70 |
| Logbook | 12/15 | 9/15 | +$1,099 |

Survival at day 60, Logbook vs no memory: p = 0.00001 (Fisher's exact test).

### What the agent learned

- **Haiku: suppliers need the account number.** A supplier only accepts an order that includes the business's bank account number. Warned about phishing, Haiku keeps withholding it: every run with no memory or with its own notes ended on the 20-days-without-an-order rule, between day 20 and day 53. The refusal email leaves the agent's view after 8 actions; the Logbook keeps it. After the first refusal, Haiku included the number in 75% of its later supplier emails with the Logbook, 5% without memory, 5% with its own notes. With notes, it wrote down the opposite lesson ("withholding bank details") in all 15 runs and kept it.
- **Sonnet: stop destroying stock.** Sonnet sends the account number from the start. Its problem was a failed stock command, which in this simulator destroys the units it took from storage. Without memory it destroyed about 230 units per run in each of the first two quarters, then ran out of cash or stopped reordering. With the Logbook: 46 units in the first quarter, then about 2 per quarter.

### What kind of record works

Same benchmark, Haiku, 15 seeds per setup:

| What the Logbook keeps | Still in business at day 60 | Account number sent after a refusal |
|---|---|---|
| Nothing (no memory) | 0/15 | 5% |
| The agent's own notes | 0/15 | 5% |
| Generic v1: every error and refusal received, grouped and counted, plus the last call that worked per tool | 3/15 | 8% |
| Generic v2: v1 plus every sender of a repeated refusal and where each address came from | 1/15 | 2% |
| Task record without its money and sales lines | 11/15 | 65% |
| Task record (suppliers found, quotes, orders refused and why, orders and their issues, failed commands, sales by price, money trend) | 12/15 | 75% |

The generic versions show the refusal ("x10, from 3 different senders: we still need the following before we can proceed: account number"), and Haiku still does not act on it. The task record states it as an outcome of the agent's own goal, for each named supplier ("Bunch Vending Support: order refused: missing info x3 (missing: account number)"), and Haiku does. Removing the money and sales lines from the task record keeps most of the effect, so the urgency of "revenue $0" is not what made the difference. Those lines seem to matter for profit instead: median +$290 without them, +$1,099 with them (paired t-test p = 0.05, a hint rather than a firm result).

## How it works

1. **Observe**: after every action, the Logbook reads the tool result the agent received. It never reads the simulator's internals.
2. **Record**: it keeps a structured record for the whole run, organized around the task (suppliers, orders, products, failures, money), not around messages.
3. **Show**: at every call it puts a short summary of that record in the prompt, next to the benchmark's own context.
4. **Optionally check**: on τ²-bench it also refused write actions that broke a written policy rule. That gate changed nothing on the score; the AhaBench gain came with no blocking at all.

The code is in [logbook/](logbook): one Logbook per benchmark, because the record has to be written in the task's terms. Its README shows what each one records, how it reaches the agent, and how to write one for a new task.

To have a model write it instead, use [author/](author): describe your agent in a spec (goal, instructions, tools, what it keeps in view, one example step, one past run), and Claude Opus 5.5 writes the Logbook as Python code. [`author/adapter.py`](author/adapter.py) plugs it into any agent loop, and an optional loop lets the author see a trial run and rewrite its Logbook.

## Limits

- **The AhaBench Logbook is written for that task.** A generic version that knows nothing about the task did not reproduce the effect (table above). A Logbook written by a model from a description of the agent did, but the author prompt's design principles were drawn from AhaBench failures, and it was tested on AhaBench only, with runs stopped at day 60.
- **One long-horizon benchmark.** The rule (the Logbook helps when feedback leaves the agent's view) fits all three benchmarks, but only one of them has that regime.
- **Sonnet 5.5 ran on the paper's 5 seeds only.** Haiku ran on 15.
- **Part of Sonnet's gain is learning to avoid a simulator bug** (a failed stock command destroys inventory, see the note in [benchmarks/ahabench](benchmarks/ahabench)).
- **Default temperature.** Claude 5.5 models reject the temperature parameter, so every run uses the model default (the AhaBench paper used 0.2).

## Repository

```text
author/            a model writes the Logbook for your agent, from a spec of it; the adapter for any agent loop
  examples/ahabench/  the spec of the AhaBench agent used in the tests
logbook/           the Logbook code: what each one records, and how to write one for your task
  ahabench/        the task Logbook and the two generic versions
  tau2-bench/      the Logbook gate (retail; the airline version was not used in the results)
  vending-bench/   the Logbook layer and its first version
benchmarks/
  ahabench/        Aha-Vending: runners, analysis, results, full logs, and the 24 Logbooks written by a model
  tau2-bench/      τ²-bench retail: runner, crash protocol, results, every conversation
  vending-bench/   Vending Bench: runner, results, every run
```

Each benchmark folder has its own README with the protocol, the numbers and the commands to reproduce. Runs use Claude models through the [Anthropic](https://www.anthropic.com) API; set `ANTHROPIC_API_KEY` before running.

## License

MIT, see [LICENSE](LICENSE).
