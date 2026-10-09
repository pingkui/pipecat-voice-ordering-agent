#!/usr/bin/env python3
"""Text -> speech -> text through the two adapters, to check the pair against the real services.

For each sentence: synthesise it with the TTS adapter, wrap the audio as WAV, send it to the STT adapter, and compare the
transcript with the original (word error rate on lower-cased words without punctuation). Reports the time each step took.
Note that synthetic speech is easier to recognise than a person on a noisy line, so this is a plumbing check, not an
accuracy benchmark.

  TTS_BASE_URL=.. TTS_API_KEY=.. TTS_MODEL=.. TTS_VOICE=.. STT_BASE_URL=.. STT_API_KEY=.. STT_MODEL=.. \\
      python tools/roundtrip_check.py [--runs N]
"""
import argparse
import asyncio
import io
import os
import re
import statistics
import sys
import time
import warnings
import wave

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from loguru import logger  # noqa: E402

from pipecat.frames.frames import ErrorFrame, TranscriptionFrame, TTSAudioRawFrame  # noqa: E402
from speech_sse_tts import SpeechSSETTSService  # noqa: E402
from transcriptions_stt import TranscriptionsSTTService  # noqa: E402

SENTENCES = [
    "I would like two large margherita pizzas for pickup.",
    "My name is Sam.",
    "Yes, that is correct.",
    "Actually, make that three pizzas, not two.",
    "Do you have anything without peanuts? I have an allergy.",
    "A table for four at seven in the evening, please.",
    "No onions on the burger.",
    "Can I speak to a person?",
]


def words(s):
    return re.sub(r"[^a-z0-9 ]", "", s.lower().replace("-", " ")).split()


def wer(ref, hyp):
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / max(1, len(r))


def to_wav(pcm, rate):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=len(SENTENCES))
    a = ap.parse_args()
    logger.remove()
    tts = SpeechSSETTSService(api_key=os.environ["TTS_API_KEY"], base_url=os.environ["TTS_BASE_URL"],
                              model=os.environ["TTS_MODEL"], voice=os.environ["TTS_VOICE"])
    stt = TranscriptionsSTTService(api_key=os.environ["STT_API_KEY"], base_url=os.environ["STT_BASE_URL"],
                                   model=os.environ["STT_MODEL"])
    tts_first, stt_times, errs = [], [], []
    for i in range(a.runs):
        text = SENTENCES[i % len(SENTENCES)]
        t0, first, pcm, rate = time.time(), None, b"", 24000
        async for f in tts.run_tts(text, "rt"):
            if isinstance(f, ErrorFrame):
                sys.exit(f"TTS error: {f.error}")
            if isinstance(f, TTSAudioRawFrame):
                first = first or (time.time() - t0)
                pcm += f.audio
                rate = f.sample_rate
        t1, heard = time.time(), None
        async for f in stt.run_stt(to_wav(pcm, rate)):
            if isinstance(f, ErrorFrame):
                sys.exit(f"STT error: {f.error}")
            if isinstance(f, TranscriptionFrame):
                heard = f.text
        dt = time.time() - t1
        e = wer(text, heard or "")
        tts_first.append(first)
        stt_times.append(dt)
        errs.append(e)
        print(f"{'OK ' if e == 0 else 'DIFF'} tts_first {first:.2f}s  stt {dt:.2f}s ({len(pcm) / 2 / rate:.1f}s audio)  "
              f"said: {text!r}\n      heard: {heard!r}")
    print(f"\nTTS first audio median {statistics.median(tts_first):.2f}s | STT median {statistics.median(stt_times):.2f}s, "
          f"max {max(stt_times):.2f}s | exact matches {sum(e == 0 for e in errs)}/{len(errs)}, "
          f"mean word error rate {sum(errs) / len(errs):.1%}")
    for s in (tts, stt):
        if s._session:
            await s._session.close()


asyncio.run(main())
