"""Local model runtime management: catalog, name resolution and downloads.

Two runtimes are supported, and the tests cover both against stand-ins rather
than the real thing: a local HTTP server that speaks Ollama's stream, and a
local directory for llama.cpp weights. That keeps the suite offline while still
exercising the parts that are easy to get wrong -- progress parsing, a stream
that ends early, an interrupted file download, and the model-name translation
that differs per runtime.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.services import model_manager
from app.services.embeddings import (
    _is_llamacpp_endpoint,
    _is_ollama_endpoint,
    api_model_name,
)


@pytest.fixture(autouse=True)
def _clean_pulls():
    """Each test starts with no download in progress."""
    model_manager._pulls.clear()
    yield
    model_manager._pulls.clear()


def _wait_for_settle(progress, timeout: float = 5.0) -> None:
    """Block until a download leaves the in-flight states."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if progress.status in ("completed", "failed"):
            return
        time.sleep(0.02)
    raise AssertionError(f"download did not settle, status={progress.status}")


class _FakeOllama(BaseHTTPRequestHandler):
    """Answers /api/tags and streams a scripted /api/pull."""

    #: Scripted NDJSON lines for the next pull.
    script: list[dict] = []
    #: When set, /api/tags reports these model names.
    tags: list[str] = []

    def log_message(self, *args):  # noqa: D102 - silence the default logging
        pass

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        body = json.dumps({"models": [{"name": n} for n in _FakeOllama.tags]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for event in _FakeOllama.script:
            self.wfile.write((json.dumps(event) + "\n").encode())
            self.wfile.flush()
        self.wfile.close()


@pytest.fixture
def fake_ollama(monkeypatch):
    """Run a stand-in Ollama on a free port and point the runtime at it."""
    server = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root = f"http://127.0.0.1:{port}"
    monkeypatch.setattr(model_manager, "DEFAULT_OLLAMA_BASE_URL", root)
    monkeypatch.setattr(model_manager.OllamaRuntime, "address", lambda self: root)
    try:
        yield root
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def models_dir(tmp_path, monkeypatch):
    """An isolated llama.cpp models directory.

    Patches the underlying setting rather than the resolved property: the
    property is derived, and the point here is to redirect where weights land.
    """
    directory = tmp_path / "models"
    monkeypatch.setattr(model_manager.settings, "llamacpp_models_dir", str(directory))
    return directory


class TestNameResolution:
    """One logical model, different identifiers per runtime.

    The two model names do not overlap, so getting the runtime wrong sends a
    name the endpoint has never heard of -- an error with no useful message.
    """

    def test_ollama_gets_its_tag(self):
        # "BAAI/bge-m3" is the HuggingFace name; Ollama only knows "bge-m3".
        assert api_model_name("BAAI/bge-m3", "http://127.0.0.1:11434/v1") == "bge-m3"

    def test_llamacpp_gets_the_gguf_filename(self):
        assert (
            api_model_name("BAAI/bge-m3", "http://127.0.0.1:8080/v1")
            == "bge-m3-Q8_0.gguf"
        )

    def test_hosted_endpoint_keeps_the_vendor_name(self):
        assert api_model_name("BAAI/bge-m3", "https://api.jina.ai/v1") == "BAAI/bge-m3"

    def test_ollama_is_not_mistaken_for_llamacpp(self):
        # Both listen on localhost. A "is it local?" test would hand Ollama's
        # port a llama-server model name and the daemon would 404 on a name
        # that looks perfectly reasonable.
        assert _is_ollama_endpoint("http://127.0.0.1:11434/v1") is True
        assert _is_llamacpp_endpoint("http://127.0.0.1:11434/v1") is False

    def test_uncatalogued_model_passes_through_unchanged(self):
        assert api_model_name("operator-model", "http://127.0.0.1:8080/v1") == (
            "operator-model"
        )

    def test_no_endpoint_uses_the_vendor_name(self):
        assert api_model_name("BAAI/bge-m3", None) == "BAAI/bge-m3"

    def test_llamacpp_detection(self):
        assert _is_llamacpp_endpoint("http://127.0.0.1:8080/v1") is True
        assert _is_llamacpp_endpoint("http://host/llama-server/v1") is True
        assert _is_llamacpp_endpoint("https://api.jina.ai/v1") is False


def _fake_gguf(payload: bytes, fail_after: int | None = None):
    """A urlopen stand-in serving ``payload`` with a real Content-Length.

    ``fail_after`` raises mid-body, standing in for a dropped connection.
    """
    import io

    class _Response(io.BytesIO):
        def __init__(self) -> None:
            super().__init__(payload)
            self.headers = {"Content-Length": str(len(payload))}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()
            return False

        def read(self, size=-1):
            if fail_after is not None:
                # Consume the allowance, then break the connection.
                allowed = max(0, fail_after - getattr(self, "_read_so_far", 0))
                if allowed == 0:
                    raise OSError("connection reset")
                self._read_so_far = getattr(self, "_read_so_far", 0) + allowed
                return super().read(min(size if size and size > 0 else allowed, allowed))
            return super().read(size)

    return lambda *a, **k: _Response()




class TestCatalog:
    def test_both_recommended_models_are_offered(self):
        for runtime_id in model_manager.RUNTIMES:
            names = {m["name"] for m in model_manager.catalog_for_runtime(runtime_id)}
            assert "BAAI/bge-m3" in names
            assert "jina-code-embeddings-1.5b" in names

    def test_ollama_cannot_fetch_the_code_model(self):
        # There is no Ollama tag for it, verified against the Ollama library.
        entries = {m["name"]: m for m in model_manager.catalog_for_runtime("ollama")}
        assert entries["jina-code-embeddings-1.5b"]["downloadable"] is False

    def test_llamacpp_can_fetch_both(self):
        entries = {m["name"]: m for m in model_manager.catalog_for_runtime("llamacpp")}
        assert all(e["downloadable"] for e in entries.values())

    def test_provenance_is_reported_per_runtime(self):
        """A community conversion is never presented as the vendor's weights.

        The code model has an official Jina GGUF; the documentation model does
        not, and BAAI publishes no GGUF at all.
        """
        ollama = {m["name"]: m for m in model_manager.catalog_for_runtime("ollama")}
        llama = {m["name"]: m for m in model_manager.catalog_for_runtime("llamacpp")}
        assert ollama["BAAI/bge-m3"]["official"] is True
        assert llama["jina-code-embeddings-1.5b"]["official"] is True
        assert llama["BAAI/bge-m3"]["official"] is False

    def test_llamacpp_installed_reflects_files_on_disk(self, models_dir):
        models_dir.mkdir(parents=True, exist_ok=True)
        (models_dir / "bge-m3-Q8_0.gguf").write_bytes(b"not a real model")
        entries = {m["name"]: m for m in model_manager.catalog_for_runtime("llamacpp")}
        assert entries["BAAI/bge-m3"]["installed"] is True
        assert entries["jina-code-embeddings-1.5b"]["installed"] is False

    def test_configured_models_are_flagged_in_use(self):
        entries = {m["name"]: m for m in model_manager.catalog_for_runtime("ollama")}
        assert entries["BAAI/bge-m3"]["in_use"] is True

    def test_unknown_runtime_is_refused(self):
        with pytest.raises(model_manager.ModelManagerError):
            model_manager.get_runtime("not-a-runtime")


class TestLlamaCppDownload:
    """A GGUF fetched by the app itself, since llama.cpp cannot fetch one."""

    def test_probe_creates_the_directory_and_does_not_need_a_server(self, models_dir):
        runtime = model_manager.get_runtime("llamacpp")
        ok, message = runtime.probe()
        assert ok is True, message
        assert models_dir.is_dir()

    def test_download_writes_the_file(self, models_dir, monkeypatch):
        payload = b"GGUF-ish bytes"
        monkeypatch.setattr(model_manager.urllib.request, "urlopen", _fake_gguf(payload))
        runtime = model_manager.get_runtime("llamacpp")
        progress = model_manager.PullProgress(model="BAAI/bge-m3", runtime="llamacpp")
        runtime.fetch("BAAI/bge-m3", progress)
        assert progress.status == "completed"
        assert (models_dir / "bge-m3-Q8_0.gguf").read_bytes() == payload

    def test_progress_reports_the_real_content_length(self, models_dir, monkeypatch):
        payload = b"x" * 5000
        monkeypatch.setattr(model_manager.urllib.request, "urlopen", _fake_gguf(payload))
        runtime = model_manager.get_runtime("llamacpp")
        progress = model_manager.PullProgress(model="BAAI/bge-m3", runtime="llamacpp")
        runtime.fetch("BAAI/bge-m3", progress)
        # A direct HTTPS download has one total for the whole transfer, so the
        # bar is exact from the first byte -- unlike the daemon's per-layer
        # stream, whose manifest phase reports nothing at all.
        assert progress.total_bytes == 5000
        assert progress.percent == 100.0

    def test_interrupted_download_leaves_no_gguf(self, models_dir, monkeypatch):
        # A half-written .gguf would be reported as installed on the next run.
        monkeypatch.setattr(
            model_manager.urllib.request, "urlopen", _fake_gguf(b"partial", fail_after=10)
        )
        runtime = model_manager.get_runtime("llamacpp")
        progress = model_manager.PullProgress(model="BAAI/bge-m3", runtime="llamacpp")
        with pytest.raises(OSError):
            runtime.fetch("BAAI/bge-m3", progress)
        assert not (models_dir / "bge-m3-Q8_0.gguf").exists()
        assert not (models_dir / "bge-m3-Q8_0.gguf.part").exists()

    def test_a_model_without_a_gguf_is_refused(self, models_dir):
        runtime = model_manager.get_runtime("llamacpp")
        progress = model_manager.PullProgress(model="not-in-catalog", runtime="llamacpp")
        with pytest.raises(model_manager.ModelManagerError):
            runtime.fetch("not-in-catalog", progress)


class TestOllamaDownload:
    """The daemon's own pull stream, which reports per layer."""

    def test_progress_is_parsed_from_the_stream(self, fake_ollama):
        _FakeOllama.script = [
            {"status": "pulling manifest"},
            {"status": "downloading", "digest": "sha256:abc", "total": 1000, "completed": 250},
            {"status": "downloading", "digest": "sha256:abc", "total": 1000, "completed": 1000},
            {"status": "success"},
        ]
        try:
            progress = model_manager.start_pull("ollama", "BAAI/bge-m3")
            _wait_for_settle(progress)
        finally:
            _FakeOllama.script = []

        assert progress.status == "completed"
        assert progress.total_bytes == 1000
        assert progress.percent == 100.0

    def test_percent_is_none_when_no_bytes_are_reported(self, fake_ollama):
        # The manifest and checksum phases carry no byte counts. Reporting a
        # number there would be a fabricated percentage.
        _FakeOllama.script = [{"status": "pulling manifest"}, {"status": "success"}]
        try:
            progress = model_manager.start_pull("ollama", "BAAI/bge-m3")
            _wait_for_settle(progress)
        finally:
            _FakeOllama.script = []

        assert progress.status == "completed"
        assert progress.percent is None

    def test_stream_ending_early_is_a_failure(self, fake_ollama):
        # No success line and the bytes do not add up: reporting a phantom
        # success would make the UI claim a model is installed when it is not.
        _FakeOllama.script = [
            {"status": "downloading", "total": 1000, "completed": 400},
        ]
        try:
            progress = model_manager.start_pull("ollama", "BAAI/bge-m3")
            _wait_for_settle(progress)
        finally:
            _FakeOllama.script = []

        assert progress.status == "failed"

    def test_installed_models_are_translated_to_catalog_names(self, fake_ollama):
        _FakeOllama.tags = ["bge-m3:latest"]
        try:
            assert model_manager.get_runtime("ollama").installed() == {"BAAI/bge-m3"}
        finally:
            _FakeOllama.tags = []

    def test_unreachable_daemon_fails_with_an_actionable_message(self, monkeypatch):
        # Port 9 (discard) refuses immediately, standing in for a stopped
        # daemon.
        monkeypatch.setattr(
            model_manager.OllamaRuntime, "address", lambda self: "http://127.0.0.1:9"
        )
        progress = model_manager.start_pull("ollama", "BAAI/bge-m3")
        assert progress.status == "failed"
        assert "Ollama" in (progress.message or "")

    def test_a_second_pull_does_not_restart(self, fake_ollama):
        _FakeOllama.script = [{"status": "downloading", "total": 10, "completed": 1}] * 200
        try:
            first = model_manager.start_pull("ollama", "BAAI/bge-m3")
            second = model_manager.start_pull("ollama", "BAAI/bge-m3")
            # Same object: the caller is told it is already running rather than
            # being sent off to download the same gigabytes twice.
            assert first is second
        finally:
            _FakeOllama.script = []


class TestRefusals:
    """A model a runtime cannot fetch gets an explanation, not a button."""

    def test_ollama_cannot_fetch_the_code_model(self, fake_ollama):
        with pytest.raises(model_manager.ModelManagerError):
            model_manager.start_pull("ollama", "jina-code-embeddings-1.5b")

    def test_unknown_model_is_refused(self):
        with pytest.raises(model_manager.ModelManagerError):
            model_manager.start_pull("ollama", "not-a-real-model")

    def test_unknown_runtime_is_refused(self):
        with pytest.raises(model_manager.ModelManagerError):
            model_manager.start_pull("nope", "BAAI/bge-m3")

    def test_status_is_idle_before_anything_runs(self):
        status = model_manager.get_pull_status("llamacpp", "BAAI/bge-m3")
        assert status.status == "idle"
        assert status.runtime == "llamacpp"


class TestCatalogAndPullApi:
    """The API surface, with no runtime present.

    An operator with nothing running must get an explanation, not an error
    page -- otherwise the panel looks broken rather than unavailable.
    """

    def test_catalog_reports_runtimes_and_models(self, client):
        resp = client.get("/api/system/embedding-models")
        assert resp.status_code == 200
        body = resp.json()
        assert {r["id"] for r in body["runtimes"]} == {"ollama", "llamacpp"}
        assert "BAAI/bge-m3" in {m["name"] for m in body["models"]}

    def test_a_runtime_can_be_selected_explicitly(self, client):
        resp = client.get("/api/system/embedding-models?runtime=llamacpp")
        assert resp.status_code == 200
        body = resp.json()
        assert body["runtime"] == "llamacpp"
        # The code model is fetchable here and not on Ollama; that asymmetry
        # is the whole point of offering both.
        entries = {m["name"]: m for m in body["models"]}
        assert entries["jina-code-embeddings-1.5b"]["downloadable"] is True

    def test_unknown_runtime_is_a_400(self, client):
        resp = client.get("/api/system/embedding-models?runtime=nope")
        assert resp.status_code == 400

    def test_pull_endpoints_reject_a_model_the_runtime_cannot_fetch(self, client):
        resp = client.post(
            "/api/system/embedding-models/pull",
            json={"model": "jina-code-embeddings-1.5b", "runtime": "ollama"},
        )
        assert resp.status_code == 400

    def test_pull_endpoint_rejects_an_unknown_model(self, client):
        resp = client.post(
            "/api/system/embedding-models/pull", json={"model": "nope", "runtime": "ollama"}
        )
        assert resp.status_code == 400

    def test_pull_status_endpoint_is_idle_before_anything_runs(self, client):
        resp = client.get("/api/system/embedding-models/pull?model=BAAI/bge-m3&runtime=ollama")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "idle"
        # Nothing was downloaded, so nothing needs rebuilding.
        assert body["reindex_recommended"] is False
