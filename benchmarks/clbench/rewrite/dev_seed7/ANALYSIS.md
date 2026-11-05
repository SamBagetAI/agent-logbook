# What the traces say, and what to change in the harness

Read from `traces/run_0000.json.gz` (the rollout) and `traces/baseline.json.gz` (the stateless baseline, same hands), 2026-11-04.

## 1. The Register's facts were right

The final text says, for Alex: "F fold 22/27, call 5" to our flop raises, and "preflop, we are BB, they open: limp 4 / raise 15".
An independent recount from the prompts gives fold 22, call 5, and limp 4, raise 15. The record is accurate; the problem is not the facts.

## 2. The agent reads it, and turns counts into rules

Over the 193 decisions after the rewrite, the reasoning cites the Register in 99% of them, as percentages it computes itself
("FLOP fold rate = 15/16 = 93.8%"), as rules it derives ("when Alex bets the flop, it signals strength and I should fold with
weak hands": 21% of decisions), and as its own worst hand, which the Register lists under BIGGEST LOSSES ("hand #29, -486, from
over-commitment": 9% of decisions). "vs our raises: P fold 0/2" becomes "Alex's 4-bet range is very strong".

## 3. What it does with that: it plays smaller

Against Alex after the rewrite (hands 34-50), the rollout against the baseline on the same cards: raises 26% of decisions
against 37%, calls 53% against 31%, raise size 0.92 pot against 1.50, showdowns 0% against 19%. The hands where the rollout did
worst relative to the baseline are its best hands played small: pocket kings, +10 against +100 (the baseline shoved, the
rollout called a 4-bet, then "the Ace is a problem"); a straight and a flush, +14.8 against +66 (bets of 15, 38, 85 against
40, 60, 160, 400). The one stage the rollout wins is Tom's return (+4.38 against -0.32): the memory of a calling station, the
Register's since hand 1, which the baseline cannot have.

## 4. The text makes the model think more, at a cost

Output tokens per decision, rollout against baseline: 353 against 302 on Tom; 1,052 against 278 right after the rewrite
(the new text was longer and per opponent); 500 to 540 against 325 afterwards. Rollout $13.28, baseline $2.44. The published
rollouts showed the same: with the Register, Sonnet writes about twice as much reasoning, and the history carries it forward.

## 5. On this task, the history itself is the harm

The stateless baseline beats the rollout here (+176 against +129), and in the published runs it beats 3 of the 5 ICL + Register
rollouts and ICL's own gain is negative. Memory in the context, with its own past reasoning, costs more than it brings on poker.
The AhaBench setup, where the Register won, keeps 8 actions in view, not the run.

## What to change, cheapest and most telling first

1. **A stateless system with the Register** (`register_stateless`): the context is reset at every instance, the Register keeps the
   record across them. The Register is the memory, nothing else is. A rollout costs what the baseline costs, about $3 instead of
   $13 to $24, and the comparison with the existing stateless baselines is on the same hands. 5 rollouts: about $15. It
   answers the question this run could not: what the Register is worth on poker, apart from the history.
2. **Design principles for the author** (prompt v3, to test with the stateless system): counts, never rates, and "too few"
   under five observations; no section ranking the agent's own losses, which primes caution; a header line "what the
   counterpart did, not what you should do". An offline rewrite shows the text for $0.02; a stateless rollout shows the play
   for $3.
3. **Shorter text**: cap the brief at 2,000 characters and tell the model, in the footer, not to restate it. Cuts the reasoning
   inflation; may cut the hedging. Cheap to try with 1.
4. **Measure value capture** in `traces.py`: bet size over pot, share of decisions that raise, pot won with winning hands,
   against the baseline. Three numbers that showed the pattern here; they should be computed for every run.
5. A fact check of the Register against the trace is low priority: the counts were right.

One rollout, one seed. Points 2 and 3 are a reading of 193 decisions, consistent with three signals (smaller bets, rules
derived from counts, the loss cited), not a proof.
