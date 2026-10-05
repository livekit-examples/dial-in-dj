"""Voice pipeline selection. DJ_PIPELINE=pipeline (default) | gemini | direct.

`pipeline` (STT -> LLM -> TTS) gives us control over turn-taking, which matters
because the caller's mic picks up the music we are playing them. `gemini` uses
Gemini Live, which owns its own turn-taking (weaker leak control, all-Google story).
`direct` has no LLM and no TTS: each user turn's transcript IS the Lyria prompt.
Lowest latency, no spoken replies.
"""

from __future__ import annotations

import os

from livekit import rtc
from livekit.agents import (
    NOT_GIVEN,
    AgentSession,
    STTContextOptions,
    TurnHandlingOptions,
    inference,
    room_io,
)
from livekit.plugins import google, noise_cancellation

PIPELINE = os.getenv("DJ_PIPELINE", "pipeline").lower()

# Genre/instrument words the STT would otherwise mangle (from Lyria's prompt guide).
KEYTERMS = [
    "Lyria", "LiveKit", "BPM", "lo-fi", "lo-fi hip hop", "synthwave", "synthpop",
    "drum and bass", "dubstep", "deep house", "acid house", "minimal techno",
    "psytrance", "trance", "moombahton", "glitch hop", "trip hop", "chiptune",
    "vaporwave", "hyperpop", "shoegaze", "post-punk", "bossa nova", "afrobeat",
    "reggaeton", "cumbia", "bhangra", "neo-soul", "boom bap", "G-funk", "grime",
    "trap", "breakbeat", "electro swing", "orchestral", "baroque",
    "Rhodes", "Moog", "Mellotron", "TR-909", "808", "303", "sitar", "tabla",
    "kalimba", "marimba", "vibraphone", "didgeridoo", "hang drum", "harpsichord",
    "banjo", "cello", "saxophone", "synth pads", "arpeggiator",
]  # fmt: skip


def build_session() -> AgentSession:
    if PIPELINE == "gemini":
        return AgentSession(
            llm=google.realtime.RealtimeModel(
                model=os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview"),
                voice=os.getenv("GEMINI_VOICE", "Puck"),
            ),
        )
    stt = inference.STT(model="assemblyai/universal-3-5-pro", language="en")
    if PIPELINE == "direct":
        return AgentSession(
            stt=stt,
            turn_handling=TurnHandlingOptions(turn_detection=inference.TurnDetector()),
        )
    return AgentSession(
        stt=stt,
        stt_context_options=STTContextOptions(
            keyterms=KEYTERMS, keyterm_detection={"enabled": True}
        ),
        llm=inference.LLM(model=os.getenv("DJ_LLM", "google/gemma-4-31b-it")),
        tts=inference.TTS(
            model="fishaudio/s2.1-pro", voice="fa4c9eb3dccc4806b382b40d61c6b10a"
        ),
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),
            # Leaked music should not read as barge-in: adaptive mode plus a
            # minimum word count keeps the DJ talking through noise.
            interruption={
                "mode": "adaptive",
                "min_words": 2,
                "resume_false_interruption": True,
            },
            preemptive_generation={"enabled": True},
        ),
    )


def _noise_cancellation(params: room_io.NoiseCancellationParams):
    # Filters only what we HEAR from the caller (never the music we send).
    # BVC = background voice cancellation: keeps the primary speaker and removes
    # other people talking nearby, noise, and our own music leaking back into
    # their mic. (ai-coustics blocked the event loop for >200 ms in testing on
    # 2026-09-30, long enough for the mixer to drop the music stream.)
    if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
        return noise_cancellation.BVCTelephony()
    return noise_cancellation.BVC()


def room_options(participant_identity: str | None = None) -> room_io.RoomOptions:
    # Outbound calls pin the session to the callee so nobody else is picked up.
    return room_io.RoomOptions(
        participant_identity=participant_identity or NOT_GIVEN,
        audio_input=room_io.AudioInputOptions(noise_cancellation=_noise_cancellation),
    )
