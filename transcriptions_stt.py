"""Speech-to-text adapter for services with an OpenAI-style `POST {base_url}/audio/transcriptions`
(multipart form with `model` and `file`, JSON reply with the text).

Pipecat's segmenting base class cuts the caller's speech at the voice-activity boundaries and hands each segment to
`run_stt` as a WAV file. This adapter uploads it and pushes the text as a transcription. It sends only `model` and
`file`, exactly like the gateway's own curl example, and does not send a language: the model detects it.
Written for `qwen3-asr-flash` behind such a gateway; check the reply shape against your service.
"""
import json
from collections.abc import AsyncGenerator

import aiohttp
from loguru import logger

from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.utils.time import time_now_iso8601


def extract_text(payload) -> str | None:
    """Pull the transcript out of a JSON reply: `{"text": ...}`, or the same nested under `data` or `output`."""
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("text"), str):
        return payload["text"]
    for key in ("data", "output", "result"):
        inner = payload.get(key)
        if isinstance(inner, dict) and isinstance(inner.get("text"), str):
            return inner["text"]
    return None


class TranscriptionsSTTService(SegmentedSTTService):
    """Uploads each speech segment to an `/audio/transcriptions` endpoint and returns the transcript."""

    Settings = STTSettings
    _settings: Settings

    def __init__(self, *, api_key: str, base_url: str, model: str, timeout_secs: float = 30.0, **kwargs):
        settings = self.Settings(model=model, language=None)
        super().__init__(settings=settings, **kwargs)
        self._api_key = api_key
        self._url = base_url.rstrip("/") + "/audio/transcriptions"
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

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        form = aiohttp.FormData()
        form.add_field("model", self._settings.model)
        form.add_field("file", audio, filename="speech.wav", content_type="audio/wav")
        try:
            await self.start_processing_metrics()
            async with self._session.post(self._url, data=form,
                                          headers={"Authorization": f"Bearer {self._api_key}"}) as resp:
                body = await resp.text()
                if resp.status != 200:
                    logger.error(f"{self} transcription failed (status {resp.status})")
                    yield ErrorFrame(error=f"STT request failed (status {resp.status}): {body[:300]}")
                    return
            await self.stop_processing_metrics()
            try:
                payload = json.loads(body)
            except ValueError:
                payload = None
            text = extract_text(payload)
            if text is None:
                yield ErrorFrame(error=f"STT reply has no text field: {body[:300]}")
                return
            text = text.strip()
            if text:
                logger.debug(f"Transcription: [{text}]")
                yield TranscriptionFrame(text, self._user_id, time_now_iso8601(), result=payload)
            else:
                logger.warning("empty transcription")
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.error(f"{self} transcription error: {e}")
            yield ErrorFrame(error=f"STT request error: {e}")
