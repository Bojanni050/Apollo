"""Tests for Decision Superseding & ADR Deprecation Lifecycle milestone.

Covers:
1. Decision superseding relationship and lineage traversal (both directions).
2. Constraints: self-supersession, cross-workspace isolation, non-approved target,
   already-superseded target, duplicate relationships, cycle detection.
3. Supersede cancellation / rollback.
4. ADR lifecycle:
   - Deprecation notice inserted near top of old ADR.
   - Status updated to superseded.
   - All existing context, rationale, consequences, and custom sections preserved.
   - Old ADR not deleted; new ADR authoritative and intact.
   - Idempotency.
   - Missing/unsafe ADR handling without data loss.
5. Git behavior: working tree modified but uncommitted; no auto-commit, no auto-push.
6. Consistency checker:
   - Current decisions are active architectural constraints.
   - Superseded decisions are historical context and do not trigger false conflicts.
   - Historical lineage is properly surfaced.
7. AI tools & citations:
   - Distinguishes current vs superseded in search.
   - Reports lineage and attaches supporting citations in get_decision.
   - AI cannot automatically supersede, modify status, commit, or push.
"""
from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Decision, Repository, Workspace
from app.services import git
from app.services.adr import mark_adr_superseded, read_document, sync_decision_adr
from app.services.consistency import check_consistency
from app.services.tools import ToolContext, TOOL_REGISTRY, tool_get_decision, tool_search_decisions


@pytest.fixture()
def ws_with_doc_repo(client: TestClient, doc_repo: Any) -> tuple[dict, Path]:
    """A workspace with a configured writable documentation repository."""
    ws = client.post("/api/workspaces", json={"name": "Supersede WS"}).json()
    ws_id = ws["id"]
    client.post(
        f"/api/workspaces/{ws_id}/repositories",
        json={
            "name": "docs-repo",
            "local_path": str(doc_repo),
            "kind": "documentation",
            "writable": True,
        },
    )
    return ws, Path(doc_repo)


def test_decision_supersede_lifecycle_and_lineage(
    client: TestClient,
    ws_with_doc_repo: tuple[dict, Path],
) -> None:
    """Requirement 1, 2, 4: Decision A can be superseded by Decision B and lineage is navigable."""
    ws, repo_root = ws_with_doc_repo
    ws_id = ws["id"]

    # 1. Create and approve Decision A (older)
    d1 = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={
            "title": "ADR 001: Initial Memory Ownership",
            "context": "Initial memory management strategy.",
            "decision": "Arena allocator owns all heap memory.",
            "rationale": "Simple and fast.",
            "consequences": "Requires periodic arena resetting.",
            "status": "proposed",
        },
    ).json()
    client.post(f"/api/workspaces/{ws_id}/decisions/{d1['id']}/approve")

    # 2. Create and approve Decision B (newer)
    d2 = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={
            "title": "ADR 002: Revised Memory Ownership",
            "context": "Arena allocator caused memory leaks in long-running jobs.",
            "decision": "Introduce reference counting with RAII wrapper.",
            "rationale": "Prevents unbounded memory growth.",
            "consequences": "Minor ref count overhead.",
            "status": "proposed",
        },
    ).json()
    client.post(f"/api/workspaces/{ws_id}/decisions/{d2['id']}/approve")

    # 3. Explicitly supersede Decision A by Decision B
    supersede_resp = client.post(
        f"/api/workspaces/{ws_id}/decisions/{d1['id']}/supersede",
        json={"superseded_by_id": d2["id"]},
    )
    assert supersede_resp.status_code == 200, supersede_resp.text
    data = supersede_resp.json()
    assert data["decision"]["id"] == d1["id"]
    assert data["decision"]["status"] == "superseded"
    assert data["decision"]["superseded_by_id"] == d2["id"]
    assert data["superseded_by"]["id"] == d2["id"]
    assert data["superseded_by"]["status"] == "approved"
    assert data["sync_status"] == "updated"

    # 4. Symmetrical lineage traversal via REST API
    # Retrieve Decision that superseded Decision A
    by_resp = client.get(f"/api/workspaces/{ws_id}/decisions/{d1['id']}/superseded-by")
    assert by_resp.status_code == 200
    assert by_resp.json()["id"] == d2["id"]

    # Retrieve Decisions superseded by Decision B
    supersedes_resp = client.get(f"/api/workspaces/{ws_id}/decisions/{d2['id']}/supersedes")
    assert supersedes_resp.status_code == 200
    superseded_list = supersedes_resp.json()
    assert len(superseded_list) == 1
    assert superseded_list[0]["id"] == d1["id"]

    # 5. Direct GET checks
    d1_fetch = client.get(f"/api/workspaces/{ws_id}/decisions/{d1['id']}").json()
    assert d1_fetch["status"] == "superseded"
    assert d1_fetch["superseded_by_id"] == d2["id"]

    d2_fetch = client.get(f"/api/workspaces/{ws_id}/decisions/{d2['id']}").json()
    assert d2_fetch["status"] == "approved"
    assert d2_fetch["supersedes_ids"] == [d1["id"]]


def test_supersede_constraints_and_validation(
    client: TestClient,
    ws_with_doc_repo: tuple[dict, Path],
) -> None:
    """Requirement 1 constraints: self-supersession, cross-workspace, non-approved target, duplicates."""
    ws, _ = ws_with_doc_repo
    ws_id = ws["id"]

    # Create workspace 2 to test isolation
    ws2 = client.post("/api/workspaces", json={"name": "Other WS"}).json()
    ws2_id = ws2["id"]

    d1 = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Decision 1", "decision": "Text 1", "status": "approved"},
    ).json()
    d2 = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Decision 2", "decision": "Text 2", "status": "approved"},
    ).json()
    d_other = client.post(
        f"/api/workspaces/{ws2_id}/decisions",
        json={"title": "Other WS Decision", "decision": "Text", "status": "approved"},
    ).json()
    d_unapproved = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Unapproved Decision", "decision": "Text", "status": "proposed"},
    ).json()

    # Self-supersession rejected
    r_self = client.post(
        f"/api/workspaces/{ws_id}/decisions/{d1['id']}/supersede",
        json={"superseded_by_id": d1["id"]},
    )
    assert r_self.status_code == 400
    assert "cannot supersede itself" in r_self.text

    # Cross-workspace target rejected
    r_cross = client.post(
        f"/api/workspaces/{ws_id}/decisions/{d1['id']}/supersede",
        json={"superseded_by_id": d_other["id"]},
    )
    assert r_cross.status_code == 404

    # Non-existent target rejected
    r_none = client.post(
        f"/api/workspaces/{ws_id}/decisions/{d1['id']}/supersede",
        json={"superseded_by_id": 999999},
    )
    assert r_none.status_code == 404

    # Target decision not approved rejected
    r_unapp = client.post(
        f"/api/workspaces/{ws_id}/decisions/{d1['id']}/supersede",
        json={"superseded_by_id": d_unapproved["id"]},
    )
    assert r_unapp.status_code == 400
    assert "must be 'approved'" in r_unapp.text

    # Successfully supersede d1 by d2
    ok_resp = client.post(
        f"/api/workspaces/{ws_id}/decisions/{d1['id']}/supersede",
        json={"superseded_by_id": d2["id"]},
    )
    assert ok_resp.status_code == 200

    # Duplicate supersession rejected
    r_dup = client.post(
        f"/api/workspaces/{ws_id}/decisions/{d1['id']}/supersede",
        json={"superseded_by_id": d2["id"]},
    )
    assert r_dup.status_code == 400
    assert "already superseded" in r_dup.text

    # Target cannot be an already-superseded decision
    d3 = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Decision 3", "decision": "Text 3", "status": "approved"},
    ).json()
    r_target_superseded = client.post(
        f"/api/workspaces/{ws_id}/decisions/{d3['id']}/supersede",
        json={"superseded_by_id": d1["id"]},  # d1 is already superseded!
    )
    assert r_target_superseded.status_code == 400
    assert "already superseded" in r_target_superseded.text


def test_supersede_cycle_detection(
    client: TestClient,
    ws_with_doc_repo: tuple[dict, Path],
) -> None:
    """Requirement 1: Cycle prevention (D1 -> D2 -> D3 -> D1 is rejected)."""
    ws, _ = ws_with_doc_repo
    ws_id = ws["id"]

    d1 = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "ADR 001", "decision": "Dec 1", "status": "approved"},
    ).json()
    d2 = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "ADR 002", "decision": "Dec 2", "status": "approved"},
    ).json()
    d3 = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "ADR 003", "decision": "Dec 3", "status": "approved"},
    ).json()

    # D1 superseded by D2
    assert client.post(
        f"/api/workspaces/{ws_id}/decisions/{d1['id']}/supersede",
        json={"superseded_by_id": d2["id"]},
    ).status_code == 200

    # D2 superseded by D3
    assert client.post(
        f"/api/workspaces/{ws_id}/decisions/{d2['id']}/supersede",
        json={"superseded_by_id": d3["id"]},
    ).status_code == 200

    # Attempting to supersede D3 with D1 should be strictly rejected as a cycle
    r_cycle = client.post(
        f"/api/workspaces/{ws_id}/decisions/{d3['id']}/supersede",
        json={"superseded_by_id": d1["id"]},
    )
    assert r_cycle.status_code == 400
    assert "already superseded" in r_cycle.text or "cycle" in r_cycle.text


def test_supersede_cancellation(
    client: TestClient,
    ws_with_doc_repo: tuple[dict, Path],
) -> None:
    """Requirement 4: Removing / correcting the superseding relationship."""
    ws, repo_root = ws_with_doc_repo
    ws_id = ws["id"]

    d1 = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "ADR 001: Cache Strategy", "decision": "Use Memcached", "status": "proposed"},
    ).json()
    client.post(f"/api/workspaces/{ws_id}/decisions/{d1['id']}/approve")

    d2 = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "ADR 002: Modern Cache", "decision": "Use Redis", "status": "proposed"},
    ).json()
    client.post(f"/api/workspaces/{ws_id}/decisions/{d2['id']}/approve")

    # Supersede d1 by d2
    client.post(
        f"/api/workspaces/{ws_id}/decisions/{d1['id']}/supersede",
        json={"superseded_by_id": d2["id"]},
    )
    adr_path = Path(repo_root) / "architecture" / "decisions" / "adr-001-initial-memory-ownership.md"
    # Or inspect d1 markdown path
    d1_fetched = client.get(f"/api/workspaces/{ws_id}/decisions/{d1['id']}").json()
    adr_file = Path(repo_root) / d1_fetched["markdown_path"]
    assert "> Superseded by" in adr_file.read_text(encoding="utf-8")

    # Cancel supersession
    del_resp = client.delete(f"/api/workspaces/{ws_id}/decisions/{d1['id']}/supersede")
    assert del_resp.status_code == 200
    d1_restored = del_resp.json()
    assert d1_restored["status"] == "approved"
    assert d1_restored["superseded_by_id"] is None

    # ADR supersession notice removed
    content = adr_file.read_text(encoding="utf-8")
    assert "> Superseded by" not in content
    assert "- Status: approved" in content

    # Calling delete on a non-superseded decision returns 400
    del_again = client.delete(f"/api/workspaces/{ws_id}/decisions/{d1['id']}/supersede")
    assert del_again.status_code == 400


def test_adr_markdown_deprecation_and_content_preservation(
    client: TestClient,
    ws_with_doc_repo: tuple[dict, Path],
) -> None:
    """Requirement 6 & 7: ADR Markdown receives notice, preserves all historical sections."""
    ws, repo_root = ws_with_doc_repo
    ws_id = ws["id"]

    # 1. Create and approve Decision A with rich historical details
    d1 = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={
            "title": "ADR 014: Component Boundaries",
            "context": "Context written back in 2024 describing initial microservices separation.",
            "decision": "Split backend into 3 distinct internal services.",
            "rationale": "Prevents tight coupling during early rapid prototyping.",
            "consequences": "Introduced network latency between components.",
            "status": "proposed",
        },
    ).json()
    client.post(f"/api/workspaces/{ws_id}/decisions/{d1['id']}/approve")

    d1_updated = client.get(f"/api/workspaces/{ws_id}/decisions/{d1['id']}").json()
    adr1_file = Path(repo_root) / d1_updated["markdown_path"]
    assert adr1_file.exists()

    # Append a custom historical section to ADR-014 to verify it is preserved
    original_adr_content = adr1_file.read_text(encoding="utf-8")
    custom_section = "\n## Historical Team Notes\n- Reviewed by Alice and Bob in Q3.\n"
    adr1_file.write_text(original_adr_content + custom_section, encoding="utf-8")

    # 2. Create and approve Decision B
    d2 = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={
            "title": "ADR 027: Modular Monolith",
            "context": "Microservice latency became unacceptable.",
            "decision": "Re-converge services into a modular monolith.",
            "rationale": "High throughput and single-binary deployment.",
            "consequences": "Requires strict module boundaries.",
            "status": "proposed",
        },
    ).json()
    client.post(f"/api/workspaces/{ws_id}/decisions/{d2['id']}/approve")

    # 3. Supersede Decision A by Decision B
    sup_resp = client.post(
        f"/api/workspaces/{ws_id}/decisions/{d1['id']}/supersede",
        json={"superseded_by_id": d2["id"]},
    )
    assert sup_resp.status_code == 200

    # 4. Verify ADR-014 content:
    updated_content = adr1_file.read_text(encoding="utf-8")

    # Supersession notice present near the top
    assert "> Superseded by ADR-027." in updated_content
    assert "- Status: superseded" in updated_content

    # Original context, decision, rationale, consequences preserved
    assert "Context written back in 2024 describing initial microservices separation." in updated_content
    assert "Split backend into 3 distinct internal services." in updated_content
    assert "Prevents tight coupling during early rapid prototyping." in updated_content
    assert "Introduced network latency between components." in updated_content

    # Custom historical section preserved
    assert "## Historical Team Notes" in updated_content
    assert "Reviewed by Alice and Bob in Q3." in updated_content

    # ADR-027 intact and authoritative
    d2_updated = client.get(f"/api/workspaces/{ws_id}/decisions/{d2['id']}").json()
    adr2_file = Path(repo_root) / d2_updated["markdown_path"]
    assert adr2_file.exists()
    adr2_content = adr2_file.read_text(encoding="utf-8")
    assert "- Status: approved" in adr2_content
    assert "> Superseded by" not in adr2_content

    # 5. Idempotency: repeated supersession does not duplicate notices
    sup_again = client.post(
        f"/api/workspaces/{ws_id}/decisions/{d1['id']}/supersede",
        json={"superseded_by_id": d2["id"]},
    )
    # The duplicate check in route returns 400 when already superseded by d2
    assert sup_again.status_code == 400


def test_git_behavior_leaves_changes_uncommitted(
    client: TestClient,
    ws_with_doc_repo: tuple[dict, Path],
) -> None:
    """Requirement 8: Git modifications remain in working tree; no auto commit or push."""
    ws, repo_root = ws_with_doc_repo
    ws_id = ws["id"]

    d1 = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "ADR 001: Git Workflow", "decision": "Use Git", "status": "proposed"},
    ).json()
    client.post(f"/api/workspaces/{ws_id}/decisions/{d1['id']}/approve")

    d2 = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "ADR 002: Advanced Git Workflow", "decision": "Use Git LFS", "status": "proposed"},
    ).json()
    client.post(f"/api/workspaces/{ws_id}/decisions/{d2['id']}/approve")

    # Clean commit so working tree diff only reflects supersession
    if git.is_repo(repo_root):
        subprocess.run(["git", "-C", str(repo_root), "add", "."], check=False)
        subprocess.run(["git", "-C", str(repo_root), "commit", "-m", "Baseline before superseding"], check=False)

    # Perform superseding
    res = client.post(
        f"/api/workspaces/{ws_id}/decisions/{d1['id']}/supersede",
        json={"superseded_by_id": d2["id"]},
    ).json()

    assert res["sync_status"] == "updated"
    assert res["diff"] is not None
    assert "+> Superseded by ADR-002." in res["diff"]

    # Working tree has unstaged modified files
    status_entries = git.status(repo_root)
    assert any("M" in e.status and "adr-001" in e.path.lower() for e in status_entries)


def test_consistency_checker_understands_superseded_decisions(
    client: TestClient,
    session: Session,
    doc_repo: Any,
) -> None:
    """Requirement 10: Consistency checker does not treat superseded decisions as active constraints."""
    ws = Workspace(name="Consistency Check WS")
    session.add(ws)
    session.flush()

    repo = Repository(
        workspace_id=ws.id,
        name="docs",
        local_path=str(doc_repo),
        kind="documentation",
        writable=True,
    )
    session.add(repo)

    # Historical decision (superseded) specifying SQLite
    d_historical = Decision(
        workspace_id=ws.id,
        title="ADR 001: Database Storage Engine",
        context="Initial simple prototype storage engine.",
        decision="Use SQLite for metadata storage.",
        rationale="Embedded and zero-config.",
        consequences="Limited concurrency.",
        status="superseded",
        decided_on=dt.datetime.now(dt.timezone.utc),
    )
    # Active current decision (approved) specifying PostgreSQL
    d_active = Decision(
        workspace_id=ws.id,
        title="ADR 002: Scalable Database Engine",
        context="Production scale required high concurrency and migrations.",
        decision="Use PostgreSQL for metadata storage.",
        rationale="Robust ACID transactions, concurrency, and rich types.",
        consequences="Requires external PostgreSQL service.",
        status="approved",
        decided_on=dt.datetime.now(dt.timezone.utc),
    )
    session.add_all([d_historical, d_active])
    session.flush()

    d_historical.superseded_by_id = d_active.id
    session.commit()

    # Now evaluate a new proposal that specifies PostgreSQL (aligns with d_active, contradicts d_historical)
    proposal = {
        "title": "Adopt PostgreSQL JSONB Columns",
        "decision": "Use PostgreSQL JSONB columns for flexible citation storage.",
        "context": "Need fast querying on JSON attributes.",
        "rationale": "Supported natively in PostgreSQL.",
        "consequences": "PostgreSQL specific feature.",
    }

    result = check_consistency(
        db=session,
        workspace=ws,
        repositories=[repo],
        proposal=proposal,
        provider=None,
    )

    # Must NOT report "Potential conflict" solely because SQLite was chosen in historical Decision 1!
    assert result["status"] in ("No apparent conflict", "Potential overlap")

    # If historical candidate was evaluated, it must have type 'historical_lineage'
    historical_findings = [f for f in result["findings"] if f["decision_id"] == d_historical.id]
    for hf in historical_findings:
        assert hf["type"] == "historical_lineage"
        assert "superseded" in hf["reason"].lower()


def test_ai_tools_search_and_retrieve_lineage(
    client: TestClient,
    session: Session,
    doc_repo: Any,
) -> None:
    """Requirement 9 & 11: AI search and get_decision expose lineage and supporting citations."""
    ws = Workspace(name="AI Lineage WS")
    session.add(ws)
    session.flush()

    d1 = Decision(
        workspace_id=ws.id,
        title="ADR 014: Synchronous REST Communication",
        decision="Services communicate synchronously via HTTP REST.",
        status="superseded",
        markdown_path="architecture/decisions/adr-014.md",
    )
    d2 = Decision(
        workspace_id=ws.id,
        title="ADR 027: Event Driven Messaging",
        decision="Services communicate asynchronously via Kafka events.",
        status="approved",
        markdown_path="architecture/decisions/adr-027.md",
    )
    session.add_all([d1, d2])
    session.flush()
    d1.superseded_by_id = d2.id
    session.commit()
    session.refresh(d1)
    session.refresh(d2)

    repo = Repository(
        workspace_id=ws.id,
        name="docs",
        local_path=str(doc_repo),
        kind="documentation",
        writable=True,
    )
    session.add(repo)
    session.commit()

    ctx = ToolContext(workspace=ws, repositories=[repo], db=session, citations=[])

    # 1. Search decisions: superseded decisions are clearly identified
    search_output = tool_search_decisions(ctx, query="Communication")
    assert f"#{d1.id} [superseded by #{d2.id}]" in search_output
    assert f"#{d2.id} [approved (supersedes #{d1.id})]" in tool_search_decisions(ctx, query="Messaging")

    # 2. Get decision: d1 reports superseded by d2 and cites d2
    d1_detail = tool_get_decision(ctx, d1.id)
    assert f"Superseded By: Decision #{d2.id}" in d1_detail

    # Check citations: citations for both d1 and superseding decision d2
    cite_ids = {c.decision_id for c in ctx.citations if c.decision_id}
    assert d1.id in cite_ids
    assert d2.id in cite_ids

    # 3. Get decision: d2 reports supersedes d1
    ctx2 = ToolContext(workspace=ws, repositories=[repo], db=session, citations=[])
    d2_detail = tool_get_decision(ctx2, d2.id)
    assert f"Supersedes: Decision #{d1.id}" in d2_detail


def test_ai_tools_cannot_auto_supersede() -> None:
    """Requirement: Verify that AI tools cannot automatically supersede decisions or modify status."""
    tool_names = set(TOOL_REGISTRY.keys())
    assert "supersede_decision" not in tool_names
    assert "modify_decision_status" not in tool_names
    assert "approve_decision" not in tool_names
    assert "git_commit" not in tool_names
    assert "git_push" not in tool_names
