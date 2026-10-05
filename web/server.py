"""Demo page for Dial-in DJ: the visualizer, "Talk in browser", and the dial-in number.

    uv run web/server.py            # then open http://localhost:8787

Serves index.html, static/ and the API in api/index.py. Vercel runs this same
module as the project's Python entrypoint (it imports `app`, not main()).
Locally, credentials come from ../.env.local; on Vercel, from project env vars.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT.parent / ".env.local")
sys.path.insert(0, str(ROOT))

from api.index import exception_handlers, routes  # noqa: E402


async def index(_) -> FileResponse:
    return FileResponse(ROOT / "index.html")


async def favicon(_) -> FileResponse:
    # Browsers ask for /favicon.ico on their own; index.html also links it.
    return FileResponse(ROOT / "static" / "favicon.ico")


async def no_cache(request, call_next):
    # Local demo: always serve the latest HTML/JS/CSS after an edit.
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


app = Starlette(
    routes=[
        Route("/", index),
        Route("/favicon.ico", favicon),
        *routes,
        Mount("/static", StaticFiles(directory=ROOT / "static")),
    ],
    middleware=[Middleware(BaseHTTPMiddleware, dispatch=no_cache)],
    exception_handlers=exception_handlers,
)


def main() -> None:
    import uvicorn  # local only; Vercel serves `app` itself

    port = int(os.getenv("PORT", "8787"))
    print(f"Dial-in DJ web: http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
