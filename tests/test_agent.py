"""Turn-level evals for the DJ's tool use. These call LiveKit Inference, so they
need LIVEKIT_* credentials in .env.local. Lyria is faked; see test_audio.py and
scripts/lyria_smoke.py for the music side.

    uv run pytest tests/test_agent.py
"""

import os

import pytest
from dotenv import load_dotenv
from livekit.agents import AgentSession, inference

from agent import DJ

load_dotenv(".env.local")
pytestmark = pytest.mark.skipif(
    not os.getenv("LIVEKIT_API_KEY"), reason="needs LiveKit Inference credentials"
)


class FakeDJ:
    def __init__(self) -> None:
        self.prompts: dict[str, float] = {"deep house": 1.0}
        self.config: dict = {}
        self.playing = True
        self.last_filtered = None

    async def set_prompts(self, prompts, **_):
        self.prompts = dict(prompts)

    async def set_config(self, **changes):
        self.config.update({k: v for k, v in changes.items() if v is not None})

    async def pause(self):
        self.playing = False

    async def resume(self):
        self.playing = True

    def describe(self) -> str:
        return f"Playing: {', '.join(self.prompts)}. Settings: {self.config}."


def _llm():
    return inference.LLM(model=os.getenv("DJ_LLM", "google/gemma-4-31b-it"))


async def _run(text: str, dj: FakeDJ):
    async with _llm() as model, AgentSession(llm=model) as session:
        await session.start(DJ(dj))
        return await session.run(user_input=text)


async def test_vibe_request_calls_set_vibe():
    dj = FakeDJ()
    result = await _run("Play me something chill to study to", dj)
    result.expect.contains_function_call(name="set_vibe")
    assert "deep house" not in dj.prompts


async def test_faster_calls_set_tempo():
    dj = FakeDJ()
    result = await _run("Make it way faster", dj)
    result.expect.contains_function_call(name="set_tempo")
    assert dj.config.get("bpm", 0) > 124


async def test_add_instrument_keeps_current_vibe():
    dj = FakeDJ()
    result = await _run("Add a saxophone on top", dj)
    result.expect.contains_function_call(name="add_layer")
    assert "deep house" in dj.prompts and len(dj.prompts) == 2


async def test_ignores_side_conversation():
    dj = FakeDJ()
    result = await _run("Hey Sam, did you grab the pizza from the car?", dj)
    for ev in result.events:
        assert ev.type != "function_call", "DJ acted on side chatter"
    assert dj.prompts == {"deep house": 1.0}


async def test_rock_request_is_not_softened():
    dj = FakeDJ()
    await _run("Play some rock and roll", dj)
    first = next(iter(dj.prompts)).lower()
    assert "rock" in first and "light" not in first
