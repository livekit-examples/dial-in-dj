"""Lyria RealTime session wrapper: prompt state, crossfades, reconnects, frames.

See docs/LYRIA_NOTES.md for the API facts this relies on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from google import genai
from google.genai import types
from livekit import rtc

from .audio import (
    SAMPLES_PER_FRAME,
    Ducker,
    PcmBuffer,
    silence,
    stereo_to_mono,
    to_frame,
)

logger = logging.getLogger("dj.lyria")

MODEL = os.getenv("LYRIA_MODEL", "models/lyria-realtime-exp")
# Docs page says v1beta, the cookbook says v1alpha. Settle with scripts/lyria_smoke.py.
API_VERSION = os.getenv("LYRIA_API_VERSION", "v1alpha")
# Sessions are reportedly capped at ~10 min, so roll over before that.
SESSION_MAX_S = float(os.getenv("LYRIA_SESSION_MAX_S", "540"))
PREROLL_S = float(os.getenv("LYRIA_PREROLL_S", "1.0"))
MAX_BUFFER_S = float(os.getenv("LYRIA_MAX_BUFFER_S", "6.0"))
# On a new vibe, drop queued audio beyond this so the change is heard sooner.
# Lyria delivers ~2 s chunks at about real time, so going much below 2 s risks
# an underrun (a short silence) while the next chunk arrives.
# Every call the agent makes to Lyria is logged as a structured "lyria_send"
# record (visible in `lk agent logs`). Set LYRIA_LOG_FILE to also append JSONL.
LYRIA_LOG_FILE = os.getenv("LYRIA_LOG_FILE")
REPROMPT_KEEP_S = float(os.getenv("LYRIA_REPROMPT_KEEP_S", "2.0"))
# Higher follows prompts more closely (Lyria default 4.0, max 6.0).
GUIDANCE = float(os.getenv("LYRIA_GUIDANCE", "5.0"))
# Give up (and tell the listener) after this many failed connects in a row
# without ever getting a session, e.g. quota exhausted or the model unavailable.
MAX_CONNECT_FAILURES = int(os.getenv("LYRIA_MAX_CONNECT_FAILURES", "3"))

# Lyria drifts to solo piano when prompts are vague or moods dominate, so every
# mix carries a small negative-weight "piano" prompt unless the caller asked for
# piano or keys. Negative weights are allowed (verified 2026-09-30: audio keeps
# flowing, nothing filtered). Set LYRIA_AVOID="" to disable.
AVOID = [t.strip() for t in os.getenv("LYRIA_AVOID", "piano").split(",") if t.strip()]
AVOID_SHARE = float(os.getenv("LYRIA_AVOID_SHARE", "0.3"))
_KEYS_WORDS = ("piano", "keys", "keyboard", "rhodes", "harpsichord", "clavichord")


def with_avoid(prompts: dict[str, float]) -> dict[str, float]:
    """Add negative-weight prompts for AVOID tags the caller didn't ask for."""
    positive = sum(w for w in prompts.values() if w > 0)
    if positive <= 0:
        return dict(prompts)
    wanted = " ".join(prompts).lower()
    out = dict(prompts)
    for tag in AVOID:
        words = _KEYS_WORDS if tag == "piano" else (tag.lower(),)
        if not any(w in wanted for w in words):
            out[tag] = -round(AVOID_SHARE * positive, 2)
    return out


def make_client(api_key: str | None = None) -> genai.Client | None:
    key = api_key or os.getenv("GOOGLE_API_KEY")
    if not key:
        return None
    return genai.Client(api_key=key, http_options={"api_version": API_VERSION})


# Changing these only takes effect after reset_context().
_RESET_KEYS = {"bpm", "scale"}


class LyriaDJ:
    """Owns one long-lived Lyria session and exposes it as a LiveKit frame stream."""

    def __init__(
        self,
        prompts: dict[str, float] | None = None,
        *,
        api_key: str | None = None,
        client: genai.Client | None = None,
        on_filtered: Callable[[str, str], None] | None = None,
    ) -> None:
        self.prompts: dict[str, float] = dict(prompts or {"deep house": 1.0})
        self.config: dict[str, Any] = {"guidance": GUIDANCE}
        self.playing = True
        self.ducker = Ducker()
        self.last_filtered: tuple[str, str] | None = None
        # Called once per rejected tag with (text, reason). Assignable after init.
        self.on_filtered = on_filtered
        # Called once if Lyria can't be reached at all (see MAX_CONNECT_FAILURES).
        self.on_unavailable: Callable[[], None] | None = None
        # Tags Lyria rejected since the last set_prompts(); kept out of resends.
        self._rejected: set[str] = set()
        self._api_key = api_key or os.getenv("GOOGLE_API_KEY")
        self._client = client
        self._buffer = PcmBuffer(MAX_BUFFER_S)
        self._session: Any = None
        self._ready = asyncio.Event()
        self._run_task: asyncio.Task[None] | None = None
        self._fade_task: asyncio.Task[None] | None = None
        self._closed = False

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        if self._run_task is None:
            self._run_task = asyncio.create_task(self._run(), name="lyria-run")

    async def aclose(self) -> None:
        self._closed = True
        tasks = [t for t in (self._fade_task, self._run_task) if t and not t.done()]
        for task in tasks:
            task.cancel()
        # Wait so the Lyria websocket closes and the job process can exit.
        if tasks:
            await asyncio.wait(tasks, timeout=3.0)

    async def _run(self) -> None:
        if not self._api_key:
            # Keep the call alive so voice/tools can still be tested without music.
            logger.error("GOOGLE_API_KEY is not set; Lyria music is disabled")
            return
        # Off the event loop: client creation builds an SSL context (~100-300 ms).
        client = self._client or await asyncio.to_thread(make_client, self._api_key)
        backoff = 1.0
        failures = 0
        while not self._closed:
            try:
                async with client.aio.live.music.connect(model=MODEL) as session:
                    self._session = session
                    await self._apply_state(session)
                    self._ready.set()
                    backoff = 1.0
                    failures = 0
                    logger.info("lyria session up (%s)", API_VERSION)
                    await asyncio.wait_for(
                        self._receive(session), timeout=SESSION_MAX_S
                    )
            except TimeoutError:
                logger.info("rolling lyria session after %.0fs", SESSION_MAX_S)
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1
                if failures >= MAX_CONNECT_FAILURES and self.on_unavailable:
                    logger.exception("lyria unavailable after %d attempts", failures)
                    self.on_unavailable()
                    return
                logger.exception("lyria session failed; reconnecting in %.0fs", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
            finally:
                self._ready.clear()
                self._session = None

    async def _receive(self, session: Any) -> None:
        async for message in session.receive():
            if message.server_content and message.server_content.audio_chunks:
                for chunk in message.server_content.audio_chunks:
                    if chunk.data:
                        self._buffer.push(stereo_to_mono(chunk.data))
            elif message.filtered_prompt:
                self._handle_filtered(
                    message.filtered_prompt.text or "",
                    message.filtered_prompt.filtered_reason or "",
                )

    def _handle_filtered(self, text: str, reason: str) -> None:
        # Lyria re-reports a rejected tag for every crossfade step; act once.
        if text in self._rejected:
            return
        self._rejected.add(text)
        record = {
            "op": "filtered_prompt",
            "t": round(time.time(), 3),
            "text": text,
            "reason": reason,
        }
        logger.warning("lyria_recv %s", json.dumps(record), extra={"lyria": record})
        if LYRIA_LOG_FILE:
            with open(LYRIA_LOG_FILE, "a") as f:
                f.write(json.dumps(record) + "\n")
        # Rejection is not deterministic (the same tag can pass later), so we
        # only drop it from the current mix instead of blacklisting it.
        if text in self.prompts and len(self.prompts) > 1:
            del self.prompts[text]
        self.last_filtered = (text, reason)
        if self.on_filtered:
            self.on_filtered(text, reason)

    async def _call(self, session: Any, op: str, **kwargs: Any) -> None:
        """Send one command to Lyria and record exactly what was sent."""
        record: dict[str, Any] = {"op": op, "t": round(time.time(), 3)}
        if "prompts" in kwargs:
            record["prompts"] = {p.text: p.weight for p in kwargs["prompts"]}
        if "config" in kwargs:
            record["config"] = kwargs["config"].model_dump(
                exclude_none=True, mode="json"
            )
        record["queued_audio_s"] = round(self._buffer.seconds, 2)
        logger.info("lyria_send %s", json.dumps(record), extra={"lyria": record})
        if LYRIA_LOG_FILE:
            with open(LYRIA_LOG_FILE, "a") as f:
                f.write(json.dumps(record) + "\n")
        await getattr(session, op)(**kwargs)

    async def _apply_state(self, session: Any) -> None:
        await self._call(session, "set_weighted_prompts", prompts=self._weighted())
        await self._call(
            session, "set_music_generation_config", config=self._gen_config()
        )
        if self.playing:
            await self._call(session, "play")

    # ---- audio out -------------------------------------------------------

    async def frames(self) -> AsyncIterator[rtc.AudioFrame]:
        """Infinite 20 ms mono 48 kHz frames for BackgroundAudioPlayer.play().

        Never blocks: the mixer drops streams that stall (~200 ms), so on
        underrun we emit silence and re-buffer PREROLL_S before resuming.
        """
        primed = False
        preroll = int(PREROLL_S * 48000)
        while not self._closed:
            samples = None
            if primed or len(self._buffer) >= preroll:
                primed = True
                samples = self._buffer.pop(SAMPLES_PER_FRAME)
                if samples is None:
                    primed = False
            if samples is None or not self.playing:
                samples = silence()
            yield to_frame(self.ducker.apply(samples))

    # ---- controls (safe to call any time; state is re-applied on reconnect) --

    async def set_prompts(
        self, prompts: dict[str, float], *, crossfade_s: float = 3.0, steps: int = 4
    ) -> None:
        """Move to a new prompt mix, stepping weights so the change isn't abrupt."""
        if self._fade_task:
            self._fade_task.cancel()
        self._rejected.clear()
        self.last_filtered = None
        old = dict(self.prompts)
        self.prompts = dict(prompts)
        logger.info(
            "new prompts %s (queued audio %.1fs)", self.prompts, self._buffer.seconds
        )
        self._buffer.keep_last(REPROMPT_KEEP_S)
        if crossfade_s <= 0 or not old:
            await self._send_prompts(self.prompts)
            return
        self._fade_task = asyncio.create_task(
            self._crossfade(old, dict(prompts), crossfade_s, steps)
        )

    async def _crossfade(
        self, old: dict[str, float], new: dict[str, float], seconds: float, steps: int
    ) -> None:
        for i in range(1, steps + 1):
            t = i / steps
            mix: dict[str, float] = {}
            for text, w in old.items():
                if text not in new and t < 1:
                    mix[text] = w * (1 - t)
            for text, w in new.items():
                start = old.get(text, 0.0)
                mix[text] = start + (w - start) * t
            await self._send_prompts(
                {k: round(v, 3) for k, v in mix.items() if abs(v) > 1e-3}
            )
            if i < steps:
                await asyncio.sleep(seconds / steps)

    async def set_config(self, **changes: Any) -> None:
        """Merge config changes. Unset keys with None. bpm/scale trigger reset_context."""
        for key, value in changes.items():
            if value is None:
                self.config.pop(key, None)
            else:
                self.config[key] = value
        session = self._session
        if session is None:
            return
        await self._call(
            session, "set_music_generation_config", config=self._gen_config()
        )
        if _RESET_KEYS & changes.keys():
            await self._call(session, "reset_context")
            self._buffer.keep_last(0.5)

    async def pause(self) -> None:
        self.playing = False
        if self._session:
            await self._call(self._session, "pause")

    async def resume(self) -> None:
        self.playing = True
        if self._session:
            await self._call(self._session, "play")

    async def drop(self) -> None:
        """Hard transition: restart the groove with current prompts and config."""
        if self._session:
            await self._call(self._session, "reset_context")
            self._buffer.keep_last(0.5)

    async def wait_ready(self, timeout: float = 10.0) -> bool:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
            return True
        except TimeoutError:
            return False

    def describe(self) -> str:
        """Compact state for the LLM; this lands in every tool result."""
        mix = ", ".join(f"{t} {w:g}" for t, w in self.prompts.items())
        cfg = ", ".join(f"{k}={v}" for k, v in self.config.items() if k != "guidance")
        return (
            f"{mix}"
            + (f"; {cfg}" if cfg else "")
            + ("; paused" if not self.playing else "")
        )

    # ---- helpers -----------------------------------------------------------

    async def _send_prompts(self, prompts: dict[str, float]) -> None:
        prompts = {t: w for t, w in prompts.items() if t not in self._rejected}
        if self._session is None or not prompts:
            return
        await self._call(
            self._session,
            "set_weighted_prompts",
            prompts=[
                types.WeightedPrompt(text=t, weight=w)
                for t, w in with_avoid(prompts).items()
            ],
        )

    def _weighted(self) -> list[types.WeightedPrompt]:
        return [
            types.WeightedPrompt(text=t, weight=w)
            for t, w in with_avoid(self.prompts).items()
        ]

    def _gen_config(self) -> types.LiveMusicGenerationConfig:
        # Lyria resets any omitted field to default, so always send the full config.
        cfg = dict(self.config)
        if "scale" in cfg:
            cfg["scale"] = types.Scale[cfg["scale"]]
        return types.LiveMusicGenerationConfig(**cfg)
