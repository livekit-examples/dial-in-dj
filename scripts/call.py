"""Have the Dial-in DJ call a phone number.

    uv run scripts/call.py +14155551234

This dispatches the `dial-in-dj` agent into a fresh room with the number in the
job metadata. The agent then dials out through SIP_OUTBOUND_TRUNK_ID and starts
the music when the callee picks up. Outbound calling is off by default: set both
ALLOW_OUTBOUND_CALLS=1 and SIP_OUTBOUND_TRUNK_ID as agent secrets to enable it.
It uses your own LiveKit API credentials; never expose it as a public endpoint.
Equivalent CLI:

    lk dispatch create --new-room --agent-name dial-in-dj \\
        --metadata '{"phone_number": "+14155551234"}'
"""

import asyncio
import json
import re
import secrets
import sys

from dotenv import load_dotenv
from livekit import api

load_dotenv(".env.local")


async def main(phone_number: str) -> None:
    room = f"dj-out-{secrets.token_hex(4)}"
    async with api.LiveKitAPI() as lkapi:
        dispatch = await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name="dial-in-dj",
                room=room,
                metadata=json.dumps({"phone_number": phone_number}),
            )
        )
    print(f"dispatched {dispatch.id} to room {room}, calling {phone_number}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not re.fullmatch(r"\+\d{8,15}", sys.argv[1]):
        sys.exit("usage: uv run scripts/call.py +<E.164 number>, e.g. +14155551234")
    asyncio.run(main(sys.argv[1]))
