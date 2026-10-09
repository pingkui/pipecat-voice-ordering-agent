#!/usr/bin/env python3
"""Offline tests for transcriptions_stt.py against a local fake server. No network, no key.
Run: python3 test_stt_adapter.py"""
import asyncio
import io
import json
import sys
import warnings
import wave

warnings.filterwarnings("ignore")
from aiohttp import web                                   # noqa: E402
from loguru import logger                                 # noqa: E402

from pipecat.frames.frames import ErrorFrame, TranscriptionFrame  # noqa: E402
from transcriptions_stt import TranscriptionsSTTService, extract_text  # noqa: E402

logger.remove()
results = []
seen = {}


def check(name, cond):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name)


def make_wav(seconds=0.5, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(rate * seconds))
    return buf.getvalue()


async def transcriptions(request):
    reader = await request.multipart()
    fields = {}
    async for part in reader:
        fields[part.name] = (part.filename, await part.read())
    seen["headers"], seen["fields"] = dict(request.headers), fields
    mode = fields["model"][1].decode()
    if mode == "http401":
        return web.json_response({"message": "bad key"}, status=401)
    if mode == "badjson":
        return web.Response(text="not json at all")
    if mode == "notext":
        return web.json_response({"result": "no text key here"})
    if mode == "empty":
        return web.json_response({"text": "   "})
    if mode == "nested":
        return web.json_response({"data": {"text": " two large pizzas "}})
    return web.json_response({"text": "  Two large margherita pizzas, please.  "})


async def run(svc, audio):
    return [f async for f in svc.run_stt(audio)]


async def main():
    app = web.Application()
    app.router.add_post("/v1/audio/transcriptions", transcriptions)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 8092).start()

    made = []

    def svc(model):
        x = TranscriptionsSTTService(api_key="test-key", base_url="http://127.0.0.1:8092/v1", model=model)
        made.append(x)
        return x

    wav = make_wav()
    s = svc("ok")
    frames = await run(s, wav)
    check("a transcript becomes one TranscriptionFrame with trimmed text",
          len(frames) == 1 and isinstance(frames[0], TranscriptionFrame)
          and frames[0].text == "Two large margherita pizzas, please.")
    check("the request carries bearer auth, only model and file, and the WAV bytes unchanged",
          seen["headers"].get("Authorization") == "Bearer test-key" and set(seen["fields"]) == {"model", "file"}
          and seen["fields"]["file"][1] == wav and seen["fields"]["file"][0] == "speech.wav")
    frames = await run(svc("nested"), wav)
    check("a transcript nested under data is found", len(frames) == 1 and frames[0].text == "two large pizzas")
    frames = await run(svc("http401"), wav)
    check("an HTTP error becomes an ErrorFrame with the status",
          len(frames) == 1 and isinstance(frames[0], ErrorFrame) and "401" in frames[0].error)
    frames = await run(svc("badjson"), wav)
    check("a reply that is not JSON becomes an ErrorFrame", len(frames) == 1 and isinstance(frames[0], ErrorFrame))
    frames = await run(svc("notext"), wav)
    check("a JSON reply without text becomes an ErrorFrame", len(frames) == 1 and isinstance(frames[0], ErrorFrame))
    frames = await run(svc("empty"), wav)
    check("an empty transcript produces no frame", frames == [])
    dead = TranscriptionsSTTService(api_key="k", base_url="http://127.0.0.1:1/v1", model="m")
    frames = await run(dead, wav)
    check("an unreachable server becomes an ErrorFrame, not an exception",
          len(frames) == 1 and isinstance(frames[0], ErrorFrame))
    check("extract_text handles odd payloads", extract_text(None) is None and extract_text([]) is None
          and extract_text({"text": 5}) is None and extract_text({"output": {"text": "x"}}) == "x")
    for x in made + [dead]:
        if x._session:
            await x._session.close()
    await runner.cleanup()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
