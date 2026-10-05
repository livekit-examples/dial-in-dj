"""Audio plumbing between Lyria RealTime and LiveKit.

Lyria emits raw 16-bit PCM, 48 kHz, interleaved stereo. LiveKit's
BackgroundAudioPlayer mixes at 48 kHz mono, and the phone leg is mono anyway,
so everything here works in 48 kHz mono int16.
"""

from __future__ import annotations

import numpy as np
from livekit import rtc

SAMPLE_RATE = 48000
LYRIA_CHANNELS = 2
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000  # 960


def stereo_to_mono(pcm: bytes) -> np.ndarray:
    """Downmix interleaved int16 stereo bytes to a mono int16 array."""
    samples = np.frombuffer(pcm, dtype=np.int16)
    if samples.size % LYRIA_CHANNELS:
        samples = samples[: samples.size - samples.size % LYRIA_CHANNELS]
    stereo = samples.reshape(-1, LYRIA_CHANNELS).astype(np.int32)
    return (stereo.sum(axis=1) // LYRIA_CHANNELS).astype(np.int16)


class PcmBuffer:
    """FIFO of mono int16 samples with a cap to bound reprompt latency."""

    def __init__(self, max_seconds: float = 8.0) -> None:
        self._chunks: list[np.ndarray] = []
        self._size = 0
        self._max = int(max_seconds * SAMPLE_RATE)
        self.dropped = 0

    def __len__(self) -> int:
        return self._size

    @property
    def seconds(self) -> float:
        return self._size / SAMPLE_RATE

    def push(self, samples: np.ndarray) -> None:
        if samples.size == 0:
            return
        self._chunks.append(samples)
        self._size += samples.size
        overflow = self._size - self._max
        if overflow > 0:
            self.dropped += overflow
            self._discard(overflow)

    def pop(self, n: int) -> np.ndarray | None:
        """Return exactly n samples, or None if not enough are buffered."""
        if self._size < n:
            return None
        out = np.empty(n, dtype=np.int16)
        filled = 0
        while filled < n:
            head = self._chunks[0]
            take = min(n - filled, head.size)
            out[filled : filled + take] = head[:take]
            filled += take
            if take == head.size:
                self._chunks.pop(0)
            else:
                self._chunks[0] = head[take:]
        self._size -= n
        return out

    def keep_last(self, seconds: float) -> None:
        """Drop all but the newest `seconds` of audio (faster reprompt response)."""
        excess = self._size - int(seconds * SAMPLE_RATE)
        if excess > 0:
            self._discard(excess)

    def clear(self) -> None:
        self._chunks.clear()
        self._size = 0

    def _discard(self, n: int) -> None:
        while n > 0 and self._chunks:
            head = self._chunks[0]
            if head.size <= n:
                self._chunks.pop(0)
                self._size -= head.size
                n -= head.size
            else:
                self._chunks[0] = head[n:]
                self._size -= n
                n = 0


class Ducker:
    """Smoothed music gain. Set `target`; `apply` ramps toward it per frame."""

    def __init__(self, gain: float = 1.0, ramp_seconds: float = 0.25) -> None:
        self.target = gain
        self.current = gain
        # max gain change per sample so a full 0->1 swing takes ramp_seconds
        self._step = 1.0 / max(1, int(ramp_seconds * SAMPLE_RATE))

    def apply(self, samples: np.ndarray) -> np.ndarray:
        n = samples.size
        delta = self.target - self.current
        max_delta = self._step * n
        end = (
            self.target
            if abs(delta) <= max_delta
            else self.current + np.sign(delta) * max_delta
        )
        if self.current == end == 1.0:
            return samples
        gain = np.linspace(self.current, end, n, endpoint=False, dtype=np.float32)
        self.current = float(end)
        out = samples.astype(np.float32) * gain
        return np.clip(out, -32768, 32767).astype(np.int16)


def to_frame(samples: np.ndarray) -> rtc.AudioFrame:
    return rtc.AudioFrame(
        data=samples.tobytes(),
        sample_rate=SAMPLE_RATE,
        num_channels=1,
        samples_per_channel=samples.size,
    )


def silence(n: int = SAMPLES_PER_FRAME) -> np.ndarray:
    return np.zeros(n, dtype=np.int16)
