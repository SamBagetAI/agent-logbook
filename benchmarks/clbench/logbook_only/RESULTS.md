# The Register as the only memory: two development rollouts on poker

2026-11-04. Schedule `dev_seed7` (the evaluation's five stages with seeds 7 to 11: no evaluation hand), Claude Sonnet 4.6 as the
agent, the published poker Register unchanged, no rewrite. System `logbook_only` ([`../code/logbook_only/`](../code/logbook_only)):
the context is cleared at the end of every hand; the Register keeps its record and its text opens every message. One run without
and one with the skill, four lines in the system prompt ([`../code/usage_note.txt`](../code/usage_note.txt)): counts are counts,
not rules; start from today's numbers; no advice in there; do not restate it. The day before, on the same hands: ICL + Register
(the whole run in context) and its stateless baseline ([`../rewrite/dev_seed7/`](../rewrite/dev_seed7)).

| Same 120 hands | ICL + Register | Stateless baseline | **Register only** | **Register only + skill** |
|---|---|---|---|---|
| Total reward | +128.7 | +176.3 | **+230.0** | **+313.7** |
| Own stateless baseline, same day | +176.3 | | +135.9 | +192.9 |
| Gain over own baseline | −47.6 | | +94.1 | +120.8 |
| Tom, calling station, hands 1-20 | +5.16 | +5.04 | +9.14 | +13.98 |
| Alex, loose aggressive, 21-50 | −0.74 | +2.49 | +1.68 | +1.72 |
| Tom again, 51-60 | +4.38 | −0.32 | −0.42 | −0.40 |
| fit-or-fold, 61-95 | +0.22 | +0.55 | +0.65 | +0.24 |
| Alex again, 96-120 | −0.15 | −0.62 | −0.88 | −0.88 |
| Output tokens per decision | 631 | 330 | 592 | 375 |
| Cost of the rollout | $13.28 | $2.44 | $5.00 | $4.13 |

What the decisions show (same measures as [`../rewrite/dev_seed7/ANALYSIS.md`](../rewrite/dev_seed7/ANALYSIS.md)):

| | ICL + Register | Register only | Register only + skill | Stateless |
|---|---|---|---|---|
| Mean raise, in pots | 0.85 | 1.03 | 0.96 | 1.05 |
| Decisions that raise | 29% | 35% | 34% | 42% |
| Rule phrases repeated 3 times or more | 26 | 0 | 0 | 0 |
| "never" / "always" per decision | 1.52 | 1.49 | 0.99 | 0.04 |
| Reasoning, characters | 1,263 | 1,641 | 1,051 | 940 |

- **The frozen rules came from the history.** With the whole run in context, 26 rule phrases repeat three times or more; with the
  Register as the only memory, none. Bets go back to a pot.
- **Hardening small counts comes from the model.** Without the history, "never" and "always" stay at 1.5 per decision; the skill
  halves them, it does not remove them.
- **Without the skill, the Register alone makes the model reason longest** (1,641 characters): it derives everything from the counts
  at every decision. The skill brings that to 1,051 and the restating of the Register to zero, for a higher total.
- **The gain is taken against the calling station**, where the Register says `fold 0/call 7` and the agent, free of its earlier
  conclusions, bets. Against the three other opponents everyone is near zero, this day and the day before.
- **Stateless baselines vary a lot on the same hands**: +135.9, +176.3, +192.9. One rollout per setup, one seed: the ordering is
  clear, the sizes are not.

Traces (run, baseline, artifacts) in [`dev_seed7/traces/`](dev_seed7/traces) and [`dev_seed7_note/traces/`](dev_seed7_note/traces).
Not measured: the other five tasks, and the evaluation schedule.

## The other tasks, one rollout each (2026-11-04, evening)

Evaluation schedule, one rollout, the stateless baseline skipped (`LOGBOOK_SKIP_BASELINE=1`), Register as the only memory,
with the skill. Published rollouts of the same model for comparison. Traces in [`sanity/`](sanity).

| Total reward | ICL, 5 published | ICL + Register, 5 published | **Register only + skill**, 1 | Stateless |
|---|---|---|---|---|
| Blind Spectrum Monitoring, 90 scans | 32.1 to 39.8, mean 36.6 | 49.7 to 54.4, mean 52.5 | **+53.6** ($1.29, against $3.58) | 19.7 |
| Cohort Studies, 20 studies | −2.2 to −0.5, mean −1.4 | −2.6 to −0.3, mean −0.9 | **−0.36** ($7.77, against $5.15) | −0.5 |
| Database Exploration, 40 questions | 9.6 to 17.3, mean 15.0 | 11.6 to 20.8, mean 15.5 | **+8.1** ($2.45) | 4.7 |

- **Spectrum: the whole gain, without any history, at a third of the price.** The memory the task needs is a table of
  transmitters seen in past scans; the Register holds it. The context at scan 90 is 3,800 tokens instead of hundreds of
  thousands.
- **Cohort: the best of the eleven rollouts, on the noisiest task of the six**, and the most expensive run of the three: the
  Register's text is long there and the model reasons at length over it.
- **Database: below every ICL rollout, barely above no memory.** The agent made 337 calls against 156 in the published
  ICL + Register rollout: without the history it no longer has the rows its earlier queries returned, so it queries again,
  and each query costs reward. The memory this task needs is content, the results themselves, not counts; a 4,000-character
  Register cannot carry it. Score before the schema change 4.9, after 3.3.

So the Register alone replaces the history where what must be remembered is a count or a table (spectrum, poker), and
not where it is the content of past results (database). Codebase Adaptation and Sales Prediction were not run: their tasks
run in Docker, absent on this machine. One rollout per task.
