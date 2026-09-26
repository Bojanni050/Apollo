"""Static frontend serving, for the desktop build.

Development never uses this: `npm run dev` serves the frontend through Vite,
which proxies ``/api`` to this server. The desktop build has no Vite, so this
module serves the production bundle from the same process -- which also makes
the webview same-origin with the API, so no CORS configuration is needed and the
session cookie behaves like an ordinary same-origin cookie.

Usage
-----
    python -m app.serve            # uvicorn on 127.0.0.1:5274
    python -m app.serve --port 0   # let the OS pick a free port

Why a module rather than a flag on the app
------------------------------------------
Mounting is a one-way, process-wide change to the ASGI application, so it must
not happen merely because ``app.main`` was imported: a test or a CLI that
imports the app must not silently start serving files. Keeping it in an
explicit entry point makes the behaviour opt-in and obvious.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fastapi.staticfiles import StaticFiles

#: Repository root, so this works regardless of the current directory.
BACKEND_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"


def build_app(dist: Path | None = None):
    """Return the FastAPI app with the built frontend mounted at the root.

    Raises a clear error when the bundle is missing, because the resulting 404s
    on every asset are otherwise very hard to diagnose.
    """
    dist = dist or FRONTEND_DIST

    if not (dist / "index.html").is_file():
        raise SystemExit(
            f"No frontend build at {dist}.\n"
            "Build it first:  npm --prefix frontend run build"
        )

    # Imported here, not at module scope, so that importing this module does not
    # require a database connection or a valid configuration.
    from app.main import app

    # Mounted last so it cannot shadow the /api routes: Starlette matches routes
    # in registration order, and the API routers were added first.
    # html=True serves index.html for the root and for unknown paths, which is
    # what a single-page app needs for client-side routing.
    app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")
    return app


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.serve",
        description="Serve the API and the built frontend on one port.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5274)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
