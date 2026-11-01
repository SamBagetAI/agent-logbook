# Logbook on τ²-bench (retail)

**Result: no gain on the benchmark's score.** In a short customer-service conversation the agent sees everything at every turn, so a record has nothing to add. In our own crash test, where the agent loses its context mid-task, the Logbook removed every repeated action.

## Benchmark

[τ²-bench](https://github.com/sierra-research/tau2-bench) by [Sierra](https://sierra.ai) (commit `122e243`). The agent serves customers of an online shop (orders, returns, exchanges, address and payment changes) under a written policy. Score: pass^k, the share of tasks solved in all k trials, checked against the expected database state and the benchmark's natural-language assertions.

Setup used here: retail domain, test split, 40 tasks x 4 trials = 160 conversations per setup. Agent: Claude Haiku 5.5, and Claude Sonnet 5.5 as a reference. User simulator and assertion judge: Claude Sonnet 5.5 for every setup (this workspace could not reach OpenAI, the default provider).

## The Logbook here

A gate. It keeps the conversation's facts (who is authenticated, each order and its status, actions already done) and checks every write action against the policy rules (authenticated user, order status, same user, explicit yes from the customer, items matching what was confirmed, ...). A refused call never reaches the environment: the agent sees why and tries again.

## Standard benchmark

| 160 conversations each | pass^1 | pass^4 | Logbook refusals | Agent cost |
|---|---|---|---|---|
| Haiku 5.5, no Logbook | 0.875 | 0.850 | - | $0.44 |
| Haiku 5.5 + Logbook | 0.850 | 0.725 | 3 | $0.44 |
| Sonnet 5.5, no Logbook | 0.900 | 0.850 | - | $6.06 |

The Logbook stepped in on 2 of 160 conversations (3 refusals). In the other 158 the model received exactly the same prompts as without it, so the gap between the two Haiku rows is the model's run-to-run variation (default temperature), not an effect of the Logbook.

## Crash test (our protocol, not part of the benchmark)

The agent is stopped right after its first successful write. A fresh instance continues the same conversation with (a) the transcript only, (b) the notes the first instance chose to save, or (c) the Logbook's record.

| 160 conversations each | Conversations with a repeated write | pass^1 | Errors found by τ²'s own reviewer (40 conversations) |
|---|---|---|---|
| Haiku 5.5, transcript only | 49 | 0.863 | - |
| Haiku 5.5, own notes | 57 | 0.894 | 91 |
| Haiku 5.5, Logbook | **0** | 0.875 | 78 |
| Sonnet 5.5, own notes | 27 | 0.931 | 86 |
| Sonnet 5.5, Logbook | **0** | 0.925 | 60 |

When the context is thrown away mid-task, the Logbook stops the agent from redoing what it already did (p < 1e-8 for each comparison, one-sided Fisher's exact test). The score does not move.

Note: given the chance, the agents rarely saved notes before the crash (Haiku 0 times in 160 conversations, Sonnet 5 times), so the "own notes" rows mostly measure a restart from the transcript alone.

## Files

- `code/run_tau.py`: runs τ²-bench with the standard agent or the Logbook agent (tau2's own environment, tasks, user simulator and evaluation)
- [`logbook/tau2-bench/logbook_agent.py`](../../logbook/tau2-bench/logbook_agent.py): the Logbook for the retail domain (used here) and the agent that plugs it into tau2; [`logbook_airline.py`](../../logbook/tau2-bench/logbook_airline.py): the same for the airline domain (not used in these results)
- `code/crash_agent.py`: the crash protocol
- `code/report_tau.py`, `code/compare_tau.py`: tables and paired bootstrap intervals; `code/review_tau.py`: τ²'s conversation reviewer, run blind to the setup
- `results/table.txt`, `results/tau_results.json`: the numbers above
- `data/simulations.zip`: every conversation (tau2 `results.json` per setup); `data/reviews.zip`: the reviewer's verdicts

Note: the "viol" column in `results/table.txt` replays the Logbook's own rules on every conversation, so it is circular and not used as a result.

## Reproduce

```bash
git clone https://github.com/sierra-research/tau2-bench && cd tau2-bench && git checkout 122e243
pip install -e .
export ANTHROPIC_API_KEY=...
CODE=/path/to/agent-logbook/benchmarks/tau2-bench/code   # a clone of this repo: the runner loads the Logbook from logbook/
python $CODE/run_tau.py --arm logbook --agent-model claude-haiku-5-5 --split test --trials 4 --name eval-haiku-logbook
TAU2_SIMULATIONS=data/simulations python $CODE/compare_tau.py
```
