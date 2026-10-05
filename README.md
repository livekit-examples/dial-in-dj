# Dial-in DJ

**Call a phone number, say what you want to hear, and a live AI DJ plays it.
Talk again at any time and the music changes on the fly.**

Dial-in DJ is a [LiveKit Agents](https://docs.livekit.io/agents/) example that connects three pieces:

| Piece | What it does |
|---|---|
| **LiveKit Telephony** | A LiveKit Phone Number routes each call into its own LiveKit room. No SIP trunk needed. |
| **LiveKit Agents** (Python) | A voice agent listens, understands requests like "add a saxophone" or "faster", and steers the music. |
| **Google Lyria RealTime** | Generates instrumental music continuously and reacts to new prompts within about 2 seconds. |

You can also talk to the DJ from a browser, with full-quality audio and a music visualizer.

---

## How it works

![How Dial-in DJ works: the caller's phone connects through the phone network to LiveKit Cloud, where the phone number, call routing, the live room, the speech tools and the AI DJ run. The DJ sends music instructions to Google's Lyria, which streams new music back. The code ships from GitHub.](docs/images/how-it-works.svg)

PNG for slides: [how-it-works.png](docs/images/how-it-works.png)

| What it is | In plain words | Where it runs |
|---|---|---|
| **Phone number** | answers the call | LiveKit Cloud (a LiveKit Phone Number) |
| **Call routing** | gives every caller their own private room and sends the DJ in | LiveKit Cloud (SIP dispatch rule, `telephony/`) |
| **The live room** | where the caller, the DJ and the music meet in real time | LiveKit Cloud (one room per call) |
| **The DJ** | the AI that listens, understands and steers the music | LiveKit Cloud agent, code in `src/` |
| **Speech tools** | the DJ's ears (speech to text), brain (language model) and voice (text to speech) | LiveKit Inference: AssemblyAI, Gemma, Fish Audio |
| **Lyria** | composes new instrumental music live and changes style within about 2 seconds | Google Gemini API (Lyria RealTime) |
| **Demo page** | "Talk in browser", the dial-in number and a visualizer that moves with the music | `web/`, on any Python host (Vercel config included) |

### A call, step by step

![A call to the Dial-in DJ, step by step: 1. the caller dials; 2. the DJ says hi and music starts; 3. the caller asks for something; 4. the DJ understands and confirms; 5. Lyria morphs the music; 6. the caller keeps steering, repeating steps 3 to 5; 7. the caller hangs up and everything shuts down.](docs/images/caller-story.svg)

PNG for slides: [caller-story.png](docs/images/caller-story.png)

More technical detail (audio formats, buffering, design choices) is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### Three modes (`DJ_PIPELINE`)

| Mode | Stack | Talks back? | Best for |
|---|---|---|---|
| `pipeline` (default) | AssemblyAI STT → LLM with tools → Fish Audio TTS, all through LiveKit Inference | Yes, short confirmations | Phone calls: the agent controls turn-taking, which matters because the music leaks into the caller's mic |
| `gemini` | Gemini Live realtime model with the same tools | Yes | An all-Google stack (Gemini + Lyria). Less control over false barge-ins |
| `direct` | STT only. Each finished sentence becomes the Lyria prompt | No | Lowest latency and the simplest mental model |

---

## Quickstart

**Prerequisites:**
- macOS or Linux, with [`uv`](https://docs.astral.sh/uv/)
- the [LiveKit CLI](https://docs.livekit.io/home/cli/) 2.18.8 or later (`brew install livekit-cli`)
- a [LiveKit Cloud](https://cloud.livekit.io) project
- a **Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey)**. Lyria RealTime
  works only with Gemini API keys, not Vertex AI.

```bash
git clone https://github.com/livekit-examples/dial-in-dj.git
cd dial-in-dj
cp .env.example .env.local      # then fill in the values (see below)
uv sync
uv run python src/agent.py download-files   # turn-detector and noise-cancellation models
```

Fill in `.env.local` (it is gitignored):

| Variable | Where to get it |
|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | `lk cloud auth`, then `lk project list`, or Cloud dashboard → Settings → Keys |
| `GOOGLE_API_KEY` | Google AI Studio → Get API key |
| `DJ_PIPELINE` | `pipeline`, `gemini` or `direct` |

### 1. Check that Lyria works on its own

```bash
uv run scripts/lyria_smoke.py            # writes out/lyria_smoke.wav (techno → bossa nova)
```

Both `v1alpha` (the default) and `v1beta` worked when tested; pass `--api-version v1beta`
to try the other one.

### 2. Run the agent and talk to it in a browser

```bash
lk agent dev
```

Then either open the **Agent Console** in the LiveKit Cloud dashboard (Agents → Console,
agent `dial-in-dj`), or start the demo page:

```bash
uv run web/server.py      # http://localhost:8787, uses .env.local
```

Use headphones so the music doesn't leak into your mic. Try:

- "Play some funky disco."
- "Add a saxophone." / "Give me a trumpet solo."
- "Faster." or "Slow it way down."
- "Make it sadder." / "Darker and more minimal."
- "Drop the drums." … "Bring them back."
- "Pause." / "Resume."

### 3. Call it on the phone

Deploy the agent, create the dispatch rule and buy a LiveKit Phone Number. All the steps
are in [docs/SETUP.md](docs/SETUP.md). To show the number on the demo page, set
`DJ_DEMO_NUMBER`.

---

## Repository layout

```
src/
  agent.py            DJ persona, tools, ducking hooks, entrypoint (agent_name="dial-in-dj")
  dj/
    lyria.py          LyriaDJ: Lyria session, prompt state, crossfades, reconnects, frame stream
    audio.py          stereo→mono downmix, jitter buffer, Ducker (smoothed gain)
    pipelines.py      DJ_PIPELINE switch, noise cancellation (phone vs web)
web/
  server.py           serves the demo page and the API (locally or as a Vercel Python function)
  api/index.py        /api/token (locked-down token endpoint) and /api/config
  index.html, static/ page + livekit-client; static/vis/ holds the visualizers
scripts/
  lyria_smoke.py      standalone Lyria test that writes a WAV and prints latency stats
  call.py             have the DJ call a number (optional, needs outbound calling enabled)
telephony/
  dispatch-rule.json  routes inbound calls to agent "dial-in-dj" in per-caller rooms
tests/                unit tests (audio path, Lyria state, context trimming) and LLM evals
scenarios.yaml        end-to-end simulations: `lk agent simulate text --scenarios scenarios.yaml`
docs/                 architecture, setup, Lyria notes, diagrams
.claude/ .agents/     LiveKit's agent skills for coding assistants
```

## The DJ's tools

| Tool | Triggered by | Lyria effect |
|---|---|---|
| `set_vibe(tags)` | "play some jazz", "something for a road trip" | Replace the prompt mix, with a 3 s crossfade |
| `add_layer(tag, prominence)` | "add a saxophone", "trumpet solo" | Add an instrument at 15/35/50% of the mix (hint/featured/lead) |
| `remove_layer(tag)` | "lose the strings" | Remove a prompt, with a crossfade |
| `set_mood(moods, bpm?)` | "make it sadder", "happier" | Swap mood tags, keep the genre and instruments, optionally change tempo |
| `set_tempo(bpm)` | "faster", "slower", "one-forty" | `bpm` plus `reset_context()` (a short hard transition) |
| `set_energy(density, brightness)` | "more intense", "calmer", "brighter" | `density` / `brightness` |
| `set_key(scale)` | "switch to a minor key" | `scale` plus `reset_context()` |
| `set_mix(mute_drums, mute_bass)` | "drop the drums", "bring the bass back" | `mute_drums` / `mute_bass` |
| `pause_music` / `resume_music` | "pause", "keep going" | `pause()` / `play()` |

## Testing

```bash
uv run pytest tests/test_audio.py tests/test_lyria.py   # fast, offline
uv run pytest                                          # adds LLM evals via LiveKit Inference (needs .env.local)
lk agent simulate text --scenarios scenarios.yaml      # full simulated conversations
uv run ruff format && uv run ruff check                # CI runs these on every PR
```

For interactive debugging without audio, run `lk agent debugger start`, then
`lk agent debugger say "make it faster" --logs`.

Every command sent to Lyria is logged as a structured `lyria_send` record, so
`lk agent logs | grep lyria_` shows exactly what the DJ asked for.

## Deploying

```bash
lk agent create --secrets-file .env.local   # first time: registers the agent, writes livekit.toml
lk agent deploy                             # later deploys
lk agent logs                               # stream logs
lk agent update-secrets --secrets "DJ_PIPELINE=gemini"   # switch modes without code changes
```

The phone number, the dispatch rule, outbound calling and hosting the demo page are covered
in [docs/SETUP.md](docs/SETUP.md).

## Security notes for public deployments

- **The demo page's token endpoint is locked down.** `POST /api/token` always creates a fresh
  private room with a random identity and dispatches only the DJ. It ignores anything the
  client sends, tokens expire after 10 minutes, and it is rate limited per IP. Add a platform
  rate limit (for example a Vercel Firewall rule) for a busy public site.
- **Outbound calling is off by default.** The agent only dials out when both
  `ALLOW_OUTBOUND_CALLS=1` and `SIP_OUTBOUND_TRUNK_ID` are set. Never expose outbound dispatch
  through a public endpoint, because anyone could make it call any number at your expense.
- **Every session costs money.** Each call or browser session uses agent time, LiveKit
  Inference and a Lyria stream. Set `DJ_MAX_SESSION_S` so an idle tab can't hold a session
  forever, and cap your agent deployment's replicas. Lyria RealTime's quotas aren't documented;
  if it can't be reached, the DJ says so and ends the session, and the page shows "The DJ is
  busy" when no agent joins within 20 seconds.
- **Phone numbers end up in room names** (`dj-_<caller>_<random>`). Treat logs and room lists
  as personal data.

## Tuning knobs

All of these are environment variables, so you can change them without editing code.

| Var | Default | Effect |
|---|---|---|
| `LYRIA_PREROLL_S` | `1.0` | Audio buffered before playback starts. Higher is smoother, lower is snappier |
| `LYRIA_MAX_BUFFER_S` | `6.0` | Cap on buffered audio |
| `LYRIA_REPROMPT_KEEP_S` | `2.0` | Buffered audio kept on a new vibe, so changes are heard sooner |
| `LYRIA_GUIDANCE` | `5.0` | How closely Lyria follows the prompts (0–6) |
| `LYRIA_AVOID` | `piano` | Tags pushed away with a negative weight unless the caller asks for them |
| `LYRIA_SESSION_MAX_S` | `540` | Roll the Lyria session over before its reported ~10 minute limit |
| `LYRIA_API_VERSION` | `v1alpha` | Gemini API version for Lyria (`v1beta` also works) |
| `LYRIA_LOG_FILE` | unset | Also append every Lyria command to this JSONL file |
| `DJ_LLM` | `google/gemma-4-31b-it` | LLM for `pipeline` mode (LiveKit Inference model string) |
| `DJ_MAX_CTX_ITEMS` | `12` | Chat history kept per call, to keep each LLM request small |
| `DJ_MAX_SESSION_S` | `0` (no limit) | End each session after this many seconds with a short goodbye. Set it for public deployments |
| `LYRIA_MAX_CONNECT_FAILURES` | `3` | Failed Lyria connects in a row before the DJ apologizes and ends the session |
| `GEMINI_LIVE_MODEL` / `GEMINI_VOICE` | `gemini-3.1-flash-live-preview` / `Puck` | Model and voice for `gemini` mode |

The ducking levels (`DUCK_AGENT_SPEAKING`, `DUCK_USER_SPEAKING`) are constants at the top
of `src/agent.py`.

## Known limitations

- **Phone audio is narrowband mono** (G.711 at about 3.4 kHz, or G.722 at about 7 kHz), so music
  sounds like hold music on a phone. The browser path gets full-band Opus.
- **Music leaks back into the caller's mic**, especially on speakerphone. The agent uses
  LiveKit's background voice cancellation, adaptive interruptions, `min_words=2` and ducking
  to cope with it.
- **Lyria RealTime is an experimental model.** It is instrumental only, may reject some prompts
  (the DJ swaps in an alternative), takes up to about 2 s to react, and needs a few seconds to
  settle after a tempo or key change.
- **Session length:** Lyria sessions are reportedly capped at about 10 minutes, so `LyriaDJ`
  reconnects automatically.

## References

- Lyria RealTime: <https://ai.google.dev/gemini-api/docs/realtime-music-generation>
- Lyria prompt guide: <https://ai.google.dev/gemini-api/docs/lyria-prompt-guide>
- LiveKit Agents: <https://docs.livekit.io/agents/>
- LiveKit Phone Numbers: <https://docs.livekit.io/telephony/start/phone-numbers/>
- Background audio: <https://docs.livekit.io/agents/multimodality/audio/background-audio/>

## License

Apache-2.0, see [LICENSE](LICENSE). Third-party code is listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
