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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence request logging
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        messages = payload.get("messages", [])

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
