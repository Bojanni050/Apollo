"""Architectural Consistency Checking Service.

Compares a proposed architectural decision against existing approved decisions
and their ADR representations to identify potential:
- Contradictions / conflicting responsibilities ("Potential conflict")
- Duplicated decisions / significant overlaps ("Potential overlap")
- Compatible decisions ("No apparent conflict")
- Insufficient data ("Insufficient evidence")

IMPORTANT:
The consistency checker never approves, rejects, modifies, commits, or pushes.
It only reports observations and evidence for operator review.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.base import LLMProvider
from app.models import Decision, Repository, Workspace
from app.services.paths import safe_path

logger = logging.getLogger("apollo")

# Common English stop words to exclude from keyword extraction
STOP_WORDS = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "also", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "cannot", "could", "couldn't",
    "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during",
    "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't",
    "have", "haven't", "having", "he", "her", "here", "hers", "herself", "him",
    "himself", "his", "how", "i", "if", "in", "into", "is", "isn't", "it", "it's",
    "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself", "no",
    "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought", "our",
    "ours", "ourselves", "out", "over", "own", "same", "shan't", "she", "should",
    "shouldn't", "so", "some", "such", "than", "that", "that's", "the", "their",
    "theirs", "them", "themselves", "then", "there", "there's", "these", "they",
    "this", "those", "through", "to", "too", "under", "until", "up", "very", "was",
    "wasn't", "we", "were", "weren't", "what", "what's", "when", "where", "which",
    "while", "who", "whom", "why", "with", "won't", "would", "wouldn't", "you",
    "your", "yours", "yourself", "yourselves", "use", "using", "adopt", "used",
    "need", "system", "architecture",
})


def extract_keywords(text: str) -> set[str]:
    """Extract meaningful lowercase word tokens (min length 3) excluding stop words."""
    words = re.findall(r"\b[a-zA-Z0-9_\-]{3,}\b", text.lower())
    return {w for w in words if w not in STOP_WORDS and not w.isdigit()}


def _read_adr_excerpt(repositories: list[Repository], markdown_path: str | None) -> str:
    """Attempt to read up to 2000 chars of ADR content from doc repositories."""
    if not markdown_path:
        return ""
    for repo in repositories:
        if repo.kind == "documentation":
            try:
                full_path = safe_path(repo.local_path, markdown_path)
                if full_path.is_file():
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    return content[:2000]
            except Exception:
                continue
    return ""


def check_consistency(
    db: Session,
    workspace: Workspace,
    repositories: list[Repository],
    proposal: dict[str, Any],
    provider: LLMProvider | None = None,
) -> dict[str, Any]:
    """Check a proposed Decision against existing approved Decisions.

    Returns a structured dictionary:
    {
        "status": "No apparent conflict" | "Potential conflict" | "Potential overlap" | "Insufficient evidence",
        "summary": str,
        "candidates_evaluated": list[dict],
        "findings": list[dict],
        "evidence": list[dict],
    }
    """
    title = str(proposal.get("title") or "").strip()
    decision_text = str(proposal.get("decision") or "").strip()
    context_text = str(proposal.get("context") or "").strip()
    rationale_text = str(proposal.get("rationale") or "").strip()
    consequences_text = str(proposal.get("consequences") or "").strip()
    exclude_id = proposal.get("decision_id")

    combined_proposal_text = f"{title} {decision_text} {context_text} {rationale_text} {consequences_text}".strip()
    proposal_keywords = extract_keywords(combined_proposal_text)

    # 1. Retrieve approved and superseded decisions in the workspace
    query = select(Decision).where(
        Decision.workspace_id == workspace.id,
        Decision.status.in_(["approved", "superseded"]),
    )
    if exclude_id is not None:
        query = query.where(Decision.id != exclude_id)
    candidate_decisions = db.scalars(query.order_by(Decision.id.asc())).all()

    if not candidate_decisions or not proposal_keywords:
        return {
            "status": "Insufficient evidence",
            "summary": "No approved architectural decisions exist in this workspace to evaluate against.",
            "candidates_evaluated": [],
            "findings": [],
            "evidence": [],
        }

    # 2. Score candidate decisions based on keyword overlap and active status
    scored_candidates: list[tuple[int, bool, Decision, set[str]]] = []
    for d in candidate_decisions:
        cand_text = f"{d.title} {d.decision} {d.context} {d.rationale}".strip()
        cand_keywords = extract_keywords(cand_text)
        overlap = proposal_keywords & cand_keywords
        if overlap:
            is_superseded = (d.status == "superseded" or d.superseded_by_id is not None)
            # Prioritize active approved decisions over historical superseded decisions
            scored_candidates.append((len(overlap), not is_superseded, d, overlap))

    # Sort descending by (is_active, overlap score)
    scored_candidates.sort(key=lambda x: (x[1], x[0]), reverse=True)
    top_candidates = [(score, d, overlap) for score, is_active, d, overlap in scored_candidates[:5]]

    if not top_candidates:
        return {
            "status": "Insufficient evidence",
            "summary": (
                "No architectural decisions in this workspace share relevant architectural terms "
                f"({', '.join(sorted(list(proposal_keywords)[:6]))}). Insufficient evidence to establish consistency or conflict."
            ),
            "candidates_evaluated": [],
            "findings": [],
            "evidence": [],
        }

    candidates_info: list[dict[str, Any]] = []
    evidence_list: list[dict[str, Any]] = []
    for score, d, overlap in top_candidates:
        adr_excerpt = _read_adr_excerpt(repositories, d.markdown_path)
        is_sup = (d.status == "superseded" or d.superseded_by_id is not None)
        candidates_info.append({
            "id": d.id,
            "title": d.title,
            "decision": d.decision,
            "context": d.context,
            "rationale": d.rationale,
            "status": d.status,
            "is_superseded": is_sup,
            "superseded_by_id": d.superseded_by_id,
            "markdown_path": d.markdown_path,
            "adr_excerpt": adr_excerpt,
            "matching_terms": sorted(list(overlap)),
        })
        evidence_list.append({
            "decision_id": d.id,
            "title": d.title,
            "path": d.markdown_path,
            "matching_terms": sorted(list(overlap)),
            "status": d.status,
            "is_superseded": is_sup,
        })

    # 3. Perform comparison via LLM if available, otherwise heuristic analysis
    findings: list[dict[str, Any]] = []
    overall_status = "No apparent conflict"
    summary = ""

    if provider is not None:
        try:
            llm_result = _compare_via_llm(
                provider=provider,
                proposal={
                    "title": title,
                    "decision": decision_text,
                    "context": context_text,
                    "rationale": rationale_text,
                    "consequences": consequences_text,
                },
                candidates=candidates_info,
            )
            if llm_result and "status" in llm_result:
                return {
                    "status": llm_result["status"],
                    "summary": llm_result.get("summary", ""),
                    "candidates_evaluated": candidates_info,
                    "findings": llm_result.get("findings", []),
                    "evidence": evidence_list,
                }
        except Exception as exc:
            logger.warning("LLM consistency check failed, falling back to heuristic evaluation: %s", exc)

    # Heuristic evaluation fallback
    return _compare_heuristically(
        proposal_title=title,
        proposal_decision=decision_text,
        proposal_context=context_text,
        proposal_keywords=proposal_keywords,
        candidates=candidates_info,
        evidence=evidence_list,
    )


def _compare_heuristically(
    proposal_title: str,
    proposal_decision: str,
    proposal_context: str,
    proposal_keywords: set[str],
    candidates: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    """Deterministic rule-based consistency evaluation."""
    findings: list[dict[str, Any]] = []
    has_conflict = False
    has_overlap = False

    prop_full = f"{proposal_title} {proposal_decision} {proposal_context}".lower()

    # Conflicting or opposing responsibility indicators
    conflict_subject_pairs = [
        ("memory", "ownership"),
        ("storage", "database"),
        ("cache", "caching"),
        ("auth", "authentication"),
        ("rpc", "messaging"),
        ("event", "streaming"),
    ]

    for cand in candidates:
        cand_full = f"{cand['title']} {cand['decision']} {cand['context']} {cand['rationale']}".lower()
        matching_terms = set(cand["matching_terms"])

        # Check if this candidate is historical (superseded)
        if cand.get("is_superseded"):
            findings.append({
                "type": "historical_lineage",
                "decision_id": cand["id"],
                "title": cand["title"],
                "reason": (
                    f"Historical context: Decision #{cand['id']} ('{cand['title']}') was superseded"
                    + (f" by Decision #{cand.get('superseded_by_id')}" if cand.get("superseded_by_id") else "")
                    + ". This historical decision is preserved for lineage context and does not act as an active architectural constraint."
                ),
                "proposed_claim": proposal_title,
                "existing_claim": cand["title"],
                "markdown_path": cand["markdown_path"],
            })
            continue

        # Check for direct overlap / duplication
        # If title similarity or decision text similarity is very high
        if cand["title"].lower() in prop_full or proposal_title.lower() in cand_full:
            has_overlap = True
            findings.append({
                "type": "overlap",
                "decision_id": cand["id"],
                "title": cand["title"],
                "reason": f"Proposal addresses the same core architectural subject as approved Decision #{cand['id']} ('{cand['title']}').",
                "proposed_claim": proposal_title,
                "existing_claim": cand["title"],
                "markdown_path": cand["markdown_path"],
            })
            continue

        # Check for potential conflict: shared architectural subject with contradictory assignments/tools
        detected_conflict = False
        for subj, attr in conflict_subject_pairs:
            if (subj in prop_full or attr in prop_full) and (subj in cand_full or attr in cand_full):
                # E.g. memory ownership: check if different actors/components are assigned
                # If candidate assigns to component A and proposed assigns to component B
                if "owns" in prop_full or "owned by" in prop_full or "belongs to" in prop_full or "responsibility" in prop_full:
                    if "owns" in cand_full or "owned by" in cand_full or "belongs to" in cand_full or "responsibility" in cand_full:
                        has_conflict = True
                        detected_conflict = True
                        findings.append({
                            "type": "conflict",
                            "decision_id": cand["id"],
                            "title": cand["title"],
                            "reason": (
                                f"Conflicting architectural responsibilities detected around '{subj}' with approved Decision #{cand['id']} ('{cand['title']}'). "
                                "Both decisions define responsibility or ownership on this domain."
                            ),
                            "proposed_claim": proposal_decision or proposal_title,
                            "existing_claim": cand["decision"] or cand["title"],
                            "markdown_path": cand["markdown_path"],
                        })
                        break

                # Different primary technology choices for the same tier
                tech_choices = [
                    {"postgresql", "sqlite", "mysql", "mongodb"},
                    {"redis", "memcached"},
                    {"grpc", "rest", "graphql"},
                    {"rabbitmq", "kafka"},
                ]
                for tech_set in tech_choices:
                    prop_techs = {t for t in tech_set if t in prop_full}
                    cand_techs = {t for t in tech_set if t in cand_full}
                    if prop_techs and cand_techs and prop_techs != cand_techs:
                        has_conflict = True
                        detected_conflict = True
                        findings.append({
                            "type": "conflict",
                            "decision_id": cand["id"],
                            "title": cand["title"],
                            "reason": (
                                f"Incompatible technology selections for '{subj}': proposal specifies {', '.join(prop_techs)}, "
                                f"whereas approved Decision #{cand['id']} specified {', '.join(cand_techs)}."
                            ),
                            "proposed_claim": proposal_decision or proposal_title,
                            "existing_claim": cand["decision"] or cand["title"],
                            "markdown_path": cand["markdown_path"],
                        })
                        break

        if not detected_conflict:
            # Overlap if high matching term count
            if len(matching_terms) >= 3:
                has_overlap = True
                findings.append({
                    "type": "overlap",
                    "decision_id": cand["id"],
                    "title": cand["title"],
                    "reason": f"High terminology and topic overlap on ({', '.join(sorted(matching_terms))}) with Decision #{cand['id']}.",
                    "proposed_claim": proposal_title,
                    "existing_claim": cand["title"],
                    "markdown_path": cand["markdown_path"],
                })
            else:
                findings.append({
                    "type": "compatible",
                    "decision_id": cand["id"],
                    "title": cand["title"],
                    "reason": f"Touches related architectural concepts ({', '.join(sorted(matching_terms))}) with no apparent direct contradictions.",
                    "proposed_claim": proposal_title,
                    "existing_claim": cand["title"],
                    "markdown_path": cand["markdown_path"],
                })

    if has_conflict:
        status = "Potential conflict"
        summary = f"Detected {len([f for f in findings if f['type'] == 'conflict'])} potential architectural contradiction(s) or conflicting responsibility with existing approved decisions."
    elif has_overlap:
        status = "Potential overlap"
        summary = f"Detected {len([f for f in findings if f['type'] == 'overlap'])} potential overlap(s) or topic duplication with existing approved decisions."
    elif findings:
        status = "No apparent conflict"
        summary = f"Evaluated {len(candidates)} relevant approved decision(s). No contradictions or conflicting responsibilities detected."
    else:
        status = "Insufficient evidence"
        summary = "Insufficient evidence to determine architectural consistency."

    return {
        "status": status,
        "summary": summary,
        "candidates_evaluated": candidates,
        "findings": findings,
        "evidence": evidence,
    }


def _compare_via_llm(
    provider: LLMProvider,
    proposal: dict[str, str],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Invoke LLM provider to analyze proposal against candidate decisions."""
    prompt = f"""You are an architectural consistency analysis engine for Apollo.
Your task is to compare a proposed architectural decision against existing approved architectural decisions and ADRs.

Analyze the proposal for:
1. Contradictions or conflicting responsibilities (status = "Potential conflict")
2. Duplicated decisions or significant overlaps on the same subject (status = "Potential overlap")
3. Compatibility with no apparent contradictions (status = "No apparent conflict")
4. Insufficient context to compare (status = "Insufficient evidence")

CRITICAL RULES:
- Provide evidence and observations. You must NOT decide whether the proposal is acceptable or approved.
- Every finding MUST reference the specific existing decision_id.
- Return ONLY valid JSON in the exact schema below.

---
PROPOSED DECISION:
Title: {proposal.get('title', '')}
Context: {proposal.get('context', '')}
Decision: {proposal.get('decision', '')}
Rationale: {proposal.get('rationale', '')}
Consequences: {proposal.get('consequences', '')}

---
EXISTING ARCHITECTURAL DECISIONS:
"""
    active_cands = [c for c in candidates if not c.get("is_superseded")]
    hist_cands = [c for c in candidates if c.get("is_superseded")]

    if active_cands:
        prompt += "\nCURRENT ACTIVE DECISIONS (authoritative architectural constraints):\n"
        for c in active_cands:
            prompt += f"""Decision #{c['id']}: {c['title']}
Context: {c.get('context', '')}
Decision: {c.get('decision', '')}
Rationale: {c.get('rationale', '')}
ADR Path: {c.get('markdown_path', 'None')}
ADR Content: {c.get('adr_excerpt', '')[:500]}
"""

    if hist_cands:
        prompt += "\nHISTORICAL (SUPERSEDED) DECISIONS (for historical context only, NOT active constraints):\n"
        for c in hist_cands:
            prompt += f"""Decision #{c['id']} [SUPERSEDED by #{c.get('superseded_by_id')}]: {c['title']}
Context: {c.get('context', '')}
Decision: {c.get('decision', '')}
Rationale: {c.get('rationale', '')}
"""
        prompt += "\nNOTE: Differences from historical/superseded decisions must NOT be reported as conflicts.\n"

    prompt += """
---
Respond in this JSON format:
{
  "status": "No apparent conflict" | "Potential conflict" | "Potential overlap" | "Insufficient evidence",
  "summary": "Short 1-2 sentence overview of findings",
  "findings": [
    {
      "type": "conflict" | "overlap" | "compatible" | "historical_lineage",
      "decision_id": 123,
      "title": "Title of existing decision",
      "reason": "Clear explanation of contradiction, overlap, or historical lineage",
      "proposed_claim": "Claim in proposed decision",
      "existing_claim": "Claim in existing approved decision",
      "markdown_path": "path or null"
    }
  ]
}
"""

    messages = [
        {"role": "system", "content": "You are a strict architectural consistency analyzer. Always output raw JSON."},
        {"role": "user", "content": prompt},
    ]

    response = provider.chat(messages, temperature=0.1)
    text = response.content.strip()

    # Extract JSON if enclosed in markdown code fences
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1)

    try:
        data = json.loads(text)
        if isinstance(data, dict) and "status" in data:
            superseded_ids = {c["id"] for c in candidates if c.get("is_superseded")}
            sanitized_findings = []
            has_active_conflict = False
            has_active_overlap = False
            for f in data.get("findings", []):
                dec_id = f.get("decision_id")
                if dec_id in superseded_ids:
                    f["type"] = "historical_lineage"
                    f["reason"] = f"[Historical Lineage] {f.get('reason', '')}"
                else:
                    if f.get("type") == "conflict":
                        has_active_conflict = True
                    elif f.get("type") == "overlap":
                        has_active_overlap = True
                sanitized_findings.append(f)
            data["findings"] = sanitized_findings
            if not has_active_conflict and data.get("status") == "Potential conflict":
                data["status"] = "Potential overlap" if has_active_overlap else "No apparent conflict"
            return data
    except Exception:
        pass
    return None
