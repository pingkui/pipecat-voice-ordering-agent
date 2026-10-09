"""Text-to-speech adapter for services that expose an OpenAI-style `POST {base_url}/audio/speech` and, with
`"stream": true`, answer with server-sent events:

    data: {"type": "speech.audio.delta", "audio": "<base64 PCM>"}
    ...
    data: {"type": "speech.audio.done", "usage": {}}

The first delta starts with a WAV header (its length fields are placeholders); the rest is raw 16-bit mono PCM.
This was written against the `qwen3-tts-flash` model behind such a gateway; nothing in it is specific to one vendor,
but the event shape is: check it against your service before relying on it.
"""
import base64
import json
from collections.abc import AsyncGenerator

import aiohttp
from loguru import logger

from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService

DEFAULT_SAMPLE_RATE = 24000


class WavStreamStripper:
    """Removes the WAV header from the front of a streamed audio body, however the chunks are cut.

    Feed chunks in order. A stream that does not begin with RIFF is passed through untouched. For a WAV header the
    PCM starts right after the `data` chunk id and its 4-byte length, and the sample rate comes from the `fmt ` chunk.
    Until the whole header has arrived, `feed` returns no PCM.
    """

    MAX_HEADER = 256

    def __init__(self):
        self._buf = b""
        self._done = False
        self.sample_rate: int | None = None

    def feed(self, chunk: bytes) -> bytes:
        if self._done:
            return chunk
        self._buf += chunk
        if len(self._buf) < 4:
            return b""
        if self._buf[:4] != b"RIFF":
            self._done, out, self._buf = True, self._buf, b""
            return out
        if len(self._buf) >= 28 and self.sample_rate is None:
            self.sample_rate = int.from_bytes(self._buf[24:28], "little")
        pos = self._buf.find(b"data", 12, self.MAX_HEADER)
        if pos == -1 or len(self._buf) < pos + 8:
            if len(self._buf) > self.MAX_HEADER:        # not a header we understand: give up and pass it on
                self._done, out, self._buf = True, self._buf, b""
                return out
            return b""
        self._done, out, self._buf = True, self._buf[pos + 8:], b""
        return out


def parse_sse_data(line: str) -> dict | None:
    """Parse one SSE line; returns the JSON object for a `data:` line, else None. `[DONE]` is treated as no data."""
    line = line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        return json.loads(payload)
    except ValueError:
        return None


class SpeechSSETTSService(TTSService):
    """Streams speech from an `/audio/speech` endpoint that answers with the SSE events described above."""

    Settings = TTSSettings
    _settings: Settings

    def __init__(self, *, api_key: str, base_url: str, model: str, voice: str,
                 sample_rate: int | None = None, timeout_secs: float = 30.0, **kwargs):
        settings = self.Settings(model=model, voice=voice, language=None)
        super().__init__(sample_rate=sample_rate or DEFAULT_SAMPLE_RATE, push_start_frame=True,
                         push_stop_frames=True, settings=settings, **kwargs)
        self._api_key = api_key
        self._url = base_url.rstrip("/") + "/audio/speech"
        self._timeout = aiohttp.ClientTimeout(total=timeout_secs)
        self._session: aiohttp.ClientSession | None = None

    def can_generate_metrics(self) -> bool:
        return True

    async def stop(self, frame):
        await super().stop(frame)
        if self._session:
            await self._session.close()

    async def cancel(self, frame):
        await super().cancel(frame)
        if self._session:
            await self._session.close()

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        body = {"model": self._settings.model, "input": text, "voice": self._settings.voice, "stream": True}
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        try:
            async with self._session.post(self._url, json=body, headers=headers) as resp:
                if resp.status != 200:
                    detail = (await resp.text())[:300]
                    logger.error(f"{self} speech request failed (status {resp.status})")
                    yield ErrorFrame(error=f"TTS request failed (status {resp.status}): {detail}")
                    return
                await self.start_tts_usage_metrics(text)
                strip = WavStreamStripper()
                carry = b""                    # keeps PCM 16-bit aligned if a chunk ends mid-sample
                first = True
                src_rate = DEFAULT_SAMPLE_RATE  # the rate the service really sends; the output transport resamples
                async for raw in resp.content:
                    event = parse_sse_data(raw.decode("utf-8", errors="replace"))
                    if event is None:
                        continue
                    kind = str(event.get("type", ""))
                    if "error" in kind:
                        yield ErrorFrame(error=f"TTS stream error: {str(event)[:300]}")
                        return
                    if kind == "speech.audio.done":
                        break
                    audio_b64 = event.get("audio")
                    if not audio_b64:
                        continue
                    pcm = strip.feed(base64.b64decode(audio_b64))
                    if strip.sample_rate:
                        src_rate = strip.sample_rate
                    pcm = carry + pcm
                    carry = pcm[len(pcm) // 2 * 2:]
                    pcm = pcm[: len(pcm) // 2 * 2]
                    if not pcm:
                        continue
                    if first:
                        await self.stop_ttfb_metrics()
                        first = False
                    yield TTSAudioRawFrame(pcm, src_rate, 1, context_id=context_id)
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.error(f"{self} speech request error: {e}")
            yield ErrorFrame(error=f"TTS request error: {e}")
