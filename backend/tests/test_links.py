"""Tests for real Markdown links, and for the document focus pointer.

Two things are being pinned here, both of which were promises the UI made that
the code did not keep:

1. The context panel claimed to describe a document while showing only Delphi
   Pulse's *inferred* connections. A link the author wrote was invisible. These
   tests are about what the text says, not what a model proposed.

2. "Ask about this document" opened a chat window and nothing more. The button
   has to be able to name the file, and a name that does not resolve must be
   refused rather than passed on -- otherwise the model answers "this document"
   from a guess.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.links import collect_links


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A small repository with links in every awkward direction."""
    (tmp_path / "architecture").mkdir()
    (tmp_path / "decisions").mkdir()
    (tmp_path / "architecture" / "overview.md").write_text(
        "# Overview\n\n"
        "See the [memory ADR](../decisions/memory-adr.md) and "
        "[the foundations](./foundations.md).\n"
        "Back to [self](overview.md#section) and up [one level]"
        "(../architecture/overview.md).\n",
        encoding="utf-8",
    )
    (tmp_path / "architecture" / "foundations.md").write_text(
        "# Foundations\n\nNothing here yet.\n", encoding="utf-8"
    )
    (tmp_path / "decisions" / "memory-adr.md").write_text(
        "# Memory ADR\n\n"
        "Supersedes [the old note](../architecture/legacy.md).\n"
        "External: [the RFC](https://example.org/rfc) and [mail](mailto:a@b.c).\n",
        encoding="utf-8",
    )
    (tmp_path / "architecture" / "legacy.md").write_text(
        "# Legacy\n\nSuperseded by [the memory ADR](../decisions/memory-adr.md).\n",
        encoding="utf-8",
    )
    return tmp_path


class TestOutbound:
    def test_relative_links_resolve_against_the_linking_file(
        self, repo: Path
    ) -> None:
        result = collect_links(repo, "architecture/overview.md")
        assert {l.path for l in result.outbound} == {
            "decisions/memory-adr.md",
            "architecture/foundations.md",
        }

    def test_link_text_is_kept(self, repo: Path) -> None:
        result = collect_links(repo, "architecture/overview.md")
        texts = {l.path: l.text for l in result.outbound}
        assert texts["decisions/memory-adr.md"] == "memory ADR"

    def test_a_link_to_itself_is_not_a_neighbour(self, repo: Path) -> None:
        # `overview.md#section` and `../architecture/overview.md` both resolve
        # to the subject. Listing them would claim the document is related to
        # itself, which is noise rather than context.
        result = collect_links(repo, "architecture/overview.md")
        assert "architecture/overview.md" not in {l.path for l in result.outbound}

    def test_external_links_are_reported_but_not_resolved(self, repo: Path) -> None:
        result = collect_links(repo, "decisions/memory-adr.md")
        assert {e.target for e in result.external} == {
            "https://example.org/rfc",
            "mailto:a@b.c",
        }
        # Not documents, so they are never offered as openable rows.
        assert all("example.org" not in l.path for l in result.outbound)

    def test_a_missing_target_is_dropped_rather_than_listed(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "a.md").write_text("[gone](./nowhere.md)\n", encoding="utf-8")
        result = collect_links(tmp_path, "a.md")
        assert result.outbound == []


class TestInbound:
    def test_backlinks_are_found(self, repo: Path) -> None:
        result = collect_links(repo, "decisions/memory-adr.md")
        assert {l.path for l in result.inbound} == {
            "architecture/overview.md",
            "architecture/legacy.md",
        }

    def test_inbound_text_is_the_link_text_from_the_other_file(
        self, repo: Path
    ) -> None:
        result = collect_links(repo, "decisions/memory-adr.md")
        texts = {l.path: l.text for l in result.inbound}
        assert texts["architecture/legacy.md"] == "the memory ADR"

    def test_a_document_with_no_backlinks_says_so(self, tmp_path: Path) -> None:
        (tmp_path / "lonely.md").write_text("# Lonely\n", encoding="utf-8")
        (tmp_path / "other.md").write_text("# Other\n", encoding="utf-8")
        result = collect_links(tmp_path, "lonely.md")
        assert result.inbound == []

    def test_matching_is_on_the_resolved_path_not_the_written_text(
        self, tmp_path: Path
    ) -> None:
        # Two different spellings of the same destination: one level up, and two
        # levels up from a nested file. A textual comparison would see two
        # different target strings, treat them as different documents, and
        # report a phantom neighbour.
        (tmp_path / "architecture").mkdir()
        (tmp_path / "notes" / "deep").mkdir(parents=True)
        (tmp_path / "architecture" / "overview.md").write_text("# O\n", encoding="utf-8")
        (tmp_path / "notes" / "one.md").write_text(
            "[o](../architecture/overview.md)\n", encoding="utf-8"
        )
        (tmp_path / "notes" / "deep" / "two.md").write_text(
            "[o](../../architecture/overview.md)\n", encoding="utf-8"
        )
        result = collect_links(tmp_path, "architecture/overview.md")
        assert {l.path for l in result.inbound} == {"notes/one.md", "notes/deep/two.md"}


class TestSafety:
    def test_a_link_escaping_the_root_is_dropped(self, tmp_path: Path) -> None:
        # `../../..` resolves outside the repository. It is not a document here,
        # and following it would be a read outside the authorized root.
        outside = tmp_path.parent / "outside.md"
        outside.write_text("secret\n", encoding="utf-8")
        (tmp_path / "a.md").write_text("[out](../../outside.md)\n", encoding="utf-8")
        try:
            result = collect_links(tmp_path, "a.md")
            assert result.outbound == []
        finally:
            outside.unlink(missing_ok=True)

    def test_the_inbound_scan_stays_inside_the_repository(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")
        result = collect_links(tmp_path, "a.md")
        for link in [*result.outbound, *result.inbound]:
            assert ".." not in link.path.split("/")

    def test_a_non_document_suffix_is_not_offered_as_openable(
        self, tmp_path: Path
    ) -> None:
        # A diagram is a real reference, but the reading pane cannot open it, so
        # listing it would be a dead row.
        (tmp_path / "a.md").write_text("[diagram](./d.png)\n", encoding="utf-8")
        (tmp_path / "d.png").write_bytes(b"\x89PNG\r\n")
        result = collect_links(tmp_path, "a.md")
        assert result.outbound == []


class TestRobustness:
    def test_an_empty_document_has_no_links(self, tmp_path: Path) -> None:
        (tmp_path / "empty.md").write_text("", encoding="utf-8")
        result = collect_links(tmp_path, "empty.md")
        assert (result.outbound, result.inbound) == ([], [])

    def test_a_document_that_does_not_exist_is_empty_not_an_error(
        self, tmp_path: Path
    ) -> None:
        # The panel must still describe what it can when one file is missing,
        # rather than the whole request failing.
        (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")
        result = collect_links(tmp_path, "nope.md")
        assert result.path == "nope.md"
        assert result.outbound == []

    def test_the_same_link_twice_is_one_reference(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text(
            "[b](./b.md) and again [b](./b.md)\n", encoding="utf-8"
        )
        (tmp_path / "b.md").write_text("# B\n", encoding="utf-8")
        result = collect_links(tmp_path, "a.md")
        assert len(result.outbound) == 1


class TestEndpoint:
    """The route, including the refusals.

    The panel reads this over HTTP, so the guarantees above are only worth
    anything if the endpoint preserves them -- and in particular if it does not
    become a way to read a file outside the registered repository.
    """

    def test_the_endpoint_reports_both_directions(
        self, client, workspace: dict, doc_repo: Path
    ) -> None:
        (doc_repo / "notes.md").write_text(
            "# Scratch\n\nSee [the principles](./foundation/principles.md).\n",
            encoding="utf-8",
        )
        (doc_repo / "architecture" / "overview.md").write_text(
            "# Architecture overview\n\nBack to [the notes](../notes.md).\n",
            encoding="utf-8",
        )
        repo_id = workspace["repositories"][0]["id"]
        body = client.get(
            f"/api/workspaces/{workspace['id']}/repositories/{repo_id}/document/links",
            params={"path": "notes.md"},
        )
        assert body.status_code == 200
        payload = body.json()
        assert [l["path"] for l in payload["outbound"]] == ["foundation/principles.md"]
        assert [l["path"] for l in payload["inbound"]] == ["architecture/overview.md"]

    def test_a_path_escaping_the_repository_is_refused(
        self, client, workspace: dict
    ) -> None:
        repo_id = workspace["repositories"][0]["id"]
        response = client.get(
            f"/api/workspaces/{workspace['id']}/repositories/{repo_id}/document/links",
            params={"path": "../../../etc/passwd"},
        )
        assert response.status_code == 400

    def test_another_workspaces_repository_is_not_reachable(
        self, client, workspace: dict
    ) -> None:
        other = client.post("/api/workspaces", json={"name": "Other"}).json()
        response = client.get(
            f"/api/workspaces/{other['id']}/repositories/{workspace['repositories'][0]['id']}/document/links",
            params={"path": "notes.md"},
        )
        assert response.status_code == 404
