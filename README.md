![Agent Logbook. Agents forget. The Logbook doesn’t. A record kept by code, outside the model, shown at every decision.](assets/banner.png)

# Agent Logbook

An agent only knows what it is sent at each call. The Logbook is a record kept by code, outside the model, and shown to the agent at every decision.

![How the Logbook works. A supplier refuses an order because it lacks the account number. Eight actions later, the refusal has left the agent's view. Without a Logbook, the agent sends the same order again and the run stops. With the Logbook, the refusal stays in every prompt and the order goes through.](assets/how-it-works.gif)

## Results

Four public benchmarks, each scored with its own metrics.

| Benchmark | What the agent sees | Effect of the Logbook |
|---|---|---|
| [AhaBench](benchmarks/ahabench): a year running a vending business | its last 8 actions | runs still in business at day 60: 0/15 → **12/15** |
| [Continual Learning Bench](benchmarks/clbench): radio spectrum monitoring | the whole run | learning gain: 26.5% → **46.6%**, same cost |
| Continual Learning Bench: poker, cohort studies | the whole run | none |
| [τ²-bench](benchmarks/tau2-bench): customer service conversations | the whole conversation | none |
| [Vending Bench](benchmarks/vending-bench): 30 days of a vending business | its last 8 to 9 days, plus its own notes | none |

- **It helps when the feedback an agent needs has left its view, or is spread over many steps.** Where the agent already sees what it needs, it changes nothing.
- **A small model with a Logbook can beat a bigger one without.** On AhaBench, Claude Haiku 5.5 with the Logbook beat Claude Sonnet 5.5 without it on 4 of 5 seeds, at about 1/26 of the cost.
- **What the record says matters.** A raw list of error messages did not change the agent's behavior. The same facts in the task's terms ("order refused, missing: account number") did.
- **A model can write it.** From a description of the agent and one failed run, Claude Opus 5.5 wrote Logbooks that kept Haiku in business at day 60 in 15/15 runs (no memory: 5/15).

## How it works

1. **Observe**: after each action, read what the agent received.
2. **Record**: keep a structured record of the run, in the task's terms.
3. **Show**: put a short summary of it in every prompt.

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

- Two strong effects, on two tasks. The other tasks show none.
- The author prompt was designed from AhaBench failures.
- Each benchmark folder gives its protocol, numbers, limits and the commands to reproduce.

## Repository

```text
author/       a model writes the Logbook for your agent
logbook/      the Logbooks written by hand
benchmarks/   ahabench, clbench, tau2-bench, vending-bench: protocol, results, every run
```

Runs use Claude models through the [Anthropic](https://www.anthropic.com) API. License: MIT ([LICENSE](LICENSE)).
