# Register on Vending Bench (Prosus)

**Result: no measurable gain.** The run is 30 days, the agent keeps its last 60,000 characters of history (about the last 8 to 9 days) and already has the benchmark's own notes tools. The Register added nothing the score could see.

## Benchmark

[Vending Bench](https://github.com/ProsusAI/vending-bench) by [Prosus](https://www.prosus.com) (commit `f9a1d7e`). The agent runs vending machines for 30 simulated days with 22 business tools (suppliers, orders, storage, slots, prices, customer complaints and refunds). It starts with €1,500; the score is the final money balance.

Harness: the benchmark's own runner (`scripts/run_openrouter.py`), kept as published (briefing, tools, memory policy, rolling window of complete tool exchanges capped at 60,000 characters), with the transport swapped from OpenRouter to the Anthropic API. Model: Claude Haiku 5.5, default temperature, up to 4,096 output tokens.

## The two setups

- **Default**: the benchmark as published. The agent keeps memory with its `write_note`, `read_notes` and reminder tools.
- **Register**: the same, plus a Register between the agent and the tools. It records what the agent received (complaints and the amounts still owed, orders and deliveries, storage, slots, offers seen), appends a short "where am I" record to `get_status` and to the morning report, and refuses three actions the briefing calls irreversible mistakes: a refund that is not the full amount still owed, a payment whose reference matches nothing the agent saw, the same order twice in one day.

## Result

| Claude Haiku 5.5, seeds 11 to 40 | Final balance, mean | Median | Register refusals | API cost, 30 runs |
|---|---|---|---|---|
| Default (own notes) | €1,909 | €1,917 | - | $6.77 |
| Register | €1,929 | €1,904 | 12 | $6.83 |

Paired by seed: +€20 on average, the Register ahead on 15 of 30 seeds, p = 0.70 (paired t-test). No measurable difference.

Seeds 1 to 10 ran a first Register version that also showed slot stock levels "when last seen"; overnight sales made those counts stale and misleading, so that line was removed and the comparison rerun on fresh seeds 11 to 40. The first batch is included in `data/` for completeness.

## Files

- `code/run_vb.py`: the benchmark's runner with the Anthropic transport, both setups
- [`register/vending-bench/vb_register.py`](../../register/vending-bench/vb_register.py): the Register used for seeds 11 to 40; [`vb_register_v1.py`](../../register/vending-bench/vb_register_v1.py): the first version (seeds 1 to 10)
- `results/runs.csv`: one row per run
- `data/runs_register_v2_seeds11-40.zip`, `data/runs_register_v1_seeds1-10.zip`: full conversations, trajectories and results per run, recorded when the Register was still called the Logbook (the runs inside keep that name)

## Reproduce

```bash
git clone https://github.com/ProsusAI/vending-bench && cd vending-bench && git checkout f9a1d7e
# install the simulator as described in its README, then:
export ANTHROPIC_API_KEY=... VENDING_BENCH_ROOT=$(pwd)
CODE=/path/to/agent-register/benchmarks/vending-bench/code   # a clone of this repo: the runner loads the Register from register/
python $CODE/run_vb.py --arm register --model claude-haiku-5-5 --seeds 11 12 13 --output runs/register
```
