# The Register code

A Register is a record kept by code, outside the model. Every Register here does the same three things:

| Step | When | What it does |
|---|---|---|
| Record | after every action | reads the tool result the agent received and updates a record organized around the task (suppliers, orders, products, failures, money), not around messages |
| Show | before a model call | puts a short summary of that record in the agent's context |
| Check (optional) | before an action runs | refuses an action that breaks a written rule; the refusal goes back to the agent as a tool result |

It never reads the benchmark's internals: every line it shows comes from something the agent was shown. It keeps its record for the whole run, even when the agent's own history is trimmed or lost.

There is one Register per benchmark, because the record has to be written in the task's terms. These files are the exact code behind the published results, moved here from `benchmarks/*/code` without changes. The runners in [benchmarks/](../benchmarks) load them from this folder. Replaying the published AhaBench logs through these files rebuilds every Register block the agents saw, byte for byte (37,837 model calls, [`verify_replay.py`](../benchmarks/ahabench/code/verify_replay.py)).

## The Registers

| Benchmark | Code | What it records | How the agent sees it | What it checks | Result |
|---|---|---|---|---|---|
| [AhaBench](../benchmarks/ahabench), Aha-Vending | [`ahabench/vending_logbook.py`](ahabench/vending_logbook.py) | money trend, sales and profit at each price, assortment and weather, slot sizes, failed commands and the units they destroyed, suppliers found, quotes, orders and their issues, refused orders and why | in the prompt's PLANNER NOTE block at every decision, through [`logbook_provider.py`](../benchmarks/ahabench/code/logbook_provider.py) | nothing (the check switch was off in the published runs) | Haiku 5.5: 12/15 runs still in business at day 60, 0/15 without it |
| AhaBench, generic | [`ahabench/generic_logbook.py`](ahabench/generic_logbook.py) | every failure and refusal, grouped and counted, and the last call that worked per tool; v2 adds who sent each refusal and where each address came from | same | nothing | 3/15 (v1) and 1/15 (v2) at day 60 |
| [τ²-bench](../benchmarks/tau2-bench) retail | [`tau2-bench/logbook_agent.py`](tau2-bench/logbook_agent.py) | who is authenticated, each order and its status, the writes already done | after a restart (crash test); otherwise only through its refusals | every write, against rules taken from the written policy | no score gain; after a crash, 0 conversations with a repeated write, 27 to 57 of 160 without it |
| τ²-bench airline | [`tau2-bench/logbook_airline.py`](tau2-bench/logbook_airline.py) | the same, for the airline policy | same | same | written, not used in the published results |
| [Vending Bench](../benchmarks/vending-bench) | [`vending-bench/vb_logbook.py`](vending-bench/vb_logbook.py) (first version: [`vb_logbook_v1.py`](vending-bench/vb_logbook_v1.py)) | complaints and the amounts still owed, orders and deliveries, storage, slots, offers seen | appended to the results of `get_status` and `wait_for_next_day` | a refund that is not the full amount still owed, a payment whose reference matches nothing seen, the same order twice in a day | none: €1,929 vs €1,909 without it (p = 0.70) |

Same three steps, different method names:

| Class | Record | Show | Check |
|---|---|---|---|
| `VendingLogbook` | `observe` | `brief` | `check` |
| `GenericLogbook` | `record` | `brief` | none |
| `RetailLogbook`, `AirlineLogbook` | `observe` | `brief` | `check` |
| `Logbook` (Vending Bench) | `after` | `brief` | `check` |
| `Logbook` written by a model ([author/](../author)) | `observe` | `brief` | none |

## Writing one for your task

To have a model write it, see [author/](../author): from a spec of your agent and one past run, Claude Opus 5.5 writes the Register, a Python class with `observe(step)` and `brief()` (record and show, no check), and [`author/adapter.py`](../author/adapter.py) plugs it into your loop. On AhaBench, its Registers kept 15 of 15 runs in business at day 60, against 14 of 15 for the one written by hand.

To write it yourself, here is what the three benchmarks taught us:

- **It helps when the agent needs a total over many steps.** A refusal that left its view, a transmitter seen in earlier scans: what one prompt no longer holds, or holds without adding it up. On Continual Learning Bench the agent kept its whole history and still gained where there was something to add up (radio scans, code fixes). If one prompt already holds what the agent needs (a short conversation), expect nothing on the score, and a cost: on poker the model wrote twice as much reasoning with the Register in front of it, and on cohort studies one run applied the rule set the Register listed as best and collapsed.
- **Record outcomes, in the task's terms.** The same supplier refusal, kept as a raw error line ("x10 [read_email from 3 different senders: ...]: We can prepare this order, but we still need the following before we can proceed: account number"), did not change what Haiku did. Kept as an outcome of the agent's own goal, per supplier ("Bunch Vending Support: order refused: missing info x3 (missing: account number)"), it did.
- **Only what the agent was shown.** Parse tool results, never the environment's state. It keeps the comparison fair, and it is all you have outside a benchmark.
- **Short and stable.** The same sections, in the same order, at every call.
- **Blocking is optional.** On τ²-bench the gate changed nothing on the score; the AhaBench gain came with no blocking at all.

A minimal skeleton:

```python
class Register:
    def __init__(self):
        self.record = {}                 # organized around the task: suppliers, orders, products, ...

    def observe(self, action, result):   # after every tool call, with the text the agent received
        ...                              # parse it and update the record, in the task's terms

    def brief(self) -> str:              # before every model call
        return "LOGBOOK (kept since the start; your history only shows the last steps)\n- ..."

    def check(self, action):             # optional: a reason to refuse the action, or None
        return None
```

In the agent loop: call `observe` after each tool result, put `brief()` in the context before each model call, and when `check` returns a reason, send it back to the model as the tool result instead of running the action.
