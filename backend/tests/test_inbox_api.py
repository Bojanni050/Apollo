"""The inbox's HTTP surface, the way the drop zone will use it.

The service tests pin the naming and collision behaviour; these pin the contract
the browser depends on -- that a drop is one request, that listing a workspace
puts nothing on disk, and that a stored document is immediately readable through
the ordinary document route the reading pane already uses.

The route-table check at the end is the same kind of assertion the groups tests
make: "there is no way to do X" is only true while it is checked against the
table, not hoped for.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import storage
from app.services.storage import INBOX_DIR, storage_root, workspace_inbox


def _upload(
    client: TestClient,
    workspace_id: int,
    name: str,
    content: bytes,
    content_type: str = "application/octet-stream",
):
    return client.post(
        f"/api/workspaces/{workspace_id}/inbox/upload",
        files={"file": (name, content, content_type)},
    )


def _bare_workspace(client: TestClient) -> dict:
    """A workspace with nothing registered: the state a reader starts in."""
    return client.post("/api/workspaces", json={"name": "Mijn documenten"}).json()


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_an_empty_workspace_lists_an_empty_inbox(client: TestClient) -> None:
    workspace = _bare_workspace(client)

    listing = client.get(f"/api/workspaces/{workspace['id']}/inbox")

    assert listing.status_code == 200
    body = listing.json()
    assert body["files"] == []
    assert body["directory"] == INBOX_DIR
    # Null rather than an id: there is nothing to open yet, and the interface
    # needs to be able to tell that apart from an empty folder that exists.
    assert body["repository_id"] is None


def test_listing_creates_nothing_on_disk(client: TestClient) -> None:
    """Looking at a workspace is not a decision to keep documents in it.

    Otherwise every workspace somebody clicked on would leave a folder behind,
    and the storage root would fill with directories nobody asked for.
    """
    workspace = _bare_workspace(client)

    client.get(f"/api/workspaces/{workspace['id']}/inbox")

    assert not storage_root().exists()


def test_the_listing_follows_the_folder(client: TestClient) -> None:
    """The disk is the truth: a file the reader tidied away disappears here."""
    workspace = _bare_workspace(client)
    _upload(client, workspace["id"], "verslag.md", b"# Verslag\n")
    _upload(client, workspace["id"], "notities.md", b"# Notities\n")

    (workspace_inbox(workspace["id"]) / "verslag.md").unlink()

    names = [f["name"] for f in client.get(f"/api/workspaces/{workspace['id']}/inbox").json()["files"]]
    assert names == ["notities.md"]


# ---------------------------------------------------------------------------
# Uploading
# ---------------------------------------------------------------------------


def test_a_dropped_document_is_stored_and_readable_through_the_document_route(
    client: TestClient,
) -> None:
    """The whole point: what lands in the inbox opens in the reading pane."""
    workspace = _bare_workspace(client)

    created = _upload(client, workspace["id"], "verslag.md", "# Verslag\n\nTekst.\n")

    assert created.status_code == 201
    body = created.json()
    assert body["path"] == f"{INBOX_DIR}/verslag.md"
    assert body["name"] == "verslag.md"
    assert body["readable"] is True
    assert body["unreadable_reason"] is None

    # Through the ordinary document route, with the repository id the upload
    # just returned -- no special path for inbox documents.
    document = client.get(
        f"/api/workspaces/{workspace['id']}/repositories/{body['repository_id']}/document",
        params={"path": body["path"]},
    )
    assert document.status_code == 200
    assert document.json()["raw_markdown"] == "# Verslag\n\nTekst.\n"


def test_ten_mixed_files_all_land(client: TestClient) -> None:
    """The acceptance test for this phase, at the level the drop zone works."""
    workspace = _bare_workspace(client)
    files = [
        ("notulen.md", b"# Notulen\n"),
        ("plan.txt", b"Planning voor het najaar.\n"),
        ("lees-mij.markdown", b"# Lees mij\n"),
        ("bijlage.mdx", b"# Bijlage\n"),
        # Stored but unreadable: Apollo keeps what it was given and says so
        # rather than pretending the file was never dropped.
        ("oude-scan.pdf", b"not really a pdf"),
        ("concept.docx", b"not a zip either"),
    ]

    for name, content in files:
        assert _upload(client, workspace["id"], name, content).status_code == 201

    listing = client.get(f"/api/workspaces/{workspace['id']}/inbox").json()
    assert len(listing["files"]) == len(files)
    assert listing["repository_id"] is not None
    on_disk = sorted(p.name for p in workspace_inbox(workspace["id"]).iterdir())
    assert on_disk == sorted(name for name, _ in files)


def test_the_same_name_twice_keeps_both_documents(client: TestClient) -> None:
    """Dropping a second `verslag.md` must not replace the first one."""
    workspace = _bare_workspace(client)

    first = _upload(client, workspace["id"], "verslag.md", b"# Eerste\n")
    second = _upload(client, workspace["id"], "verslag.md", b"# Tweede\n")

    assert first.json()["name"] == "verslag.md"
    assert second.json()["name"] == "verslag-2.md"
    assert (workspace_inbox(workspace["id"]) / "verslag.md").read_bytes() == b"# Eerste\n"


def test_a_file_that_is_not_a_document_is_refused_with_a_reason(
    client: TestClient,
) -> None:
    workspace = _bare_workspace(client)

    refused = _upload(client, workspace["id"], "archief.zip", b"PK\x03\x04")

    assert refused.status_code == 400
    assert "not a document" in refused.json()["detail"]


def test_a_file_above_the_ceiling_is_refused_before_anything_is_stored(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _bare_workspace(client)
    monkeypatch.setattr(storage, "MAX_UPLOAD_BYTES", 8)

    refused = _upload(client, workspace["id"], "groot.md", b"x" * 9)

    assert refused.status_code == 400
    assert not storage_root().exists()


def test_a_traversal_in_the_dropped_name_stays_inside_the_inbox(
    client: TestClient,
) -> None:
    workspace = _bare_workspace(client)

    created = _upload(client, workspace["id"], "../../../ontsnapt.md", b"# Weg\n")

    assert created.status_code == 201
    assert created.json()["path"] == f"{INBOX_DIR}/ontsnapt.md"


def test_an_upload_into_a_workspace_that_does_not_exist_is_a_404(
    client: TestClient,
) -> None:
    assert _upload(client, 999999, "verslag.md", b"# Verslag\n").status_code == 404


# ---------------------------------------------------------------------------
# The intake is not the documentation
# ---------------------------------------------------------------------------


def test_the_storage_repository_is_not_the_workspaces_documentation(
    client: TestClient,
) -> None:
    """The rule that keeps a scan from examining the intake instead of the docs.

    A workspace whose only repository is Apollo's storage has *no* documentation
    repository, and an inventory run must say so rather than completing over an
    empty inbox and reporting "nothing found" about the reader's documentation.
    """
    workspace = _bare_workspace(client)
    _upload(client, workspace["id"], "verslag.md", b"# Verslag\n")

    run = client.post(f"/api/workspaces/{workspace['id']}/inventory/runs", json={})

    assert run.status_code == 409
    assert "no documentation repository" in run.json()["detail"]


def test_dropping_a_file_does_not_displace_the_registered_folder(
    client: TestClient, workspace: dict
) -> None:
    """Two documentation repositories can coexist; the registered folder stays
    the one that means 'my documentation'."""
    created = _upload(client, workspace["id"], "verslag.md", b"# Verslag\n")

    repos = client.get(f"/api/workspaces/{workspace['id']}").json()["repositories"]
    registered = next(
        r for r in repos if r["kind"] == "documentation" and not r["is_storage"]
    )
    storage_repo = next(r for r in repos if r["is_storage"])

    assert registered["name"] == "gaia-docs"
    assert created.json()["repository_id"] == storage_repo["id"]
    assert storage_repo["id"] != registered["id"]


# ---------------------------------------------------------------------------
# The route table
# ---------------------------------------------------------------------------


def test_there_is_no_route_that_removes_a_document_from_the_inbox() -> None:
    """Checked against the table, because that is where the rule could break."""
    schema = create_app().openapi()
    paths = schema["paths"]
    # Sanity: the endpoints this test is about have to be in the table, or the
    # assertion below would pass over an empty set.
    assert "/api/workspaces/{workspace_id}/inbox" in paths
    assert "/api/workspaces/{workspace_id}/inbox/upload" in paths

    deletes = [path for path, operations in paths.items() if "delete" in operations]

    assert [path for path in deletes if "inbox" in path] == []

