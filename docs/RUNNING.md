**English** | [简体中文](zh-CN/RUNNING.md)

# Running it

## 1. Install
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt     # pins pipecat-ai 1.12.0 with the extras this project uses
```
Python 3.11 or newer (required by pipecat-ai 1.12.0). The first start downloads nothing large; the Silero voice-activity model ships with the package.

## 2. Run the tests that need no keys
```bash
.venv/bin/python test_core.py      # 31 checks on the ordering rules
.venv/bin/python test_tools.py     # 7 checks on the helper tools
```

## 3. Run the scenario tests against a model (text only)
You need any OpenAI-compatible chat API that supports tool calling.
```bash
export LLM_BASE_URL=https://.../v1
export LLM_API_KEY=...
export LLM_MODEL=...
.venv/bin/python chat_sim.py --model $LLM_MODEL            # 10 scripted callers, asserts the final order state
.venv/bin/python test_pipeline.py                          # same scenarios through a real Pipecat pipeline
```
Each scenario makes several model calls, so this spends a small amount on your provider. `chat_sim.py --only NAME --repeat 3`
runs one scenario several times, which is the quickest way to see whether a scenario is flaky.

## 4. Run the voice bot
Accounts you need (each provider has its own pricing and free tier; check them before you start):
- **Speech-to-text:** Deepgram, `DEEPGRAM_API_KEY`.
- **Text-to-speech:** Cartesia, `CARTESIA_API_KEY` and `CARTESIA_VOICE_ID` (pick a voice from their library).
- **LLM:** the same three variables as above. For a phone-like feel use a **fast, non-reasoning model**; a reasoning model
  spends seconds before the first token (measured here: median 2.4 s and p90 4.4 s per call with a reasoning model, text only).

```bash
export DEEPGRAM_API_KEY=... CARTESIA_API_KEY=... CARTESIA_VOICE_ID=...
export LLM_BASE_URL=... LLM_API_KEY=... LLM_MODEL=...
.venv/bin/python bot.py
```
Open **http://localhost:7860/client/**, allow the microphone, press connect, and talk. The server listens on `127.0.0.1` only.

Swapping a provider is a change in `bot.py`: replace `DeepgramSTTService` or `CartesiaTTSService` with another Pipecat
service class (for example another STT or TTS vendor) and keep the rest of the pipeline.

### Using it from another machine
Browsers only allow microphone access on `localhost` or HTTPS. The simplest safe way to reach a bot running on a server is an
SSH tunnel, then open `http://localhost:7860/client/` on your own machine:
```bash
ssh -L 7860:127.0.0.1:7860 user@your-server
```
Do not bind the bot to a public address without authentication and rate limits; see `SECURITY.md`.

## 5. What a run writes
| file | content |
|---|---|
| `latency.jsonl` | one line per turn: time from the end of the caller's speech to the first reply audio |
| `calls.jsonl` | one summary line per finished call (outcome, order, reservations, transfers) |
| `runs/*.json` | transcripts and tool calls from `chat_sim.py` |

All three are git-ignored. Summarise latency with `python tools/latency_report.py`. Render `runs/` as readable sample calls
with `python tools/make_sample_calls.py > docs/examples/sample_calls.md`.

## Troubleshooting
| symptom | likely cause |
|---|---|
| `KeyError: 'DEEPGRAM_API_KEY'` (or another key) when a client connects | the variable is not exported in the shell that runs `bot.py` |
| page loads but no audio is sent | microphone permission denied, or the page is not on `localhost`/HTTPS |
| the bot answers, but only after several seconds | the LLM is slow (reasoning model, distant region); look at `latency.jsonl` and the log line `user-to-bot latency` |
| the bot asks for the name or pickup/delivery again | by design: `read_back` is refused until both are set |
| a scenario fails once and passes the next time | model variance; rerun with `--repeat 3` and look at the transcript in `runs/` |
| `ModuleNotFoundError: fastapi` | the `runner` extra is missing; reinstall from `requirements.txt` |
