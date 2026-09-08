"""Rime TTS provider for LiveKit Agents 1.8.

Rime's ``/v1/rime-tts`` API returns a complete WAV response for one text input.
That maps to LiveKit's ``ChunkedStream`` interface (rather than
``SynthesizeStream``, which is for APIs that accept text incrementally).
"""

from __future__ import annotations

import asyncio

import aiohttp
from livekit.agents import (
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    DEFAULT_API_CONNECT_OPTIONS,
    tts,
    utils,
)


RIME_TTS_URL = "https://users.rime.ai/v1/rime-tts"
RIME_MODEL_ID = "mistv3"
RIME_SAMPLE_RATE = 24_000
RIME_NUM_CHANNELS = 1


class RimeTTS(tts.TTS):
    """One-shot Rime TTS implementation backed by the Rime WAV endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        speaker: str = "cove",
        base_url: str = RIME_TTS_URL,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=RIME_SAMPLE_RATE,
            num_channels=RIME_NUM_CHANNELS,
        )
        if not api_key:
            raise ValueError("A Rime API key is required")

        self._api_key = api_key
        self._speaker = speaker
        self._active_streams: set[RimeTTSChunkedStream] = set()
        # A trailing slash can be treated as a different route by some API proxies.
        self._base_url = (base_url or RIME_TTS_URL).rstrip("/")

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        """Synchronously create the one-shot synthesis stream required by LiveKit."""
        stream = RimeTTSChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
        )
        self._active_streams.add(stream)
        stream._synthesize_task.add_done_callback(lambda _: self._active_streams.discard(stream))
        return stream

    async def abort_all(self) -> None:
        """Immediately cancel active Rime requests when the caller barges in."""
        streams = list(self._active_streams)
        if streams:
            await asyncio.gather(*(stream.aclose() for stream in streams), return_exceptions=True)


class RimeTTSChunkedStream(tts.ChunkedStream):
    """Streams Rime's audio chunks incrementally to reduce perceived response latency."""

    def __init__(
        self,
        *,
        tts: RimeTTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: RimeTTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        headers = {
            "Accept": "audio/wav",
            "Authorization": f"Bearer {self._tts._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "text": self.input_text,
            "modelId": RIME_MODEL_ID,
            "speaker": self._tts._speaker,
            "lang": "en",
            "samplingRate": RIME_SAMPLE_RATE,
        }

        client_timeout = aiohttp.ClientTimeout(total=self._conn_options.timeout)
        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.post(
                    self._tts._base_url,
                    json=payload,
                    headers=headers,
                ) as response:
                    if response.status >= 400:
                        body = await response.text()
                        raise APIStatusError(
                            f"Rime TTS returned HTTP {response.status}",
                            status_code=response.status,
                            request_id=response.headers.get("x-request-id"),
                            body=body,
                            retryable=response.status >= 500,
                        )

                    request_id = response.headers.get("x-request-id") or utils.shortuuid()
                    segment_id = request_id

                    # Stream-initialization: Emit metadata and begin chunks immediately
                    output_emitter.initialize(
                        request_id=request_id,
                        sample_rate=RIME_SAMPLE_RATE,
                        num_channels=RIME_NUM_CHANNELS,
                        mime_type="audio/wav",
                        stream=True,
                    )
                    output_emitter.start_segment(segment_id=segment_id)

                    # Stream network chunks directly into the LiveKit audio pipeline
                    async for chunk in response.content.iter_chunked(4096):
                        if chunk:
                            output_emitter.push(chunk)

                    output_emitter.end_segment()
        except aiohttp.ServerTimeoutError as exc:
            raise APITimeoutError("Rime TTS request timed out") from exc
        except aiohttp.ClientError as exc:
            raise APIStatusError(
                f"Rime TTS request failed: {exc}", retryable=True
            ) from exc