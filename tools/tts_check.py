#!/usr/bin/env python3
"""Calls the configured TTS endpoint through the adapter a few times and reports time to first audio, audio length and
the real-time factor. Optionally saves the last result as a WAV file so you can listen to it.

  TTS_BASE_URL=... TTS_API_KEY=... TTS_MODEL=... TTS_VOICE=... python tools/tts_check.py [--runs 5] [--wav out.wav]
"""
import argparse
import asyncio
import os
import statistics
import sys
import time
import warnings
import wave

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from loguru import logger  # noqa: E402

from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame  # noqa: E402
from speech_sse_tts import SpeechSSETTSService  # noqa: E402

SENTENCES = [
    "Thanks for calling Maple and Thyme. How can I help you today?",
    "Two large margherita pizzas and one caesar salad, for pickup, for Sam. The total is thirty nine dollars. Does that sound right?",
    "Sorry, we do not have sushi. Could I suggest the pad thai instead?",
]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--wav")
    a = ap.parse_args()
    logger.remove()
    svc = SpeechSSETTSService(api_key=os.environ["TTS_API_KEY"], base_url=os.environ["TTS_BASE_URL"],
                              model=os.environ["TTS_MODEL"], voice=os.environ["TTS_VOICE"])
    firsts, rtfs, last_pcm, rate = [], [], b"", 24000
    for i in range(a.runs):
        text = SENTENCES[i % len(SENTENCES)]
        t0, first, pcm = time.time(), None, b""
        async for f in svc.run_tts(text, "check"):
            if isinstance(f, ErrorFrame):
                sys.exit(f"error: {f.error}")
            if isinstance(f, TTSAudioRawFrame):
                if first is None:
                    first = time.time() - t0
                pcm += f.audio
                rate = f.sample_rate
        total = time.time() - t0
        dur = len(pcm) / 2 / rate
        firsts.append(first)
        rtfs.append(total / dur)
        last_pcm = pcm
        print(f"run {i + 1}: first audio {first:.2f}s, total {total:.2f}s, audio {dur:.2f}s, "
              f"text {len(text)} chars")
    print(f"first audio: median {statistics.median(firsts):.2f}s, max {max(firsts):.2f}s over {len(firsts)} runs; "
          f"synthesis time / audio length: median {statistics.median(rtfs):.2f}")
    if a.wav:
        with wave.open(a.wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(last_pcm)
        print("saved", a.wav)
    if svc._session:
        await svc._session.close()


asyncio.run(main())
