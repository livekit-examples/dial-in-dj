# Lyria RealTime notes

Researched 2026-09-30 against the docs page "Last updated 2026-09-17", the Gemini cookbook,
and `google-genai` 2.25.0 as installed. Mark items **VERIFIED** when we have confirmed them ourselves.

## Facts
| Item | Value | Status |
|---|---|---|
| Model | `models/lyria-realtime-exp` (experimental, the only realtime model) | docs |
| SDK | `google-genai` (Python) `client.aio.live.music.connect(model=...)`. JS: `@google/genai` `ai.live.music.connect` | docs |
| Key | Gemini API key only. **Not Vertex** (the JS SDK throws on Vertex) | docs |
| API version | Docs page: `v1beta`. Cookbook: `v1alpha` | **VERIFIED 2026-09-30: both work** (default stays `v1alpha`) |
| Output | Raw 16-bit PCM, **48 kHz, stereo**, interleaved, ~2 s chunks | model card. `LiveMusicGenerationConfig` has no sample-rate field in 2.25.0 |
| Control latency | ≤ 2 s | model card |
| Settle time after start/reset | ~5–10 s | Google dev.to guide, unofficial |
| Session length | ~10 min, then reconnect | unofficial |
| Quota / concurrency / price | Not documented. "Free with quota limitations" | cookbook |
| Content | Instrumental only. Safety-filtered prompts arrive in `filtered_prompt` | docs |
| Watermark | Output is always watermarked | docs |

## Controls
- `set_weighted_prompts([WeightedPrompt(text, weight)])`: weight is any non-zero value; 1.0 is a good start.
- `set_music_generation_config(LiveMusicGenerationConfig(...))`: **always send the full config.**
  - `bpm` 60–200 and `scale` (the `types.Scale` enum) only apply after `reset_context()`.
  - `density` 0–1, `brightness` 0–1, `guidance` 0–6 (default 4; higher follows prompts more but transitions more abruptly)
  - `temperature` 0–3 (default 1.1), `top_k` 1–1000 (default 40), `seed`
  - `mute_bass`, `mute_drums`, `only_bass_and_drums`
  - `music_generation_mode`: QUALITY (default) / DIVERSITY / VOCALIZATION (wordless vocals)
- `play()`, `pause()` (resume in place), `stop()` (resets context), `reset_context()` (hard transition, keeps playing)

## Prompting that works
- Short tags, one idea each: "Rhodes piano", "lo-fi hip hop", "dreamy". Keep instruments and moods as separate prompts.
- Transition gradually by stepping weights (e.g. piano 1.0 + breakbeat 0.3 → piano 0.3 + breakbeat 0.8).
- Vocabulary from the prompt guide:
  - Genres: Acid House, Afrobeat, Bossa Nova, Chillout, Chiptune, Deep House, Disco Funk, Drum & Bass,
    Dubstep, Lo-Fi Hip Hop, Minimal Techno, Neo-Soul, Psytrance, Reggaeton, Shoegaze, Synthpop, Trap Beat, Vaporwave…
  - Instruments: 303 Acid Bass, 808 Hip Hop Beat, Alto Saxophone, Cello, Hang Drum, Kalimba, Moog Oscillations,
    Rhodes Piano, Sitar, Spacey Synths, Steel Drum, TR-909 Drum Machine, Warm Acoustic Guitar…
  - Moods: Chill, Dark, Dreamy, Euphoric, Funky, Groovy, Melancholic, Ominous, Upbeat, Whimsical…

## Our measurements
Append dated rows. Never overwrite.

| Date | Who | api_version | First audio | Avg chunk bytes | Notes |
|---|---|---|---|---|---|
| 2026-09-30 | maintainers | v1alpha | 10.17 s (cold, first call) | 384000 (= 2.0 s) | 12 s run, only 4 s of audio: cold start |
| 2026-09-30 | maintainers | v1alpha | 3.65 s | 384000 (= 2.0 s) | 30 s run, 28 s of audio: keeps up with real time |
| 2026-09-30 | maintainers | v1beta | 3.94 s | 384000 (= 2.0 s) | 12 s run, 8 s of audio |

## Sources
- https://ai.google.dev/gemini-api/docs/realtime-music-generation
- https://ai.google.dev/gemini-api/docs/models/lyria-realtime-exp
- https://ai.google.dev/gemini-api/docs/lyria-prompt-guide
- https://github.com/google-gemini/cookbook/blob/main/quickstarts/Get_started_LyriaRealTime.py
