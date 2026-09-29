"""The group's HTTP surface, exercised the way the drag-and-drop UI will.

The service tests already pin the behaviour; these pin the contract the browser
depends on -- that a drop is one request, that a group can be emptied without a
file changing, and above all that there is no way to ask this API to remove a
document from disk. The last one is a property of the route table, so it is
asserted against the table rather than hoped for.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def _doc_repo_id(workspace: dict) -> int:
    return next(r["id"] for r in workspace["repositories"] if r["name"] == "gaia-docs")


def _paths(app) -> set[str]:
    """Every route path in the app.

    Read from the OpenAPI schema rather than by walking ``app.routes``: FastAPI
    keeps included routers behind a wrapper object, so walking the attribute
    finds none of them -- and a check that silently finds nothing would pass
    while asserting nothing, which is the worst possible outcome for a test
    whose subject is "there is no way to do X".
    """
    return set(app.openapi().get("paths", {}))


def _before(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.parts
    }


# ---------------------------------------------------------------------------
# The route table itself
# ---------------------------------------------------------------------------


def test_there_is_no_way_to_delete_a_document_through_this_api() -> None:
    """The product's hard rule, checked where it would be broken.

    "Apollo verwijdert nooit documenten" is only true while no route accepts a
    request that could remove one. A DELETE on a *group* is fine -- it removes
    the view. So every DELETE route is listed here and checked: each one is
    scoped to a group or a placement, and none of them takes a document and
    returns nothing.
    """
    app = create_app()
    paths = _paths(app)
    # Sanity check that this list is real: a group route has to be in it, or the
    # assertions below would pass over an empty set.
    assert "/api/workspaces/{workspace_id}/groups" in paths

    deletes = sorted(
        p for p in paths if "group" in p.lower() and "{group_id}" in p
    )

    # There are exactly these three group-scoped routes, and none of them is
    # about a document on disk: two deletes (the group, a placement) and the
    # accept endpoint, which only flips a flag.
    assert deletes == [
        "/api/workspaces/{workspace_id}/groups/{group_id}",
        "/api/workspaces/{workspace_id}/groups/{group_id}/accept",
        "/api/workspaces/{workspace_id}/groups/{group_id}/documents",
    ]


# ---------------------------------------------------------------------------
# Creating and editing
# ---------------------------------------------------------------------------


def test_a_group_is_created_empty(client: TestClient, workspace: dict) -> None:
    base = f"/api/workspaces/{workspace['id']}/groups"

    created = client.post(base, json={"name": "Projecten"})

    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Projecten"
    assert body["source"] == "user"
    assert body["document_count"] == 0
    assert body["reviewed"] is True


def test_a_delphi_proposal_arrives_unreviewed(client: TestClient, workspace: dict) -> None:
    base = f"/api/workspaces/{workspace['id']}/groups"

    created = client.post(base, json={"name": "Gaia sources", "source": "ai"})

    assert created.json()["reviewed"] is False


def test_accepting_a_proposal_over_the_api(client: TestClient, workspace: dict) -> None:
    base = f"/api/workspaces/{workspace['id']}/groups"
    gid = client.post(base, json={"name": "Gaia sources", "source": "ai"}).json()["id"]

    accepted = client.post(f"{base}/{gid}/accept")

    assert accepted.status_code == 200
    assert accepted.json()["reviewed"] is True
    # Reflected in the list too, not only in the response to the accept call.
    listed = client.get(base).json()
    assert next(g for g in listed if g["id"] == gid)["reviewed"] is True


def test_accepting_an_unknown_group_is_404(client: TestClient, workspace: dict) -> None:
    response = client.post(f"/api/workspaces/{workspace['id']}/groups/999999/accept")

    assert response.status_code == 404


def test_rejecting_a_proposal_over_the_api_is_the_existing_delete(
    client: TestClient, workspace: dict
) -> None:
    """No separate reject endpoint: the board rejects by deleting."""
    base = f"/api/workspaces/{workspace['id']}/groups"
    gid = client.post(base, json={"name": "Gaia sources", "source": "ai"}).json()["id"]
    client.post(
        f"{base}/{gid}/documents",
        json={"repository_id": _doc_repo_id(workspace), "path": "notes.md"},
    )

    rejected = client.delete(f"{base}/{gid}")

    assert rejected.status_code == 204
    assert client.get(base).json() == []


def test_a_duplicate_name_is_refused_with_a_reason(
    client: TestClient, workspace: dict
) -> None:
    base = f"/api/workspaces/{workspace['id']}/groups"
    client.post(base, json={"name": "Projecten"})

    again = client.post(base, json={"name": "Projecten"})

    assert again.status_code == 400
    assert "already exists" in again.json()["detail"]


def test_a_group_is_renamed_without_its_documents_moving(
    client: TestClient, workspace: dict
) -> None:
    base = f"/api/workspaces/{workspace['id']}/groups"
    gid = client.post(base, json={"name": "Oud"}).json()["id"]
    client.post(
        f"{base}/{gid}/documents",
        json={"repository_id": _doc_repo_id(workspace), "path": "notes.md"},
    )

    renamed = client.patch(f"{base}/{gid}", json={"name": "Nieuw"})

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Nieuw"
    assert renamed.json()["document_count"] == 1


# ---------------------------------------------------------------------------
# Dragging
# ---------------------------------------------------------------------------


def test_a_document_is_dropped_into_a_group(
    client: TestClient, workspace: dict
) -> None:
    base = f"/api/workspaces/{workspace['id']}/groups"
    gid = client.post(base, json={"name": "Projecten"}).json()["id"]

    dropped = client.post(
        f"{base}/{gid}/documents",
        json={"repository_id": _doc_repo_id(workspace), "path": "notes.md"},
    )

    assert dropped.status_code == 201
    assert dropped.json()["path"] == "notes.md"
    assert dropped.json()["placed_by"] == "user"
    listed = client.get(f"{base}/{gid}/documents").json()
    assert [d["path"] for d in listed] == ["notes.md"]


def test_a_placement_made_here_cannot_be_declared_provisional(
    client: TestClient, workspace: dict
) -> None:
    """The reader's arrangement is what a later analysis must not undo, so it
    cannot be handed in as a maybe -- not even by a client that asks nicely."""
    base = f"/api/workspaces/{workspace['id']}/groups"
    gid = client.post(base, json={"name": "Projecten"}).json()["id"]

    response = client.post(
        f"{base}/{gid}/documents",
        json={
            "repository_id": _doc_repo_id(workspace),
            "path": "notes.md",
            "placed_by": "ai",
        },
    )

    # The field is not part of the request at all: this is the reader acting, and
    # the server is the only one who decides whose decision this was.
    assert response.json()["placed_by"] == "user"


def test_a_document_moves_between_groups_in_one_request(
    client: TestClient, workspace: dict
) -> None:
    """One request, so a failure cannot leave it in both or neither."""
    base = f"/api/workspaces/{workspace['id']}/groups"
    repo_id = _doc_repo_id(workspace)
    left = client.post(base, json={"name": "Links"}).json()["id"]
    right = client.post(base, json={"name": "Rechts"}).json()["id"]
    client.post(f"{base}/{left}/documents", json={"repository_id": repo_id, "path": "notes.md"})

    response = client.post(
        f"{base}/move",
        json={
            "repository_id": repo_id,
            "path": "notes.md",
            "from_group_id": left,
            "to_group_id": right,
        },
    )

    # 200 with a body, not 204: the response now says whether a *file* was
    # proposed to move, which is the one thing a drag cannot leave ambiguous.
    assert response.status_code == 200
    assert response.json()["proposal_id"] is None
    assert client.get(f"{base}/{left}/documents").json() == []
    assert [d["path"] for d in client.get(f"{base}/{right}/documents").json()] == ["notes.md"]


def test_a_move_touches_no_file(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    before = _before(doc_repo)
    base = f"/api/workspaces/{workspace['id']}/groups"
    repo_id = _doc_repo_id(workspace)
    left = client.post(base, json={"name": "Links"}).json()["id"]
    right = client.post(base, json={"name": "Rechts"}).json()["id"]
    client.post(f"{base}/{left}/documents", json={"repository_id": repo_id, "path": "notes.md"})

    client.post(
        f"{base}/move",
        json={"repository_id": repo_id, "path": "notes.md",
              "from_group_id": left, "to_group_id": right},
    )

    assert _before(doc_repo) == before


def test_dropping_a_document_onto_the_same_group_again_is_fine(
    client: TestClient, workspace: dict
) -> None:
    base = f"/api/workspaces/{workspace['id']}/groups"
    gid = client.post(base, json={"name": "Projecten"}).json()["id"]
    payload = {"repository_id": _doc_repo_id(workspace), "path": "notes.md"}

    client.post(f"{base}/{gid}/documents", json=payload)
    again = client.post(f"{base}/{gid}/documents", json=payload)

    assert again.status_code == 201
    assert len(client.get(f"{base}/{gid}/documents").json()) == 1


def test_a_document_that_does_not_exist_cannot_be_dropped(
    client: TestClient, workspace: dict
) -> None:
    base = f"/api/workspaces/{workspace['id']}/groups"
    gid = client.post(base, json={"name": "Projecten"}).json()["id"]

    response = client.post(
        f"{base}/{gid}/documents",
        json={"repository_id": _doc_repo_id(workspace), "path": "nope.md"},
    )

    assert response.status_code == 400
    assert "Not a file" in response.json()["detail"]


def test_a_path_escaping_the_repository_cannot_be_dropped(
    client: TestClient, workspace: dict
) -> None:
    base = f"/api/workspaces/{workspace['id']}/groups"
    gid = client.post(base, json={"name": "Projecten"}).json()["id"]

    response = client.post(
        f"{base}/{gid}/documents",
        json={"repository_id": _doc_repo_id(workspace), "path": "../../../etc/passwd"},
    )

    assert response.status_code == 400
    assert "traversal" in response.json()["detail"]


# ---------------------------------------------------------------------------
# The archive
# ---------------------------------------------------------------------------


def test_a_document_can_be_archived_and_brought_back(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    """Archiving is a move into a group, so it is reversible and keeps the file."""
    before = _before(doc_repo)
    base = f"/api/workspaces/{workspace['id']}/groups"
    repo_id = _doc_repo_id(workspace)
    current = client.post(base, json={"name": "Huidig"}).json()["id"]
    archive = client.post(
        base, json={"name": "Archief", "is_archive": True}
    ).json()["id"]
    client.post(f"{base}/{current}/documents", json={"repository_id": repo_id, "path": "notes.md"})

    to_archive = client.post(
        f"{base}/move",
        json={"repository_id": repo_id, "path": "notes.md",
              "from_group_id": current, "to_group_id": archive},
    )
    assert to_archive.status_code == 200
    # The archive stands for a folder, so archiving proposes a move rather than
    # performing one. Dragging it in is not the same as having done it.
    proposal_id = to_archive.json()["proposal_id"]
    assert proposal_id is not None

    # Kept, not deleted -- and not yet moved either.
    assert _before(doc_repo) == before
    assert (doc_repo / "notes.md").is_file()

    # And back again.
    client.post(
        f"{base}/move",
        json={"repository_id": repo_id, "path": "notes.md",
              "from_group_id": archive, "to_group_id": current},
    )
    assert len(client.get(f"{base}/{current}/documents").json()) == 1
    assert client.get(f"{base}/{archive}/documents").json() == []


def test_archived_and_accepted_actually_lands_in_the_archive_folder(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    """The whole point of the archive folder, end to end.

    Two steps, and the first one changes nothing on disk. That separation is
    what keeps "archive" from quietly becoming a delete: the file is still there
    to be read at its old path until somebody says otherwise.
    """
    base = f"/api/workspaces/{workspace['id']}/groups"
    repo_id = _doc_repo_id(workspace)
    archive = client.post(
        base, json={"name": "Archief", "is_archive": True}
    ).json()
    assert archive["folder"] == "Archief"

    proposal_id = client.post(
        f"{base}/{archive['id']}/documents",
        json={"repository_id": repo_id, "path": "notes.md"},
    ).json()["proposal_id"]
    assert proposal_id is not None
    assert (doc_repo / "notes.md").is_file()

    client.post(
        f"/api/workspaces/{workspace['id']}/proposals/{proposal_id}/accept"
    )
    assert (doc_repo / "Archief" / "notes.md").is_file()
    assert not (doc_repo / "notes.md").exists()


def test_the_archive_is_listed_last(client: TestClient, workspace: dict) -> None:
    base = f"/api/workspaces/{workspace['id']}/groups"
    client.post(base, json={"name": "Alpha"})
    client.post(base, json={"name": "Archief", "is_archive": True})
    client.post(base, json={"name": "Beta"})

    assert [g["name"] for g in client.get(base).json()] == ["Alpha", "Beta", "Archief"]


def test_only_one_archive_is_allowed(client: TestClient, workspace: dict) -> None:
    base = f"/api/workspaces/{workspace['id']}/groups"
    client.post(base, json={"name": "Archief", "is_archive": True})

    second = client.post(base, json={"name": "Oud", "is_archive": True})

    assert second.status_code == 400
    assert "already has an archive" in second.json()["detail"]


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


def test_a_group_belongs_to_one_workspace(
    client: TestClient, workspace: dict
) -> None:
    other = client.post("/api/workspaces", json={"name": "Other"}).json()
    gid = client.post(
        f"/api/workspaces/{workspace['id']}/groups", json={"name": "Projecten"}
    ).json()["id"]

    # Read it from the other workspace: nothing.
    assert client.get(f"/api/workspaces/{other['id']}/groups/{gid}/documents").status_code == 404
    # And the listing does not leak it.
    assert [g["name"] for g in client.get(f"/api/workspaces/{other['id']}/groups").json()] == []


def test_where_a_document_lives_is_answerable(
    client: TestClient, workspace: dict
) -> None:
    """The sidebar's core question: "where does this sit?"."""
    base = f"/api/workspaces/{workspace['id']}/groups"
    repo_id = _doc_repo_id(workspace)
    one = client.post(base, json={"name": "Projecten"}).json()["id"]
    two = client.post(base, json={"name": "Referentie"}).json()["id"]
    client.post(f"{base}/{one}/documents", json={"repository_id": repo_id, "path": "notes.md"})
    client.post(f"{base}/{two}/documents", json={"repository_id": repo_id, "path": "notes.md"})

    response = client.get(
        f"/api/workspaces/{workspace['id']}/documents/groups",
        params={"repository_id": repo_id, "path": "notes.md"},
    )

    assert [g["name"] for g in response.json()] == ["Projecten", "Referentie"]


def test_deleting_a_group_leaves_the_file(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    before = _before(doc_repo)
    base = f"/api/workspaces/{workspace['id']}/groups"
    gid = client.post(base, json={"name": "Projecten"}).json()["id"]
    client.post(
        f"{base}/{gid}/documents",
        json={"repository_id": _doc_repo_id(workspace), "path": "notes.md"},
    )

    deleted = client.delete(f"{base}/{gid}")

    assert deleted.status_code == 204
    assert _before(doc_repo) == before
    assert (doc_repo / "notes.md").is_file()
    assert client.get(base).json() == []


# ---------------------------------------------------------------------------
# Areas and templates
# ---------------------------------------------------------------------------


def test_a_group_can_be_created_under_an_area(client: TestClient, workspace: dict) -> None:
    base = f"/api/workspaces/{workspace['id']}/groups"
    area_id = client.post(base, json={"name": "Architectuur"}).json()["id"]

    topic = client.post(
        base, json={"name": "Geheugenbeleid", "parent_group_id": area_id}
    )

    assert topic.status_code == 201
    assert topic.json()["parent_group_id"] == area_id


def test_applying_a_template_creates_its_areas(client: TestClient, workspace: dict) -> None:
    response = client.post(
        f"/api/workspaces/{workspace['id']}/groups/apply-template",
        json={"template": "software"},
    )

    assert response.status_code == 200
    names = {area["name"] for area in response.json()}
    assert names == {"Architectuur", "Product", "Besluiten", "Onderzoek"}
    assert all(area["reviewed"] for area in response.json())
    assert all(area["folder"] == area["name"] for area in response.json())

    listed = {g["name"] for g in client.get(f"/api/workspaces/{workspace['id']}/groups").json()}
    assert names <= listed


def test_applying_a_template_twice_does_not_duplicate_areas(
    client: TestClient, workspace: dict
) -> None:
    base = f"/api/workspaces/{workspace['id']}/groups/apply-template"
    client.post(base, json={"template": "research"})

    client.post(base, json={"template": "research"})

    names = [
        g["name"]
        for g in client.get(f"/api/workspaces/{workspace['id']}/groups").json()
        if g["name"] in ("Literatuur", "Methode", "Resultaten", "Besluiten")
    ]
    assert sorted(names) == ["Besluiten", "Literatuur", "Methode", "Resultaten"]


def test_an_unknown_template_is_refused(client: TestClient, workspace: dict) -> None:
    response = client.post(
        f"/api/workspaces/{workspace['id']}/groups/apply-template",
        json={"template": "nonexistent"},
    )

    assert response.status_code == 400
    assert "Unknown template" in response.json()["detail"]
