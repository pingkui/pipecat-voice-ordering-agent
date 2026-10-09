#!/usr/bin/env python3
"""A synthetic caller for the whole voice pipeline.

The scripted scenarios from chat_sim.py are spoken by a second synthetic voice and fed into the real pipeline
(voice-activity detection, speech-to-text, LLM with tools, text-to-speech) at real-time pace, with silence in between like a
live microphone. For every turn it records what the speech-to-text heard and how long the agent took to start answering,
measured from the end of the caller's audio to the first audio of the reply. At the end the order state is checked as in
the text tests.

What this is and is not: it exercises everything except the browser, WebRTC and a real microphone. The caller's speech is
clean synthetic speech, which is easier to recognise than a person on a noisy line, so latency and accuracy here are a best
case. The reply audio is measured where it leaves the text-to-speech service, before any network to a listener.

  (same environment variables as bot.py, plus CALLER_VOICE) python tools/virtual_caller.py [--only NAME] [--repeat N]
"""
import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from loguru import logger  # noqa: E402

import bot as voice_bot  # noqa: E402
import chat_sim  # noqa: E402
import order_core as core  # noqa: E402
from pipecat.audio.utils import create_stream_resampler  # noqa: E402
from pipecat.audio.vad.silero import SileroVADAnalyzer  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    ErrorFrame,
    FunctionCallInProgressFrame,
    InputAudioRawFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.worker import PipelineParams, PipelineWorker  # noqa: E402
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402
from pipecat.processors.aggregators.llm_response_universal import (  # noqa: E402
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameProcessor  # noqa: E402
from pipecat.workers.runner import WorkerRunner  # noqa: E402

# Some gateways limit a key to a number of requests per minute (the one this was built against allows about 10 for
# text-to-speech: measured, the 11th request in a burst is refused with 429 for about 50 s). To measure the pipeline and not the
# quota, every TTS request is recorded, and before each turn the caller waits until the last minute has room for a whole
# turn (the caller's line plus the agent's sentences). The wait happens before the caller speaks, so it never counts as
# reply latency.
TTS_REQUESTS_PER_MINUTE = int(os.environ.get("TTS_REQUESTS_PER_MINUTE", "0"))     # 0 = no pacing
TTS_REQUESTS_PER_TURN = int(os.environ.get("TTS_REQUESTS_PER_TURN", "6"))
_tts_times = []


def gated(tts):
    """Wrap a TTS service so that every run_tts call is recorded in the rolling window."""
    original = tts.run_tts

    async def run_tts(text, context_id):
        _tts_times.append(time.time())
        async for f in original(text, context_id):
            yield f

    tts.run_tts = run_tts
    return tts


async def wait_for_capacity():
    if not TTS_REQUESTS_PER_MINUTE:
        return
    while True:
        now = time.time()
        _tts_times[:] = [t for t in _tts_times if now - t < 60]
        if len(_tts_times) + TTS_REQUESTS_PER_TURN <= TTS_REQUESTS_PER_MINUTE:
            return
        await asyncio.sleep(max(0.3, 60 - (now - _tts_times[0]) + 0.1))


RATE = 16000
FRAME_MS = 20
FRAME_BYTES = RATE * 2 * FRAME_MS // 1000


class Tap(FrameProcessor):
    """Records what passes through: heard transcripts, reply audio times and errors."""

    def __init__(self):
        super().__init__()
        self.heard, self.audio_times, self.errors, self.events = [], [], [], []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        now = time.time()
        if isinstance(frame, TranscriptionFrame):
            self.heard.append((now, frame.text))
            self.events.append((now, "transcript"))
        elif isinstance(frame, TTSAudioRawFrame):
            self.audio_times.append(now)
            if not self.events or self.events[-1][1] != "tts_audio":
                self.events.append((now, "tts_audio"))
        elif isinstance(frame, FunctionCallInProgressFrame):
            self.events.append((now, "tool_call"))
        elif isinstance(frame, LLMFullResponseStartFrame):
            self.events.append((now, "llm_start"))
        elif isinstance(frame, LLMTextFrame):
            if not self.events or self.events[-1][1] != "llm_text":
                self.events.append((now, "llm_text"))
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self.events.append((now, "vad_stop"))
        elif isinstance(frame, ErrorFrame):
            self.errors.append(frame.error)
        await self.push_frame(frame, direction)


class Mic:
    """Plays queued speech into the pipeline in real time and sends silence when nothing is queued."""

    def __init__(self, worker):
        self.worker, self.buf, self.stop = worker, b"", False
        self.speech_end = None          # wall-clock time when the last queued speech frame finished playing

    def say(self, pcm16k: bytes):
        pad = (-len(pcm16k)) % FRAME_BYTES
        self.speech_end = None
        self.buf += pcm16k + b"\x00" * pad

    async def run(self):
        nxt = time.monotonic()
        silence = b"\x00" * FRAME_BYTES
        while not self.stop:
            if self.buf:
                chunk, self.buf = self.buf[:FRAME_BYTES], self.buf[FRAME_BYTES:]
                if not self.buf:
                    self.speech_end = time.time() + FRAME_MS / 1000
            else:
                chunk = silence
            await self.worker.queue_frame(InputAudioRawFrame(audio=chunk, sample_rate=RATE, num_channels=1))
            nxt += FRAME_MS / 1000
            await asyncio.sleep(max(0.0, nxt - time.monotonic()))


def settled(context):
    msgs = context.get_messages()
    last = msgs[-1] if msgs else {}
    return last.get("role") == "assistant" and not last.get("tool_calls") and bool(last.get("content"))


async def synth(tts, text, resampler):
    pcm, rate = b"", 24000
    async for f in tts.run_tts(text, "caller"):
        if isinstance(f, ErrorFrame):
            raise RuntimeError(f"caller TTS failed: {f.error}")
        if isinstance(f, TTSAudioRawFrame):
            pcm += f.audio
            rate = f.sample_rate
    return await resampler.resample(pcm, rate, RATE)


async def run_scenario(sc, caller_tts):
    session = core.OrderSession(core.load_menu())
    stt, agent_tts = voice_bot.make_stt(), gated(voice_bot.make_tts())
    llm = voice_bot.make_llm(os.environ["LLM_BASE_URL"], os.environ["LLM_API_KEY"], os.environ["LLM_MODEL"], session.menu)
    context = LLMContext(messages=[], tools=voice_bot.build_tools(session))
    pair = LLMContextAggregatorPair(context, user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()))
    user_agg, assistant_agg = pair.user(), pair.assistant()

    @user_agg.event_handler("on_user_turn_stopped")
    async def _spoke(aggregator, strategy, message):
        session.note_customer_turn()

    heard_tap, out_tap = Tap(), Tap()
    worker = PipelineWorker(Pipeline([stt, heard_tap, user_agg, llm, agent_tts, out_tap, assistant_agg]),
                            params=PipelineParams(enable_metrics=False))
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())
    mic = Mic(worker)
    mic_task = asyncio.create_task(mic.run())
    resampler = create_stream_resampler()
    turns = []
    lines = list(sc["customer"])
    try:
        await asyncio.sleep(1.0)
        i = 0
        while i < len(lines):
            line = lines[i]
            n_audio, n_heard = len(out_tap.audio_times), len(heard_tap.heard)
            await wait_for_capacity()
            mic.say(await synth(caller_tts, line, resampler))
            deadline = time.time() + 90
            while time.time() < deadline:
                await asyncio.sleep(0.2)
                if (mic.speech_end and len(out_tap.audio_times) > n_audio and settled(context)
                        and time.time() - out_tap.audio_times[-1] > 1.5):
                    break
            else:
                turns.append({"said": line, "heard": None, "latency": None, "timeout": True})
                break
            first = next((t for t in out_tap.audio_times[n_audio:] if t >= mic.speech_end), None)
            heard = " ".join(t for ts, t in heard_tap.heard[n_heard:])
            ev = sorted(heard_tap.events + out_tap.events)
            marks = [(round(t - mic.speech_end, 2), name) for t, name in ev
                     if mic.speech_end - 0.5 <= t <= (first or mic.speech_end) + 0.01]
            turns.append({"said": line, "heard": heard, "latency": (first - mic.speech_end) if first else None,
                          "timeline": marks})
            i += 1
            if (i == len(lines) and sc.get("finish_with_yes") and not session.placed
                    and session.read_back_version == session.version and not session.confirmed):
                lines.append("Yes, that's correct.")
    finally:
        mic.stop = True
        await worker.cancel()
        await asyncio.gather(run_task, mic_task, return_exceptions=True)
    ok, why = sc["check"](session, [])
    return {"name": sc["name"], "ok": ok, "why": why, "turns": turns, "errors": out_tap.errors + heard_tap.errors,
            "summary": session.summary()}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args()
    logger.remove()
    logger.add(sys.stderr, level="ERROR")
    caller_tts = gated(voice_bot.SpeechSSETTSService(
        api_key=os.environ["TTS_API_KEY"], base_url=os.environ["TTS_BASE_URL"], model=os.environ["TTS_MODEL"],
        voice=os.environ.get("CALLER_VOICE", "Ethan")))
    out_dir = os.path.join(HERE, "runs")
    os.makedirs(out_dir, exist_ok=True)
    lats, passed, total = [], 0, 0
    for sc in chat_sim.SCENARIOS:
        if a.only and sc["name"] != a.only:
            continue
        for k in range(a.repeat):
            total += 1
            try:
                r = await run_scenario(sc, caller_tts)
            except Exception as e:
                r = {"name": sc["name"], "ok": False, "why": f"{type(e).__name__}: {e}", "turns": [], "errors": []}
            passed += r["ok"]
            sl = [t["latency"] for t in r["turns"] if t.get("latency") is not None]
            lats += sl
            print(f"{'PASS' if r['ok'] else 'FAIL'}  {sc['name']} #{k + 1}  turns={len(r['turns'])}"
                  + (f"  median reply latency {statistics.median(sl):.2f}s" if sl else "")
                  + ("" if r["ok"] else f"  [{r['why']}]"))
            for t in r["turns"]:
                lat = f"{t['latency']:.2f}s" if t.get("latency") is not None else "n/a"
                print(f"      said: {t['said']!r}\n      heard: {t.get('heard')!r}   reply after {lat}")
                if t.get("timeline"):
                    print("      timeline (s after the caller stopped): " + ", ".join(f"{n} {s:+.2f}" for s, n in t["timeline"]))
            if r["errors"]:
                print("      errors:", r["errors"][:3])
            with open(os.path.join(out_dir, f"voice-{time.strftime('%Y%m%d-%H%M%S')}-{sc['name']}-{k + 1}.json"), "w") as f:
                json.dump(r, f, indent=1, ensure_ascii=False)
    if caller_tts._session:
        await caller_tts._session.close()
    print(f"\n{passed}/{total} scenarios passed through the full voice pipeline (synthetic caller)")
    if lats:
        s = sorted(lats)
        p = lambda q: s[max(1, -(-int(q * len(s)) // 100)) - 1] if len(s) > 1 else s[0]
        print(f"reply latency (end of caller audio to first reply audio) over {len(s)} turns: "
              f"median {statistics.median(s):.2f}s, p90 {p(90):.2f}s, max {s[-1]:.2f}s")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
