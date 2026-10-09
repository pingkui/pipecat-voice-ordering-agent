# Voice ordering agent (Pipecat) with a deterministic core

A phone-style voice agent for a restaurant: it takes food orders, answers menu questions, books tables and hands
the call to a person when it should. The caller talks in a browser tab (no phone number needed).

The design rule: **the model talks, code decides.** Menu lookup, prices and totals, quantities, opening hours, and
the rule that an order may only be placed after the caller confirmed a read-back of its *current* contents all live
in `order_core.py`, as plain code with its own tests. The model only chooses which tool to call and phrases the reply.

```
browser mic -> speech-to-text -> LLM (tools) -> text-to-speech -> browser speaker
                                    |
                              order_core.py   (menu, prices, hours, confirmation rules)
```

## Documentation
| document | read it for |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | components, the tools, the confirmation gate as a state machine, design choices |
| [`docs/RUNNING.md`](docs/RUNNING.md) | install, keys, running the tests and the voice bot, troubleshooting |
| [`docs/TESTING.md`](docs/TESTING.md) | what each test proves, the scenarios, how to add one, how to measure voice latency properly |
| [`docs/SECURITY.md`](docs/SECURITY.md) | exposure, untrusted callers, personal data, secrets, phone-line risks |
| [`docs/examples/sample_calls.md`](docs/examples/sample_calls.md) | real scripted calls with tool calls and final state |

## What is in here
| file | purpose |
|---|---|
| `order_core.py`, `menu.json` | the ordering logic and a fictional menu |
| `bot.py` | the Pipecat voice bot (browser WebRTC transport, Silero VAD, Deepgram STT, Cartesia TTS, any OpenAI-compatible LLM) |
| `test_core.py` | 31 deterministic checks, no model, no network |
| `chat_sim.py` | scripted callers talk to the real model through the same prompt and tools; the final order state is asserted |
| `test_pipeline.py` | the same scenarios through a real Pipecat pipeline (LLM service, tool schema, handlers, context aggregation) with speech replaced by text |
| `test_tools.py` | 7 checks on the latency summary maths |
| `tools/` | `latency_report.py` summarises `latency.jsonl`; `make_sample_calls.py` renders scenario runs as readable calls |

## Guarantees that do not depend on the model behaving
- Prices and totals are computed from the menu by code. The model never states a price from memory.
- Pizzas need a size; unknown items, bad quantities (not 1 to 20, not an integer), and out-of-hours or oversized reservations are refused with a reason the model can relay.
- **Confirmation gate:** `confirm_order` fails unless (a) `read_back` was called after the last change to the order **and** (b) the caller has spoken since that read-back. Any later change withdraws the confirmation. So an order cannot be placed in the same turn as its read-back, and a stale read-back cannot be confirmed.
- Allergy and health questions are routed to a person; the menu data only lists allergens and says staff must confirm.

## Results so far
- `test_core.py`: **31/31** checks pass.
- `chat_sim.py`, real model (Kimi `kimi-k2.6`), **10/10** scenarios pass: simple pickup, change of mind mid-order, item not on the menu, special instructions, allergy question goes to staff, no order without a clear yes, impatient caller (twice), reservation inside and outside opening hours.
- `test_pipeline.py`, same model through Pipecat 1.12.0: **10/10** scenarios pass.
- The development server starts and serves the browser client on `127.0.0.1:7860`.
- Model-only latency measured in the text simulation: **median 2.4 s, p90 4.4 s per LLM call** (46 calls). That is the reasoning model's raw call time with no streaming to speech; it is too slow for a natural phone conversation, which is why `bot.py` takes the LLM from environment variables so a fast, non-reasoning model can be dropped in.

## Not done yet (be skeptical until it is)
- **No end-to-end voice run yet.** Speech-to-text and text-to-speech need provider keys that were not available when this was built, so real audio latency, barge-in (interrupting the agent) and speech recognition accuracy are **unmeasured**. `bot.py` logs the user-to-bot latency of every turn to `latency.jsonl`; `docs/TESTING.md` describes how to turn that into a defensible figure (`tools/latency_report.py`). No such figure exists yet.
- Scenario runs are single runs on one model: they show the harness and the rules work, not a pass rate.
- No telephony (Twilio/SIP) and no POS integration; orders are kept in memory and written to `calls.jsonl` as a summary.
- Scripted callers are polite and clear. Noisy audio, accents and people who talk over the agent are not covered.

## Run it
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python test_core.py                       # no keys needed
.venv/bin/python test_tools.py
export LLM_BASE_URL=... LLM_API_KEY=... LLM_MODEL=...
.venv/bin/python chat_sim.py --model $LLM_MODEL     # text-mode scenarios
.venv/bin/python test_pipeline.py                   # same scenarios through Pipecat
export DEEPGRAM_API_KEY=... CARTESIA_API_KEY=... CARTESIA_VOICE_ID=...
.venv/bin/python bot.py                             # then open http://localhost:7860/client/
```
`bot.py` serves on localhost only. It calls paid APIs, so do not expose it to the internet without authentication and rate limits.

## License
MIT, see `LICENSE`.
