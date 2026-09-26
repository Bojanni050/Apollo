"""End-to-end check against a REAL uvicorn server running in production mode.

Unit tests use an in-memory database and an in-process client. This starts an
actual server so the middleware stack, the cookie round-trip over the wire and
the startup validation are all exercised as an operator would meet them.

Run with the environment already configured (see tests/verify_startup.py):

    python -m tests.verify_live
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("VERIFY_URL", "http://127.0.0.1:8123")
USERNAME = os.environ["VERIFY_USERNAME"]
PASSWORD = os.environ["VERIFY_PASSWORD"]
VERBOSE = os.environ.get("VERIFY_VERBOSE") == "1"


def call(method: str, path: str, body=None, headers=None) -> tuple[int, dict, dict]:
    """Return (status, parsed_body, headers). Never raises on 4xx/5xx."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read()
            parsed = json.loads(raw) if raw else {}
            return response.status, parsed, dict(response.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        parsed = json.loads(raw) if raw else {}
        return exc.code, parsed, dict(exc.headers)


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"{'PASS' if condition else 'FAIL'}  {label}{(' -- ' + detail) if detail else ''}")
    return condition


def main() -> int:
    # Wait for the server to accept connections.
    for _ in range(40):
        try:
            urllib.request.urlopen(f"{BASE}/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.25)
    else:
        print("server did not come up", file=sys.stderr)
        return 1

    results = []

    status, body, _ = call("GET", "/api/health")
    results.append(check("health is public", status == 200 and body["status"] == "ok"))

    status, _, headers = call("GET", "/api/workspaces")
    results.append(check("unauthenticated read is refused", status == 401,
                          f"status={status}"))
    challenge = next(
        (v for k, v in headers.items() if k.lower() == "www-authenticate"), ""
    )
    results.append(check("401 carries WWW-Authenticate", challenge == "Bearer",
                         f"value={challenge!r}"))

    status, _, _ = call("POST", "/api/workspaces", {"name": "Sneaky"})
    results.append(check("unauthenticated write is refused", status == 401,
                          f"status={status}"))

    status, _, _ = call("POST", "/api/auth/login",
                        {"username": USERNAME, "password": "wrong"})
    results.append(check("wrong password is refused", status == 401, f"status={status}"))

    # Real login, keeping the Set-Cookie value.
    status, body, headers = call("POST", "/api/auth/login",
                                 {"username": USERNAME, "password": PASSWORD})
    results.append(check("login succeeds", status == 200 and body.get("authenticated"),
                         f"status={status}"))
    if VERBOSE:
        print("  login response headers:", headers)
    cookie_header = next(
        (v for k, v in headers.items() if k.lower() == "set-cookie"), ""
    )
    results.append(check("session cookie is HttpOnly",
                         "httponly" in cookie_header.lower()))
    results.append(check("session cookie is SameSite=Strict",
                         "samesite=strict" in cookie_header.lower()))
    results.append(check("session cookie is Secure in production",
                         "secure" in cookie_header.lower()))

    cookie = cookie_header.split(";")[0]
    auth = {"Cookie": cookie}

    status, _, _ = call("GET", "/api/workspaces", headers=auth)
    results.append(check("session cookie grants access", status == 200, f"status={status}"))

    status, body, _ = call("POST", "/api/workspaces", {"name": "Live"}, headers=auth)
    results.append(check("authenticated write succeeds", status == 201, f"status={status}"))
    workspace_id = body.get("id")

    # A repository outside the configured roots must be refused.
    status, body, _ = call(
        "POST", f"/api/workspaces/{workspace_id}/repositories",
        {"name": "outside", "local_path": "C:/Windows/System32", "kind": "documentation"},
        headers=auth,
    )
    results.append(check("workspace outside allowed root is refused", status == 400,
                         f"status={status}"))

    status, _, _ = call("POST", "/api/auth/logout", headers=auth)
    results.append(check("logout succeeds", status == 204, f"status={status}"))

    passed = sum(results)
    print(f"\n{passed}/{len(results)} live checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
