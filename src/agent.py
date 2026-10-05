import json
import logging
import os
import re
import textwrap
import time

from dotenv import load_dotenv
from livekit import api
from livekit.agents import (
    Agent,
    AgentServer,
    AgentStateChangedEvent,
    BackgroundAudioPlayer,
    JobContext,
    JobProcess,
    ModelSettings,
    RunContext,
    StopResponse,
    UserStateChangedEvent,
    cli,
    function_tool,
    llm,
)

from dj.lyria import LyriaDJ, make_client
from dj.pipelines import PIPELINE, build_session, room_options

logger = logging.getLogger("agent")

load_dotenv(".env.local")

DEFAULT_VIBE = {"deep house": 1.0, "warm synth pads": 0.6, "groovy": 0.5}

# Music gain while someone is talking. Tune on a real phone (see docs/TASKS.md).
DUCK_AGENT_SPEAKING = 0.25
DUCK_USER_SPEAKING = 0.4

# Tags that describe a feeling rather than a genre or instrument. set_mood swaps
# these out so a new mood isn't outvoted by the old one.
MOOD_WORDS = {
    "upbeat", "groovy", "happy", "euphoric", "energetic", "funky", "joyful",
    "uplifting", "party", "bright", "cheerful", "playful", "fun", "sad",
    "melancholic", "melancholy", "somber", "sombre", "dark", "moody", "gloomy",
    "emotional", "heartbroken", "mournful", "chill", "relaxed", "calm", "dreamy",
    "ominous", "tense", "aggressive", "angry", "romantic", "nostalgic",
    "peaceful", "mellow", "hopeful", "triumphant", "epic", "whimsical", "eerie",
}  # fmt: skip


def _clean_tags(tags: list[str]) -> list[str]:
    """Split tags the LLM sometimes packs into one string ('a", "b' or 'a, b')."""
    out = []
    for tag in tags:
        for part in re.split(r'["\\]*\s*,\s*["\\]*', tag):
            part = part.strip(' "\\')
            if part:
                out.append(part)
    return out


SCALES = [
    "C_MAJOR_A_MINOR", "D_FLAT_MAJOR_B_FLAT_MINOR", "D_MAJOR_B_MINOR",
    "E_FLAT_MAJOR_C_MINOR", "E_MAJOR_D_FLAT_MINOR", "F_MAJOR_D_MINOR",
    "G_FLAT_MAJOR_E_FLAT_MINOR", "G_MAJOR_E_MINOR", "A_FLAT_MAJOR_F_MINOR",
    "A_MAJOR_G_FLAT_MINOR", "B_FLAT_MAJOR_G_MINOR", "B_MAJOR_A_FLAT_MINOR",
]  # fmt: skip

INSTRUCTIONS = textwrap.dedent(
    """\
    You are the Dial-in DJ, a live DJ the caller reached by phone. You control a
    real-time music generator with your tools. The music is playing under your
    voice the whole time, so talk as little as possible and let the music speak.

    # How to DJ

    - Every request about the music becomes a tool call. Call the tool first,
      then confirm in five words or fewer, like "Dropping into deep house." or
      "Tempo up. Let's go."
    - Translate what the caller says into short music tags: genres, instruments,
      and moods, one idea per tag. For example "something chill for studying"
      becomes lo-fi hip hop, Rhodes piano, relaxed.
    - Be accurate: the first tag is the exact genre the caller asked for, named
      the way a record store would ("hard rock", "bossa nova", "drum and bass"),
      followed by the one or two instruments that define that genre and one
      mood. Do not soften or blend the request ("light rock and roll" when they
      said rock), and do not carry tags over from the previous vibe.
    - Prefer plain, standard words: genre names, instrument names and simple
      moods such as chill, dark, dreamy, euphoric, funky or upbeat. Avoid
      abstract phrases like "jazz elements" or "vibes".
    - Keep the caller's exact instrument. Never swap it for a different one
      ("upright bass" stays "upright bass", "brushed drums" stays "brushed
      drums"). Only if it matches one of these names, use this spelling:
      Alto Saxophone, Trumpet, Rhodes Piano, Smooth Pianos, Harpsichord, Synth
      Pads, Moog Oscillations, 303 Acid Bass, Precision Bass, Boomy Bass, 808 Hip
      Hop Beat, TR-909 Drum Machine, Funk Drums, Conga Drums, Tabla, Warm
      Acoustic Guitar, Flamenco Guitar, Slide Guitar, Shredding Guitar, Sitar,
      Harp, Cello, Viola Ensemble, Fiddle, Banjo, Mandolin, Koto, Kalimba,
      Marimba, Vibraphone, Steel Drum, Hang Drum, Glockenspiel, Harmonica,
      Bagpipes, Didgeridoo, Flute, Woodwinds, Tuba. Use "lead" prominence when
      they want to really hear it or ask for a solo.
    - When the caller asks for a genre with a specific instrument ("jazz with a
      trumpet"), put the instrument in set_vibe as the second tag.
    - Use set_vibe to replace the whole sound. Use add_layer and remove_layer
      for "add some saxophone" or "lose the strings". Use set_tempo for faster
      and slower; one step is about fifteen beats per minute. Use set_energy for
      "more intense", "busier", "brighter" or "more muffled".
    - Use set_mood whenever the caller asks for a feeling: sadder, happier,
      darker, more hopeful, angrier, more romantic. Pass two plain mood tags
      (sad becomes melancholic, sad; happy becomes happy, upbeat). For sad or
      calm moods also pass a slower bpm, for happy or energetic ones a faster bpm.
    - The generator is instrumental only. It cannot sing lyrics or copy a
      specific artist or song. If asked, describe that artist's style in tags
      instead and say you are doing "something in that spirit".
    - If a tool result or a system note says a tag was rejected, immediately
      replace it with a close, plainer alternative using add_layer, then say
      at most a few words about it.

    # Who you are listening to

    - Only act on requests that are clearly addressed to you and about the
      music. People near the caller may be talking to each other; if what you
      hear is side conversation, chatter, or unrelated to the music, do not
      call any tool and do not reply at all. Stay silent.

    # Voice rules

    - Plain spoken text only, no lists, markdown or emojis. Never read tool
      names or numbers like weights aloud.
    - Stay quiet between requests. Do not ask follow-up questions unless the
      request was unclear.
    """
)


# Lyria prompt weights are relative, so a new layer's audibility depends on its
# share of the total weight, not its absolute value. Target shares per prominence:
LAYER_SHARE = {"hint": 0.15, "featured": 0.35, "lead": 0.5}
MAX_LAYERS = 5


def layer_mix(prompts: dict[str, float], tag: str, prominence: str) -> dict[str, float]:
    """Add `tag` so it gets its target share of the mix; keep at most MAX_LAYERS."""
    share = LAYER_SHARE.get(prominence, LAYER_SHARE["featured"])
    others = {t: w for t, w in prompts.items() if t.lower() != tag.lower()}
    # Drop the weakest extras (never the first tag, which is the genre).
    while len(others) >= MAX_LAYERS:
        items = list(others.items())
        weakest = min(items[1:], key=lambda kv: kv[1])[0]
        del others[weakest]
    total = sum(others.values()) or 1.0
    weight = round(share / (1 - share) * total, 2)
    return {**others, tag: weight}


# Keep only the last N chat items (messages, tool calls, tool results). The DJ
# needs recent requests, not the whole call; the current mix is re-stated each
# turn instead, so trimming never loses musical state.
MAX_CTX_ITEMS = int(os.getenv("DJ_MAX_CTX_ITEMS", "12"))
CTX_TRIM_SLACK = 8

# Zero-width and other invisible characters. The LLM sometimes "stays silent"
# by emitting only these, which made the TTS raise "no audio frames".
_INVISIBLE = re.compile("[\u200b-\u200f\u2060\ufeff]")


async def _visible(text):
    async for chunk in text:
        chunk = _INVISIBLE.sub("", chunk)
        if chunk:
            yield chunk


class DJ(Agent):
    def __init__(self, dj: LyriaDJ, outbound: bool = False) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.dj = dj
        self.outbound = outbound

    async def on_enter(self) -> None:
        if self.outbound:
            greeting = (
                "You just called this person. Say hi in one short line as the "
                "Dial-in DJ and ask what they want to hear. Deep house is playing."
            )
        else:
            greeting = (
                "Greet the caller in one short line as the Dial-in DJ and "
                "ask what they want to hear. Deep house is already playing."
            )
        self.session.generate_reply(instructions=greeting)

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        # Touching the context invalidates preemptive generation (the reply the
        # LLM started while the caller was still talking), so only trim once the
        # history is well past the cap: roughly one turn in four, not every turn.
        if len(self.chat_ctx.items) <= MAX_CTX_ITEMS + CTX_TRIM_SLACK:
            return
        ctx = self.chat_ctx.copy().truncate(max_items=MAX_CTX_ITEMS)
        # Old tool results carried the mix; restate it once so nothing is lost.
        ctx.add_message(role="system", content=f"Now playing: {self.dj.describe()}")
        await self.update_chat_ctx(ctx)
        turn_ctx.truncate(max_items=MAX_CTX_ITEMS)
        turn_ctx.add_message(
            role="system", content=f"Now playing: {self.dj.describe()}"
        )

    async def tts_node(self, text, model_settings: ModelSettings):
        cleaned = _visible(text)
        head: list[str] = []
        async for chunk in cleaned:
            head.append(chunk)
            if chunk.strip():
                break
        else:
            return  # nothing audible: a deliberate silent reply

        async def replay():
            for chunk in head:
                yield chunk
            async for chunk in cleaned:
                yield chunk

        async for frame in Agent.default.tts_node(self, replay(), model_settings):
            yield frame

    def _status(self) -> str:
        status = self.dj.describe()
        if self.dj.last_filtered:
            text, reason = self.dj.last_filtered
            status += f" Last filtered prompt: {text!r} ({reason})."
            self.dj.last_filtered = None
        return status

    @function_tool
    async def set_vibe(self, context: RunContext, tags: list[str]) -> str:
        """Replace the music with a new style. Crossfades over a few seconds.

        Args:
            tags: Two to five short music tags, most important first, such as
                ["bossa nova", "nylon guitar", "relaxed"] or ["drum and bass", "dark"].
        """
        tags = _clean_tags(tags)[:6]
        prompts = {t: round(1.0 - 0.15 * i, 2) for i, t in enumerate(tags)}
        await self.dj.set_prompts(prompts)
        return self._status()

    @function_tool
    async def add_layer(
        self, context: RunContext, tag: str, prominence: str = "featured"
    ) -> str:
        """Bring an instrument or element into the current music without replacing it.

        Args:
            tag: Use the generator's instrument names when you can, such as
                "Alto Saxophone", "Rhodes Piano", "Warm Acoustic Guitar",
                "TR-909 Drum Machine" or "Cello". Moods and textures also work.
            prominence: "hint" for a subtle touch, "featured" (default) so it is
                clearly heard, "lead" when the caller wants it front and center.
        """
        prompts = layer_mix(self.dj.prompts, tag, prominence)
        await self.dj.set_prompts(prompts, crossfade_s=2.0)
        return self._status()

    @function_tool
    async def remove_layer(self, context: RunContext, tag: str) -> str:
        """Remove an element from the current music.

        Args:
            tag: The element to remove, matching one of the tags currently playing.
        """
        remaining = {
            t: w for t, w in self.dj.prompts.items() if tag.lower() not in t.lower()
        }
        if remaining and remaining != self.dj.prompts:
            await self.dj.set_prompts(remaining, crossfade_s=2.0)
        return self._status()

    @function_tool
    async def set_tempo(self, context: RunContext, bpm: int) -> str:
        """Set the tempo. Causes a short hard transition, so avoid tiny changes.

        Args:
            bpm: Beats per minute, from 60 to 200. House is about 124, drum and bass about 174.
        """
        await self.dj.set_config(bpm=max(60, min(200, bpm)))
        return self._status()

    @function_tool
    async def set_energy(
        self,
        context: RunContext,
        density: float | None = None,
        brightness: float | None = None,
    ) -> str:
        """Adjust how busy and how bright the music is. Leave a value out to keep it.

        Args:
            density: 0.0 is sparse and minimal, 1.0 is busy and intense.
            brightness: 0.0 is dark and muffled, 1.0 is bright and crisp.
        """
        changes = {}
        if density is not None:
            changes["density"] = max(0.0, min(1.0, density))
        if brightness is not None:
            changes["brightness"] = max(0.0, min(1.0, brightness))
        await self.dj.set_config(**changes)
        return self._status()

    @function_tool
    async def set_mood(
        self, context: RunContext, moods: list[str], bpm: int | None = None
    ) -> str:
        """Change how the music feels while keeping its genre and instruments.

        Args:
            moods: One to three plain mood tags, such as ["melancholic", "sad"],
                ["happy", "upbeat"], ["dark", "ominous"] or ["dreamy", "calm"].
            bpm: Optional new tempo that fits the mood, for example about 80 for
                sad and about 128 for happy. Leave it out to keep the tempo.
        """
        moods = _clean_tags(moods)[:3]
        if not moods:
            return self._status()
        kept = {
            t: w
            for t, w in self.dj.prompts.items()
            if not set(t.lower().replace("-", " ").split()) & MOOD_WORDS
        }
        # Weighted above the genre so the feeling wins over the genre's default.
        await self.dj.set_prompts(
            {**kept, **dict.fromkeys(moods, 1.3)}, crossfade_s=2.0
        )
        if bpm is not None:
            await self.dj.set_config(bpm=max(60, min(200, bpm)))
        return self._status()

    @function_tool
    async def set_key(self, context: RunContext, scale: str) -> str:
        """Change the musical key. Causes a short hard transition.

        Args:
            scale: One of C_MAJOR_A_MINOR, D_FLAT_MAJOR_B_FLAT_MINOR, D_MAJOR_B_MINOR,
                E_FLAT_MAJOR_C_MINOR, E_MAJOR_D_FLAT_MINOR, F_MAJOR_D_MINOR,
                G_FLAT_MAJOR_E_FLAT_MINOR, G_MAJOR_E_MINOR, A_FLAT_MAJOR_F_MINOR,
                A_MAJOR_G_FLAT_MINOR, B_FLAT_MAJOR_G_MINOR, B_MAJOR_A_FLAT_MINOR.
        """
        if scale not in SCALES:
            return f"Unknown scale {scale!r}. {self._status()}"
        await self.dj.set_config(scale=scale)
        return self._status()

    @function_tool
    async def set_mix(
        self,
        context: RunContext,
        mute_drums: bool | None = None,
        mute_bass: bool | None = None,
    ) -> str:
        """Mute or unmute the drums or the bass, for breakdowns and drops.

        Args:
            mute_drums: True to drop the drums out, False to bring them back.
            mute_bass: True to drop the bass out, False to bring it back.
        """
        changes = {}
        if mute_drums is not None:
            changes["mute_drums"] = mute_drums or None
        if mute_bass is not None:
            changes["mute_bass"] = mute_bass or None
        await self.dj.set_config(**changes)
        return self._status()

    @function_tool
    async def pause_music(self, context: RunContext) -> str:
        """Pause the music."""
        await self.dj.pause()
        return self._status()

    @function_tool
    async def resume_music(self, context: RunContext) -> str:
        """Resume the music after a pause."""
        await self.dj.resume()
        return self._status()


class DirectDJ(Agent):
    """No LLM, no TTS: the caller's words become the prompt."""

    def __init__(self, dj: LyriaDJ) -> None:
        super().__init__(instructions="Forward each user turn to Lyria.")
        self.dj = dj

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        text = (new_message.text_content or "").strip()
        if text:
            await self.dj.set_prompts({text: 1.0}, crossfade_s=1.5)
        raise StopResponse()


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    # Building the Gemini client imports anyio and creates an SSL context, which
    # blocked the job's event loop for 100-300 ms when done at call start.
    proc.userdata["genai_client"] = make_client()


server.setup_fnc = prewarm


OUTBOUND_IDENTITY = "dj-callee"


def _dial_target(ctx: JobContext) -> str | None:
    """Phone number to call, from dispatch metadata like {"phone_number": "+1..."}.

    Outbound calling is off unless ALLOW_OUTBOUND_CALLS=1: a deployment that
    can dial any number is a toll-fraud risk, so it must be switched on
    deliberately (together with SIP_OUTBOUND_TRUNK_ID).
    """
    if os.getenv("ALLOW_OUTBOUND_CALLS") != "1":
        if ctx.job.metadata and "phone_number" in ctx.job.metadata:
            logger.warning("ignoring phone_number: ALLOW_OUTBOUND_CALLS is not set")
        return None
    try:
        meta = json.loads(ctx.job.metadata or "{}")
    except json.JSONDecodeError:
        return None
    number = meta.get("phone_number") if isinstance(meta, dict) else None
    return number.strip() if isinstance(number, str) and number.strip() else None


async def _place_call(ctx: JobContext, phone_number: str) -> bool:
    """Ring the callee and wait for them to pick up. Needs an outbound SIP trunk."""
    trunk_id = os.getenv("SIP_OUTBOUND_TRUNK_ID")
    if not trunk_id:
        logger.error("outbound call requested but SIP_OUTBOUND_TRUNK_ID is not set")
        return False
    try:
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=trunk_id,
                sip_call_to=phone_number,
                participant_identity=OUTBOUND_IDENTITY,
                participant_name="Dial-in DJ caller",
                krisp_enabled=True,
                wait_until_answered=True,
            )
        )
    except api.SipCallError as e:
        logger.warning(f"outbound call failed: {e.sip_status_code} {e.sip_status}")
        return False
    logger.info("outbound call answered")
    return True


@server.rtc_session(agent_name="dial-in-dj")
async def dial_in_dj(ctx: JobContext):
    phone_number = _dial_target(ctx)
    ctx.log_context_fields = {
        "room": ctx.room.name,
        "pipeline": PIPELINE,
        "direction": "outbound" if phone_number else "inbound",
    }

    dj = LyriaDJ(DEFAULT_VIBE, client=ctx.proc.userdata.get("genai_client"))
    ctx.add_shutdown_callback(dj.aclose)

    session = build_session()

    @session.on("agent_state_changed")
    def _duck_for_agent(ev: AgentStateChangedEvent) -> None:
        if ev.new_state == "speaking":
            dj.ducker.target = DUCK_AGENT_SPEAKING
        elif dj.ducker.target == DUCK_AGENT_SPEAKING:
            dj.ducker.target = 1.0

    @session.on("user_state_changed")
    def _duck_for_user(ev: UserStateChangedEvent) -> None:
        if ev.new_state == "speaking":
            dj.ducker.target = min(dj.ducker.target, DUCK_USER_SPEAKING)
        elif dj.ducker.target == DUCK_USER_SPEAKING:
            dj.ducker.target = 1.0

    last_fix = 0.0

    def _on_filtered(text: str, reason: str) -> None:
        # Lyria reports rejections seconds after the tool call returned, so have
        # the DJ swap in an alternative on its own. Rate-limited to avoid loops.
        nonlocal last_fix
        if PIPELINE == "direct" or time.monotonic() - last_fix < 10:
            return
        last_fix = time.monotonic()
        session.generate_reply(
            instructions=f"The music generator rejected the tag {text!r}. Replace it "
            "now with a close, plainer alternative using add_layer, then confirm in "
            "a few words. Do not mention filters or errors."
        )

    # Start Lyria before dialing: its cold start (3-10 s) overlaps the ringing.
    dj.on_filtered = _on_filtered
    dj.start()

    await ctx.connect()
    if phone_number and not await _place_call(ctx, phone_number):
        ctx.shutdown()
        return

    agent = (
        DirectDJ(dj) if PIPELINE == "direct" else DJ(dj, outbound=bool(phone_number))
    )
    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_options(OUTBOUND_IDENTITY if phone_number else None),
    )

    # Music goes out on its own track next to the agent's voice.
    background = BackgroundAudioPlayer()
    await background.start(room=ctx.room, agent_session=session)
    background.play(dj.frames())
    ctx.add_shutdown_callback(background.aclose)


if __name__ == "__main__":
    cli.run_app(server)
