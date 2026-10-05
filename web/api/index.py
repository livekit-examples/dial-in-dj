"""Dial-in DJ demo API. Runs as a Vercel Python function and under web/server.py.

Endpoints (credentials stay server side):
    GET  /api/config   the demo phone number, if DJ_DEMO_NUMBER is set
    POST /api/token    standard LiveKit token endpoint; dispatches the DJ

The token endpoint is safe to expose publicly: the server alone chooses the
room, the identity and which agent is dispatched. Request fields such as
room_name, participant_identity or room_config are ignored, so a visitor can
only ever get a fresh private room with the DJ in it. Tokens are short-lived
and the endpoint is rate limited per client IP.

Needs LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET in the environment.
"""

import logging
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta

from livekit import api as lk
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger("dial-in-dj.web")

AGENT_NAME = os.getenv("DJ_AGENT_NAME", "dial-in-dj")
# Shown on the page as "Call the DJ". Leave unset for a browser-only demo.
DEMO_NUMBER = os.getenv("DJ_DEMO_NUMBER") or None
# The token is only used to join right away, so it can expire quickly.
TOKEN_TTL = timedelta(minutes=10)
# Per-IP limit on new sessions. In-memory, so on serverless hosts it applies per
# instance: put a platform rate limit (e.g. a Vercel Firewall rule) in front of
# a busy public deployment.
RATE_LIMIT = int(os.getenv("DJ_TOKENS_PER_IP_PER_HOUR", "20"))
_recent: dict[str, deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _allow(ip: str, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    hits = _recent[ip]
    while hits and now - hits[0] > 3600:
        hits.popleft()
    if len(hits) >= RATE_LIMIT:
        return False
    hits.append(now)
    return True


async def config(_: Request) -> JSONResponse:
    return JSONResponse({"phone_number": DEMO_NUMBER})


async def token(request: Request) -> JSONResponse:
    # Same response shape as LiveKit's standard token endpoint, so client SDKs'
    # TokenSource.endpoint("/api/token") works:
    # https://docs.livekit.io/frontends/build/authentication/endpoint/
    if not _allow(_client_ip(request)):
        return JSONResponse(
            {"error": "Too many sessions from your network. Try again later."},
            status_code=429,
        )
    room = f"dj-web-{secrets.token_hex(6)}"
    identity = f"web-{secrets.token_hex(4)}"
    tok = (
        lk.AccessToken()
        .with_identity(identity)
        .with_ttl(TOKEN_TTL)
        .with_grants(
            lk.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_publish_data=True,
                can_subscribe=True,
            )
        )
        .with_room_config(
            lk.RoomConfiguration(agents=[lk.RoomAgentDispatch(agent_name=AGENT_NAME)])
        )
    )
    return JSONResponse(
        {"server_url": os.environ["LIVEKIT_URL"], "participant_token": tok.to_jwt()},
        status_code=201,
    )


async def on_error(_: Request, exc: Exception) -> JSONResponse:
    # Details go to the server log only; the client gets a generic message.
    logger.exception("request failed", exc_info=exc)
    if isinstance(exc, KeyError):
        return JSONResponse(
            {"error": "The server is not configured yet."}, status_code=500
        )
    return JSONResponse({"error": "Something went wrong."}, status_code=500)


routes = [
    Route("/api/config", config, methods=["GET"]),
    Route("/api/token", token, methods=["POST"]),
]

exception_handlers = {Exception: on_error}

app = Starlette(routes=routes, exception_handlers=exception_handlers)
