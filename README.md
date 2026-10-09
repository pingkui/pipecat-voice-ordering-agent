**English** | [简体中文](README.zh-CN.md)

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
| `speech_sse_tts.py` | text-to-speech adapter for `/audio/speech` endpoints that stream server-sent events (written against `qwen3-tts-flash`) |
| `transcriptions_stt.py` | speech-to-text adapter for `/audio/transcriptions` endpoints (multipart `model` + `file`, JSON reply); written from the gateway's example for `qwen3-asr-flash` |
| `test_stt_adapter.py` | 15 offline checks of that adapter against a local fake server |
| `test_tts_adapter.py` | 15 offline checks of that adapter against a local fake server |
| `test_tools.py` | 7 checks on the latency summary maths |
| `tools/` | `latency_report.py` summarises `latency.jsonl`; `make_sample_calls.py` renders scenario runs as readable calls; `tts_check.py` and `roundtrip_check.py` check the speech services; `virtual_caller.py` runs the scenarios through the whole voice pipeline with a synthetic caller |

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

- Switching the reasoning off helps a lot (measured from this server to the Kimi API, streaming, 3 runs per setting, so a rough figure):
  first token after **1.36 s** median with `kimi-k2.6` and `{"thinking": {"type": "disabled"}}`, against 4.84 s with the default (one
  successful run out of three). The scenarios were rerun with reasoning off: **10/10 pass**, full-call median 1.9 s, p90 2.5 s (55 calls).
  Set it with `LLM_EXTRA_BODY='{"thinking":{"type":"disabled"}}'`. A first run with reasoning off failed one scenario, but that was the
  script, not the agent: the model re-asked "pickup?" and used up the caller's confirmation line (see `finish_with_yes` in `docs/TESTING.md`).

- Text-to-speech measured live through the adapter (`qwen3-tts-flash` behind a gateway, 6 requests, voice `Cherry`): **first audio at a median of
  1.10 s** (max 1.33 s), and synthesis takes about 0.36 s per second of audio, so it keeps ahead of playback. The output is valid 24 kHz
  16-bit mono audio. The first-audio time is on top of the model's first-token time, so it counts against the call's latency budget.

- **End to end with the real services and a synthetic caller** (`tools/virtual_caller.py`: the scripted callers are spoken by a second
  synthetic voice and fed in real time into voice-activity detection, `qwen3-asr-flash`, Kimi with reasoning off, and `qwen3-tts-flash`):
  **10/10 scenarios pass** over 21 turns, including the safety ones. Speech recognition changed the wording on 6 of the 21 turns
  (names: "Ann" heard as "Anne" or "Anna"; "margherita" as "margarita"; spelled-out numbers) and none changed an outcome.
- **Where the time goes** (same run, end of the caller's audio to the first reply audio): median **7.7 s**, p90 9.0 s, max 10.1 s.
  Voice-activity detection ends the turn within about 0.2 s; the speech-to-text transcript arrives a median 1.7 s after the caller stops
  (max 3.4 s); a turn with no tool call then takes about 3.8 s more to the first audio (model first sentence plus text-to-speech), 5.6 s in total;
  every tool-call round adds another 1.5 to 2.7 s, so turns with tool calls take a median 8.4 s. Fewer sequential tool rounds,
  a filler phrase while tools run, a faster model and streaming speech recognition are the obvious next steps; none is done.
- **The text-to-speech key is rate limited**: about 10 requests a minute, measured (the 11th request in a burst gets 429 for about 50 s).
  Every sentence is one request, so a sustained conversation can hit it. The adapters retry a 429 up to three times, which does not
  help against a quota; the test tool paces its turns (`TTS_REQUESTS_PER_MINUTE`).
- Text in, speech out, text back (`tools/roundtrip_check.py`, 8 sentences): the pair returns the same words for 6 of 8 (mean word error
  rate 4.5%; "My name is Sam" came back as "My name is Xiao"); text-to-speech first audio median 0.77 s, speech-to-text median 1.55 s.

## Not done yet (be skeptical until it is)
- **Not tried with a human caller.** The end-to-end run above used a synthetic caller (clean synthetic speech fed into the pipeline). No person has spoken to it through a browser microphone, so real speech recognition accuracy on accents and noise, barge-in (interrupting the agent) and WebRTC behaviour are **unmeasured**. `bot.py` logs the user-to-bot latency of every turn to `latency.jsonl`; `docs/TESTING.md` describes how to measure it properly (`tools/latency_report.py`).
- **The latency is too high for a natural phone call** (see the measured figures above): about 7.7 s from the end of the caller's speech to the first reply audio.
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
