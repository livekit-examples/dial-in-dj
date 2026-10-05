"""Standalone Lyria RealTime smoke test. No LiveKit involved.

    uv run scripts/lyria_smoke.py                 # uses LYRIA_API_VERSION or v1alpha
    uv run scripts/lyria_smoke.py --api-version v1beta --seconds 30

Plays one prompt, crossfades to a second halfway through, and writes
out/lyria_smoke.wav (48 kHz stereo, exactly what Lyria sent). Prints time to
first audio and chunk sizes so we can record them in docs/LYRIA_NOTES.md.
"""

import argparse
import asyncio
import os
import time
import wave
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv(".env.local")


async def main(api_version: str, seconds: float) -> None:
    client = genai.Client(
        api_key=os.environ["GOOGLE_API_KEY"], http_options={"api_version": api_version}
    )
    out = Path("out/lyria_smoke.wav")
    out.parent.mkdir(exist_ok=True)
    pcm = bytearray()
    t0 = time.monotonic()
    first_audio = None
    chunk_sizes: list[int] = []

    async with client.aio.live.music.connect(
        model="models/lyria-realtime-exp"
    ) as session:

        async def receive() -> None:
            nonlocal first_audio
            async for msg in session.receive():
                if msg.server_content and msg.server_content.audio_chunks:
                    for chunk in msg.server_content.audio_chunks:
                        if first_audio is None:
                            first_audio = time.monotonic() - t0
                        chunk_sizes.append(len(chunk.data))
                        pcm.extend(chunk.data)
                elif msg.filtered_prompt:
                    print("FILTERED:", msg.filtered_prompt)

        task = asyncio.create_task(receive())
        await session.set_weighted_prompts(
            prompts=[types.WeightedPrompt(text="minimal techno", weight=1.0)]
        )
        await session.set_music_generation_config(
            config=types.LiveMusicGenerationConfig(bpm=124, temperature=1.0)
        )
        await session.play()
        await asyncio.sleep(seconds / 2)
        print("-> crossfading to bossa nova")
        for w in (0.25, 0.5, 0.75, 1.0):
            await session.set_weighted_prompts(
                prompts=[
                    types.WeightedPrompt(
                        text="minimal techno", weight=max(1 - w, 0.01)
                    ),
                    types.WeightedPrompt(text="bossa nova, nylon guitar", weight=w),
                ]
            )
            await asyncio.sleep(0.75)
        await asyncio.sleep(max(0.0, seconds / 2 - 3))
        await session.stop()
        task.cancel()

    with wave.open(str(out), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(bytes(pcm))

    audio_s = len(pcm) / (48000 * 2 * 2)
    print(f"api_version={api_version} ok")
    print(f"time_to_first_audio={first_audio:.2f}s" if first_audio else "NO AUDIO")
    print(
        f"chunks={len(chunk_sizes)} avg_bytes={sum(chunk_sizes) // max(1, len(chunk_sizes))}"
    )
    print(f"audio_seconds={audio_s:.1f} wall_seconds={time.monotonic() - t0:.1f}")
    print(f"wrote {out}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--api-version", default=os.getenv("LYRIA_API_VERSION", "v1alpha"))
    p.add_argument("--seconds", type=float, default=20)
    a = p.parse_args()
    asyncio.run(main(a.api_version, a.seconds))
