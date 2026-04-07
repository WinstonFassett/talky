"""Audio cue generation for talky.

Ticket b3c4 — three ascending / three descending beeps that play when the
mic opens and closes. Lives here (not in voice_daemon.py) so both the
voice daemon (local audio path) and the browser convo pipeline (pipecat
path) can produce identical cues from the same generator.

Design choices:
- **Three discrete beeps, not a single sweep.** The user explicitly prefers
  three short tones ("boop, boop, boop") over a continuous sweep, which
  sounded like a "reverse raindrop" and felt goofy.
- **Ascending for "mic opening", descending for "mic closing".** Matches
  the common mental model: pitch goes up when something activates,
  down when it deactivates.
- **Short beeps, short gaps.** Total cue is ~240 ms so it doesn't bleed
  into the start of the user's speech.
- **Envelope per-beep.** Each beep gets its own fade-in/fade-out so the
  edges don't click.
- **16-bit mono PCM at 16 kHz.** Compatible with pipecat's default
  OutputAudioRawFrame format and the daemon's PyAudio playback.
- **Stdlib only** (math + struct). No numpy dependency.

The public API is `start_cue_pcm(sample_rate)` and `stop_cue_pcm(sample_rate)`,
both returning `bytes`. Callers inject the bytes however their audio path
expects: PyAudio write for the daemon, `OutputAudioRawFrame` queueing for
pipecat.
"""

from __future__ import annotations

import math
import struct

# Default sample rate matches the daemon's existing tone cache. Pipecat's
# OutputAudioRawFrame will accept any rate as long as we tell it which.
DEFAULT_SAMPLE_RATE = 16000

# Beep profile. Three discrete tones, ascending for "start", descending
# for "stop". Tuned to feel snappy but not jarring.
_BEEP_DURATION_S = 0.06        # 60 ms per beep
_BEEP_GAP_S = 0.025            # 25 ms silence between beeps
_BEEP_AMPLITUDE = 10000        # 16-bit PCM amplitude (~30% of full scale)
_BEEP_FADE_FRACTION = 0.25     # first/last 25% of each beep is the fade envelope

_ASCENDING_FREQS = (600.0, 800.0, 1000.0)
_DESCENDING_FREQS = (1000.0, 800.0, 600.0)

# Cache by sample rate — most callers will only ever use one rate.
_CACHE: dict[tuple[str, int], bytes] = {}


def _generate_beep(freq_hz: float, duration_s: float, sample_rate: int) -> bytes:
    """Single sine beep with a fade envelope. Returns 16-bit little-endian PCM."""
    n_samples = int(sample_rate * duration_s)
    fade_samples = max(1, int(n_samples * _BEEP_FADE_FRACTION))
    out = bytearray()
    for i in range(n_samples):
        t = i / sample_rate
        if i < fade_samples:
            envelope = i / fade_samples
        elif i > n_samples - fade_samples:
            envelope = max(0.0, (n_samples - i) / fade_samples)
        else:
            envelope = 1.0
        value = int(_BEEP_AMPLITUDE * envelope * math.sin(2.0 * math.pi * freq_hz * t))
        # Clamp for safety even though envelope*amplitude can't exceed int16 range here.
        if value > 32767:
            value = 32767
        elif value < -32768:
            value = -32768
        out.extend(struct.pack("<h", value))
    return bytes(out)


def _silence(duration_s: float, sample_rate: int) -> bytes:
    """`duration_s` of silence as 16-bit PCM zeroes."""
    n_samples = int(sample_rate * duration_s)
    return b"\x00\x00" * n_samples


def _build_cue(freqs: tuple[float, ...], sample_rate: int) -> bytes:
    """Concatenate beeps with gaps into a single PCM buffer."""
    gap_pcm = _silence(_BEEP_GAP_S, sample_rate)
    parts: list[bytes] = []
    for i, f in enumerate(freqs):
        if i > 0:
            parts.append(gap_pcm)
        parts.append(_generate_beep(f, _BEEP_DURATION_S, sample_rate))
    return b"".join(parts)


def start_cue_pcm(sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
    """PCM bytes for the 'mic opening' cue — three ascending beeps."""
    key = ("start", sample_rate)
    if key not in _CACHE:
        _CACHE[key] = _build_cue(_ASCENDING_FREQS, sample_rate)
    return _CACHE[key]


def stop_cue_pcm(sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
    """PCM bytes for the 'mic closing' cue — three descending beeps."""
    key = ("stop", sample_rate)
    if key not in _CACHE:
        _CACHE[key] = _build_cue(_DESCENDING_FREQS, sample_rate)
    return _CACHE[key]


def cue_duration_s() -> float:
    """Total wall-clock duration of a cue, for timing/alignment.

    Three beeps + two gaps. Useful for callers that want to wait out the
    cue before starting/finishing the listen window.
    """
    return 3 * _BEEP_DURATION_S + 2 * _BEEP_GAP_S
