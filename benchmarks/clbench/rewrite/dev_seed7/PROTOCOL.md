# Development run: a Register rewritten in the middle of a poker rollout

Written before the run's result was read (2026-11-03, 18:10).

**Question.** Does a Register rewritten in the middle of the rollout, by a model, from the rollout so far, change what the
agent does on a schedule whose opponents change, compared with the Register written before the run?

**What runs.** `clbench run exploitable_poker --system register_icl --schedule dev_seed7 --runs 1`, Claude Sonnet 4.6 as the
agent (the model of the published runs), with:

- the published poker Register ([`../../written_registers/exploitable_poker.py`](../../written_registers/exploitable_poker.py)),
  written before any run from the task's brief, unchanged, as the starting Register;
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
stage before and after it; the Register's errors; the rollout's cost with the author's call. One rollout, one seed: it says
whether the mechanism runs end to end on the real harness and what the agent does with the rewritten text, not whether the
score improves. That needs 5 rollouts on `default` against 5 same-day rollouts without the rewrite.

**What this run does not settle.** The effect on the score, and whether a rewrite at 25% is the right point.

## During the run (2026-11-04, 12:40, 55 hands played)

- The first attempt (2026-11-03, 18:08) was cut: the process hung on network errors during the night and was killed when the
  session ended; the harness writes traces at the end, so nothing of it was saved but the author's output at hand 31
  ([`author_attempt1_cut/`](author_attempt1_cut)). The second attempt runs detached, with live snapshots on disk.
- The rewrite happened at hand 34, not 31: `rewrite_after_instances` counts the instances the system was asked about, and
  three hands (24, 28, 31) ended before the system acted, the opponent having folded first. The rule is kept; it is now
  said in the system's docstring. Under the evaluation's permuted runs, hands are shuffled within each stage, so a rewrite
  "after 30 hands" lands somewhere in stage 2 either way.
- The author's call: Claude Haiku 5.5, 1 round, $0.019, a 292-line Register that keys every count by the opponent's name,
  valid on the 101 decisions so far. The Register's text went from 2,206 to 1,243 characters at the switch.

## Result (2026-11-04, 13:51)

The run completed: 120 hands, 294 calls, $13.28 (the author's call: $0.019); the stateless baseline, $2.44. Traces, run and
baseline, in [`traces/`](traces); the author's inputs, code and log in [`author/`](author).

**The mechanism ran end to end on the real harness.** The rewrite happened at hand 34 (see above), Haiku's Register replayed the
101 decisions so far with 0 errors and took over; 0 Register errors over the 294 calls; the author's call is in the rollout's
cost under its own call type. The agent read the new text: at hand 34 its reasoning quotes the per-opponent line
("when we are button and act last: F check 4/bet 1") and draws on it.

**On reward, no sign that it helped.** Mean reward per hand, the rollout against the stateless baseline on the same hands:

| Stage | Rollout (Register, rewritten at hand 34) | Stateless baseline |
|---|---|---|
| Tom, calling station, hands 1-20 | +5.16 | +5.04 |
| Alex, loose aggressive, 21-33 (old Register) | -2.82 | -2.00 |
| Alex, 34-50 (rewritten Register) | +0.85 | **+5.92** |
| Tom again, 51-60 | **+4.38** | -0.32 |
| fit-or-fold, 61-95 | +0.22 | +0.55 |
| Alex again, 96-120 | -0.15 | -0.62 |
| Total | +128.7 | **+176.3** |

The rise from -2.82 to +0.85 against Alex after the rewrite, which looked like an effect while the run was going, is the
cards: on the same hands, the agent with no memory at all went from -2.00 to +5.92. With the per-opponent Register the agent
captured +0.85 of it. The one stage the rollout wins clearly is the return of Tom (+4.38 against -0.32): memory of the
calling station, which the Register had from hand 1 and the baseline never has. Over the 120 hands the rollout is 47.6 below
the baseline. That is the published pattern on this task: of the 5 published ICL + Register rollouts on the evaluation
schedule, 3 are below their stateless baseline (120, 139 and 151 against 274), and the task's normalized gain is negative for
ICL with or without the Register.

**What this settles, and not.** One rollout, one seed: it says the mechanism works and what the agent did with the rewritten
text, not what it does to the score. It gives no reason to expect a gain on poker from the rewrite alone: the agent used the
per-opponent facts and still played Alex worse than no memory. The next question is not poker. It is whether a rewrite helps
on a task where the Register already helps (radio spectrum, code fixes), or on a task where the first Register missed something
the run shows. That needs 5 rollouts against 5 same-day controls, on the evaluation schedule.
