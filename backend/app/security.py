"""Authentication for a single-user, self-hosted deployment.

There is one local account, configured entirely through the environment. No
registration, no password reset, no third-party identity provider: the operator
who deploys the app is the user.

Two credentials are accepted, both checked server-side on every request:

* a **session cookie** issued by ``POST /api/auth/login`` after a successful
  password check, and
* a **bearer token** (``Authorization: Bearer <token>``) for CLI and scripts.

Design notes
------------
* Passwords are stored as PBKDF2-HMAC-SHA256 hashes with a per-user random
  salt. Verification is constant-time and always runs a full derivation, so a
  wrong username and a wrong password take the same time.
* Session cookies are opaque, HMAC-signed values carrying an expiry. There is
  no server-side session store, so a stateless deployment behind a reverse
  proxy needs no shared session memory.
* The HMAC comparison uses :func:`hmac.compare_digest`, never ``==``.
* ``auth_enabled=False`` is honoured only in development, and the startup
  validation in :mod:`app.config` rejects it in production.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import os
import secrets
import time

from app.config import Settings

logger = logging.getLogger("apollo.auth")

#: Bumped if the cookie format ever changes, so old cookies are rejected
#: instead of mis-parsed.
SESSION_TOKEN_VERSION = "v1"

#: Cost factor for new password hashes. 600k iterations of PBKDF2-HMAC-SHA256
#: is a few tens of milliseconds on a typical server.
PBKDF2_ITERATIONS = 600_000

HASH_SCHEME = "pbkdf2_sha256"

#: Longest cookie/header value we will even look at, to bound the work an
#: unauthenticated caller can cause.
MAX_CREDENTIAL_LENGTH = 4096


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def _b64encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64decode(text: str) -> bytes:
    # validate=True turns a malformed cookie into an error rather than
    # silently decoding to garbage.
    return base64.b64decode(text.encode("ascii"), validate=True)


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Hash a password for storage.

    Returns ``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>``.
    """
    if not password:
        raise ValueError("Refusing to hash an empty password.")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{HASH_SCHEME}${iterations}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    """Check a password against a stored hash, in constant time.

    Returns False for any malformed or unsupported hash rather than raising,
    so a corrupted configuration cannot turn into an authentication bypass.
    """
    try:
        scheme, iterations_raw, salt_b64, hash_b64 = encoded.split("$", 3)
        if scheme != HASH_SCHEME:
            return False
        iterations = int(iterations_raw)
        if iterations < 1:
            return False
        salt = _b64decode(salt_b64)
        expected = _b64decode(hash_b64)
    except (ValueError, binascii.Error):
        return False

    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


def check_credentials(settings: Settings, username: str, password: str) -> bool:
    """Validate a username/password pair against the configured account."""
    expected_user = settings.auth_username or ""
    # Compare the username in constant time too, and keep the password work
    # unconditional so response timing does not reveal which field was wrong.
    username_ok = hmac.compare_digest(
        (username or "").encode("utf-8"), expected_user.encode("utf-8")
    )

    password_ok = False
    if settings.auth_password_hash:
        password_ok = verify_password(password or "", settings.auth_password_hash)
    elif settings.auth_password is not None:
        password_ok = hmac.compare_digest(
            (password or "").encode("utf-8"),
            settings.auth_password.encode("utf-8"),
        )

    return username_ok and password_ok


def check_api_token(settings: Settings, token: str) -> bool:
    """Validate a bearer token, if one is configured."""
    expected = settings.auth_api_token
    if not expected or not token:
        return False
    return hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))



# ---------------------------------------------------------------------------
# Session tokens
# ---------------------------------------------------------------------------


def _sign(payload: str, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).digest()
    return _b64encode(digest)


def create_session_token(settings: Settings, username: str) -> str:
    """Mint a signed, expiring session token.

    Format: ``<version>.<username_b64>.<expiry>.<signature_b64>``. The
    signature covers everything before it, so none of it can be tampered with.
    """
    secret = settings.session_secret
    if not secret:
        raise RuntimeError("SESSION_SECRET is required to issue session cookies.")

    expires_at = int(time.time()) + int(settings.session_max_age_seconds)
    payload = ".".join(
        [
            SESSION_TOKEN_VERSION,
            _b64encode(username.encode("utf-8")),
            str(expires_at),
        ]
    )
    return f"{payload}.{_sign(payload, secret)}"


def verify_session_token(settings: Settings, token: str) -> str | None:
    """Return the username if the token is valid and unexpired, else None."""
    secret = settings.session_secret
    if not secret or not token or len(token) > MAX_CREDENTIAL_LENGTH:
        return None

    try:
        version, username_b64, expires_raw, signature = token.split(".", 3)
    except ValueError:
        return None

    if version != SESSION_TOKEN_VERSION:
        return None

    payload = f"{version}.{username_b64}.{expires_raw}"
    # Check the signature before trusting anything else in the token.
    if not hmac.compare_digest(
        signature.encode("utf-8"), _sign(payload, secret).encode("utf-8")
    ):
        return None

    try:
        expires_at = int(expires_raw)
        username = _b64decode(username_b64).decode("utf-8")
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None

    if expires_at <= int(time.time()):
        return None

    # A token is only ever valid for the account it was minted for.
    expected_user = settings.auth_username or ""
    if not hmac.compare_digest(username.encode("utf-8"), expected_user.encode("utf-8")):
        return None

    return username


# ---------------------------------------------------------------------------
# Request authentication
# ---------------------------------------------------------------------------


def extract_bearer_token(authorization: str | None) -> str | None:
    """Pull the token out of an ``Authorization: Bearer ...`` header."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    token = value.strip()
    if len(token) > MAX_CREDENTIAL_LENGTH:
        return None
    return token


def authenticate_request(settings: Settings, request) -> str | None:
    """Identify the caller. Returns the username, or None if unauthenticated.

    A bearer token is accepted first so that scripted clients never have to
    manage a cookie jar; the session cookie is the browser path.
    """
    token = extract_bearer_token(request.headers.get("authorization"))
    if token is not None and check_api_token(settings, token):
        return settings.auth_username or "api-token"

    cookie = request.cookies.get(settings.auth_cookie_name)
    if cookie:
        username = verify_session_token(settings, cookie)
        if username is not None:
            return username

    return None


def generate_session_secret(nbytes: int = 32) -> str:
    """A URL-safe secret suitable for ``SESSION_SECRET``."""
    return secrets.token_urlsafe(nbytes)


def main() -> None:
    """``python -m app.security`` -- secret generation helpers.

    Deliberately separate from startup, so a secret can be produced without
    booting the app or touching the database.
    """
    import argparse
    import getpass
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m app.security",
        description="Generate the secrets a production deployment needs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    secret_cmd = sub.add_parser("generate-secret", help="Print a SESSION_SECRET.")
    secret_cmd.add_argument("--bytes", type=int, default=32)

    hash_cmd = sub.add_parser(
        "hash-password", help="Print an AUTH_PASSWORD_HASH for a password."
    )
    hash_cmd.add_argument(
        "--password",
        help="Read interactively when omitted, so it never lands in shell history.",
    )
    hash_cmd.add_argument("--iterations", type=int, default=PBKDF2_ITERATIONS)

    args = parser.parse_args()

    if args.command == "generate-secret":
        print(generate_session_secret(args.bytes))
        return

    password = args.password or getpass.getpass("Password: ")
    if not password:
        print("Refusing to hash an empty password.", file=sys.stderr)
        raise SystemExit(1)
    print(hash_password(password, iterations=args.iterations))


if __name__ == "__main__":  # pragma: no cover - operator convenience
    main()
