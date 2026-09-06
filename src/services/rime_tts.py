"""Rime TTS provider for LiveKit Agents 1.8.

Rime's ``/v1/rime-tts`` API returns a complete WAV response for one text input.
That maps to LiveKit's ``ChunkedStream`` interface (rather than
``SynthesizeStream``, which is for APIs that accept text incrementally).
"""

from __future__ import annotations

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
        # A trailing slash can be treated as a different route by some API proxies.
        self._base_url = (base_url or RIME_TTS_URL).rstrip("/")

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        """Synchronously create the one-shot synthesis stream required by LiveKit."""
        return RimeTTSChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
        )

    async def _request_audio(
        self,
        text: str,
        *,
        timeout: float,
    ) -> tuple[bytes, str]:
        """Request one WAV payload and return it with Rime's request id, if supplied."""
        headers = {
            "Accept": "audio/wav",
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        # Keep these field names and values aligned with the verified Rime request.
        payload = {
            "text": text,
            "modelId": RIME_MODEL_ID,
            "speaker": self._speaker,
            "lang": "en",
            "samplingRate": RIME_SAMPLE_RATE,
        }

        client_timeout = aiohttp.ClientTimeout(total=timeout)
        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.post(
                    self._base_url,
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

                    audio = await response.read()
                    if not audio:
                        raise APIStatusError(
                            "Rime TTS returned an empty audio response",
                            status_code=response.status,
                            request_id=response.headers.get("x-request-id"),
                            retryable=True,
                        )

                    return audio, response.headers.get("x-request-id", "")
        except aiohttp.ServerTimeoutError as exc:
            raise APITimeoutError("Rime TTS request timed out") from exc
        except aiohttp.ClientError as exc:
            raise APIStatusError(
                f"Rime TTS request failed: {exc}", retryable=True
            ) from exc


class RimeTTSChunkedStream(tts.ChunkedStream):
    """Feeds Rime's complete WAV response through LiveKit's AudioEmitter."""

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
        audio, provider_request_id = await self._tts._request_audio(
            self.input_text,
            timeout=self._conn_options.timeout,
        )

        # initialize() is mandatory. AudioEmitter owns decoding WAV bytes into
        # SynthesizedAudio events, so never push AudioFrame/SynthesizedAudio here.
        request_id = provider_request_id or utils.shortuuid()
        segment_id = provider_request_id or utils.shortuuid()
        output_emitter.initialize(
            request_id=request_id,
            sample_rate=RIME_SAMPLE_RATE,
            num_channels=RIME_NUM_CHANNELS,
            mime_type="audio/wav",
            stream=True,
        )
        output_emitter.start_segment(segment_id=segment_id)
        output_emitter.push(audio)
        output_emitter.end_segment()
