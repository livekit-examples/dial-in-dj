# Setup: LiveKit Cloud, phone number, deploy

All commands run from the repo root with LiveKit CLI 2.18.8 or later (`lk --version`).

## 0. Auth and project
```bash
lk cloud auth                  # opens the browser and links the CLI to your LiveKit Cloud project
lk project list                # confirm the default project is the one you want
```
Local dev credentials go in `.env.local` (see `.env.example`). It is gitignored.

## 1. Deploy the agent
```bash
lk agent create --secrets-file .env.local   # registers "dial-in-dj", writes livekit.toml, builds, deploys
lk agent status
lk agent logs
```
`lk agent create` writes a `livekit.toml` that points at your own Cloud agent. It is
gitignored in this repo; keep it local (or commit it in your own fork).

Later deploys: `lk agent deploy`. Change a secret or mode with
`lk agent update-secrets --secrets "DJ_PIPELINE=gemini"` (this triggers a rolling restart).
LiveKit Cloud injects the `LIVEKIT_*` values automatically.

## 2. Dispatch rule
```bash
lk sip dispatch create telephony/dispatch-rule.json   # prints the dispatch rule ID
lk sip dispatch list
```
Each caller gets their own room (`dj-_<caller>_<random>`), and the agent `dial-in-dj` is
explicitly dispatched into it. Room names contain the caller's phone number, so redact them
in screenshots and logs you share.

## 3. Phone number (LiveKit Phone Numbers, US only)
```bash
lk number search --country-code US --area-code 415     # choose any area code
lk number purchase --numbers +1415XXXXXXX --sip-dispatch-rule-id <DISPATCH_RULE_ID>
lk number list
# If you bought it before creating the rule:
lk number update --id <PHONE_NUMBER_ID> --sip-dispatch-rule-id <DISPATCH_RULE_ID>
```
Anyone who has the number can call it, and every call uses agent, inference and Lyria time.
Think about who you share it with.

## 4. Test call
1. Stop any local `lk agent dev` against the same project; otherwise your laptop may take the call.
2. Call the number. You should hear the greeting, then music within a few seconds.
3. Troubleshooting:
   - No answer: run `lk sip dispatch list` and check the number has a rule.
   - The agent never joins: check `lk agent status` and that the `agent_name` matches.
   - Silence: search `lk agent logs` for `lyria` and check `GOOGLE_API_KEY` is set as a secret.

## 5. Local workers and a deployed agent
Any `lk agent dev` running against a project registers as `dial-in-dj` too and can take
real calls with whatever code is on that laptop. Use a separate project (or agent name)
for development, or stop local workers before phone tests.

## 6. Outbound calls (optional: the DJ calls you)
Outbound calling is **off by default**. A deployment that can dial arbitrary numbers is a
toll-fraud risk, so it has to be enabled on purpose. When the agent is dispatched with
`{"phone_number": "+1..."}` in the job metadata *and* outbound calling is enabled, it dials
that number, waits for an answer, then starts the DJ (Lyria warms up while it rings).

LiveKit Phone Numbers are inbound-only, so outbound needs a SIP trunk from a provider
(Twilio, Telnyx, Plivo, ...):
1. Create an elastic SIP trunk and a number with the provider (see the provider quickstarts
   at https://docs.livekit.io/telephony/start/sip-trunk-setup/).
2. `lk sip outbound create outbound-trunk.json` with the provider's address, number and credentials.
3. `lk agent update-secrets --secrets "SIP_OUTBOUND_TRUNK_ID=ST_xxxx,ALLOW_OUTBOUND_CALLS=1"`.

Then, with your own LiveKit credentials:
```bash
uv run scripts/call.py +14155551234
# or: lk dispatch create --new-room --agent-name dial-in-dj --metadata '{"phone_number": "+14155551234"}'
```
Never expose outbound dispatch through a public endpoint. The demo page in `web/` does not.

## 7. Browser testing without the demo page
The Agent Console in the LiveKit Cloud dashboard (Agents → your agent → Console) works
against any agent in your project, with full-band audio and no code.

## 8. Demo page on Vercel (or any Python host)
The project's Root Directory is `web`. Vercel detects Python and runs `web/server.py`
as one function that serves the page, `static/` and `/api/*` (routes in
`web/api/index.py`). `web/requirements.txt` lists the dependencies, and
`web/vercel.json` selects the Python framework.
```bash
cd web && npx vercel deploy --prod
```

`POST /api/token` follows LiveKit's [standard token endpoint](https://docs.livekit.io/frontends/build/authentication/endpoint/)
response format. The server always creates a fresh private `dj-web-*` room, a random
identity and a dispatch of `dial-in-dj`; anything the client sends is ignored. Tokens
expire after 10 minutes and each client IP gets `DJ_TOKENS_PER_IP_PER_HOUR` sessions
(default 20). That limit is in memory, so on serverless hosts it applies per instance:
add a platform rate limit (for example a Vercel Firewall rule) for a busy public site.

Set these under Settings → Environment Variables, then redeploy:

| Var | Value |
|---|---|
| `LIVEKIT_URL` | `wss://<project>.livekit.cloud` |
| `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | Cloud dashboard → Settings → Keys |
| `DJ_DEMO_NUMBER` | optional: the number to show as "Call the DJ". Unset = browser only |
| `DJ_AGENT_NAME` | optional, default `dial-in-dj` |
| `DJ_TOKENS_PER_IP_PER_HOUR` | optional, default `20` |

Check it: `curl -XPOST https://<your-deployment>/api/token -d '{}'` should return 201
with `server_url` and `participant_token`.
