**English** | [简体中文](zh-CN/TESTING.md)

# Testing

## What each test proves
| command | needs | proves | does not prove |
|---|---|---|---|
| `python test_core.py` | nothing | the ordering rules: sizes, quantities, totals, delivery, opening hours, confirmation gate | anything about a model or speech |
| `python test_tools.py` | nothing | the latency summary maths | the latency itself |
| `python chat_sim.py --model M` | an LLM key | with a real model and the real prompt, scripted callers end in the right order state | speech recognition, audio quality, timing |
| `python test_pipeline.py` | an LLM key | the same scenarios work through Pipecat's LLM service, tool schema, function-call handlers and context aggregation | the audio path, turn detection on real speech |
| manual voice session | all keys | the whole thing end to end | pass rates (too few runs) |

The assertions are always on the **final state** of the order, not on what the agent said. A model can phrase a reply a
hundred ways; the state is what the kitchen would receive.

## The scenarios
| scenario | the caller does | state that must hold at the end |
|---|---|---|
| `simple_pickup` | orders pizzas and a salad for pickup, gives a name, says yes | placed, total computed by code |
| `change_of_mind` | orders three pizzas, then says "make that two" | placed with two pizzas, price for two |
| `unknown_item` | asks for something not on the menu, then orders fries | only fries in the order |
| `special_instructions` | burger with no onions | note saved on the line, order placed |
| `allergy_goes_to_staff` | asks if a dish has peanuts, says the allergy is severe | transferred to staff, nothing placed |
| `no_confirmation_no_order` | builds an order, then says they will call back | not placed |
| `impatient_no_followup` | tells the agent to place it now and skip the read-back | not placed in that turn |
| `impatient_then_confirms` | same, then says yes after the read-back | placed |
| `reservation_out_of_hours` | wants a table at 3 a.m. | no reservation |
| `reservation_ok` | table for four at 19:00 | reservation with party 4 at 19:00 |

Results so far are in the main README. They are single runs per scenario on one model.

## Adding a scenario
Append a dict to `SCENARIOS` in `chat_sim.py`:
```python
{"name": "split_order",
 "customer": ["Two fries and a lemonade for pickup, name is Ben.", "Yes."],
 "check": lambda s, calls: (True, "") if s.placed and s.total_cents() == 1400 else (False, "wrong total")}
```
`s` is the `OrderSession`; return `(ok, reason)`. Both `chat_sim.py` and `test_pipeline.py` pick it up. Good scenarios to add are
the ones where the caller pushes against a rule: a quantity of 50, a changed address after the read-back, two items with
different notes, a caller who answers a question that was not asked.

## Measuring voice latency (the number that is still missing)
The latency of a voice agent is the time between the caller **finishing** a sentence and hearing the **first audio** of the reply.
`bot.py` records exactly that per turn into `latency.jsonl`. A defensible measurement:
1. Fix the providers and the region the bot runs in, and write them down next to the result.
2. Use a fixed script of at least 30 turns, mixing short answers ("yes"), order requests that trigger tool calls, and
   questions that need no tool.
3. Run it several times, at different times of day.
4. Report **median, p90 and p95**, plus the share of turns under 1 s and under 2 s: `python tools/latency_report.py`.
5. Report tool-call turns separately: they cost an extra model call.
6. State what was not controlled (network distance between you, the server and the providers).

Do not quote a single fast turn. Until this has been run with real keys, **no end-to-end latency figure exists for this
project**; the only number measured is the model-only call time in the text simulator.

## Manual checks that automated tests cannot do
- **Barge-in:** speak while the agent is talking. It should stop and listen; note how long that takes.
- **Noise and accents:** try a noisy room and a non-native accent, and note which words the speech-to-text gets wrong
  (menu item names are the usual casualty).
- **Silence:** stay quiet after a read-back. The agent must not place the order.
- **Hang-up mid-order:** disconnect after a read-back; `calls.jsonl` should record `order_not_placed`.
