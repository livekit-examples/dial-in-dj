# Architecture

## Call flow
1. **Inbound call** → LiveKit Phone Number → dispatch rule `dial-in-dj` (`telephony/dispatch-rule.json`)
   creates the room `dj-_<caller>_<random>` and explicitly dispatches agent `dial-in-dj`.
2. **Agent job** (`src/agent.py: dial_in_dj`):
   - `LyriaDJ(DEFAULT_VIBE).start()` opens the Lyria websocket in the background.
   - `build_session()` builds the `AgentSession` for the `DJ_PIPELINE` mode.
   - `session.start(DJ(dj))` joins the room and the DJ greets the caller.
   - `BackgroundAudioPlayer().play(dj.frames())` publishes the music on its own track.
3. **Reprompt:** caller speech → STT → LLM tool call → `LyriaDJ.set_prompts` / `set_config`
   → Lyria. New audio arrives within about 2 s.

## Audio path
```
Lyria  ──16-bit PCM, 48 kHz, stereo, ~2 s chunks──►  LyriaDJ._receive
        stereo_to_mono()            (BackgroundAudioPlayer mixes 48 kHz MONO only)
        PcmBuffer (cap 6 s)         jitter buffer; trimmed to 2 s on a new vibe, 0.5 s after reset_context
        frames(): 20 ms frames      pre-roll 1 s; on underrun emits silence and re-buffers
        Ducker.apply()              smoothed gain: 1.0 → 0.25 (agent speaking) / 0.4 (caller)
BackgroundAudioPlayer mixer ──► LiveKit track ──► SIP (G.711/G.722, mono) or browser (Opus)
```

Why the iterator never blocks: the mixer drops a stream that doesn't yield for about 200 ms
(`stream_timeout_ms=200` in livekit-agents 1.8.3), so underruns yield silence instead of waiting.

## Why each design choice
| Choice | Reason |
|---|---|
| One long-lived Lyria session per call | Lyria keeps musical context, so prompt changes morph the groove instead of restarting it |
| Crossfade by stepping weights | Google recommends intermediate weights. Drastic prompt swaps sound abrupt |
| Full config resent on every change | Lyria resets omitted fields to their defaults |
| `reset_context()` only for bpm and scale | Only those two need it. It is a hard transition, so the tool docs warn the LLM |
| Session rollover at 540 s | Sessions reportedly end at about 10 min. State is reapplied on reconnect |
| Ducking in our own iterator | `BackgroundAudioPlayer` volume is fixed per `play()` call |
| `BVCTelephony` for SIP callers, `BVC` for web | Cleans only what we hear: background voices, noise, and our own music leaking back through the mic |
| Adaptive interruptions + `min_words=2` | Leaked music should not register as barge-in |
| Negative-weight `piano` prompt | Lyria drifts to solo piano when prompts are vague; avoided unless the caller asks for piano or keys |
| Instrument layers sized by share of the mix | Weights are relative, so a requested instrument gets 15/35/50% of the total (hint/featured/lead) |
| Chat history capped at ~12 items | Keeps each LLM request small on long calls; the current mix is restated when history is trimmed |

## Modes
- **pipeline:** `inference.STT` (AssemblyAI) + `inference.LLM` + `inference.TTS` (Fish Audio), `TurnDetector`.
- **gemini:** `google.realtime.RealtimeModel`. Gemini owns turn-taking, so the interruption tuning above doesn't apply.
- **direct:** STT + turn detector only. `DirectDJ.on_user_turn_completed` sends the transcript
  as the prompt and raises `StopResponse`. It never speaks.

## Web demo page (`web/`)
- `web/server.py` serves `index.html`, `static/` and the API in `web/api/index.py`
  (Starlette). The same module runs locally (`uv run web/server.py`) and as a Vercel Python function.
- `POST /api/token` mints a 10-minute token for a fresh `dj-web-*` room with a random identity
  and dispatches `dial-in-dj` through the token's room configuration. The server makes every
  choice; client fields are ignored, so the endpoint can't be used to join someone else's
  room or dispatch other agents. It is rate limited per client IP.
- `GET /api/config` returns `DJ_DEMO_NUMBER` (or `null`), which shows or hides "Call the DJ".
- The page plays every remote audio track through a Web Audio analyser that drives the
  visualizers in `static/vis/` (a metallic blob and particle spheres).

## Observability
Every command sent to Lyria (prompts including each crossfade step, config, play/pause/reset)
is logged as a structured `lyria_send` record with the queued audio depth; rejected prompts
are logged as `lyria_recv`. Set `LYRIA_LOG_FILE` to also append them as JSONL.
