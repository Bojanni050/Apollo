"""Tests for authentication, workspace path restrictions and CORS.

The properties under test are mostly negative: an anonymous caller must not
reach workspace data, an unauthorized write must not touch the filesystem, and
a repository outside the configured roots must not be registrable. Each of these
is a boundary that, once wrong, is silently exploitable.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import DEV_CORS_ORIGINS, SecurityConfigurationError, Settings
from app.security import (
    check_api_token,
    check_credentials,
    create_session_token,
    hash_password,
    verify_password,
    verify_session_token,
)
from app.services.paths import PathSecurityError, assert_authorized_root

TEST_USERNAME = "operator"
TEST_PASSWORD = "correct horse battery staple"
TEST_SECRET = "s" * 48

#: Security variables the test session sets for the shared client. Any test that
#: builds its own ``Settings`` must not inherit them, or it would silently
#: construct a development configuration while claiming to test production.
_SECURITY_ENV_VARS = (
    "APP_ENV",
    "AUTH_ENABLED",
    "AUTH_USERNAME",
    "AUTH_PASSWORD",
    "AUTH_PASSWORD_HASH",
    "AUTH_API_TOKEN",
    "SESSION_SECRET",
    "CORS_ORIGINS",
    "ALLOWED_WORKSPACE_ROOTS",
    "ALLOW_UNRESTRICTED_WORKSPACE_ROOTS",
)


class _IsolatedEnv:
    """Temporarily remove the security environment variables.

    ``pydantic-settings`` reads ``os.environ`` for every field, and init
    keywords only take precedence over it -- so a test asserting on production
    defaults must actually clear the environment first.
    """

    def __enter__(self) -> "_IsolatedEnv":
        self._saved = {k: os.environ.pop(k) for k in _SECURITY_ENV_VARS
                       if k in os.environ}
        return self

    def __exit__(self, *exc_info) -> None:
        for key, value in self._saved.items():
            os.environ[key] = value


def isolated_settings(**overrides) -> Settings:
    """Build a Settings object that ignores the test session's environment."""
    with _IsolatedEnv():
        return Settings(_env_file=None, **overrides)


def prod_settings(**overrides) -> Settings:
    """A minimal valid production configuration, with optional overrides."""
    base = {
        "app_env": "production",
        "auth_enabled": True,
        "auth_username": TEST_USERNAME,
        "auth_password": TEST_PASSWORD,
        "session_secret": TEST_SECRET,
        "cors_origins": ["https://docs.example.com"],
        "allowed_workspace_roots": ["/srv/docs"],
    }
    base.update(overrides)
    return isolated_settings(**base)


def login(client: TestClient) -> None:
    """Establish a session with the test credentials."""
    response = client.post(
        "/api/auth/login",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200, response.text


@pytest.fixture()
def secured_client(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """The shared client, but with authentication switched on.

    The rest of the suite runs unauthenticated for convenience; this fixture
    flips the real settings object that the middleware reads.
    """
    from app.config import settings as global_settings

    monkeypatch.setattr(global_settings, "auth_enabled", True)
    monkeypatch.setattr(global_settings, "auth_username", TEST_USERNAME)
    monkeypatch.setattr(global_settings, "auth_password", TEST_PASSWORD)
    monkeypatch.setattr(global_settings, "auth_password_hash", None)
    monkeypatch.setattr(global_settings, "session_secret", TEST_SECRET)
    monkeypatch.setattr(global_settings, "auth_api_token", None)
    return client


# ---------------------------------------------------------------------------
# 1. Unauthenticated API request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/workspaces"),
        ("post", "/api/workspaces"),
        ("get", "/api/workspaces/1"),
        ("patch", "/api/workspaces/1"),
        ("delete", "/api/workspaces/1"),
        ("post", "/api/workspaces/1/repositories"),
        ("get", "/api/workspaces/1/repositories/1/tree"),
        ("get", "/api/workspaces/1/repositories/1/document"),
        ("get", "/api/workspaces/1/repositories/1/search"),
        ("get", "/api/workspaces/1/repositories/1/git"),
        ("get", "/api/workspaces/1/proposals"),
        ("post", "/api/workspaces/1/proposals/edit"),
        ("post", "/api/workspaces/1/proposals/1/accept"),
        ("get", "/api/workspaces/1/chat/status"),
        ("get", "/api/workspaces/1/conversations"),
        ("post", "/api/workspaces/1/conversations/1/messages"),
        ("get", "/api/workspaces/1/inventory/runs"),
        ("post", "/api/workspaces/1/inventory/runs/1/apply"),
    ],
)
def test_unauthenticated_request_is_rejected(secured_client: TestClient, method, path) -> None:
    """Every data, write and AI endpoint refuses an anonymous caller."""
    response = getattr(secured_client, method)(path)
    assert response.status_code == 401, f"{method.upper()} {path} was not protected"
    assert response.json()["detail"] == "Authentication required."
    assert response.headers["www-authenticate"] == "Bearer"


def test_health_is_public(secured_client: TestClient) -> None:
    """Health stays reachable for probes; it exposes no workspace data."""
    assert secured_client.get("/api/health").json()["status"] == "ok"


def test_auth_status_is_public(secured_client: TestClient) -> None:
    """The UI needs this before it can decide whether to show a login form."""
    body = secured_client.get("/api/auth/status").json()
    assert body == {"auth_required": True, "authenticated": False, "username": None}


def test_forged_session_cookie_is_rejected(secured_client: TestClient) -> None:
    """A cookie that is not correctly signed grants nothing."""
    secured_client.cookies.set("gaia_session", "v1.YWRtaW4.9999999999.forged")
    assert secured_client.get("/api/workspaces").status_code == 401


# ---------------------------------------------------------------------------
# 2. Authenticated API request
# ---------------------------------------------------------------------------


def test_login_issues_a_working_session(secured_client: TestClient) -> None:
    response = secured_client.post(
        "/api/auth/login",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200
    assert response.json()["authenticated"] is True

    # The session cookie the server set now grants access.
    assert secured_client.get("/api/workspaces").status_code == 200
    status = secured_client.get("/api/auth/status").json()
    assert status["authenticated"] is True
    assert status["username"] == TEST_USERNAME


def test_session_cookie_is_hardened(secured_client: TestClient) -> None:
    """HttpOnly keeps the session from page scripts; Strict blocks cross-site use."""
    response = secured_client.post(
        "/api/auth/login",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    cookie_header = response.headers.get("set-cookie", "").lower()
    assert "httponly" in cookie_header
    assert "samesite=strict" in cookie_header
    assert "path=/" in cookie_header


@pytest.mark.parametrize(
    "username,password",
    [
        (TEST_USERNAME, "wrong password"),
        ("someone-else", TEST_PASSWORD),
        ("someone-else", "wrong"),
    ],
)
def test_bad_credentials_do_not_authenticate(secured_client: TestClient, username, password) -> None:
    response = secured_client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 401
    assert secured_client.get("/api/workspaces").status_code == 401


def test_empty_credentials_are_rejected_by_validation(secured_client: TestClient) -> None:
    """Empty input never reaches the password check at all."""
    for payload in ({"username": "", "password": ""}, {"username": TEST_USERNAME}):
        response = secured_client.post("/api/auth/login", json=payload)
        assert response.status_code == 422
    assert secured_client.get("/api/workspaces").status_code == 401


def test_login_error_does_not_reveal_which_field_wrong(secured_client: TestClient) -> None:
    """One message for both cases, so usernames cannot be enumerated."""
    unknown = secured_client.post(
        "/api/auth/login", json={"username": "nobody", "password": "whatever"}
    )
    bad_password = secured_client.post(
        "/api/auth/login", json={"username": TEST_USERNAME, "password": "whatever"}
    )
    assert unknown.json()["detail"] == bad_password.json()["detail"]


def test_bearer_token_authenticates(secured_client: TestClient, monkeypatch) -> None:
    """Scripted clients can authenticate without managing a cookie jar."""
    from app.config import settings as global_settings

    monkeypatch.setattr(global_settings, "auth_api_token", "cli-token-value")
    assert check_api_token(global_settings, "cli-token-value") is True
    assert check_api_token(global_settings, "wrong") is False

    response = secured_client.get(
        "/api/workspaces", headers={"Authorization": "Bearer cli-token-value"}
    )
    assert response.status_code == 200


def test_bearer_token_cannot_be_guessed(secured_client: TestClient, monkeypatch) -> None:
    from app.config import settings as global_settings

    monkeypatch.setattr(global_settings, "auth_api_token", "cli-token-value")
    response = secured_client.get(
        "/api/workspaces", headers={"Authorization": "Bearer not-the-token"}
    )
    assert response.status_code == 401


def test_logout_invalidates_the_session(secured_client: TestClient) -> None:
    login(secured_client)
    assert secured_client.get("/api/workspaces").status_code == 200
    assert secured_client.post("/api/auth/logout").status_code == 204
    assert secured_client.get("/api/workspaces").status_code == 401


def test_authenticated_write_succeeds(secured_client: TestClient) -> None:
    """Authentication is not a read-only restriction: a real user can work."""
    login(secured_client)
    response = secured_client.post("/api/workspaces", json={"name": "Authenticated"})
    assert response.status_code == 201
    assert response.json()["name"] == "Authenticated"


# ---------------------------------------------------------------------------
# 3. Unauthorized write operation
# ---------------------------------------------------------------------------


def test_unauthorized_cannot_create_workspace(secured_client: TestClient) -> None:
    assert secured_client.post("/api/workspaces", json={"name": "Sneaky"}).status_code == 401


def test_unauthorized_cannot_register_repository(secured_client: TestClient) -> None:
    response = secured_client.post(
        "/api/workspaces/1/repositories",
        json={"name": "evil", "local_path": str(Path.cwd()), "kind": "documentation"},
    )
    assert response.status_code == 401


def test_unauthorized_cannot_delete_workspace(workspace: dict, secured_client: TestClient) -> None:
    """A destructive configuration call is refused, and the workspace survives.

    ``workspace`` is listed first so the fixture builds its data while the
    client is still unauthenticated, exactly as a normal user would.
    """
    assert secured_client.delete(f"/api/workspaces/{workspace['id']}").status_code == 401
    login(secured_client)
    assert secured_client.get(f"/api/workspaces/{workspace['id']}").status_code == 200


def test_unauthorized_cannot_plan_or_accept_a_proposal(
    workspace: dict, doc_repo: Path, secured_client: TestClient
) -> None:
    """The accept endpoint is the only filesystem writer; it must be gated."""
    login(secured_client)
    docs_id = next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")
    proposal = secured_client.post(
        f"/api/workspaces/{workspace['id']}/proposals/edit",
        json={
            "repository_id": docs_id,
            "path": "notes.md",
            "new_content": "# Rewritten by an attacker\n",
        },
    )
    assert proposal.status_code == 201
    proposal_id = proposal.json()["id"]

    # Drop the session; the apply must now fail and the file must be untouched.
    secured_client.post("/api/auth/logout")
    accepted = secured_client.post(
        f"/api/workspaces/{workspace['id']}/proposals/{proposal_id}/accept"
    )
    assert accepted.status_code == 401
    assert "unfiled" in (doc_repo / "notes.md").read_text(encoding="utf-8")


def test_unauthorized_cannot_create_a_proposal(
    workspace: dict, secured_client: TestClient
) -> None:
    docs_id = next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")
    response = secured_client.post(
        f"/api/workspaces/{workspace['id']}/proposals/move",
        json={
            "repository_id": docs_id,
            "source_path": "notes.md",
            "target_dir": "architecture",
        },
    )
    assert response.status_code == 401


def test_unauthorized_cannot_apply_inventory(
    workspace: dict, doc_repo: Path, secured_client: TestClient, monkeypatch
) -> None:
    """An inventory apply moves real files, so it must require a session."""
    from tests.test_chat_agent import ScriptedProvider
    from tests.test_inventory import _inventory_response

    monkeypatch.setattr(
        "app.api.routes_inventory.get_provider",
        lambda: ScriptedProvider(
            [
                _inventory_response(
                    [{"path": "notes.md", "suggested_path": "architecture", "confidence": 0.9}]
                )
            ]
        ),
    )
    login(secured_client)
    run = secured_client.post(
        f"/api/workspaces/{workspace['id']}/inventory/runs", json={}
    ).json()

    # Drop the session; the request must now fail.
    secured_client.post("/api/auth/logout")
    response = secured_client.post(
        f"/api/workspaces/{workspace['id']}/inventory/runs/{run['id']}/apply", json={}
    )
    assert response.status_code == 401
    assert (doc_repo / "notes.md").exists()
    assert not (doc_repo / "architecture" / "notes.md").exists()


# ---------------------------------------------------------------------------
# 4 & 5. Workspace roots: inside and outside
# ---------------------------------------------------------------------------


@pytest.fixture()
def restricted_roots(monkeypatch: pytest.MonkeyPatch):
    """Pin the allowed roots to a given list, with no dev escape hatch."""

    def apply(roots: list[str]) -> None:
        from app.config import settings as global_settings

        monkeypatch.setattr(global_settings, "allowed_workspace_roots", roots)
        monkeypatch.setattr(global_settings, "allow_unrestricted_workspace_roots", False)

    return apply


def test_workspace_inside_allowed_root_is_accepted(
    client: TestClient, doc_repo: Path, restricted_roots
) -> None:
    restricted_roots([str(doc_repo.parent)])
    ws = client.post("/api/workspaces", json={"name": "W"}).json()
    response = client.post(
        f"/api/workspaces/{ws['id']}/repositories",
        json={"name": "docs", "local_path": str(doc_repo), "kind": "documentation"},
    )
    assert response.status_code == 201
    assert response.json()["local_path"] == str(doc_repo.resolve())


def test_workspace_outside_allowed_root_is_rejected(
    client: TestClient, tmp_path: Path, doc_repo: Path, restricted_roots
) -> None:
    """A directory outside every configured root must be refused."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    restricted_roots([str(allowed)])

    ws = client.post("/api/workspaces", json={"name": "W"}).json()
    response = client.post(
        f"/api/workspaces/{ws['id']}/repositories",
        json={"name": "docs", "local_path": str(doc_repo), "kind": "documentation"},
    )
    assert response.status_code == 400
    assert "not inside any authorized workspace root" in response.json()["detail"]

    other = client.post(
        f"/api/workspaces/{ws['id']}/repositories",
        json={"name": "other", "local_path": str(outside), "kind": "source"},
    )
    assert other.status_code == 400


def test_empty_allowed_roots_fails_closed(
    client: TestClient, doc_repo: Path, restricted_roots
) -> None:
    """The core fix: an empty root list no longer means 'any directory'.

    Previously this configuration let any existing directory be registered,
    which turned a documentation tool into a filesystem browser.
    """
    restricted_roots([])
    ws = client.post("/api/workspaces", json={"name": "W"}).json()
    response = client.post(
        f"/api/workspaces/{ws['id']}/repositories",
        json={"name": "docs", "local_path": str(doc_repo), "kind": "documentation"},
    )
    assert response.status_code == 400
    assert "No workspace roots are configured" in response.json()["detail"]


def test_development_mode_still_allows_unrestricted_roots(tmp_path: Path) -> None:
    """The development escape hatch stays available, but only when requested."""
    repo = tmp_path / "anything"
    repo.mkdir()
    assert assert_authorized_root(repo, [], allow_unrestricted=True) == repo.resolve()
    with pytest.raises(PathSecurityError):
        assert_authorized_root(repo, [])


def test_nested_directory_inside_root_is_allowed(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    nested = root / "team" / "service"
    nested.mkdir(parents=True)
    assert assert_authorized_root(nested, [str(root)]) == nested.resolve()


def test_workspace_root_must_be_a_directory(tmp_path: Path) -> None:
    """Pointing a 'repository' at a file is refused."""
    a_file = tmp_path / "notes.md"
    a_file.write_text("# hi", encoding="utf-8")
    with pytest.raises(PathSecurityError):
        assert_authorized_root(a_file, [str(tmp_path)])


def test_sibling_directory_sharing_a_prefix_is_rejected(tmp_path: Path) -> None:
    """'/srv/docs' must not authorize '/srv/docs-private'.

    A naive string-prefix check would; containment must be path-component-wise.
    """
    root = tmp_path / "docs"
    sibling = tmp_path / "docs-private"
    root.mkdir()
    sibling.mkdir()
    with pytest.raises(PathSecurityError):
        assert_authorized_root(sibling, [str(root)])


# ---------------------------------------------------------------------------
# 6. Path traversal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "../../../Windows/System32/config/SAM",
        "architecture/../../../etc/passwd",
        "..",
        "./../secrets",
    ],
)
def test_path_traversal_is_rejected(client: TestClient, workspace: dict, rel: str) -> None:
    docs_id = next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")
    response = client.get(
        f"/api/workspaces/{workspace['id']}/repositories/{docs_id}/document",
        params={"path": rel},
    )
    assert response.status_code == 400


def test_traversal_in_tree_request_is_rejected(client: TestClient, workspace: dict) -> None:
    docs_id = next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")
    response = client.get(
        f"/api/workspaces/{workspace['id']}/repositories/{docs_id}/tree",
        params={"path": "../../.."},
    )
    assert response.status_code == 400


def test_traversal_in_registered_repository_path_is_rejected(
    client: TestClient, doc_repo: Path, tmp_path: Path, restricted_roots
) -> None:
    """A '..' path resolving outside the root is refused after resolution."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    restricted_roots([str(allowed)])

    sneaky = allowed / ".." / ".." / doc_repo.name
    ws = client.post("/api/workspaces", json={"name": "W"}).json()
    response = client.post(
        f"/api/workspaces/{ws['id']}/repositories",
        json={"name": "docs", "local_path": str(sneaky), "kind": "documentation"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# 7. Symlink escape
# ---------------------------------------------------------------------------


def test_symlinked_repository_escaping_the_root_is_rejected(
    client: TestClient, tmp_path: Path, doc_repo: Path, restricted_roots
) -> None:
    """A symlink inside an allowed root that points outside it is refused.

    ``resolve()`` is applied before the containment check, so the link is
    followed to its real target and the target is what gets rejected.
    """
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    link = allowed / "docs-link"
    try:
        link.symlink_to(doc_repo, target_is_directory=True)
    except OSError:  # pragma: no cover - platform without symlink privileges
        pytest.skip("symlinks unavailable")
    restricted_roots([str(allowed)])

    ws = client.post("/api/workspaces", json={"name": "W"}).json()
    response = client.post(
        f"/api/workspaces/{ws['id']}/repositories",
        json={"name": "docs", "local_path": str(link), "kind": "documentation"},
    )
    assert response.status_code == 400
    assert "not inside any authorized workspace root" in response.json()["detail"]


def test_symlink_whose_target_is_inside_the_root_is_allowed(
    client: TestClient, tmp_path: Path, restricted_roots
) -> None:
    """A symlink resolving to a location still inside the root remains usable."""
    allowed = tmp_path / "allowed"
    real = allowed / "real-docs"
    real.mkdir(parents=True)
    link = allowed / "docs-link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:  # pragma: no cover
        pytest.skip("symlinks unavailable")
    restricted_roots([str(allowed)])

    ws = client.post("/api/workspaces", json={"name": "W"}).json()
    response = client.post(
        f"/api/workspaces/{ws['id']}/repositories",
        json={"name": "docs", "local_path": str(link), "kind": "documentation"},
    )
    assert response.status_code == 201
    # The stored path is the resolved real location, not the link itself.
    assert response.json()["local_path"] == str(real.resolve())


# ---------------------------------------------------------------------------
# 8. Production startup validation
# ---------------------------------------------------------------------------


def test_production_without_allowed_workspace_roots_refuses_to_start() -> None:
    """The headline requirement: no roots configured in production is fatal."""
    with pytest.raises(SecurityConfigurationError) as exc:
        prod_settings(allowed_workspace_roots=[]).validate_security()
    assert "ALLOWED_WORKSPACE_ROOTS" in str(exc.value)


def test_production_without_credentials_refuses_to_start() -> None:
    with pytest.raises(SecurityConfigurationError) as exc:
        prod_settings(
            auth_username=None, auth_password=None, auth_password_hash=None
        ).validate_security()
    message = str(exc.value)
    assert "AUTH_USERNAME" in message
    assert "AUTH_PASSWORD_HASH" in message


def test_production_cannot_disable_authentication() -> None:
    with pytest.raises(SecurityConfigurationError) as exc:
        prod_settings(auth_enabled=False).validate_security()
    assert "AUTH_ENABLED" in str(exc.value)


def test_production_cannot_allow_unrestricted_roots() -> None:
    with pytest.raises(SecurityConfigurationError) as exc:
        prod_settings(allow_unrestricted_workspace_roots=True).validate_security()
    assert "ALLOW_UNRESTRICTED_WORKSPACE_ROOTS" in str(exc.value)


def test_production_rejects_a_short_session_secret() -> None:
    with pytest.raises(SecurityConfigurationError) as exc:
        prod_settings(session_secret="too-short").validate_security()
    assert "SESSION_SECRET" in str(exc.value)


def test_production_rejects_a_missing_session_secret() -> None:
    with pytest.raises(SecurityConfigurationError) as exc:
        prod_settings(session_secret=None).validate_security()
    assert "SESSION_SECRET" in str(exc.value)


def test_valid_production_configuration_passes() -> None:
    """A correctly configured deployment must still be able to start."""
    prod_settings().validate_security()


def test_defaults_are_production_and_authenticated() -> None:
    """Unset configuration must never mean 'open'."""
    default = isolated_settings()
    assert default.app_env == "production"
    assert default.auth_enabled is True
    assert default.cors_origins == []
    assert default.allowed_workspace_roots == []
    # And that default configuration is refused outright.
    with pytest.raises(SecurityConfigurationError):
        default.validate_security()


def test_development_configuration_is_permitted() -> None:
    """Local development stays frictionless."""
    isolated_settings(
        app_env="development",
        auth_enabled=False,
        allow_unrestricted_workspace_roots=True,
    ).validate_security()


def test_settings_reject_an_unknown_app_env() -> None:
    """A typo must not silently fall back to a permissive mode."""
    with pytest.raises(ValidationError):
        isolated_settings(app_env="staging")


def test_unrestricted_roots_flag_is_ignored_in_production() -> None:
    """Even if set, the property stays False outside development."""
    config = prod_settings(allow_unrestricted_workspace_roots=True)
    assert config.unrestricted_workspace_roots is False


# ---------------------------------------------------------------------------
# 9. CORS origin validation
# ---------------------------------------------------------------------------


def _cors_client(origins: list[str], db_session_factory) -> TestClient:
    """A test client whose CORS policy is exactly ``origins``.

    Deliberately not used as a context manager: entering it would run the real
    lifespan and try to bootstrap the configured PostgreSQL database.
    """
    from app.db import get_db
    from app.main import create_app

    config = isolated_settings(app_env="development", cors_origins=origins)
    application = create_app(config)

    def override_get_db():
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    application.dependency_overrides[get_db] = override_get_db
    return TestClient(application)


def test_cors_allows_a_configured_origin(db_session_factory) -> None:
    client = _cors_client(["https://docs.example.com"], db_session_factory)
    response = client.get("/api/health", headers={"Origin": "https://docs.example.com"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://docs.example.com"


def test_cors_refuses_an_unlisted_origin(db_session_factory) -> None:
    """An attacker's site must get no CORS permission to read responses."""
    client = _cors_client(["https://docs.example.com"], db_session_factory)
    response = client.get("/api/health", headers={"Origin": "https://evil.example.com"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_is_refused_for_unlisted_origin(db_session_factory) -> None:
    client = _cors_client(["https://docs.example.com"], db_session_factory)
    response = client.options(
        "/api/workspaces",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_is_granted_for_listed_origin(db_session_factory) -> None:
    client = _cors_client(["https://docs.example.com"], db_session_factory)
    response = client.options(
        "/api/workspaces",
        headers={
            "Origin": "https://docs.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://docs.example.com"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_is_explicit_in_production() -> None:
    with pytest.raises(SecurityConfigurationError) as exc:
        prod_settings(cors_origins=[]).validate_security()
    assert "CORS_ORIGINS" in str(exc.value)


def test_cors_rejects_wildcard_in_production() -> None:
    """'*' with credentials would let any site make authenticated requests."""
    with pytest.raises(SecurityConfigurationError) as exc:
        prod_settings(cors_origins=["*"]).validate_security()
    assert "'*'" in str(exc.value)


def test_development_cors_defaults_to_localhost() -> None:
    dev = isolated_settings(app_env="development", cors_origins=[])
    assert dev.effective_cors_origins == DEV_CORS_ORIGINS


def test_production_cors_does_not_default_to_localhost() -> None:
    """The permissive development default must not leak into production."""
    prod = isolated_settings(
        app_env="production",
        cors_origins=[],
        auth_username="u",
        auth_password="p",
        session_secret=TEST_SECRET,
        allowed_workspace_roots=["/srv"],
    )
    assert prod.effective_cors_origins == []


# ---------------------------------------------------------------------------
# Credential primitives
# ---------------------------------------------------------------------------


def test_password_hash_round_trip() -> None:
    encoded = hash_password(TEST_PASSWORD, iterations=1000)  # fast for tests
    assert verify_password(TEST_PASSWORD, encoded) is True
    assert verify_password("wrong", encoded) is False


def test_password_hash_is_salted() -> None:
    """The same password must not produce the same stored hash twice."""
    a = hash_password(TEST_PASSWORD, iterations=1000)
    b = hash_password(TEST_PASSWORD, iterations=1000)
    assert a != b
    assert verify_password(TEST_PASSWORD, a)
    assert verify_password(TEST_PASSWORD, b)


def test_empty_password_is_never_hashed() -> None:
    with pytest.raises(ValueError):
        hash_password("")


@pytest.mark.parametrize("malformed", ["", "garbage", "pbkdf2_sha256$x$y", "md5$1$a$b"])
def test_malformed_hash_fails_closed(malformed: str) -> None:
    """A corrupt configuration must deny access, not grant it."""
    assert verify_password(TEST_PASSWORD, malformed) is False


def test_session_token_round_trip() -> None:
    config = prod_settings()
    token = create_session_token(config, TEST_USERNAME)
    assert verify_session_token(config, token) == TEST_USERNAME


def test_session_token_is_signed() -> None:
    """Editing the expiry must invalidate the token."""
    config = prod_settings()
    token = create_session_token(config, TEST_USERNAME)
    version, username_b64, _, signature = token.split(".", 3)
    forged = f"{version}.{username_b64}.99999999999.{signature}"
    assert verify_session_token(config, forged) is None


def test_expired_session_token_is_rejected() -> None:
    config = prod_settings(session_max_age_seconds=-1)
    token = create_session_token(config, TEST_USERNAME)
    assert verify_session_token(config, token) is None


def test_token_from_another_secret_is_rejected() -> None:
    token = create_session_token(prod_settings(), TEST_USERNAME)
    assert verify_session_token(prod_settings(session_secret="x" * 48), token) is None


def test_token_for_another_user_is_rejected() -> None:
    config = prod_settings()
    token = create_session_token(config, "someone-else")
    assert verify_session_token(config, token) is None


def test_check_credentials_requires_both_fields() -> None:
    config = prod_settings()
    assert check_credentials(config, TEST_USERNAME, TEST_PASSWORD) is True
    assert check_credentials(config, TEST_USERNAME, "nope") is False
    assert check_credentials(config, "nope", TEST_PASSWORD) is False


def test_hashed_password_is_accepted_in_settings() -> None:
    """A stored hash works in place of a plaintext password."""
    config = prod_settings(
        auth_password=None,
        auth_password_hash=hash_password(TEST_PASSWORD, iterations=1000),
    )
    assert check_credentials(config, TEST_USERNAME, TEST_PASSWORD) is True
    assert check_credentials(config, TEST_USERNAME, "wrong") is False


def test_api_token_is_optional() -> None:
    """With no token configured, no bearer token can ever authenticate."""
    assert check_api_token(prod_settings(auth_api_token=None), "anything") is False
