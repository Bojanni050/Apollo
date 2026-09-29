"""The folder the reader works in.

The rule with the most weight behind it is the one that must not change for
anybody by accident: **a workspace that never chose a folder behaves exactly as
it did before this existed.** Everything stays under Apollo's own storage until
somebody says otherwise, and nothing is moved on the strength of a column nobody
filled in.

The second rule is that choosing a folder never moves a document. It changes
where new ones go and what the inbox repository row points at; the files that are
already there stay exactly where they are.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.models import Repository
from app.services import storage as storage_service
from app.services.storage import (
    INBOX_DIR,
    ensure_working_repo,
    storage_root,
    workspace_inbox,
)


def _upload(
    client: TestClient, workspace_id: int, name: str, body: bytes = b"# Notities\n"
):
    return client.post(
        f"/api/workspaces/{workspace_id}/inbox/upload",
        files={"file": (name, body, "text/markdown")},
    )


def _working(client: TestClient, workspace_id: int) -> dict:
    return client.get(f"/api/workspaces/{workspace_id}/working-dir").json()


def _choose(client: TestClient, workspace_id: int, path: Path | str) -> dict:
    return client.put(
        f"/api/workspaces/{workspace_id}/working-dir",
        json={"path": str(path)},
    )


# ---------------------------------------------------------------------------
# Nothing changes until somebody chooses
# ---------------------------------------------------------------------------


def test_a_workspace_that_never_chose_is_asked_to(
    client: TestClient, workspace: dict
) -> None:
    """Reported as a question, not as a fact: "Apollo made this folder up" and
    "you chose this one" are different answers and the interface needs to know
    which one it is showing."""
    body = _working(client, workspace["id"])

    assert body["working_dir"] is None
    assert body["warning"] is not None
    assert str(storage_service.storage_root()) in body["inbox_dir"]


def test_a_drop_before_any_choice_lands_where_it_always_did(
    client: TestClient, workspace: dict
) -> None:
    _upload(client, workspace["id"], "verslag.md")

    assert (workspace_inbox(workspace["id"]) / "verslag.md").is_file()


# ---------------------------------------------------------------------------
# Choosing
# ---------------------------------------------------------------------------


def test_a_chosen_folder_becomes_where_documents_go(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    chosen = tmp_path / "mijn-werkmap"
    chosen.mkdir()

    body = _choose(client, workspace["id"], chosen).json()

    assert body["working_dir"] == str(chosen)
    assert body["inbox_dir"] == str(chosen / INBOX_DIR)
    _upload(client, workspace["id"], "verslag.md")
    assert (chosen / INBOX_DIR / "verslag.md").is_file()
    # And not in Apollo's storage, which is the entire point of choosing.
    assert not (storage_root() / f"workspace_{workspace['id']}" / INBOX_DIR).exists()


def test_choosing_a_folder_creates_its_repository_without_an_upload(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    """The point of the change: a reader who chose a folder and uploaded
    nothing yet must still be able to chat, because a repository row already
    exists for the folder they chose."""
    chosen = tmp_path / "mijn-werkmap"
    chosen.mkdir()

    _choose(client, workspace["id"], chosen)

    repos = client.get(f"/api/workspaces/{workspace['id']}").json()["repositories"]
    storage_repo = next(r for r in repos if r.get("is_storage"))
    assert storage_repo["local_path"] == str(chosen)
    assert (chosen / INBOX_DIR).is_dir()


def test_a_chosen_folder_is_stored_resolved(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    """A path that means something different after the application is started
    from another directory is worse than no choice at all."""
    chosen = tmp_path / "mijn-werkmap"
    chosen.mkdir()

    _choose(client, workspace["id"], chosen)

    assert Path(_working(client, workspace["id"])["working_dir"]).is_absolute()


def test_an_empty_folder_is_accepted_without_a_warning(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    """Empty is what this wants, so it is the case with nothing to say."""
    chosen = tmp_path / "leeg"
    chosen.mkdir()

    body = _choose(client, workspace["id"], chosen).json()

    assert body["empty"] is True
    assert body["entries"] == 0
    assert body["warning"] is None


def test_a_folder_with_something_in_it_is_accepted_and_reported(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    """A folder somebody already keeps documents in is a legitimate choice.
    Refusing it would be Apollo deciding their own folders are not allowed; the
    warning is how it says what it will and will not touch."""
    chosen = tmp_path / "al-groen"
    chosen.mkdir()
    (chosen / "mijn-geschrift.md").write_text("# Mijn\n", encoding="utf-8")

    body = _choose(client, workspace["id"], chosen).json()

    assert body["empty"] is False
    assert body["entries"] == 1
    assert "never touch" in body["warning"]
    assert (chosen / "mijn-geschrift.md").read_text(encoding="utf-8") == "# Mijn\n"


def test_choosing_moves_no_document(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    """The documents stay where they are. A choice changes where new ones go, not
    what happened to the old ones -- moving them would be an action nobody asked
    for, performed by a setting."""
    _upload(client, workspace["id"], "verslag.md", b"# Het oude\n")
    existing = workspace_inbox(workspace["id"]) / "verslag.md"
    fingerprint = existing.read_bytes()
    chosen = tmp_path / "nieuw"
    chosen.mkdir()

    _choose(client, workspace["id"], chosen)

    assert existing.read_bytes() == fingerprint
    assert not (chosen / INBOX_DIR / "verslag.md").exists()


def test_the_inbox_repository_follows_the_choice(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    """The row is moved, because a row pointing at a folder the workspace no
    longer uses is a claim about documents that are not there."""
    _upload(client, workspace["id"], "verslag.md")
    chosen = tmp_path / "mijn-werkmap"
    chosen.mkdir()

    _choose(client, workspace["id"], chosen)

    repos = client.get(f"/api/workspaces/{workspace['id']}").json()["repositories"]
    storage_repo = next(r for r in repos if r.get("is_storage"))
    assert storage_repo["local_path"] == str(chosen)


def test_going_back_leaves_nothing_pointing_at_the_old_folder(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    """The half that is easy to forget, and the one that loses documents without
    ever reporting an error: the storage row is what every *read* of a stored
    document goes through. Leave it on the folder the reader gave up and a new
    document is written to one place and looked for in another -- it exists, and
    the app cannot see it."""
    _upload(client, workspace["id"], "eerste.md")
    chosen = tmp_path / "mijn-werkmap"
    chosen.mkdir()
    _choose(client, workspace["id"], chosen)

    _choose(client, workspace["id"], "")

    # Read it back the way the application does: through the storage repository,
    # which resolves paths against its own local_path. Asserting only on the
    # inbox listing would pass either way, because the listing and the write both
    # go through the working folder -- the row is the only thing that can be left
    # behind, so the row is the only thing worth checking.
    _upload(client, workspace["id"], "tweede.md", b"# Tweede\n\nNa de terugweg.\n")
    repos = client.get(f"/api/workspaces/{workspace['id']}").json()["repositories"]
    storage_repo = next(r for r in repos if r.get("is_storage"))
    assert storage_repo["local_path"] == str(workspace_inbox(workspace["id"]).parent)

    opened = client.get(
        f"/api/workspaces/{workspace['id']}/repositories/{storage_repo['id']}/document",
        params={"path": f"{INBOX_DIR}/tweede.md"},
    )
    assert opened.status_code == 200, opened.text
    assert "Na de terugweg" in opened.json()["raw_markdown"]


# ---------------------------------------------------------------------------
# What is refused
# ---------------------------------------------------------------------------


def test_a_folder_that_does_not_exist_is_refused(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    response = _choose(client, workspace["id"], tmp_path / "nee")

    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]


def test_a_file_is_refused(client: TestClient, workspace: dict, tmp_path: Path) -> None:
    target = tmp_path / "bestand.md"
    target.write_text("# Geen map\n", encoding="utf-8")

    response = _choose(client, workspace["id"], target)

    assert response.status_code == 400
    assert "not a folder" in response.json()["detail"]


def test_apollos_own_storage_is_refused(
    client: TestClient, workspace: dict
) -> None:
    """Choosing the folder Apollo would have used anyway gains nothing and costs
    the reader the knowledge that their documents are not in a folder of their
    own."""
    response = _choose(client, workspace["id"], storage_root())

    assert response.status_code == 400
    assert "Apollo's own storage" in response.json()["detail"]


def test_nothing_is_written_into_a_refused_choice(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    """A refusal that created the folder it refused would be a refusal with a
    side effect."""
    target = tmp_path / "nee"

    _choose(client, workspace["id"], target)

    assert not target.exists()


# ---------------------------------------------------------------------------
# The history that makes a move recoverable
# ---------------------------------------------------------------------------


def test_a_chosen_folder_becomes_a_git_repository(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    """Not decoration. The move engine refuses to relocate a file Git does not
    track, so without this every move proposal for a dropped-in document would be
    refused and the proposal flow would look broken."""
    from app.services import git

    chosen = tmp_path / "mijn-werkmap"
    chosen.mkdir()

    _choose(client, workspace["id"], chosen)

    assert git.is_repo(chosen)
    assert git.head_revision(chosen)


def test_a_folder_is_only_initialised_once(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    """Choosing again must not commit anything: a repository that recorded its
    own reconfiguration would make the history say things that never happened to
    the documents."""
    from app.services import git

    chosen = tmp_path / "mijn-werkmap"
    chosen.mkdir()
    _choose(client, workspace["id"], chosen)
    _upload(client, workspace["id"], "verslag.md")
    # Taken *after* the drop, because the drop is a commit of its own: what is
    # being tested is that choosing again adds nothing on top of it.
    first = git.head_revision(chosen)

    _choose(client, workspace["id"], chosen)

    assert git.head_revision(chosen) == first


def test_what_was_in_the_folder_is_recorded_before_anything_else(
    tmp_path: Path
) -> None:
    """The initial commit is the folder as the reader left it. That is the
    baseline every later move is diffed against."""
    from app.services import git

    chosen = tmp_path / "reeds-vol"
    (chosen / "notities").mkdir(parents=True)
    (chosen / "notities" / "januari.md").write_text("# Januari\n", encoding="utf-8")

    ensure_working_repo(chosen)

    assert not git.status(chosen), "the folder as it was should be committed, not pending"
    assert "chosen" in git.recent_log(chosen, 1)[0].get("subject", "")


def test_an_empty_folder_is_still_a_repository(tmp_path: Path) -> None:
    """An empty folder has nothing to commit, and "nothing to commit" is not a
    reason to leave the reader without a history: the starting point exists even
    when there is nothing yet to put in it."""
    from app.services import git

    chosen = tmp_path / "leeg"
    chosen.mkdir()

    ensure_working_repo(chosen)

    assert git.is_repo(chosen)
    assert git.head_revision(chosen)


def test_a_dropped_in_document_is_recorded_too(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    """The half that makes a move possible at all. A document that arrived after
    the repository existed is untracked until something commits it, and an
    untracked file cannot be relocated by the move engine -- so without this every
    move of a dropped-in document would be refused, and the repository created a
    moment earlier would have bought nothing."""
    from app.services import git

    chosen = tmp_path / "mijn-werkmap"
    chosen.mkdir()
    _choose(client, workspace["id"], chosen)

    _upload(client, workspace["id"], "verslag.md")

    assert not git.status(chosen), "a stored document should be recorded, not pending"
    assert "verslag.md" in git.recent_log(chosen, 1)[0].get("subject", "")


def test_the_history_is_apollos_work_not_the_readers(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    """Attributing these commits to the reader's Git identity would be a small lie
    in a log they will read."""
    from app.services import git

    chosen = tmp_path / "mijn-werkmap"
    chosen.mkdir()

    _choose(client, workspace["id"], chosen)

    author = git._run(chosen, ["log", "-1", "--format=%an <%ae>"]).strip()
    assert "Apollo" in author


# ---------------------------------------------------------------------------
# A folder that was already a repository
# ---------------------------------------------------------------------------


def _existing_repo(tmp_path: Path, name: str = "eigen-map") -> Path:
    """A folder the reader already keeps under version control, uncommitted.

    This is the folder they are most likely to choose -- their own documents, in
    a project they already track -- and it is the one the initial-commit path
    never touches.
    """
    import subprocess

    root = tmp_path / name
    root.mkdir()
    (root / "notities").mkdir()
    (root / "notities" / "januari.md").write_text("# Januari\n", encoding="utf-8")
    (root / "notities" / "februari.md").write_text("# Februari\n", encoding="utf-8")
    for args in (
        ["init", "-b", "main"],
        ["config", "user.name", "Someone Else"],
        ["config", "user.email", "someone@example.com"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    return root


def test_choosing_a_folder_that_is_already_a_repository_leaves_its_documents_unrecorded(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    """The dead end this button exists to get out of, stated as a fact.

    ``ensure_working_repo`` returns immediately when the folder is already a
    repository -- correctly, it must not touch somebody's history -- and so the
    documents in it stay untracked. The move engine refuses to relocate an
    untracked file, so a group with a folder proposes a move, the reader accepts,
    and the engine says no. Pinned so the fix cannot quietly stop being needed,
    and so nobody later reads it as a failure of the button.
    """
    from app.services import git

    chosen = _existing_repo(tmp_path)

    _choose(client, workspace["id"], chosen)

    assert git.is_repo(chosen), "the folder is still a repository, untouched"
    assert not git.is_tracked(chosen, "notities/januari.md")
    # And the interface is expected to say so, rather than promising a history
    # it does not have.
    assert _working(client, workspace["id"])["untracked"] == 2


def test_the_button_records_what_was_already_there(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    from app.services import git

    chosen = _existing_repo(tmp_path)
    _choose(client, workspace["id"], chosen)

    adopted = client.post(f"/api/workspaces/{workspace['id']}/working-dir/adopt")

    assert adopted.status_code == 200
    assert adopted.json()["ok"] is True
    assert adopted.json()["committed"] == 2
    assert git.is_tracked(chosen, "notities/januari.md")
    assert git.is_tracked(chosen, "notities/februari.md")
    assert _working(client, workspace["id"])["untracked"] == 0


def test_a_document_can_then_actually_be_moved(
    client: TestClient,
    workspace: dict,
    tmp_path: Path,
    db_session_factory,
) -> None:
    """The reason the button matters, end to end.

    Without it this ends in a 409 that names Git rather than this application,
    which reads as a bug in the product rather than as a folder that was never
    made recoverable.
    """
    chosen = _existing_repo(tmp_path)
    _choose(client, workspace["id"], chosen)
    client.post(f"/api/workspaces/{workspace['id']}/working-dir/adopt")

    # Point the workspace's documentation repository at the reader's own folder.
    # It already has one -- every workspace does -- and the API deliberately does
    # not offer to repoint it, so the row is set the way the rest of the system
    # reads it. A second repository would be refused, correctly: two
    # documentation repositories in one workspace is a state nothing expects.
    repo_id = next(
        r["id"] for r in workspace["repositories"] if r["name"] == "gaia-docs"
    )
    with db_session_factory() as db:
        row = db.get(Repository, repo_id)
        row.local_path = str(chosen)
        db.commit()
    group = client.post(
        f"/api/workspaces/{workspace['id']}/groups",
        json={"name": "Notities", "folder": "Archief-notities"},
    ).json()
    assert group["folder"] == "Archief-notities"

    dropped = client.post(
        f"/api/workspaces/{workspace['id']}/groups/{group['id']}/documents",
        json={"repository_id": repo_id, "path": "notities/januari.md"},
    ).json()
    accepted = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/{dropped['proposal_id']}/accept"
    )

    assert accepted.status_code == 200
    assert (chosen / "Archief-notities" / "januari.md").is_file()
    assert not (chosen / "notities" / "januari.md").exists()


def test_the_button_moves_nothing_and_rewrites_nothing(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    """It writes a commit, and that is all.

    The reader is being asked to point this at a folder of their own, so the
    guarantee is measured rather than promised: every file, byte for byte, before
    and after.
    """
    from app.services import git

    chosen = _existing_repo(tmp_path)
    before = {
        p.relative_to(chosen).as_posix(): p.read_bytes()
        for p in sorted(chosen.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(chosen).parts
    }
    _choose(client, workspace["id"], chosen)

    client.post(f"/api/workspaces/{workspace['id']}/working-dir/adopt")

    after = {
        p.relative_to(chosen).as_posix(): p.read_bytes()
        for p in sorted(chosen.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(chosen).parts
    }
    assert after == before, "recording the folder changed something in it"
    assert not git.status(chosen), "the folder should be clean afterwards"


def test_the_button_leaves_the_readers_git_identity_alone(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    """Two promises, and the second is the subtle one.

    The commit is Apollo's work, so it should *read* as Apollo's work. But the
    folder is the reader's, and ``user.name`` in it is a setting they chose --
    so it must not be rewritten to make the first promise come out right. The
    author is therefore overridden for that one commit rather than stored.
    """
    from app.services import git

    chosen = _existing_repo(tmp_path)
    before = git._run(chosen, ["config", "--get", "user.name"]).strip()
    _choose(client, workspace["id"], chosen)

    client.post(f"/api/workspaces/{workspace['id']}/working-dir/adopt")

    who = git._run(chosen, ["log", "-1", "--format=%an"]).strip()
    after = git._run(chosen, ["config", "--get", "user.name"]).strip()
    assert who == "Apollo", f"the commit should be Apollo's work, not {who!r}"
    assert after == before == "Someone Else", "the reader's own identity was rewritten"


def test_the_button_does_not_touch_existing_commits(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    """Somebody else's history is not Apollo's to rewrite.

    The reader may well have chosen a folder that already has real work in it, so
    "add one commit" has to mean *add*, not amend or reset.
    """
    import subprocess

    from app.services import git

    chosen = _existing_repo(tmp_path)
    subprocess.run(["git", "-C", str(chosen), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(chosen), "commit", "-q", "-m", "their own work"],
        check=True,
        capture_output=True,
    )
    original = subprocess.run(
        ["git", "-C", str(chosen), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _choose(client, workspace["id"], chosen)

    client.post(f"/api/workspaces/{workspace['id']}/working-dir/adopt")

    subjects = [e.get("subject", "") for e in git.recent_log(chosen, 10)]
    assert "their own work" in subjects
    # The earlier revision is still reachable, so nothing was amended or reset.
    still_there = subprocess.run(
        ["git", "-C", str(chosen), "cat-file", "-e", f"{original}^{{commit}}"],
        capture_output=True,
    )
    assert still_there.returncode == 0


def test_the_button_is_idempotent(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    """Pressing it twice says "nothing to record" rather than making a second
    empty commit, and does not claim to have done anything it did not."""
    chosen = _existing_repo(tmp_path)
    _choose(client, workspace["id"], chosen)

    first = client.post(f"/api/workspaces/{workspace['id']}/working-dir/adopt").json()
    second = client.post(f"/api/workspaces/{workspace['id']}/working-dir/adopt").json()

    assert first["committed"] == 2
    assert second["committed"] == 0
    assert second["ok"] is True


def test_the_button_needs_a_folder_first(client: TestClient, workspace: dict) -> None:
    """Refused rather than inventing a folder: there is nothing here to record, and
    recording Apollo's own storage would be a claim nobody made."""
    adopted = client.post(f"/api/workspaces/{workspace['id']}/working-dir/adopt")

    assert adopted.status_code == 409
    assert "Choose a working folder" in adopted.json()["detail"]


def test_a_folder_with_nothing_to_record_does_not_need_the_button(
    client: TestClient, workspace: dict, tmp_path: Path
) -> None:
    """The button is not offered when there is nothing to do.

    A folder Apollo prepared already has a history, so every document in it is
    recoverable and the card must not imply otherwise.
    """
    chosen = tmp_path / "leeg"
    chosen.mkdir()
    _choose(client, workspace["id"], chosen)

    assert _working(client, workspace["id"])["untracked"] == 0
