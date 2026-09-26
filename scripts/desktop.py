#!/usr/bin/env python
"""Desktop shell for Gaia Docs Architect.

    python scripts\\desktop.py

Serves the production frontend build and the API from a single local process,
then opens them in a native OS window. No browser, no second terminal, no port
juggling.

Why pywebview rather than Electron/Tauri
----------------------------------------
The application is already a local HTTP server, so a desktop shell only has to
supply a window. pywebview uses the operating system's own webview (WebView2 on
Windows, WebKitGTK on Linux, WKWebView on macOS), which means:

* no second copy of Chromium is downloaded or shipped,
* no JavaScript runtime is added to the distribution, and
* the Python backend needs no new packaging story -- it is the same process.

Electron would bundle a ~200MB browser for a tool that already runs on the
user's machine, and would still have to spawn this same Python server, so all
of the complexity would remain with a large binary on top.

Design notes
------------
* uvicorn runs in a daemon thread. The webview's event loop owns the main
  thread, which is what the GUI toolkit requires.
* The port is chosen by asking the OS for a free one (find_free_port) rather
  than hard-coding 5274, so a running dev server does not block the desktop app
  and two instances can coexist.
* The window only opens once /api/health answers, so the user never sees a
  half-loaded page or a connection-refused error.
* The server thread is a daemon, so closing the window ends the process.
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

#: How long to wait for the API to answer before giving up on the window.
STARTUP_TIMEOUT_SECONDS = 30.0


def log(message: str) -> None:
    """Print to stdout, flushed, so it interleaves correctly with uvicorn."""
    print(message, flush=True)


def find_free_port(preferred: int = 5274) -> int:
    """Return ``preferred`` when it is free, otherwise an OS-assigned port.

    A fixed port would make a second instance fail outright, so a busy port
    falls back to an ephemeral one.

    The probe deliberately does NOT set SO_REUSEADDR. On Windows that option
    lets a socket bind an address another process is actively listening on,
    so the probe would report a busy port as free and the server would then
    fail with WSAEADDRINUSE. Omitting it makes the probe reflect reality: a
    bind that succeeds here is a port we can genuinely take.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for_health(port: int, timeout: float = STARTUP_TIMEOUT_SECONDS) -> bool:
    """Block until ``/api/health`` answers, or the timeout expires."""
    url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 300:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.3)
    return False


def build_frontend() -> bool:
    """Build the frontend if ``frontend/dist`` is missing.

    The desktop app serves static files, so there is nothing to show until the
    bundle exists. Building here (rather than failing) means a fresh clone
    works with one command.
    """
    if (FRONTEND_DIST / "index.html").is_file():
        return True

    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    log("No frontend build found. Running 'npm run build' (this takes a minute)...")
    try:
        result = subprocess.run(
            [npm, "run", "build"],
            cwd=REPO_ROOT / "frontend",
            check=False,
        )
    except FileNotFoundError:
        log("ERROR: npm was not found. Install Node.js, or run 'npm run build' in frontend/.")
        return False

    if result.returncode != 0 or not (FRONTEND_DIST / "index.html").is_file():
        log("ERROR: the frontend build failed. See the output above.")
        return False
    return True


def mount_frontend():
    """Mount the built frontend at the root and return the FastAPI app.

    Serving the bundle from the same origin as /api means the desktop app is
    same-origin exactly like the dev server's proxy: no CORS configuration and
    no second port. Returns the app so the caller can start it.
    """
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    # The working directory must be backend/ so the relative .env path in
    # SettingsConfigDict resolves to backend/.env.
    os.chdir(BACKEND_DIR)

    from fastapi.staticfiles import StaticFiles
    from app.main import app as fastapi_app

    # Mounted last so it cannot shadow the /api routes registered above it.
    # html=True serves index.html for the root and for unknown paths, which is
    # what a single-page app needs.
    fastapi_app.mount(
        "/",
        StaticFiles(directory=str(FRONTEND_DIST), html=True),
        name="desktop",
    )
    return fastapi_app


def run_browser(fastapi_app, port: int) -> int:
    """Serve the app and open it in the default browser, blocking until stopped.

    The escape hatch for machines with no GUI toolkit (a server, a container,
    an SSH session), and for anyone who simply prefers their own browser.
    """
    import uvicorn

    log(f"Serving http://127.0.0.1:{port} -- open it in your browser.")
    try:
        uvicorn.run(fastapi_app, host="127.0.0.1", port=port, log_level="warning")
    except KeyboardInterrupt:
        log("\nStopped.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Open Gaia Docs Architect in a desktop window.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5274,
        help="Preferred port; a free one is chosen automatically if taken.",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Open in the default browser instead of a desktop window.",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip building the frontend; fail if frontend/dist is missing.",
    )
    args = parser.parse_args()

    if not args.no_build and not build_frontend():
        return 1
    if not (FRONTEND_DIST / "index.html").is_file():
        log("ERROR: frontend/dist/index.html is missing. Run 'npm run build' in frontend/.")
        return 1

    port = find_free_port(args.port)
    url = f"http://127.0.0.1:{port}"

    if args.browser:
        return run_browser(mount_frontend(), port)

    try:
        import webview
    except ImportError:
        log("ERROR: pywebview is not installed, so no window can be opened.")
        log("Install it with:")
        log("  .venv\\Scripts\\python -m pip install -e .\\backend[desktop]")
        log("Or run with --browser to use your normal browser instead:")
        log("  .venv\\Scripts\\python scripts\\desktop.py --browser")
        return 1

    # The frontend must be mounted before uvicorn starts, and it must be the
    # same app object the server runs, so mount first and then start it.
    fastapi_app = mount_frontend()

    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            fastapi_app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            # No reload: a reloader would spawn a process this launcher does
            # not own, and would survive the window closing.
            reload=False,
        )
    )
    threading.Thread(target=server.run, name="gaia-api", daemon=True).start()

    log("Starting the API...")
    if not wait_for_health(port):
        log(f"ERROR: the API did not become healthy on port {port}.")
        log("Check backend/.env -- APP_ENV must be 'development' for local work.")
        return 1
    log("API is ready.")

    log(f"Opening the desktop window at {url}")
    try:
        webview.create_window(
            "Gaia Docs Architect",
            url,
            width=1440,
            height=900,
            min_size=(1024, 700),
        )
        # Blocks until the window is closed, then returns; the daemon server
        # thread ends with the process.
        webview.start()
    except Exception as exc:  # pragma: no cover - GUI/runtime failure
        log(f"ERROR: could not open the desktop window: {exc}")
        log(f"The app is still running at {url} -- open it in a browser.")
        return 1

    log("Closed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
