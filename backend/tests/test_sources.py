"""Tests for repository-source management and the .sources.yaml manifest.

Groups:

* **YAML** -- parsing/validation of the manifest: structure, required fields,
  invalid URLs, duplicates, and the idempotency of a repeated import.
* **Sources** -- the management API: add local, add GitHub, list, remove,
  status, and synchronization (against a *local* bare remote, so no network).
* **Retrieval** -- the inspection service: file listing, search, reads,
  ignored directories and binary-file handling.
* **Security** -- YAML cannot execute commands, credentials are never
  persisted, and invalid paths are rejected.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services.inspection import InspectionError
from app.services.manifest import ManifestError, parse_manifest, validate_manifest
from app.services.sources import (
    SourceError,
    classify_location,
    normalize_repo_url,
    sync_source,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def source_ws(client: TestClient) -> dict:
    """A workspace with no repositories, ready for source tests."""
    return client.post("/api/workspaces", json={"name": "Sources"}).json()


def _bare_remote(tmp_path: Path, name: str = "remote") -> Path:
    """A local bare Git remote standing in for GitHub.

    Tests must never touch the network. A bare repository supports clone and
    fast-forward pull exactly like a real remote, so the synchronization path
    is exercised end to end without depending on github.com being reachable.
    """
    work = tmp_path / f"{name}-work"
    work.mkdir()
    (work / "README.md").write_text("# Remote\n", encoding="utf-8")
    (work / "src").mkdir()
    (work / "src" / "intent_iq.py").write_text(
        "class IntentIQ:\n    '''Documented as background-only.'''\n    pass\n", encoding="utf-8"
    )
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "T")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "initial")
    remote = tmp_path / f"{name}.git"
    _git(work, "clone", "-q", "--bare", str(work), str(remote))
    return remote


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


@pytest.fixture(autouse=True)
def _checkout_root(tmp_path, monkeypatch):
    """Point the managed checkout root at a temp dir and reset the cached path.

    The settings object resolves the checkout root lazily through the module
    singleton, so the tests redirect it to keep the repository clean.
    """
    root = tmp_path / "checkouts"
    monkeypatch.setattr(settings, "source_checkout_root", root.as_posix())
    yield root


GAIA_MANIFEST = """\
sources:
  - repo: Gaia
    path: https://github.com/Bojanni050/Gaia-Cloud

  - repo: Foundation-Chronicle
    path: https://github.com/Bojanni050/Foundation

  - repo: Chronicle-Gaia
    path: https://github.com/Bojanni050/Chronicle-Gaia

  - repo: Gaia-Web
    path: https://github.com/Bojanni050/gaia-web

  - repo: Gaia Website
    path: https://github.com/Bojanni050/gaia-website

  - repo: Chronicle-Diary
    path: https://github.com/Bojanni050/ChronicleDiary

  - repo: Hermes
    path: https://github.com/nousresearch/hermes-agent
"""


# ---------------------------------------------------------------------------
# YAML: parsing and validation
# ---------------------------------------------------------------------------


def test_parse_valid_manifest() -> None:
    entries = parse_manifest(GAIA_MANIFEST)
    assert len(entries) == 7
    assert entries[0] == {"repo": "Gaia", "path": "https://github.com/Bojanni050/Gaia-Cloud", "branch": None}


def test_parse_manifest_with_local_paths() -> None:
    entries = parse_manifest(
        "sources:\n"
        "  - repo: Local Service\n"
        "    path: C:/src/local-service\n"
        "  - repo: Remote\n"
        "    path: https://github.com/o/r\n"
    )
    assert len(entries) == 2
    assert entries[0]["repo"] == "Local Service"


def test_parse_manifest_missing_sources_key() -> None:
    with pytest.raises(ManifestError, match="sources"):
        parse_manifest("repos:\n  - repo: x\n    path: y\n")


def test_parse_manifest_missing_repo() -> None:
    with pytest.raises(ManifestError, match="repo"):
        parse_manifest("sources:\n  - path: https://github.com/o/r\n")


def test_parse_manifest_missing_path() -> None:
    with pytest.raises(ManifestError, match="path"):
        parse_manifest("sources:\n  - repo: Thing\n")


def test_parse_manifest_invalid_yaml() -> None:
    with pytest.raises(ManifestError, match="Invalid YAML"):
        parse_manifest("sources: [unclosed\n")


def test_parse_manifest_rejects_unknown_keys() -> None:
    """A manifest must not smuggle arbitrary configuration into the app."""
    with pytest.raises(ManifestError, match="unsupported key"):
        parse_manifest(
            "sources:\n"
            "  - repo: x\n"
            "    path: https://github.com/o/r\n"
            "    database_url: postgres://evil\n"
        )


def test_parse_manifest_empty() -> None:
    with pytest.raises(ManifestError):
        parse_manifest("")


def test_validate_manifest_reports_invalid_url() -> None:
    report = validate_manifest(
        "sources:\n"
        "  - repo: Good\n"
        "    path: https://github.com/o/r\n"
        "  - repo: Hermes\n"
        "    path: ftp://not-a-git-url\n",
        {"local": {}, "github": {}},
    )
    assert len(report.valid_entries) == 1
    assert len(report.invalid_entries) == 1
    bad = report.invalid_entries[0]
    assert bad.repo == "Hermes"
    assert bad.error and "Invalid repository URL" in bad.error


def test_validate_manifest_flags_in_manifest_duplicates() -> None:
    report = validate_manifest(
        "sources:\n"
        "  - repo: One\n"
        "    path: https://github.com/o/r\n"
        "  - repo: Two\n"
        "    path: https://github.com/o/r.git\n",
        {"local": {}, "github": {}},
    )
    # .git-suffixed and bare URLs are the same repository: the second entry
    # is a duplicate, and it is reported rather than silently skipped.
    assert len(report.valid_entries) == 1
    assert len(report.invalid_entries) == 1
    assert "Duplicate repository" in (report.invalid_entries[0].error or "")


def test_normalize_url_git_suffix_and_case() -> None:
    assert (
        normalize_repo_url("https://github.com/Bojanni050/Gaia-Cloud.git")
        == normalize_repo_url("https://github.com/Bojanni050/Gaia-Cloud")
    )
    assert normalize_repo_url("HTTPS://GITHUB.COM/O/R/") == "https://github.com/O/R"


def test_normalize_url_rejects_credentials() -> None:
    with pytest.raises(SourceError, match="credentials"):
        normalize_repo_url("https://user:token@github.com/o/r")


def test_normalize_url_rejects_non_http() -> None:
    with pytest.raises(SourceError):
        normalize_repo_url("git@github.com:o/r.git")
    with pytest.raises(SourceError):
        normalize_repo_url("file:///etc/passwd")


def test_classify_location() -> None:
    assert classify_location("https://github.com/o/r") == "github"
    assert classify_location("C:/src/thing") == "local"


# ---------------------------------------------------------------------------
# YAML: API preview + import
# ---------------------------------------------------------------------------


def test_manifest_validate_endpoint_previews(
    client: TestClient, source_ws: dict
) -> None:
    body = client.post(
        f"/api/workspaces/{source_ws['id']}/sources/manifest/validate",
        json={"content": GAIA_MANIFEST},
    ).json()
    assert body["total"] == 7
    assert body["valid_count"] == 7
    assert body["invalid_count"] == 0
    assert body["new_count"] == 7
    assert [e["repo"] for e in body["entries"]][0] == "Gaia"
    assert body["entries"][0]["source_type"] == "github"


def test_manifest_import_creates_sources(client: TestClient, source_ws: dict) -> None:
    result = client.post(
        f"/api/workspaces/{source_ws['id']}/sources/manifest/import",
        json={"content": GAIA_MANIFEST, "confirm": True},
    ).json()
    assert len(result["imported"]) == 7
    assert result["invalid"] == []
    sources = client.get(f"/api/workspaces/{source_ws['id']}/sources").json()
    assert len(sources) == 7


def test_manifest_import_requires_confirmation(client: TestClient, source_ws: dict) -> None:
    response = client.post(
        f"/api/workspaces/{source_ws['id']}/sources/manifest/import",
        json={"content": GAIA_MANIFEST, "confirm": False},
    )
    assert response.status_code == 400
    assert "confirm" in response.json()["detail"].lower()
    assert client.get(f"/api/workspaces/{source_ws['id']}/sources").json() == []


def test_manifest_import_is_idempotent(client: TestClient, source_ws: dict) -> None:
    url = f"/api/workspaces/{source_ws['id']}/sources/manifest/import"
    first = client.post(url, json={"content": GAIA_MANIFEST, "confirm": True}).json()
    assert len(first["imported"]) == 7

    # Importing the same manifest again must not create duplicates.
    second = client.post(url, json={"content": GAIA_MANIFEST, "confirm": True}).json()
    assert second["imported"] == []
    assert len(second["duplicates"]) == 7
    sources = client.get(f"/api/workspaces/{source_ws['id']}/sources").json()
    assert len(sources) == 7


def test_manifest_import_idempotent_across_git_suffix(client: TestClient, source_ws: dict) -> None:
    """A .git-suffixed URL is the same repository as the bare URL."""
    url = f"/api/workspaces/{source_ws['id']}/sources/manifest/import"
    client.post(url, json={"content": GAIA_MANIFEST, "confirm": True})
    again = client.post(
        url,
        json={
            "content": (
                "sources:\n  - repo: Renamed Gaia\n"
                "    path: https://github.com/Bojanni050/Gaia-Cloud.git\n"
            ),
            "confirm": True,
        },
    ).json()
    assert again["imported"] == []
    assert again["duplicates"] == ["Gaia"]
    sources = client.get(f"/api/workspaces/{source_ws['id']}/sources").json()
    assert len(sources) == 7


def test_manifest_import_reports_invalid_entries(client: TestClient, source_ws: dict) -> None:
    result = client.post(
        f"/api/workspaces/{source_ws['id']}/sources/manifest/import",
        json={
            "content": (
                "sources:\n"
                "  - repo: Good\n"
                "    path: https://github.com/o/r\n"
                "  - repo: Hermes\n"
                "    path: not a valid repository url at all\n"
            ),
            "confirm": True,
        },
    ).json()
    assert len(result["imported"]) == 1
    assert len(result["invalid"]) == 1
    assert result["invalid"][0]["repo"] == "Hermes"
    assert result["invalid"][0]["error"]


def test_manifest_import_rejects_structurally_invalid_yaml(
    client: TestClient, source_ws: dict
) -> None:
    response = client.post(
        f"/api/workspaces/{source_ws['id']}/sources/manifest/import",
        json={"content": "sources: 5", "confirm": True},
    )
    assert response.status_code == 400
    assert client.get(f"/api/workspaces/{source_ws['id']}/sources").json() == []


def test_workspace_manifest_round_trip(client: TestClient, source_ws: dict) -> None:
    """GET /sources/manifest renders the retained manifest as a preview."""
    client.post(
        f"/api/workspaces/{source_ws['id']}/sources/manifest/import",
        json={"content": GAIA_MANIFEST, "confirm": True},
    )
    manifest = client.get(f"/api/workspaces/{source_ws['id']}/sources/manifest").json()
    assert manifest["total"] == 7
    assert manifest["duplicate_count"] == 7  # everything is now configured


# ---------------------------------------------------------------------------
# Sources: management API
# ---------------------------------------------------------------------------


def test_add_local_source(client: TestClient, source_ws: dict, source_repo: Path) -> None:
    created = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "gaia-service", "location": str(source_repo)},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["kind"] == "source"
    assert body["source_type"] == "local"
    assert body["status"] == "ready"
    assert body["writable"] is False


def test_add_local_source_detects_branch(client: TestClient, source_ws: dict, source_repo: Path) -> None:
    body = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "svc", "location": str(source_repo)},
    ).json()
    assert body["branch"] == "main"


def test_add_github_source(client: TestClient, source_ws: dict) -> None:
    body = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "Gaia", "location": "https://github.com/Bojanni050/Gaia-Cloud"},
    ).json()
    assert body["source_type"] == "github"
    assert body["source_url"] == "https://github.com/Bojanni050/Gaia-Cloud"
    assert body["status"] == "pending"  # nothing is cloned until sync
    assert body["writable"] is False


def test_add_github_source_rejects_credential_url(client: TestClient, source_ws: dict) -> None:
    response = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "Evil", "location": "https://user:ghp_token@github.com/o/r"},
    )
    assert response.status_code == 400
    assert "credentials" in response.json()["detail"].lower()


def test_add_github_source_rejects_invalid_url(client: TestClient, source_ws: dict) -> None:
    response = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "Bad", "location": "not a url"},
    )
    assert response.status_code == 400


def test_add_source_rejects_missing_local_path(client: TestClient, source_ws: dict, tmp_path: Path) -> None:
    response = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "Missing", "location": str(tmp_path / "does-not-exist")},
    )
    assert response.status_code == 400


def test_add_source_rejects_duplicate_identity(client: TestClient, source_ws: dict, source_repo: Path) -> None:
    client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "One", "location": str(source_repo)},
    )
    # A different display name but the SAME repository is still a duplicate.
    response = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "Two", "location": str(source_repo)},
    )
    assert response.status_code == 400
    assert "already registered" in response.json()["detail"]


def test_add_github_source_duplicate_by_url(client: TestClient, source_ws: dict) -> None:
    client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "Gaia", "location": "https://github.com/Bojanni050/Gaia-Cloud"},
    )
    response = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "Gaia Again", "location": "https://github.com/Bojanni050/Gaia-Cloud.git"},
    )
    assert response.status_code == 400


def test_list_and_retrieve_source(client: TestClient, source_ws: dict, source_repo: Path) -> None:
    created = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "svc", "location": str(source_repo)},
    ).json()
    listed = client.get(f"/api/workspaces/{source_ws['id']}/sources").json()
    assert [s["id"] for s in listed] == [created["id"]]


def test_remove_source(client: TestClient, source_ws: dict, source_repo: Path) -> None:
    created = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "svc", "location": str(source_repo)},
    ).json()
    assert (
        client.delete(f"/api/workspaces/{source_ws['id']}/sources/{created['id']}").status_code
        == 204
    )
    assert client.get(f"/api/workspaces/{source_ws['id']}/sources").json() == []
    # The local repository itself is untouched.
    assert (source_repo / "src" / "memory.py").exists()


def test_remove_source_rejects_documentation_repo(client: TestClient, workspace: dict) -> None:
    docs_id = next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")
    response = client.delete(f"/api/workspaces/{workspace['id']}/sources/{docs_id}")
    assert response.status_code == 409


def test_source_status_local_ready_and_missing(
    client: TestClient, source_ws: dict, tmp_path: Path
) -> None:
    local = tmp_path / "svc"
    local.mkdir()
    created = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "svc", "location": str(local)},
    ).json()
    assert created["status"] == "ready"

    # The status is computed live: the path disappearing is reported.
    local.rmdir()
    listed = client.get(f"/api/workspaces/{source_ws['id']}/sources").json()
    assert listed[0]["status"] == "missing"
    assert listed[0]["status_message"]


def test_source_status_github_pending_then_ready(
    client: TestClient, source_ws: dict, tmp_path: Path
) -> None:
    # A file:// URL is classified as remote and then rejected by URL
    # validation: only http(s) repository URLs are supported.
    remote = _bare_remote(tmp_path)
    response = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "remote", "location": remote.as_uri()},
    )
    assert response.status_code == 400
    assert "http(s)" in response.json()["detail"]


def test_sources_are_always_read_only(client: TestClient, source_ws: dict, source_repo: Path) -> None:
    """The sources API has no write path into the repository, and source rows
    created through it are never writable."""
    created = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "svc", "location": str(source_repo)},
    ).json()
    assert created["writable"] is False


# ---------------------------------------------------------------------------
# Synchronization
# ---------------------------------------------------------------------------


def _github_source_row(session, workspace_id: int, url: str, name: str):
    """A GitHub source row pointing at any Git URL (tests use a local bare repo).

    The API only accepts http(s) URLs, which is correct; the sync path itself is
    URL-scheme-agnostic because it delegates to git.
    """
    from app.models import Repository

    repo = Repository(
        workspace_id=workspace_id,
        name=name,
        local_path=str(
            Path(settings.effective_source_checkout_root) / f"workspace_{workspace_id}" / name
        ),
        branch="main",
        kind="source",
        writable=False,
        source_type="github",
        source_url=url,
        status="pending",
        status_message="Not synchronized yet.",
    )
    session.add(repo)
    session.commit()
    session.refresh(repo)
    return repo


def test_sync_github_source_clones_into_managed_checkout(
    client: TestClient, session, source_ws: dict, tmp_path: Path
) -> None:
    remote = _bare_remote(tmp_path)
    repo = _github_source_row(session, source_ws["id"], str(remote), "remote")

    synced = client.post(
        f"/api/workspaces/{source_ws['id']}/sources/{repo.id}/sync"
    ).json()
    assert synced["action"] == "cloned"
    assert synced["status"] == "ready"
    assert synced["revision"]

    # The clone lives under the managed checkout root, not wherever git likes.
    listed = client.get(f"/api/workspaces/{source_ws['id']}/sources").json()
    assert listed[0]["status"] == "ready"
    checkout = Path(listed[0]["local_path"])
    assert checkout.is_relative_to(Path(settings.effective_source_checkout_root))
    assert (checkout / "src" / "intent_iq.py").exists()

    # A second sync fast-forwards instead of re-cloning.
    again = client.post(
        f"/api/workspaces/{source_ws['id']}/sources/{repo.id}/sync"
    ).json()
    assert again["action"] == "updated"
    assert again["status"] == "ready"


def test_sync_github_source_reports_bad_remote(
    client: TestClient, source_ws: dict, tmp_path: Path
) -> None:
    created = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "nope", "location": "https://github.com/o/does-not-exist-xyz"},
    ).json()
    response = client.post(
        f"/api/workspaces/{source_ws['id']}/sources/{created['id']}/sync"
    )
    assert response.status_code == 400
    detail = response.json()["detail"].lower()
    # The message explains the private-repo limitation rather than exposing a
    # raw git error, and never stores a credential anywhere.
    assert "private repository authentication is not supported" in detail
    listed = client.get(f"/api/workspaces/{source_ws['id']}/sources").json()
    assert listed[0]["status"] == "error"
    assert listed[0]["status_message"]


def test_sync_local_source_refreshes_status(
    client: TestClient, source_ws: dict, source_repo: Path
) -> None:
    created = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "svc", "location": str(source_repo)},
    ).json()
    result = client.post(
        f"/api/workspaces/{source_ws['id']}/sources/{created['id']}/sync"
    ).json()
    assert result["action"] == "refreshed"
    assert result["status"] == "ready"


def test_sync_never_pushes(client: TestClient, session, source_ws: dict, tmp_path: Path) -> None:
    """The remote's revision is unchanged by a sync: sources are evidence."""
    remote = _bare_remote(tmp_path, "origin")
    before = subprocess.run(
        ["git", "-C", str(remote), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    repo = _github_source_row(session, source_ws["id"], str(remote), "origin")
    client.post(f"/api/workspaces/{source_ws['id']}/sources/{repo.id}/sync")
    after = subprocess.run(
        ["git", "-C", str(remote), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert before == after


# ---------------------------------------------------------------------------
# Retrieval / inspection
# ---------------------------------------------------------------------------


@pytest.fixture()
def code_repo(tmp_path: Path) -> Path:
    """A source repository with code, junk directories, and a binary file."""
    root = tmp_path / "code"
    (root / "src").mkdir(parents=True)
    (root / "src" / "intent_iq.py").write_text(
        "class IntentIQ:\n    '''Background-only reasoning.'''\n\n"
        "    def reason(self, q):\n        return q\n",
        encoding="utf-8",
    )
    (root / "src" / "hermes_integration.py").write_text(
        "def send_to_hermes(message):\n    return message\n", encoding="utf-8"
    )
    (root / "README.md").write_text("# Code Repo\n", encoding="utf-8")
    (root / "package.json").write_text('{"name": "code"}', encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.js").write_text("// junk", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "x.pyc").write_bytes(b"\x00\x01\x02")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    (root / ".gitignore").write_text("ignored_dir/\n", encoding="utf-8")
    (root / "ignored_dir").mkdir()
    (root / "ignored_dir" / "secret.txt").write_text("ignored by git", encoding="utf-8")
    return root


def test_list_code_files_skips_ignored_dirs(code_repo: Path) -> None:
    from app.services.inspection import list_code_files

    files = [f.path for f in list_code_files(code_repo)]
    assert "src/intent_iq.py" in files
    assert "README.md" in files
    assert "package.json" in files
    assert not any(p.startswith("node_modules/") for p in files)
    assert not any(p.startswith("__pycache__/") for p in files)


def test_list_code_files_respects_gitignore(code_repo: Path) -> None:
    """git ls-files keeps the checkout's own .gitignore in force."""
    _git_init(code_repo)
    from app.services.inspection import list_code_files

    files = [f.path for f in list_code_files(code_repo)]
    assert not any(p.startswith("ignored_dir/") for p in files)


def test_search_code_finds_identifiers(code_repo: Path) -> None:
    from app.services.inspection import search_code

    hits = search_code(code_repo, "IntentIQ")
    assert hits and hits[0].path == "src/intent_iq.py"
    assert hits[0].line == 1
    assert "IntentIQ" in hits[0].snippet


def test_read_source_file_returns_numbered_lines(code_repo: Path) -> None:
    from app.services.inspection import read_source_file

    content = read_source_file(code_repo, "src/intent_iq.py")
    assert "(lines 1-" in content and " of 5)" in content
    assert "   1| class IntentIQ:" in content


def test_read_source_file_refuses_binary(code_repo: Path) -> None:
    from app.services.inspection import read_source_file

    with pytest.raises(InspectionError, match="binary"):
        read_source_file(code_repo, "logo.png")


def test_read_source_file_refuses_unknown_suffix(code_repo: Path) -> None:
    from app.services.inspection import read_source_file

    with pytest.raises(InspectionError):
        read_source_file(code_repo, "logo.png")


def test_read_source_file_line_window(code_repo: Path) -> None:
    from app.services.inspection import read_source_file

    content = read_source_file(code_repo, "src/intent_iq.py", start_line=2, end_line=3)
    assert "(lines 2-3 of 5)" in content
    assert "reason" in content


def test_inspection_api_endpoints(client: TestClient, source_ws: dict, code_repo: Path) -> None:
    created = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "code", "location": str(code_repo)},
    ).json()
    base = f"/api/workspaces/{source_ws['id']}/sources/{created['id']}"

    files = client.get(f"{base}/files").json()["files"]
    assert any(f["path"] == "src/intent_iq.py" for f in files)

    file_body = client.get(
        f"{base}/file", params={"path": "src/intent_iq.py"}
    ).json()
    assert "class IntentIQ" in file_body["content"]
    assert file_body["total_lines"] >= 4

    hits = client.get(f"{base}/search", params={"q": "hermes"}).json()["hits"]
    assert hits and hits[0]["path"] == "src/hermes_integration.py"

    structure = client.get(f"{base}/structure").json()["structure"]
    assert "src/" in structure


def test_inspection_api_rejects_traversal(
    client: TestClient, source_ws: dict, code_repo: Path
) -> None:
    created = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "code", "location": str(code_repo)},
    ).json()
    response = client.get(
        f"/api/workspaces/{source_ws['id']}/sources/{created['id']}/file",
        params={"path": "../../../etc/passwd"},
    )
    assert response.status_code == 400


def test_inspection_api_before_sync_is_409(client: TestClient, source_ws: dict) -> None:
    created = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "gh", "location": "https://github.com/o/r"},
    ).json()
    response = client.get(f"/api/workspaces/{source_ws['id']}/sources/{created['id']}/files")
    assert response.status_code == 409
    assert "synchronized" in response.json()["detail"].lower()


def _git_init(repo: Path) -> None:
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial")


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def test_yaml_cannot_execute_commands(client: TestClient, source_ws: dict, tmp_path: Path) -> None:
    """A YAML with a python/object tag is configuration, not code.

    yaml.safe_load refuses to instantiate arbitrary types; the import fails
    with a structural error and nothing was executed or imported.
    """
    marker = tmp_path / "pwned.txt"
    malicious = (
        "sources:\n"
        "  - repo: !!python/object/apply:os.system\n"
        f"    path: \"touch {marker}\"\n"
    )
    response = client.post(
        f"/api/workspaces/{source_ws['id']}/sources/manifest/import",
        json={"content": malicious, "confirm": True},
    )
    assert response.status_code == 400
    assert not marker.exists()
    assert client.get(f"/api/workspaces/{source_ws['id']}/sources").json() == []


def test_credentials_never_persisted_in_manifest(
    client: TestClient, source_ws: dict, tmp_path: Path
) -> None:
    """No credential ever reaches the manifest, the database, or the disk."""
    url_with_token = "https://user:ghp_secret@github.com/o/r"
    response = client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "Leaky", "location": url_with_token},
    )
    assert response.status_code == 400

    manifest_text = client.get(f"/api/workspaces/{source_ws['id']}/sources/manifest")
    assert manifest_text.status_code == 200
    blob = str(manifest_text.json())
    assert "ghp_secret" not in blob
    for path in Path(settings.effective_source_checkout_root).rglob(".sources.yaml"):
        assert "ghp_secret" not in path.read_text(encoding="utf-8")


def test_manifest_import_rejects_invalid_local_path(
    client: TestClient, source_ws: dict, tmp_path: Path
) -> None:
    """A local entry pointing at a non-existent path is rejected up front,
    with the reason kept visible rather than silently skipped."""
    result = client.post(
        f"/api/workspaces/{source_ws['id']}/sources/manifest/import",
        json={
            "content": (
                "sources:\n"
                "  - repo: Ghost\n"
                f"    path: {tmp_path / 'nope'}\n"
                "  - repo: Real\n"
                "    path: https://github.com/o/r\n"
            ),
            "confirm": True,
        },
    ).json()
    assert len(result["imported"]) == 1
    assert len(result["invalid"]) == 1
    assert result["invalid"][0]["repo"] == "Ghost"


def test_documentation_repo_not_exposed_through_source_inspection(
    client: TestClient, workspace: dict
) -> None:
    docs_id = next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")
    response = client.get(f"/api/workspaces/{workspace['id']}/sources/{docs_id}/files")
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# AI tools
# ---------------------------------------------------------------------------


def test_ai_tools_search_and_read_source(
    client: TestClient, session, source_ws: dict, code_repo: Path
) -> None:
    """The assistant can find and cite code as architecture evidence."""
    from app.models import Repository, Workspace
    from app.services.tools import ToolContext, run_tool

    client.post(
        f"/api/workspaces/{source_ws['id']}/sources",
        json={"name": "code", "location": str(code_repo)},
    )
    ws = session.get(Workspace, source_ws["id"])
    repos = session.query(Repository).filter(Repository.workspace_id == ws.id).all()
    ctx = ToolContext(workspace=ws, repositories=repos, citations=[], db=session)

    result = run_tool("search_code", ctx, {"query": "IntentIQ"})
    assert "code/src/intent_iq.py:1" in result

    read = run_tool(
        "read_source", ctx, {"repository": "code", "path": "src/intent_iq.py"}
    )
    assert "class IntentIQ" in read
    assert read.startswith("src/intent_iq.py (lines")

    listing = run_tool(
        "list_source_files", ctx, {"repository": "code", "path": "src"}
    )
    assert "intent_iq.py" in listing

    structure = run_tool("source_structure", ctx, {"repository": "code"})
    assert "src/" in structure

    # Citations were recorded for the source files, so findings are traceable.
    assert any(c.path == "src/intent_iq.py" for c in ctx.citations)
    assert all(
        c.evidence_type == "verified_implementation"
        for c in ctx.citations
        if c.path == "src/intent_iq.py"
    )


def test_ai_tools_report_missing_source(session, client: TestClient, source_ws: dict) -> None:
    from app.models import Repository, Workspace
    from app.services.tools import ToolContext, run_tool

    ws = session.get(Workspace, source_ws["id"])
    repos = session.query(Repository).filter(Repository.workspace_id == ws.id).all()
    ctx = ToolContext(workspace=ws, repositories=repos, citations=[], db=session)
    result = run_tool("search_code", ctx, {"query": "anything"})
    assert "No source repositories" in result
