# Development run: a Logbook rewritten in the middle of a poker rollout

Written before the run's result was read (2026-11-03, 18:10).

**Question.** Does a Logbook rewritten in the middle of the rollout, by a model, from the rollout so far, change what the
agent does on a schedule whose opponents change, compared with the Logbook written before the run?

**What runs.** `clbench run exploitable_poker --system logbook_icl --schedule dev_seed7 --runs 1`, Claude Sonnet 4.6 as the
agent (the model of the published runs), with:

- the published poker Logbook ([`../../written_logbooks/exploitable_poker.py`](../../written_logbooks/exploitable_poker.py)),
  written before any run from the task's brief, unchanged, as the starting Logbook;
- `rewrite_after_instances=30`: at the first turn of hand 31, 25% of the 120 hands, the author rewrites it from the
  rollout so far. The fraction is the same rule for every task; it was not chosen after looking at this run;
- Claude Haiku 5.5 as the author, effort low, 32,000 tokens, no past run, no outcome sentence. Its call is recorded as a
  usage event of the rollout, so it is in the rollout's cost;
- the stateless baseline the harness runs with every rollout, unchanged (reset at every hand, it never reaches hand 31).

**Why `dev_seed7`.** The evaluation schedule (`default`) draws its hands from seeds 42 to 46. `dev_seed7`
([`../../code/schedules/exploitable_poker_dev_seed7.json`](../../code/schedules/exploitable_poker_dev_seed7.json)) keeps its
five stages and their sizes (20, 30, 10, 35, 25 hands; calling station, loose aggressive, calling station, fit or fold,
loose aggressive) with seeds 7 to 11. Nothing in this run is evaluation data, so the mechanism can be looked at and changed
after it. The offline rewrite in [`../poker_at_hand31_haiku.py`](../poker_at_hand31_haiku.py) read 30 hands of a published
evaluation rollout: it is not used here, nor anywhere a score is reported.

**What is read afterwards.** Whether the rewrite happened at hand 31 and what it produced (`author/`); the reward per
stage before and after it; the Logbook's errors; the rollout's cost with the author's call. One rollout, one seed: it says
whether the mechanism runs end to end on the real harness and what the agent does with the rewritten text, not whether the
score improves. That needs 5 rollouts on `default` against 5 same-day rollouts without the rewrite.

**What this run does not settle.** The effect on the score, and whether a rewrite at 25% is the right point.
