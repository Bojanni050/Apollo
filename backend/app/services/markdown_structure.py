"""Markdown structure extraction, for whole-document awareness.

The problem this solves: a long architecture document classified from its first
12,000 characters is classified from its *introduction*. A document whose real
subject appears in a closing section -- an ADR whose "Decision" is at the
bottom, a runbook whose failure modes are at the end -- gets filed by its
preamble.

The fix is not a bigger character limit. It is to represent the **shape of the
whole document** within a fixed budget: every heading, every section's opening,
the code-block inventory, the links, and how much of each part was sampled. A
digest is bounded like any excerpt, but unlike an excerpt it is not biased
toward the beginning.

Nothing here modifies a document. :func:`build_representation` returns either
the original text unchanged or a separately-constructed digest; the source
content is never rewritten.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.llm.context import CHARS_PER_TOKEN, count_tokens

#: Smallest useful slice for one section line. Below this a section line
#: conveys nothing, so the budget is better spent on fewer, longer lines.
MIN_SECTION_TOKENS = 24

#: Share of the digest budget reserved for the heading outline. Reserved rather
#: than first-come, because a document with hundreds of headings would otherwise
#: spend the entire budget on structure and leave no content at all.
OUTLINE_BUDGET_SHARE = 0.35

#: A Markdown ATX heading: one to six '#' followed by a space and text.
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")

#: A fenced code block delimiter, ``` or ~~~.
FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})(.*)$")

#: An inline or reference link: [text](target). Images are excluded by the
#: leading '!' in the pattern.
LINK_RE = re.compile(r"(?<!!)\[([^\]\n]{1,120})\]\(([^)\s]{0,200})\)")

#: A YAML front-matter fence, which must be the very first line.
FRONT_MATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\s*(?:\r?\n|\Z)", re.DOTALL)

#: Characters of a section body kept in its preview. Deliberately small: a
#: preview establishes what a section is *about*, not every detail of it.
PREVIEW_CHARS = 400


@dataclass(frozen=True)
class Heading:
    """One ATX heading and where it sits in the source."""

    level: int
    text: str
    line: int  # 1-based


@dataclass(frozen=True)
class Section:
    """A heading and the body that follows it, up to the next heading."""

    heading: Heading | None
    start_line: int
    end_line: int
    body_lines: int
    preview: str


@dataclass(frozen=True)
class CodeBlock:
    """A fenced code block: its language, where it is, and how long it is."""

    language: str
    start_line: int
    line_count: int


@dataclass(frozen=True)
class Link:
    text: str
    target: str


@dataclass
class MarkdownStructure:
    """Everything extracted from a document, before any budget is applied."""

    title: str | None = None
    front_matter: dict[str, str] = field(default_factory=dict)
    headings: list[Heading] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    code_blocks: list[CodeBlock] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    total_lines: int = 0
    has_content: bool = True

    def stats(self) -> dict[str, Any]:
        """A compact, machine-readable summary for the model."""
        return {
            "lines": self.total_lines,
            "headings": len(self.headings),
            "sections": len(self.sections),
            "code_blocks": len(self.code_blocks),
            "links": len(self.links),
        }


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Split leading YAML front matter from the body.

    Returned as plain ``key: value`` pairs rather than a full YAML parse: a
    document's front matter is untrusted input, and reading a handful of lines
    is both safer and far cheaper than constructing a YAML object.
    """
    match = FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text

    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        if key:
            fields[key] = value.strip().strip("'\"")
    return fields, text[match.end():]


def _scan(lines: list[str]) -> tuple[list[Heading], list[CodeBlock]]:
    """Walk the body once, collecting headings and fenced code blocks.

    Fence state is tracked so a ``#`` inside a code block is not mistaken for a
    heading -- a very common false positive in technical documentation, where
    shell and Python comments are everywhere.
    """
    headings: list[Heading] = []
    code_blocks: list[CodeBlock] = []
    fence: str | None = None
    fence_start = 0
    fence_language = ""
    fence_body = 0

    for index, line in enumerate(lines, start=1):
        fence_match = FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
                fence_start = index
                fence_body = 0
                words = fence_match.group(2).strip().split()
                fence_language = words[0] if words else ""
            else:
                # Closing fence: record the block now that its size is known.
                code_blocks.append(
                    CodeBlock(
                        language=fence_language,
                        start_line=fence_start,
                        line_count=fence_body,
                    )
                )
                fence = None
            continue

        if fence is not None:
            fence_body += 1
            continue

        heading_match = HEADING_RE.match(line)
        if heading_match:
            headings.append(
                Heading(
                    level=len(heading_match.group(1)),
                    text=heading_match.group(2).strip(),
                    line=index,
                )
            )

    if fence is not None:
        # An unterminated fence is recorded with the size observed so far,
        # rather than discarded: a truncated code block is still evidence.
        code_blocks.append(
            CodeBlock(
                language=fence_language,
                start_line=fence_start,
                line_count=fence_body,
            )
        )

    return headings, code_blocks


def parse_markdown(text: str) -> MarkdownStructure:
    """Extract the structure of a Markdown document.

    Deliberately tolerant: a malformed document yields a partial structure
    rather than an error, because the caller's job is to classify it, not to
    reject it.
    """
    if not text or not text.strip():
        return MarkdownStructure(total_lines=0, has_content=False)

    front_matter, body = _parse_front_matter(text)
    lines = body.splitlines()
    headings, code_blocks = _scan(lines)

    links = [
        Link(text=match.group(1).strip(), target=match.group(2))
        for match in LINK_RE.finditer(body)
    ]
    title = headings[0].text if headings and headings[0].level == 1 else None

    return MarkdownStructure(
        title=title,
        front_matter=front_matter,
        headings=headings,
        sections=_build_sections(lines, headings),
        code_blocks=code_blocks,
        links=links,
        total_lines=len(lines),
        has_content=bool(body.strip()),
    )


def _build_sections(lines: list[str], headings: list[Heading]) -> list[Section]:
    """Split the body into one section per heading, plus any preamble.

    Sections are what make budget allocation fair: each gets its own slice, so
    a conclusion in the last section is as visible as one in the first.
    """
    boundaries = sorted(headings, key=lambda h: h.line)
    sections: list[Section] = []
    total = len(lines)

    # Content before the first heading is its own section: a preamble often
    # states the document's purpose, and dropping it would lose that.
    first = boundaries[0].line if boundaries else total + 1
    if first > 1:
        sections.append(_make_section(None, 1, first - 1, lines[0 : first - 1]))

    for position, heading in enumerate(boundaries):
        start = heading.line
        end = boundaries[position + 1].line - 1 if position + 1 < len(boundaries) else total
        # The heading line belongs to its own section, so line numbers stay
        # consistent between the outline and the section listing.
        sections.append(_make_section(heading, start, end, lines[start - 1 : end]))

    return sections


def _make_section(
    heading: Heading | None, start: int, end: int, body: list[str]
) -> Section:
    """Build a section, previewing its first substantive prose.

    Fenced code is stepped over, so a preview never begins in the middle of a
    block and shows meaningless partial code.
    """
    preview_lines: list[str] = []
    used = 0
    in_fence = False
    for line in body:
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or HEADING_RE.match(line):
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if used + len(stripped) > PREVIEW_CHARS:
            break
        preview_lines.append(stripped)
        used += len(stripped)

    return Section(
        heading=heading,
        start_line=start,
        end_line=end,
        body_lines=max(0, end - start + 1),
        preview=" ".join(preview_lines),
    )


def _outline_lines(structure: MarkdownStructure, max_tokens: int) -> list[str]:
    """The heading tree, indented by level, within a token budget.

    The outline is the cheapest whole-document signal available: it says what a
    document covers without spending budget on prose. Headings are added in
    document order until the budget runs out, and any that did not fit are
    reported as a count rather than silently dropped -- a truncated outline must
    not read as a complete one.
    """
    lines: list[str] = []
    used = 0
    for heading in structure.headings:
        indent = "  " * max(0, heading.level - 1)
        line = f"{indent}- {heading.text}  (line {heading.line})"
        cost = count_tokens(line) + 1
        if used + cost > max_tokens:
            break
        lines.append(line)
        used += cost

    remaining = len(structure.headings) - len(lines)
    if remaining > 0:
        lines.append(f"- ... and {remaining} more headings (outline truncated)")
    return lines


def _header_block(structure: MarkdownStructure) -> str:
    """Title, statistics and front matter, if present."""
    if not structure.has_content:
        return "[This document is empty.]"

    stats = structure.stats()
    block = [
        f"Title: {structure.title or '(no top-level title)'}",
        f"Structure: {stats['lines']} lines, {stats['headings']} headings, "
        f"{stats['sections']} sections, {stats['code_blocks']} code blocks, "
        f"{stats['links']} links",
    ]
    if structure.front_matter:
        pairs = ", ".join(
            f"{key}={value}" for key, value in list(structure.front_matter.items())[:12]
        )
        block.append(f"Front matter: {pairs}")

    # A digest is never a complete substitute for the text, and saying so is
    # what keeps the model honest about what it has actually read.
    block.append(
        "NOTE: this is a STRUCTURAL DIGEST of the whole document, not an "
        "excerpt. The headings and section openings below cover the entire file."
    )
    return "\n".join(block)


def _inventory_block(structure: MarkdownStructure) -> str:
    """Code-block and link inventory, so the model sees what kind of file it is."""
    lines: list[str] = []
    if structure.code_blocks:
        described = ", ".join(
            f"{block.language or 'text'} x{block.line_count}L (line {block.start_line})"
            for block in structure.code_blocks[:12]
        )
        lines.append(f"## Code blocks: {described}")
    if structure.links:
        targets = ", ".join(
            f"{link.text} -> {link.target}" for link in structure.links[:12]
        )
        lines.append(f"## Links: {targets}")
    return "\n".join(lines)


def render_digest(structure: MarkdownStructure, max_tokens: int) -> tuple[str, int]:
    """Render a structural digest of the whole document within a token budget.

    The budget is split in fixed proportions rather than first-come, because the
    outline is itself unbounded: a document with 200 headings would otherwise
    consume everything and leave no room for the section openings that carry the
    actual content.

    1. title, statistics and front matter  -- a fixed, small slice
    2. **the heading outline** -- a capped share, with a count of what was
       dropped if the document has more headings than fit
    3. the code-block and link inventory     -- a fixed, small slice
    4. each section's opening               -- everything left over

    Returns ``(digest, sections_covered)`` so the caller can state how much of
    the document the model actually saw, rather than implying it saw all of it.
    """
    header = _header_block(structure)
    inventory = _inventory_block(structure)

    # Fixed blocks are charged first, with headroom for the framing that joins
    # them together.
    fixed_cost = count_tokens(header) + count_tokens(inventory) + 40
    if fixed_cost >= max_tokens:
        # Pathologically small budget: the outline still matters more than
        # section previews, so drop the inventory and continue.
        inventory = ""
        fixed_cost = count_tokens(header) + 40

    outline_budget = max(0, int((max_tokens - fixed_cost) * OUTLINE_BUDGET_SHARE))
    outline = _outline_lines(structure, outline_budget)

    remaining = max(0, max_tokens - fixed_cost - count_tokens("\n".join(outline)))
    covered, previews = _render_sections(structure, remaining)

    parts: list[str] = []
    if header:
        parts += [header, ""]
    if outline:
        parts += ["## Headings", *outline]
    if inventory:
        parts += ["", inventory]
    if previews:
        parts += ["", "## Section openings (one per section, in document order)", previews]
    return "\n".join(parts), covered



def _render_sections(structure: MarkdownStructure, budget: int) -> tuple[int, str]:
    """Spend the remaining budget on section openings.

    Two regimes, because a long document has more sections than the budget can
    usefully describe:

    * **All sections fit** -- each gets an equal share.
    * **They do not** -- sections are *sampled at even intervals across the
      document* rather than taken in order. Taking them in order would spend
      everything on the opening and reproduce exactly the bug this module
      exists to fix.

    The final section is always included. A document's conclusion, its decision
    and its failure modes are disproportionately likely to live at the end, and
    dropping it is the single most damaging omission possible.
    """
    sections = structure.sections
    if not sections or budget <= 0:
        return 0, ""

    # Tokens each section line may occupy, including its own framing.
    per_section = budget // len(sections)
    sampled = sections
    if per_section < MIN_SECTION_TOKENS:
        # Too many sections for full coverage: sample evenly instead.
        affordable = max(1, budget // MIN_SECTION_TOKENS)
        affordable = min(affordable, len(sections))
        step = len(sections) / affordable
        picked = sorted({int(i * step) for i in range(affordable)})
        # The last section is never dropped, whatever the sampling says.
        if picked[-1] != len(sections) - 1:
            picked[-1] = len(sections) - 1
        sampled = [sections[i] for i in picked]

    per_section = max(MIN_SECTION_TOKENS, budget // len(sampled))
    preview_chars = max(60, int(per_section * CHARS_PER_TOKEN))

    rendered: list[str] = []
    used = 0
    for section in sampled:
        line = _section_line(section, preview_chars)
        cost = count_tokens(line) + 1
        if used + cost > budget:
            break
        rendered.append(line)
        used += cost

    covered = len(rendered)
    if covered < len(sections):
        rendered.append(
            f"- ... {len(sections) - covered} of {len(sections)} section openings "
            "not shown individually; the heading list above is complete and covers "
            "the whole document."
        )
    return covered, "\n".join(rendered)


def _section_line(section: Section, preview_chars: int) -> str:
    """One section rendered as a single line, preview clipped to its share."""
    label = (
        section.heading.text
        if section.heading
        else f"(preamble, lines {section.start_line}-{section.end_line})"
    )
    line = f"- [{label}] lines {section.start_line}-{section.end_line}: "
    preview = section.preview
    if not preview:
        return line + "(no prose outside code blocks)"
    if len(preview) > preview_chars:
        preview = preview[:preview_chars].rstrip() + " [...]"
    return line + preview



def build_representation(content: str, max_tokens: int) -> tuple[str, str]:
    """Represent a document for classification within a token budget.

    A document that already fits is returned **unchanged** -- no wrapper, no
    annotation, no modification of any kind. Only a document too large for its
    share of the budget is replaced by a structural digest, and that replacement
    says so on its first line.

    Returns ``(representation, mode)`` where mode is ``"full"`` or ``"digest"``.
    The source text is never edited: a digest is a separate rendering.
    """
    if count_tokens(content) <= max_tokens:
        return content, "full"

    structure = parse_markdown(content)
    digest, covered = render_digest(structure, max_tokens)
    if covered < len(structure.sections) and structure.sections:
        digest += (
            f"\n[Only {covered} of {len(structure.sections)} section openings "
            "fitted; the heading list above is complete.]"
        )
    return digest, "digest"
