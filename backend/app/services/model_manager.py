"""Local model runtime management for semantic indexing.

The application does not load embedding models itself -- it talks to an
OpenAI-compatible endpoint (see services/embeddings.py). This module manages
where those weights come from.

Why a runtime at all, rather than embedding one in-process
--------------------------------------------------------
Running these models locally would mean adding torch or onnxruntime to the
backend: gigabytes of wheels, and a direct contradiction of the constraint
this codebase is built on -- "Only the standard library is used, so the
application gains no vendor dependency" (see llm/openai_compat.py).

Two runtimes are supported, behind one small protocol:

``ollama``
    A daemon that already speaks the OpenAI protocol and ships its own pull
    API with progress. Convenient, and opinionated: it decides what a model is
    called and where it lives.

``llamacpp``
    ``llama-server`` also speaks the OpenAI protocol, and is much lighter, but
    it has NO download API at all -- the documentation says to point it at a
    model you already have. So the app does that part itself: a plain HTTPS
    download of a GGUF file into a models directory, which also means progress
    is reported from a real Content-Length for the whole transfer.

The choice is configuration (``EMBEDDING_RUNTIME``), and it decides where a
download goes and which identifier the model gets on the wire. It does not
decide where embeddings are served: that stays ``EMBEDDING_API_BASE_URL``,
because either runtime may equally be left unconfigured and a hosted endpoint
used instead.

What this module does NOT do
----------------------------
It never changes configuration. Downloading makes a model available; switching
models invalidates the existing vector index, and the app must not do that
silently.

Progress and concurrency
------------------------
A download runs in a daemon thread and reports through an in-process status
object, mirroring the indexing pattern (services/indexing.py): not a job
framework, just enough state for the UI to poll. One download at a time per
(runtime, model): a repeat request for a running download returns the running
status rather than fetching the same gigabytes twice.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.config import settings
from app.services.embeddings import EMBEDDING_MODEL_CATALOG

logger = logging.getLogger("apollo")

#: Default daemon address, used when no embedding endpoint identifies one.
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
#: HuggingFace serves model files straight from a CDN; a plain GET is enough.
HF_RESOLVE = "https://huggingface.co/{repo}/resolve/main/{filename}"


class ModelManagerError(RuntimeError):
    """Raised for user-correctable model management problems."""


@dataclass
class PullProgress:
    """Live state of one model download."""

    model: str
    runtime: str = "ollama"
    status: str = "idle"  # idle | starting | downloading | completed | failed
    message: str | None = None
    #: Bytes transferred, when the source reports a total. Ollama's stream
    #: gives a per-layer total, so its bar moves in steps; a direct HTTPS
    #: download has one total for the whole file, so its bar is smooth. The
    #: difference is the source's, not a rounding bug in this code.
    total_bytes: int | None = None
    completed_bytes: int | None = None
    started_at: str | None = None
    finished_at: str | None = None

    @property
    def percent(self) -> float | None:
        """Completion as 0-100, or None while it cannot be known.

        Ollama's "pulling manifest" and "verifying sha256" phases report no
        byte counts at all. Showing 0% for those is honest; showing a fake
        halfway bar is not.
        """
        if not self.total_bytes or self.completed_bytes is None:
            return None
        return min(100.0, round(self.completed_bytes / self.total_bytes * 100, 1))

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "runtime": self.runtime,
            "status": self.status,
            "message": self.message,
            "total_bytes": self.total_bytes,
            "completed_bytes": self.completed_bytes,
            "percent": self.percent,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


#: (runtime, model) -> progress. Guarded by _LOCK; read by the status endpoint.
_pulls: dict[tuple[str, str], PullProgress] = {}
_LOCK = threading.Lock()


def get_pull_status(runtime: str, model: str) -> PullProgress:
    with _LOCK:
        return _pulls.get((runtime, model)) or PullProgress(
            model=model, runtime=runtime, status="idle"
        )


def _utcnow() -> str:
    import datetime as dt

    return dt.datetime.now(dt.timezone.utc).isoformat()



# ---------------------------------------------------------------------------
# The runtime protocol
# ---------------------------------------------------------------------------


class LocalRuntime(Protocol):
    """What the app needs from a local runtime, and nothing more.

    Deliberately small. Anything a runtime cannot do -- notably downloading,
    which llama.cpp does not support at all -- shows up as an empty download
    entry in the catalog rather than as a special case threaded through the
    rest of the application.
    """

    id: str
    label: str

    def address(self) -> str:
        """Where this runtime is, for the UI to show when something fails."""

    def probe(self) -> tuple[bool, str | None]:
        """(usable, reason-it-is-not). A failed probe is normal, not an error."""

    def installed(self) -> set[str]:
        """Catalog names this runtime already has."""

    def fetch(self, model: str, progress: PullProgress) -> None:
        """Download ``model``, updating ``progress``. Blocking."""


def _download_entry(model: str, runtime_id: str) -> dict[str, Any] | None:
    """The catalog's download recipe for one model on one runtime, if any.

    None is a normal answer, not an error: a runtime simply may not have a way
    to fetch a given model, and the caller turns that into an explanation
    rather than an exception.
    """
    entry = EMBEDDING_MODEL_CATALOG.get(model)
    if entry is None:
        return None
    return (entry.get("downloads") or {}).get(runtime_id)


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


class OllamaRuntime:
    """The Ollama daemon, driven through its own native API."""

    id = "ollama"
    label = "Ollama"

    def address(self) -> str:
        """The daemon's native API root, derived from the embedding endpoint.

        EMBEDDING_API_BASE_URL points at Ollama's OpenAI-compatible layer
        (".../v1"), but pulling is a native-API operation ("/api/pull"). When
        the embedding endpoint is an Ollama one, reuse its host so there is a
        single place to configure the runtime instead of two that can disagree.
        """
        base = (settings.embedding_api_base_url or "").rstrip("/")
        if base and ("11434" in base or "ollama" in base.lower()):
            return base[: -len("/v1")] if base.endswith("/v1") else base
        return DEFAULT_OLLAMA_BASE_URL

    def probe(self) -> tuple[bool, str | None]:
        root = self.address()
        try:
            request = urllib.request.Request(f"{root}/api/tags", method="GET")
            with urllib.request.urlopen(request, timeout=3) as response:
                json.loads(response.read().decode("utf-8"))
            return True, None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            return False, (
                f"Ollama is not reachable at {root}. Start Ollama and try again ({reason})."
            )
        except (OSError, ValueError) as exc:
            return False, f"Ollama is not reachable at {root} ({exc})."

    def installed(self) -> set[str]:
        ok, _ = self.probe()
        if not ok:
            return set()
        try:
            with urllib.request.urlopen(
                urllib.request.Request(f"{self.address()}/api/tags", method="GET"), timeout=5
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            return set()
        models = body.get("models") if isinstance(body, dict) else None
        if not isinstance(models, list):
            return set()
        tags = {
            str(m.get("name")).split(":")[0]
            for m in models
            if isinstance(m, dict) and m.get("name")
        }
        # Translate the daemon's tags back into catalog names, so the UI asks
        # "is this model available" rather than "is this string present".
        return {
            catalog
            for catalog, entry in EMBEDDING_MODEL_CATALOG.items()
            if (entry.get("identifiers") or {}).get("ollama") in tags
        }

    def fetch(self, model: str, progress: PullProgress) -> None:
        tag = (EMBEDDING_MODEL_CATALOG[model].get("identifiers") or {}).get("ollama")
        if not tag:
            raise ModelManagerError(f"Ollama has no tag for {model}.")
        request = urllib.request.Request(
            f"{self.address()}/api/pull",
            data=json.dumps({"model": tag, "stream": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        progress.status = "downloading"
        with urllib.request.urlopen(request, timeout=3600) as response:
            for raw in response:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    # A keepalive or an unexpected line must not abort a
                    # multi-gigabyte download that is otherwise fine.
                    continue
                if not isinstance(event, dict):
                    continue
                status_text = event.get("status")
                if isinstance(status_text, str):
                    progress.message = status_text
                if isinstance(event.get("total"), int):
                    progress.total_bytes = event["total"]
                if isinstance(event.get("completed"), int):
                    progress.completed_bytes = event["completed"]
                if status_text == "success":
                    progress.status = "completed"
                    progress.message = "installed"
                    return
            # Stream ended without an explicit success line. Trust it only
            # when the runtime reported a fully-satisfied total; otherwise the
            # download is incomplete, and saying so beats reporting a phantom
            # success.
            if (
                progress.completed_bytes is not None
                and progress.total_bytes is not None
                and progress.completed_bytes >= progress.total_bytes
            ):
                progress.status = "completed"
                progress.message = "installed"
                return
        if progress.status == "downloading":
            progress.status = "failed"
            progress.message = progress.message or "The download stream ended before finishing."


# ---------------------------------------------------------------------------
# llama.cpp
# ---------------------------------------------------------------------------


class LlamaCppRuntime:
    """llama.cpp: a models directory the app fills, and a server it does not.

    llama-server speaks the OpenAI protocol and can serve embeddings, but it
    has no download API at all -- its documentation assumes you already have a
    model. So the download half is a plain HTTPS GET of a GGUF file, and
    "installed" is simply a file existing on disk. Both are simpler and more
    predictable than the daemon equivalent, and the progress bar is exact for
    the whole transfer because a single HTTP response has one Content-Length.
    """

    id = "llamacpp"
    label = "llama.cpp (llama-server)"

    def models_dir(self) -> Path:
        return Path(settings.effective_llamacpp_models_dir)

    def address(self) -> str:
        return str(self.models_dir())

    def probe(self) -> tuple[bool, str | None]:
        """Whether weights can be downloaded here.

        Deliberately does NOT require llama-server to be running. Downloading a
        file and serving it are separate acts, and refusing to fetch a model
        because the server happens to be stopped would be a wrong reason to
        refuse.
        """
        directory = self.models_dir()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return False, f"Cannot create the models directory {directory} ({exc})."
        if not os.access(directory, os.W_OK):
            return False, f"The models directory {directory} is not writable."
        return True, None

    def installed(self) -> set[str]:
        directory = self.models_dir()
        if not directory.is_dir():
            return set()
        present = {p.name for p in directory.glob("*.gguf")}
        return {
            catalog
            for catalog, entry in EMBEDDING_MODEL_CATALOG.items()
            if (entry.get("identifiers") or {}).get("llamacpp") in present
        }

    def fetch(self, model: str, progress: PullProgress) -> None:
        recipe = _download_entry(model, self.id)
        if recipe is None:
            raise ModelManagerError(f"No GGUF download is defined for {model}.")
        url = HF_RESOLVE.format(repo=recipe["repo"], filename=recipe["filename"])
        # Create the directory here rather than relying on probe() having run:
        # a caller reaching fetch directly should not have to know that.
        self.models_dir().mkdir(parents=True, exist_ok=True)
        target = self.models_dir() / recipe["filename"]
        # Download beside the target and move into place at the end: an
        # interrupted transfer must never leave a half-written .gguf that a
        # later run would mistake for a complete model.
        partial = target.with_suffix(target.suffix + ".part")
        progress.status = "downloading"
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=60) as response:
                total = response.headers.get("Content-Length")
                if total and total.isdigit():
                    progress.total_bytes = int(total)
                read = 0
                with open(partial, "wb") as handle:
                    while True:
                        chunk = response.read(1024 * 256)
                        if not chunk:
                            break
                        handle.write(chunk)
                        read += len(chunk)
                        progress.completed_bytes = read
                        progress.message = recipe["filename"]
            shutil.move(str(partial), str(target))
            progress.status = "completed"
            progress.message = f"Saved as {target.name}"
        except Exception:
            # A half-written .gguf must never be left behind: a later run
            # would see a .gguf on disk and report the model as installed.
            if partial.exists():
                partial.unlink(missing_ok=True)
            raise


# ---------------------------------------------------------------------------
# Registry and public surface
# ---------------------------------------------------------------------------

#: Every runtime the app can manage models for.
RUNTIMES: dict[str, LocalRuntime] = {
    OllamaRuntime.id: OllamaRuntime(),
    LlamaCppRuntime.id: LlamaCppRuntime(),
}


def get_runtime(runtime_id: str) -> LocalRuntime:
    """The runtime with this id, or a 400-worthy error."""
    runtime = RUNTIMES.get(runtime_id)
    if runtime is None:
        raise ModelManagerError(
            f"Unknown runtime {runtime_id!r}. Known runtimes: {', '.join(sorted(RUNTIMES))}."
        )
    return runtime


def active_runtime() -> LocalRuntime:
    """The runtime named by EMBEDDING_RUNTIME."""
    return get_runtime(settings.embedding_runtime)


def describe_runtimes() -> list[dict[str, Any]]:
    """Every runtime with its availability, for the UI's selector.

    Probed concurrently on purpose. A stopped runtime fails by timing out
    (three seconds), and probing the list serially made every catalog request
    -- and therefore every switch between runtimes in the UI -- wait for the
    slowest probe, even though the answers are independent.
    """
    results: list[dict[str, Any] | None] = [None] * len(RUNTIMES)

    def probe_at(index: int, runtime: LocalRuntime) -> None:
        try:
            available, message = runtime.probe()
        except Exception as exc:  # noqa: BLE001 - a probe must never break the list
            logger.debug("Probing runtime %s failed", runtime.id, exc_info=True)
            available, message = False, f"Could not check {runtime.label}: {exc}"
        results[index] = {
            "id": runtime.id,
            "label": runtime.label,
            "available": available,
            "message": message,
            "address": runtime.address(),
            "active": runtime.id == settings.embedding_runtime,
        }

    threads = [
        threading.Thread(target=probe_at, args=(i, r), name=f"probe-{r.id}", daemon=True)
        for i, r in enumerate(RUNTIMES.values())
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        # Join with a ceiling: a runtime probe is a short network call and
        # already bounded by its own timeout, so this cannot hang long.
        thread.join(timeout=10)
    return [r for r in results if r is not None]


def catalog_for_runtime(runtime_id: str) -> list[dict[str, Any]]:
    """The recommended models as this runtime can serve and fetch them.

    A model with no download entry for this runtime is still listed, with the
    reason stated. Hiding it would leave the operator wondering whether the app
    knows the model at all; saying "no GGUF exists for this one" tells them
    what is actually true.
    """
    runtime = get_runtime(runtime_id)
    available = runtime.installed()
    result: list[dict[str, Any]] = []
    for name, entry in EMBEDDING_MODEL_CATALOG.items():
        recipe = _download_entry(name, runtime_id)
        identifier = (entry.get("identifiers") or {}).get(runtime_id)
        result.append(
            {
                "name": name,
                "label": entry.get("label", name),
                "role": entry.get("role"),
                "dimension": entry.get("dimension"),
                "note": entry.get("note"),
                "runtime": runtime_id,
                "identifier": identifier,
                "downloadable": recipe is not None,
                "installed": name in available,
                "in_use": name
                in {settings.code_embedding_model, settings.document_embedding_model},
                # Distinguishes a vendor-published build from a community
                # conversion, so a third-party artefact is never presented as
                # interchangeable with the official weights.
                "official": (recipe or {}).get("official"),
                "source": (recipe or {}).get("note"),
            }
        )
    return result


def start_pull(runtime_id: str, model: str) -> PullProgress:
    """Begin downloading ``model`` to ``runtime_id``; return its live status.

    Refuses a model this runtime cannot fetch rather than pretending to try:
    an unavailable model gets an explanation, not a button that does nothing.
    A download already running for the same (runtime, model) is returned
    as-is -- fetching the same gigabytes twice helps nobody.
    """
    runtime = get_runtime(runtime_id)
    if model not in EMBEDDING_MODEL_CATALOG:
        raise ModelManagerError(f"Unknown embedding model {model!r}.")
    if _download_entry(model, runtime_id) is None:
        raise ModelManagerError(
            f"{model} cannot be downloaded for {runtime.label}. "
            "Use a runtime that provides it, or a hosted endpoint."
        )

    key = (runtime_id, model)
    with _LOCK:
        current = _pulls.get(key)
        if current is not None and current.status in ("starting", "downloading"):
            return current
        progress = PullProgress(
            model=model, runtime=runtime_id, status="starting", started_at=_utcnow()
        )
        _pulls[key] = progress

    ok, message = runtime.probe()
    if not ok:
        # Failed before the thread starts: report it as the result of this
        # call so the UI shows the reason immediately instead of polling a
        # download that never began.
        progress.status = "failed"
        progress.message = message
        progress.finished_at = _utcnow()
        return progress

    def worker() -> None:
        try:
            runtime.fetch(model, progress)
        except Exception as exc:  # noqa: BLE001 - worker must never die silently
            progress.status = "failed"
            progress.message = f"{type(exc).__name__}: {exc}"
            logger.exception("Fetching embedding model %s via %s failed", model, runtime_id)
        finally:
            progress.finished_at = _utcnow()

    threading.Thread(target=worker, name=f"model-fetch-{runtime_id}-{model}", daemon=True).start()
    return progress
