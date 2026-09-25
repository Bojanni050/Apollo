"""A minimal OpenAI-compatible mock server, for verifying the real HTTP path.

This is a TEST FIXTURE, not part of the application. It mimics /chat/completions
well enough to exercise tool calling, so the provider can be validated without
network access or an API key.

Run:  python mock_llm_server.py [port]
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

# The model "decides" to search, then to read, then answers -- exercising the
# full tool loop over real HTTP.
SEARCH_ARGS = {"query": "memory architecture", "limit": 5}
READ_ARGS = {"repository": "gaia-docs", "path": "architecture/components/memory.md"}


# Inventory classification. Chosen to prove that placement follows CONTENT,
# not the filename: "notes.md" looks like scratch notes but actually records a
# decision, so it belongs with the decisions.
INVENTORY_ENTRIES = [
    (
        "architecture/overview.md",
        "Describes how the system is divided into services and components.",
        "architecture",
        0.95,
        [],
        False,
        "Already in the right place.",
    ),
    (
        "notes.md",
        "Records a choice: we will use SQLite for local-first storage, with the "
        "reasoning and the alternatives that were considered.",
        "architecture/decisions",
        0.82,
        ["architecture/overview.md"],
        False,
        "Despite the filename, this records an architectural decision.",
    ),
    (
        "architecture/components/memory.md",
        "Describes the memory component: what it owns and its boundaries.",
        "architecture/components",
        0.93,
        [],
        False,
        "A component description.",
    ),
    (
        "foundation/principles.md",
        "States the core principles the system assumes.",
        "foundation",
        0.9,
        [],
        False,
        "Core principles.",
    ),
    (
        "development/local-setup.md",
        "Scraps and half-formed thoughts, possibly about several things at once.",
        None,
        0.2,
        [],
        True,
        "Could be setup instructions or an unfiled note. Needs a human.",
    ),
]


def _inventory_reply(user_content: str) -> dict:
    """Classify whichever documents this request actually contained."""
    # Documents are introduced by lines of the form:  --- some/path.md ---
    seen = {
        line.strip()[3:].strip()[: -len("---")].strip()
        for line in user_content.splitlines()
        if line.strip().startswith("--- ") and line.strip().endswith(" ---")
    }
    classifications = []
    for path, purpose, suggested, confidence, overlaps, ambiguous, note in INVENTORY_ENTRIES:
        if path in seen:
            classifications.append(
                {
                    "path": path,
                    "purpose": purpose,
                    "suggested_path": suggested,
                    "confidence": confidence,
                    "overlaps": overlaps,
                    "ambiguous": ambiguous,
                    "note": note,
                }
            )
    return {
        "classifications": classifications,
        "summary": (
            "The documentation is mostly well placed, but two files are misfiled: "
            "notes.md records a decision despite its name, and development/"
            "local-setup.md could not be placed with confidence."
        ),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence request logging
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        messages = payload.get("messages", [])
        system = messages[0].get("content", "") if messages else ""
        user = messages[-1].get("content", "") if messages else ""

        # The inventory prompt is a single-shot JSON classification request
        # (no tools), which is a different shape from the chat tool loop.
        if "You are organising" in system:
            reply = json.dumps(_inventory_reply(user))
            self._send({"role": "assistant", "content": reply}, payload)
            return

        # Count how many tool results we have already seen.
        tool_results = [m for m in messages if m.get("role") == "tool"]

        if len(tool_results) == 0:
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "search_documents",
                            "arguments": json.dumps(SEARCH_ARGS),
                        },
                    }
                ],
            }
        elif len(tool_results) == 1:
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "read_document",
                            "arguments": json.dumps(READ_ARGS),
                        },
                    }
                ],
            }
        else:
            # Prove the tool results actually reached the model.
            seen = " ".join(str(m.get("content", "")) for m in tool_results)
            message = {
                "role": "assistant",
                "content": (
                    "The memory component at architecture/components/memory.md "
                    "stores conversational context and also handles retrieval. "
                    f"[tool output contained {len(seen)} chars]"
                ),
            }

        self._send(message, payload)

    def _send(self, message: dict, payload: dict) -> None:
        body = json.dumps(
            {
                "id": "mock",
                "model": payload.get("model", "mock"),
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        ).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8097
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
