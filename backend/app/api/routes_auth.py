"""Authentication endpoints.

``POST /api/auth/login`` is the only route that can create a session. It is
public by necessity -- but it reveals nothing to an attacker beyond whether a
guess was right, and rate limiting is explicitly out of scope for this phase.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse

from app.config import settings
from app.schemas import AuthStatusOut, LoginIn, LoginOut
from app.security import check_credentials, create_session_token

logger = logging.getLogger("gaia_docs_architect.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.session_max_age_seconds,
        # HttpOnly keeps the token away from JavaScript, so an XSS bug cannot
        # exfiltrate the session.
        httponly=True,
        secure=settings.cookie_secure,
        # Strict: the cookie is not sent on cross-site requests, which removes
        # the classic CSRF vector for a self-hosted app on one origin.
        samesite="strict",
        path="/",
    )


@router.post("/login", response_model=LoginOut)
def login(payload: LoginIn, response: Response) -> LoginOut:
    """Exchange a username and password for a session cookie."""
    if not settings.auth_enabled:
        # In development with auth off there is nothing to log into.
        return LoginOut(authenticated=True, username="development", auth_required=False)

    if not check_credentials(settings, payload.username, payload.password):
        # One message for both wrong-username and wrong-password, so the
        # response never confirms that a username exists.
        logger.info("Rejected login attempt for %r", payload.username[:64])
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Invalid username or password."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    username = settings.auth_username or ""
    _set_session_cookie(response, create_session_token(settings, username))
    return LoginOut(authenticated=True, username=username, auth_required=True)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    """Clear the session cookie.

    Stateless tokens cannot be revoked server-side, so the cookie is deleted
    and its expiry shortened. Shortening the expiry is what actually stops a
    stolen token from being replayed by a client that ignores the deletion.
    """
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )


@router.get("/status", response_model=AuthStatusOut)
def auth_status(request: Request) -> AuthStatusOut:
    """Whether the caller is authenticated, so the UI can show a login form."""
    from app.security import authenticate_request

    username = None
    if settings.auth_enabled:
        username = authenticate_request(settings, request)

    return AuthStatusOut(
        auth_required=settings.auth_enabled,
        authenticated=username is not None,
        username=username,
    )