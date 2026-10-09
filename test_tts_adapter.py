#!/usr/bin/env python3
"""Offline tests for speech_sse_tts.py against a local fake server that speaks the same SSE format.
No network, no key. Run: python3 test_tts_adapter.py"""
import asyncio
import base64
import json
import struct
import sys
import warnings

warnings.filterwarnings("ignore")
from aiohttp import web                                   # noqa: E402
from loguru import logger                                 # noqa: E402

from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame  # noqa: E402
from speech_sse_tts import SpeechSSETTSService, WavStreamStripper, parse_sse_data  # noqa: E402

logger.remove()
results = []


def check(name, cond):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name)


def wav_header(rate=24000):
    return (b"RIFF" + struct.pack("<I", 0x7FFFFFFF) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", 0x7FFFFFFF))


def sse(obj):
    return ("data: " + json.dumps(obj) + "\n\n").encode()


PCM = bytes(range(256)) * 20                               # 5120 bytes of recognisable data
state = {"seen": None, "limited": 0, "calls": 0}


async def speech(request):
    body = await request.json()
    state["seen"] = (dict(request.headers), body)
    mode = body["input"]
    if mode.startswith("limited"):
        state["calls"] += 1
        if state["calls"] <= int(mode[len("limited"):] or 0):
            return web.Response(status=429, text="Too many requests")
        mode = "normal"
    if mode == "http500":
        return web.Response(status=500, text="boom")
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await resp.prepare(request)
    if mode == "error_event":
        await resp.write(sse({"type": "error", "message": "voice not found"}))
        return resp
    if mode == "odd_chunks":                               # chunk boundaries fall in the middle of a 16-bit sample
        pieces = [wav_header() + PCM[:101], PCM[101:1001], PCM[1001:]]
    elif mode == "split_header":                           # the WAV header itself is cut in two
        h = wav_header()
        pieces = [h[:20], h[20:] + PCM]
    else:
        pieces = [wav_header() + PCM[:2000], PCM[2000:4000], PCM[4000:]]
    for p in pieces:
        await resp.write(sse({"type": "speech.audio.delta", "audio": base64.b64encode(p).decode()}))
    await resp.write(sse({"type": "speech.audio.done", "usage": {}}))
    return resp


async def collect(svc, text):
    return [f async for f in svc.run_tts(text, "ctx-1")]


async def main():
    app = web.Application()
    app.router.add_post("/v1/audio/speech", speech)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8091)
    await site.start()
    svc = SpeechSSETTSService(api_key="test-key", base_url="http://127.0.0.1:8091/v1", model="m", voice="v")

    frames = await collect(svc, "normal")
    audio = [f for f in frames if isinstance(f, TTSAudioRawFrame)]
    check("normal stream: audio frames are produced", len(audio) == 3)
    check("normal stream: the WAV header is stripped, PCM is intact", b"".join(f.audio for f in audio) == PCM)
    check("frames are 24 kHz mono and carry the context id",
          all(f.sample_rate == 24000 and f.num_channels == 1 and f.context_id == "ctx-1" for f in audio))
    headers, body = state["seen"]
    check("request has bearer auth, model, voice, text and stream=true",
          headers.get("Authorization") == "Bearer test-key"
          and body == {"model": "m", "input": "normal", "voice": "v", "stream": True})

    frames = await collect(svc, "odd_chunks")
    audio = [f for f in frames if isinstance(f, TTSAudioRawFrame)]
    check("chunks ending mid-sample keep the PCM aligned and lose nothing",
          all(len(f.audio) % 2 == 0 for f in audio) and b"".join(f.audio for f in audio) == PCM[: len(PCM) // 2 * 2])

    frames = await collect(svc, "split_header")
    audio = [f for f in frames if isinstance(f, TTSAudioRawFrame)]
    check("a WAV header split across two chunks is handled", b"".join(f.audio for f in audio) == PCM)

    frames = await collect(svc, "http500")
    check("an HTTP error becomes an ErrorFrame and no audio",
          len(frames) == 1 and isinstance(frames[0], ErrorFrame) and "500" in frames[0].error)

    frames = await collect(svc, "error_event")
    check("an error event in the stream becomes an ErrorFrame",
          any(isinstance(f, ErrorFrame) for f in frames) and not any(isinstance(f, TTSAudioRawFrame) for f in frames))

    import speech_sse_tts
    speech_sse_tts.RETRY_DELAYS = (0.01, 0.01)               # keep the test quick
    state["calls"] = 0
    frames = await collect(svc, "limited2")
    check("two 429 answers are retried and the third attempt succeeds",
          state["calls"] == 3 and b"".join(f.audio for f in frames if isinstance(f, TTSAudioRawFrame)) == PCM)
    state["calls"] = 0
    frames = await collect(svc, "limited9")
    check("a service that keeps answering 429 ends in an ErrorFrame after three attempts",
          state["calls"] == 3 and len(frames) == 1 and isinstance(frames[0], ErrorFrame) and "429" in frames[0].error)

    dead = SpeechSSETTSService(api_key="k", base_url="http://127.0.0.1:1/v1", model="m", voice="v")
    frames = await collect(dead, "x")
    check("an unreachable server becomes an ErrorFrame, not an exception",
          len(frames) == 1 and isinstance(frames[0], ErrorFrame))

    w = WavStreamStripper()
    out = w.feed(wav_header(16000) + b"\x01\x02")
    check("header helper reads the sample rate and returns the PCM", w.sample_rate == 16000 and out == b"\x01\x02")
    w = WavStreamStripper()
    check("a stream without a header passes through unchanged", w.feed(b"\x01\x02\x03\x04") == b"\x01\x02\x03\x04"
          and w.feed(b"\x05\x06") == b"\x05\x06" and w.sample_rate is None)
    w = WavStreamStripper()
    h = wav_header()
    out = b"".join(w.feed(h[i:i + 5]) for i in range(0, len(h), 5)) + w.feed(b"\x09\x08")
    check("a header delivered five bytes at a time is still removed", out == b"\x09\x08" and w.sample_rate == 24000)
    check("SSE helper parses data lines and ignores the rest",
          parse_sse_data('data: {"a": 1}') == {"a": 1} and parse_sse_data("data: [DONE]") is None
          and parse_sse_data(": comment") is None and parse_sse_data("data: not json") is None)

    for s in (svc, dead):
        if s._session:
            await s._session.close()
    await runner.cleanup()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
