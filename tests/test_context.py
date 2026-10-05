from livekit.agents import llm

import agent
from agent import DJ
from dj.lyria import LyriaDJ


async def test_long_history_is_trimmed_and_state_restated():
    dj = LyriaDJ({"hard rock": 1.0, "distorted guitar": 0.85}, api_key="unused")
    a = DJ(dj)
    ctx = a.chat_ctx.copy()
    for i in range(40):
        ctx.add_message(role="user" if i % 2 else "assistant", content=f"turn {i}")
    await a.update_chat_ctx(ctx)

    turn_ctx = a.chat_ctx.copy()
    msg = llm.ChatMessage(role="user", content=["make it sadder"])
    await a.on_user_turn_completed(turn_ctx, msg)

    assert len(a.chat_ctx.items) <= agent.MAX_CTX_ITEMS + 1  # + "Now playing"
    assert a.instructions.startswith("You are the Dial-in DJ")  # kept separately
    assert len(turn_ctx.items) <= agent.MAX_CTX_ITEMS + 1
    assert "hard rock 1" in turn_ctx.items[-1].text_content


def test_describe_is_compact():
    dj = LyriaDJ({"hard rock": 1.0, "guitar": 0.85}, api_key="unused")
    assert dj.describe() == "hard rock 1, guitar 0.85"


async def test_invisible_only_reply_skips_tts():
    from agent import _visible

    async def gen(*chunks):
        for c in chunks:
            yield c

    assert [c async for c in _visible(gen("​​"))] == []
    assert [c async for c in _visible(gen("​Hi", " there"))] == ["Hi", " there"]


async def test_short_history_is_left_alone():
    dj = LyriaDJ(api_key="unused")
    a = DJ(dj)
    ctx = a.chat_ctx.copy()
    for i in range(agent.MAX_CTX_ITEMS + 2):
        ctx.add_message(role="user", content=f"turn {i}")
    await a.update_chat_ctx(ctx)
    turn_ctx = a.chat_ctx.copy()
    before = len(turn_ctx.items)
    await a.on_user_turn_completed(
        turn_ctx, llm.ChatMessage(role="user", content=["x"])
    )
    assert len(turn_ctx.items) == before  # untouched: preemptive reply stays valid
