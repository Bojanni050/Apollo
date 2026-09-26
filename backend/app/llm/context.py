"""Context budgeting: making the configured context window a real limit.

``llm_context_tokens`` states how many tokens the *whole* request may occupy --
input **and** output. Before this module the setting was documented but never
read, so the application could assemble a request far larger than the model's
window and rely on the provider to reject it. This module makes the limit
enforced in the application instead.

Strategy
--------
The window is not all input. The budget is carved up front:

    +--------------------------------------------------+------------------+
    |  input                                           |  output reserve  |
    |  system + tools + history + tool results + user  |  max_output      |
    +--------------------------------------------------+------------------+

``input_budget`` is what remains for content, after subtracting the output
reserve and a small safety margin (tokenizers differ between implementations;
erring on the high side means a request we consider "fitted" really is).

Everything is then admitted in *priority* order, most valuable first, and
whatever does not fit is dropped or truncated with an explicit marker. The
guarantees, in order:

1. The system prompt and tool schemas are always kept. They are what make the
   model's behaviour and its evidence discipline correct.
2. The current user message is **never** silently removed. A request that
   cannot be answered without the user's actual question is not answerable, so
   this is truncated with a marker instead of dropped.
3. Conversation history is admitted most-recent-first, so the thread survives
   even when a long discussion must be shortened.
4. Tool results are budgeted like any other content. An oversized file read is
   truncated to the most relevant portion and is labelled as truncated, so the
   model is never left believing it saw a whole document.

Turns are admitted in *whole groups*. An assistant message that requests tool
calls is only valid when immediately followed by those tool results, so an
agentic turn is atomic: it is kept whole or dropped whole.

Nothing here is a tokenizer substitute for a specific vendor. :func:`count_tokens`
is a deliberately conservative estimate (see ``CHARS_PER_TOKEN``); the guarantee
it provides is "never larger than the budget", not "exactly equal".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

#: Average characters per token for prose and source code. Real ratios sit
#: around 4 for English; 3.6 leaves headroom so an estimate never under-counts
#: badly. Over-estimating wastes a little budget, which is the safe direction.
CHARS_PER_TOKEN = 3.6

#: Flat per-message framing overhead (role markers, separators) in OpenAI
#: wire format. Small, but it accumulates across a long history.
MESSAGE_OVERHEAD_TOKENS = 4

#: Extra tokens a tool result costs beyond its text: the tool_call_id and name
#: are transmitted alongside the content.
TOOL_RESULT_OVERHEAD_TOKENS = 3

#: Reserve kept aside so the provider has room for its own framing and the
#: request is never *exactly* at the limit.
SAFETY_MARGIN_RATIO = 0.02
SAFETY_MARGIN_MIN_TOKENS = 64

#: A single tool result may never claim more than this share of the input
#: budget, so one enormous file read cannot crowd out the conversation.
MAX_TOOL_RESULT_SHARE = 0.25

#: CJK text tokenizes roughly one character per token, far more finely than
#: Latin prose, so it is counted individually.
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]")

TRUNCATION_NOTICE = (
    "[... truncated: {omitted:,} of {total:,} characters omitted to fit the "
    "context budget ({reason}, max_context_tokens={budget:,}) ...]"
)

CONTEXT_BUDGET_ERROR = (
    "This request cannot fit within the configured model context window "
    "({needed:,} tokens needed, {available:,} available). "
    "Shorten the message, or raise LLM_CONTEXT_TOKENS."
)


def count_tokens(text: str | None) -> int:
    """Estimate the token cost of a piece of text.

    CJK characters are counted individually because they tokenize roughly
    one-per-character; everything else uses a conservative characters-per-token
    ratio.
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return cjk + int(other / CHARS_PER_TOKEN)


def count_message_tokens(message: dict[str, Any]) -> int:
    """Estimate the cost of one message in OpenAI wire format."""
    total = count_tokens(_as_text(message.get("content"))) + MESSAGE_OVERHEAD_TOKENS
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        total += count_tokens(call.get("id", ""))
        total += count_tokens(function.get("name", ""))
        total += count_tokens(function.get("arguments", ""))
    return total


def count_tool_schema_tokens(tools: list[dict[str, Any]] | None) -> int:
    """Estimate the cost of the tool definitions sent with every request."""
    if not tools:
        return 0
    return count_tokens(_json(tools)) + MESSAGE_OVERHEAD_TOKENS


def _as_text(value: Any) -> str:
    """Coerce a content field to text; providers allow null and lists."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):  # content blocks: [{type, text}, ...]
        parts = []
        for block in value:
            if isinstance(block, dict):
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(value)


def _json(value: Any) -> str:
    import json

    return json.dumps(value, separators=(",", ":"))


def truncate_text(
    text: str, max_tokens: int, *, reason: str, budget: int
) -> tuple[str, bool]:
    """Cut text to a token budget, appending a marker that says so.

    Returns ``(text, was_truncated)``. The marker names how much was dropped so
    the model can tell a partial file from a whole one, and never claims to have
    seen text that was removed.
    """
    if max_tokens <= 0:
        return "", True
    if count_tokens(text) <= max_tokens:
        return text, False

    total_chars = len(text)
    # Leave room for the marker itself so the result still fits its budget.
    marker_budget = max(16, max_tokens // 4)
    body_budget = max(0, max_tokens - marker_budget)

    # CJK costs ~1 token per character, Latin ~1 per CHARS_PER_TOKEN. Mixing
    # both in one string means the average density has to be measured, not
    # assumed -- otherwise a CJK-heavy document overshoots its budget badly.
    cjk = len(_CJK_RE.findall(text))
    density = cjk / total_chars if total_chars else 0.0
    chars_per_token = CHARS_PER_TOKEN * (1 - density) + density
    keep_chars = int(body_budget * chars_per_token)
    keep_chars = max(0, min(keep_chars, total_chars))

    notice = TRUNCATION_NOTICE.format(
        omitted=total_chars - keep_chars,
        total=total_chars,
        reason=reason,
        budget=budget,
    )
    return text[:keep_chars] + "\n" + notice, True


def truncate_document(
    text: str,
    max_tokens: int,
    *,
    query: str = "",
    reason: str = "document too large",
    budget: int,
) -> tuple[str, bool]:
    """Fit a document into a budget, keeping the most relevant parts.

    A plain head-truncation is poor for long documents: the title and overview
    usually live at the top, but the section a question is actually about can be
    anywhere. So the head is kept, and -- when the caller supplies the current
    question -- the lines with the strongest keyword overlap are pulled in as
    well. A document is never silently presented as complete: the notice reports
    how many characters were left out.
    """
    if count_tokens(text) <= max_tokens:
        return text, False
    if max_tokens <= 0:
        return "", True

    total_chars = len(text)
    marker_budget = max(16, max_tokens // 4)
    keep_budget = max(0, max_tokens - marker_budget)

    # Measure the script mix, as in truncate_text: a CJK-heavy document needs
    # far fewer characters to fill the same token budget.
    cjk = len(_CJK_RE.findall(text))
    density = cjk / total_chars if total_chars else 0.0
    chars_per_token = CHARS_PER_TOKEN * (1 - density) + density
    keep_chars = int(keep_budget * chars_per_token)

    keywords = {k for k in re.findall(r"[A-Za-z0-9_]{3,}", (query or "").lower())}

    if not keywords or keep_chars >= total_chars:
        head = text[:keep_chars]
        relevant = ""
    else:
        # Split the allowance: half for the opening, half for matching lines.
        head = text[: int(keep_chars * 0.5)]
        allowance = int(keep_chars * 0.5)
        scored: list[tuple[int, int, str]] = []
        for index, line in enumerate(text.splitlines()):
            lowered = line.lower()
            score = sum(1 for k in keywords if k in lowered)
            if score:
                scored.append((score, index, line))
        # Strongest matches first; ties keep document order for readability.
        scored.sort(key=lambda item: (-item[0], item[1]))
        chosen: list[str] = []
        used = 0
        for _, _, line in scored:
            if used + len(line) > allowance:
                continue
            chosen.append(line)
            used += len(line)
        relevant = (
            "\n[... most relevant lines matching the current question ...]\n"
            + "\n".join(chosen)
            if chosen
            else ""
        )

    omitted = max(0, total_chars - len(head) - len(relevant))
    notice = TRUNCATION_NOTICE.format(
        omitted=omitted,
        total=total_chars,
        reason=reason,
        budget=budget,
    )
    return head + relevant + "\n" + notice, True


@dataclass
class Turn:
    """A group of messages that must be kept or dropped together.

    An assistant message carrying ``tool_calls`` is only meaningful immediately
    before its tool results, so those are one turn. Ordinary history messages
    are one message each, and the current user message is always its own turn.
    """

    messages: list[dict[str, Any]]
    kind: TurnKind = "history"
    # Higher survives longer. The current request is effectively infinite.
    priority: int = 0
    # True for the message the user just sent: never dropped, only truncated.
    mandatory: bool = False
    label: str = ""

    @property
    def tokens(self) -> int:
        return sum(count_message_tokens(m) for m in self.messages)


@dataclass
class BudgetReport:
    """What the planner did -- for logging, and for the tests to assert on."""

    total_tokens: int = 0
    input_budget: int = 0
    kept_turns: int = 0
    dropped_turns: int = 0
    truncated_turns: int = 0
    notes: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)


class ContextBudgetError(RuntimeError):
    """The request cannot be made to fit, even after truncating.

    Raised instead of sending a request the provider is bound to reject, so the
    user gets an actionable application error rather than a raw provider one.
    """


@dataclass
class ContextBudget:
    """The input allowance for one model call."""

    context_tokens: int
    max_output_tokens: int
    safety_margin: int | None = None

    def __post_init__(self) -> None:
        if self.context_tokens <= 0:
            raise ValueError("context_tokens must be positive.")
        if self.max_output_tokens < 0:
            raise ValueError("max_output_tokens must not be negative.")
        if self.max_output_tokens >= self.context_tokens:
            # No room for a single input token: the config is self-contradictory.
            raise ValueError(
                f"max_output_tokens ({self.max_output_tokens:,}) must be smaller "
                f"than context_tokens ({self.context_tokens:,})."
            )

    @property
    def margin(self) -> int:
        if self.safety_margin is not None:
            return self.safety_margin
        return max(
            SAFETY_MARGIN_MIN_TOKENS, int(self.context_tokens * SAFETY_MARGIN_RATIO)
        )

    @property
    def input_budget(self) -> int:
        """Tokens available for system + tools + history + current message."""
        return self.context_tokens - self.max_output_tokens - self.margin

    @property
    def max_tool_result_tokens(self) -> int:
        """Ceiling for a single tool result, so one cannot take over the budget."""
        return max(256, int(self.input_budget * MAX_TOOL_RESULT_SHARE))

    @classmethod
    def from_settings(cls, settings: Any) -> "ContextBudget":
        return cls(
            context_tokens=settings.llm_context_tokens,
            max_output_tokens=settings.llm_max_output_tokens,
        )

    # -- measurement ----------------------------------------------------
    def total_tokens(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> int:
        return sum(count_message_tokens(m) for m in messages) + count_tool_schema_tokens(tools)

    def fits(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> bool:
        """Whether a request would fit the window, output reserve included."""
        return self.total_tokens(messages, tools) + self.max_output_tokens <= self.context_tokens

    # -- planning -------------------------------------------------------
    def plan(
        self,
        *,
        system: str,
        turns: list[Turn],
        tools: list[dict[str, Any]] | None = None,
        current_query: str = "",
    ) -> tuple[list[dict[str, Any]], BudgetReport]:
        """Assemble a message list that fits, preserving what matters.

        Turns are admitted most-valuable-first: a turn that fits is kept whole,
        one that does not is dropped -- unless it is the current user message,
        which is truncated instead. The result is re-assembled in the original
        order so the transcript still reads chronologically.
        """
        report = BudgetReport(input_budget=self.input_budget)

        system_message = {"role": "system", "content": system}
        fixed = count_message_tokens(system_message) + count_tool_schema_tokens(tools)
        remaining = self.input_budget - fixed
        if remaining <= 0:
            raise ContextBudgetError(
                "The system prompt and tool definitions alone exceed the context "
                f"budget ({fixed:,} tokens vs {self.input_budget:,} available). "
                "Raise LLM_CONTEXT_TOKENS or shorten the system prompt."
            )

        # Most valuable first; ties break on recency, so the newest survives.
        ordered = sorted(enumerate(turns), key=lambda item: (-item[1].priority, -item[0]))

        admitted: dict[int, Turn] = {}
        used = 0

        for index, turn in ordered:
            cost = turn.tokens
            spare = remaining - used
            if cost <= spare:
                admitted[index] = turn
                used += cost
                continue

            # A tool result is never simply dropped when it is too large. The
            # model explicitly asked for that evidence, and discarding it would
            # both lose the answer to the question and orphan the assistant
            # message that requested it (a tool result is invalid without the
            # tool_calls turn before it). So it is truncated instead.
            if turn.kind == "tool" and spare > 0:
                fitted = self._fit_turn(turn, spare, current_query)
                admitted[index] = fitted
                used += fitted.tokens
                report.truncated_turns += 1
                report.note("A tool result was shortened to fit the context window.")
                continue

            if turn.kind == "current" or turn.mandatory:
                # The user's actual question is never dropped. Fit it instead.
                fitted = self._fit_turn(turn, spare, current_query)
                admitted[index] = fitted
                used += fitted.tokens
                report.truncated_turns += 1
                report.note(
                    "The current message was shortened to fit the context window."
                )
                continue

            report.dropped_turns += 1
            if turn.kind == "history":
                report.note(
                    f"Dropped {turn.label or 'an older message'} to fit the context window."
                )
            else:
                report.note(f"Dropped a tool result ({turn.label or 'unlabelled'}).")

        # A request without the user's actual question is not worth sending.
        if not any(t.kind == "current" or t.mandatory for t in admitted.values()):
            raise ContextBudgetError(
                CONTEXT_BUDGET_ERROR.format(
                    needed=used + self.max_output_tokens, available=self.context_tokens
                )
            )

        messages = [system_message]
        for index, turn in sorted(admitted.items()):
            messages.extend(turn.messages)

        report.total_tokens = self.total_tokens(messages, tools)
        report.kept_turns = len(admitted)

        # Defence in depth: the arithmetic above should make this unreachable,
        # but an oversized request must never leave this module.
        if report.total_tokens + self.max_output_tokens > self.context_tokens:
            raise ContextBudgetError(
                CONTEXT_BUDGET_ERROR.format(
                    needed=report.total_tokens + self.max_output_tokens,
                    available=self.context_tokens,
                )
            )
        return messages, report

    def _fit_turn(self, turn: Turn, available: int, query: str) -> Turn:
        """Truncate a turn so it fits ``available`` tokens.

        Tool results are capped individually so one huge file read cannot consume
        the whole allowance. Documents and messages are truncated with a notice
        so the model knows it is looking at a fragment.
        """
        if available <= 0:
            return Turn(messages=[], kind=turn.kind, priority=turn.priority,
                        mandatory=turn.mandatory, label=turn.label)

        if turn.kind == "tool":
            cap = min(self.max_tool_result_tokens, available)
            fitted = []
            for message in turn.messages:
                text = _as_text(message.get("content"))
                if not text:
                    fitted.append(message)
                    continue
                capped, _ = truncate_text(
                    text,
                    cap,
                    reason=f"tool result {message.get('name', '')}".strip(),
                    budget=self.context_tokens,
                )
                fitted.append({**message, "content": capped})
            return Turn(messages=fitted, kind=turn.kind, priority=turn.priority,
                        label=turn.label)

        fitted = []
        for index, message in enumerate(turn.messages):
            text = _as_text(message.get("content"))
            if not text:
                fitted.append(message)
                continue
            if turn.kind == "history" and query:
                # Older turns use the relevance-aware path: a long earlier
                # message is more useful for its on-topic lines than its length.
                capped, _ = truncate_document(
                    text, available, query=query,
                    reason="older message", budget=self.context_tokens,
                )
            else:
                capped, _ = truncate_text(
                    text, available, reason="message too large",
                    budget=self.context_tokens,
                )
            fitted.append({**message, "content": capped})
        return Turn(messages=fitted, kind=turn.kind, priority=turn.priority,
                    mandatory=turn.mandatory, label=turn.label)


def history_turns(messages: Iterable[Any], *, max_messages: int | None = None) -> list[Turn]:
    """Turn stored history into budget-aware turns, newest weighted highest.

    ``max_messages`` is only a fetch bound (so a very long conversation does not
    load an unbounded number of rows from the database). It is deliberately
    generous and independent of the context budget: which messages actually
    survive is decided by :meth:`ContextBudget.plan`, not by this cut-off.
    """
    items = list(messages)
    if max_messages is not None and len(items) > max_messages:
        items = items[-max_messages:]
    total = len(items)

    turns: list[Turn] = []
    for index, message in enumerate(items):
        role = getattr(message, "role", None)
        content = getattr(message, "content", None)
        if role is None and isinstance(message, dict):
            role = message.get("role")
            content = message.get("content")
        if role == "tool":
            # Persisted tool output is not stored; a tool result in history
            # would be an orphan anyway, so it is skipped rather than resent.
            continue
        turns.append(
            Turn(
                messages=[{"role": role, "content": content}],
                kind="history",
                # Recency is the tie-breaker the planner sorts on.
                priority=index,
                label=f"message {index + 1} of {total}",
            )
        )
    return turns

