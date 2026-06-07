"""Microsoft Edge "Read Aloud" TTS as a Pipecat TTSService.

This talks directly to the same free, no-API-key websocket endpoint that the
Edge browser's Read Aloud feature uses (Azure Speech under the hood). It is the
default voice in Hermes Desktop and sounds notably better than Kokoro.

The free consumer endpoint only serves compressed audio — MP3 and webm-opus.
It rejects every PCM/RIFF format with a 1007 "Unsupported Edge output format"
(verified empirically; the PCM entries in MsEdgeTTS's enum are Azure
Cognitive-Services formats the *paid* endpoint accepts, not this one). Pipecat
wants raw PCM int16, so a decode step is unavoidable here. We request
`audio-24khz-48kbitrate-mono-mp3` and decode it to 24kHz mono s16le with
miniaudio (a pure-wheel decoder, no ffmpeg system dependency).

We still own the websocket rather than wrapping `rany2/edge-tts`: it gives us
direct control of streaming, voice, and prosody with no library lock-in, and
the protocol is small. The one library-shaped concern it leaves us holding is
the rolling handshake below.

The endpoint is undocumented and mildly adversarial: it gates connections with
a rolling `Sec-MS-GEC` token (SHA-256 over a 5-minute-rounded Windows-FILETIME
tick count concatenated with a fixed trusted-client token) and an Edge-matching
User-Agent. When Microsoft rotates the handshake, `compute_sec_ms_gec` /
`SEC_MS_GEC_VERSION` below are the things to re-sync. Reference implementation
to crib from when it breaks:
  https://github.com/rany2/edge-tts/blob/master/src/edge_tts/communicate.py
  https://github.com/Migushthe2nd/MsEdgeTTS/blob/master/src/MsEdgeTTS.ts
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from typing import AsyncGenerator, Optional

import miniaudio
import websockets
from loguru import logger
from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.services.tts_service import TTSService
from pipecat.utils.tracing.service_decorators import traced_tts

# Edge Read Aloud constants (stable across years; the GEC handshake is the
# moving part, not these).
TRUSTED_CLIENT_TOKEN = "6A5AA1D4EAFF4E9FB37E23D68491D6F4"
WSS_URL = (
    "wss://speech.platform.bing.com/consumer/speech/synthesize/"
    "readaloud/edge/v1"
)
# Tied to an Edge/Chrome build number; bump alongside the User-Agent if the
# endpoint starts rejecting the handshake.
SEC_MS_GEC_VERSION = "1-143.0.3650.96"
EDGE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
)
EDGE_ORIGIN = "chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold"

# The only formats the free endpoint serves are MP3 and webm-opus; PCM/RIFF are
# rejected. MP3 at 24kHz decodes cleanly to the 24kHz mono PCM Pipecat wants.
OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"
SAMPLE_RATE = 24000

_JSON_XML_DELIM = "\r\n\r\n"
_AUDIO_DELIM = b"Path:audio\r\n"


def compute_sec_ms_gec() -> str:
    """The rolling anti-abuse token the endpoint requires.

    SHA-256, uppercased hex, of: (Windows FILETIME ticks rounded down to the
    nearest 5 minutes) concatenated with the trusted-client token. The 5-minute
    rounding is why the value is stable within a window but changes over time.
    Keep in sync with rany2/edge-tts `DRM.generate_sec_ms_gec` if MS changes it.
    """
    # Unix epoch -> Windows epoch offset is 11644473600 seconds.
    ticks = int(time.time()) + 11644473600
    ticks -= ticks % 300  # round down to 5-minute window
    windows_ticks = ticks * 10_000_000  # seconds -> 100ns FILETIME ticks
    payload = f"{windows_ticks}{TRUSTED_CLIENT_TOKEN}".encode("ascii")
    return hashlib.sha256(payload).hexdigest().upper()


def _synth_url() -> str:
    return (
        f"{WSS_URL}?TrustedClientToken={TRUSTED_CLIENT_TOKEN}"
        f"&Sec-MS-GEC={compute_sec_ms_gec()}"
        f"&Sec-MS-GEC-Version={SEC_MS_GEC_VERSION}"
        f"&ConnectionId={uuid.uuid4().hex}"
    )


class EdgeTTSService(TTSService):
    """Free Microsoft Edge Read Aloud TTS. No API key, native PCM streaming."""

    def __init__(
        self,
        *,
        voice_id: str = "en-US-AndrewMultilingualNeural",
        rate: str = "+0%",
        pitch: str = "+0Hz",
        volume: str = "+0%",
        sample_rate: int = SAMPLE_RATE,
        **kwargs,
    ) -> None:
        super().__init__(sample_rate=sample_rate, **kwargs)
        self._voice = voice_id
        self._rate = rate
        self._pitch = pitch
        self._volume = volume

    def can_generate_metrics(self) -> bool:
        return True

    def set_voice(self, voice: str) -> None:
        self._voice = voice

    def _ssml(self, text: str) -> str:
        # Locale is inferred from the voice short name (e.g. "en-US-...").
        locale = "-".join(self._voice.split("-", 2)[:2]) or "en-US"
        return (
            '<speak version="1.0" '
            'xmlns="http://www.w3.org/2001/10/synthesis" '
            'xmlns:mstts="https://www.w3.org/2001/mstts" '
            f'xml:lang="{locale}">'
            f'<voice name="{self._voice}">'
            f'<prosody pitch="{self._pitch}" rate="{self._rate}" '
            f'volume="{self._volume}">{text}</prosody>'
            "</voice></speak>"
        )

    def _config_message(self) -> str:
        config = {
            "context": {
                "synthesis": {
                    "audio": {
                        "metadataoptions": {
                            "sentenceBoundaryEnabled": "false",
                            "wordBoundaryEnabled": "false",
                        },
                        "outputFormat": OUTPUT_FORMAT,
                    }
                }
            }
        }
        return (
            "Content-Type:application/json; charset=utf-8\r\n"
            f"Path:speech.config{_JSON_XML_DELIM}{json.dumps(config)}"
        )

    def _ssml_message(self, request_id: str, text: str) -> str:
        return (
            f"X-RequestId:{request_id}\r\n"
            "Content-Type:application/ssml+xml\r\n"
            f"Path:ssml{_JSON_XML_DELIM}{self._ssml(text)}"
        )

    @traced_tts
    async def run_tts(
        self, text: str, context_id: str
    ) -> AsyncGenerator[Frame, None]:
        logger.debug(f"{self}: Generating Edge TTS [{text}]")
        request_id = uuid.uuid4().hex
        ws: Optional[websockets.ClientConnection] = None
        started = False
        mp3 = bytearray()
        try:
            await self.start_ttfb_metrics()
            ws = await websockets.connect(
                _synth_url(),
                additional_headers={
                    "User-Agent": EDGE_UA,
                    "Origin": EDGE_ORIGIN,
                },
                max_size=None,
            )
            await ws.send(self._config_message())
            await ws.send(self._ssml_message(request_id, text))
            await self.start_tts_usage_metrics(text)

            # Edge streams compressed MP3 frames; accumulate until turn.end. MP3
            # frame boundaries don't align to websocket messages, so we decode
            # the whole utterance once it's complete rather than per-chunk.
            async for message in ws:
                if isinstance(message, str):
                    # Text control frames: turn.start / response / turn.end.
                    if "Path:turn.end" in message:
                        break
                    continue
                # Binary audio frame: text header, then _AUDIO_DELIM, then MP3.
                delim = message.find(_AUDIO_DELIM)
                if delim == -1:
                    continue
                mp3 += message[delim + len(_AUDIO_DELIM):]

            if not mp3:
                yield ErrorFrame(error="Edge TTS returned no audio")
                return

            # Decode MP3 -> 24kHz mono s16le PCM (off the event loop). Edge always
            # emits 24kHz here, so decode to the fixed SAMPLE_RATE rather than
            # self.sample_rate (which the pipeline only sets at start()).
            decoded = await asyncio.to_thread(
                miniaudio.decode,
                bytes(mp3),
                miniaudio.SampleFormat.SIGNED16,
                1,
                SAMPLE_RATE,
            )
            pcm = decoded.samples.tobytes()

            await self.stop_ttfb_metrics()
            yield TTSStartedFrame(context_id=context_id)
            started = True
            # Chunk into ~20ms frames so downstream pacing/interruption works.
            chunk = int(SAMPLE_RATE * 2 * 0.02)  # 2 bytes/sample, 20ms
            for i in range(0, len(pcm), chunk):
                yield TTSAudioRawFrame(pcm[i:i + chunk], SAMPLE_RATE, 1)
        except Exception as e:
            logger.error(f"{self}: Edge TTS error: {e}")
            yield ErrorFrame(error=f"Edge TTS error: {e}")
        finally:
            if ws is not None:
                await ws.close()
            if started:
                yield TTSStoppedFrame(context_id=context_id)
