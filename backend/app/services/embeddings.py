"""Embedding providers for semantic indexing.

Application code depends only on the :class:`EmbeddingProvider` protocol, never
on a vendor SDK or a model identifier. Which model serves which content type is
pure configuration (``CODE_EMBEDDING_MODEL`` / ``DOCUMENT_EMBEDDING_MODEL``),
and every embedding row records the model name and dimension it was produced
with, so a model change can never silently mix incompatible vectors.

Providers
---------
``RemoteEmbeddingProvider``
    An OpenAI-compatible ``/embeddings`` endpoint (the Jina API, a local
    inference server, vLLM, ...). Uses only the standard library, like the LLM
    provider. Network I/O runs in a worker thread so the async ``embed`` never
    blocks the event loop.

``LocalEmbeddingProvider``
    A deterministic hash-based fallback so the application -- and its test
    suite -- works with no paid external API and no network access. It is not
    a semantic embedder; it exists so that indexing, storage and retrieval are
    fully exercisable offline, and so a deployment without an embedding
    endpoint degrades instead of crashing.

The rest of the application is synchronous (sync SQLAlchemy, sync agent
loop), so this module also exposes thin synchronous wrappers
(:func:`embed_code`, :func:`embed_documents`) that drive the async protocol to
completion.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.config import settings


class EmbeddingError(RuntimeError):
    """Raised for user-correctable embedding problems (bad config, API)."""


class EmbeddingDimensionError(EmbeddingError):
    """Raised when a provider returns vectors whose shape is not usable."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    """The minimal surface the indexer needs from any embedder."""

    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed ``texts``, returning one vector per input, in order."""
        ...


@dataclass(frozen=True)
class EmbeddingResult:
    """Batched embedding output plus the model metadata stored alongside."""

    vectors: list[list[float]]
    model_name: str
    dimension: int


@dataclass(frozen=True)
class _ModelSpec:
    """A known embedding model's native dimension.

    The identifier sent on the wire is NOT taken from here: it depends on which
    endpoint is configured, so it is resolved through
    :func:`api_model_name` (and ``EMBEDDING_MODEL_CATALOG``) instead. What this
    holds is the dimension, which is a property of the model itself and is
    recorded next to every stored vector.

    The configured name (e.g. ``jina-code-embeddings-1.5b``) remains the stable
    identity in the database regardless of which identifier was used to fetch
    it. Unknown models are still allowed -- they simply require the provider
    response to define their dimension.
    """

    api_id: str
    dimension: int


#: Jina Code Embeddings 1.5B: source code; supports natural-language ->
#: code retrieval. 768 output dimensions.
#:
#: ``api_id`` is deliberately the same string as the configured model name.
#: It used to say "jina-embeddings-v3", which is a *different* model that
#: returns 1024-d vectors. Every stored row claims 768, so the first real
#: index run raised EmbeddingDimensionError on the very first vector. Naming
#: the model we actually configured keeps the recorded dimension truthful.
CODE_MODEL_SPEC = _ModelSpec(api_id="jina-code-embeddings-1.5b", dimension=768)
#: BAAI/bge-m3 for Markdown, ADRs and architecture documentation: 1024 dims.
#: The Ollama tag is "bge-m3"; see EMBEDDING_MODEL_CATALOG for why the
#: HuggingFace namespace is not what a local runtime will resolve.
DOCUMENT_MODEL_SPEC = _ModelSpec(api_id="BAAI/bge-m3", dimension=1024)


#: The models this application knows how to offer and address, keyed by the
#: configured model name.
#:
#: Two runtimes are supported, and they are not interchangeable:
#:
#: ``identifiers``
#:     The name to put on the wire for each runtime. ``None`` means that runtime
#:     cannot serve the model at all. ``BAAI/bge-m3`` is the HuggingFace
#:     repository name; a local Ollama runtime knows it as the tag ``bge-m3``
#:     and will not resolve the namespaced form at all -- sending the wrong one
#:     yields a 404 that reads like "model not found" with no hint that the name
#:     is simply wrong for that endpoint.
#:
#: ``downloads``
#:     How to fetch the weights for each runtime, where the runtime has any way
#:     to fetch them at all.
#:
#: The asymmetry below is real and verified against the HuggingFace API, not an
#: assumption: the code model has an OFFICIAL GGUF, and the documentation model
#: does not. So neither runtime can serve both models, and pretending otherwise
#: would mean offering a download that produces a model whose behaviour nobody
#: has checked.
#:
#: ``sizes`` are deliberately absent. A wrong size is worse than no size: it is
#: a number the operator trusts when deciding whether to wait. Sizes are
#: resolved from the actual download instead (see services/model_manager.py).
EMBEDDING_MODEL_CATALOG: dict[str, dict[str, Any]] = {
    "jina-code-embeddings-1.5b": {
        "label": "Jina Code Embeddings 1.5B",
        "role": "code",
        "dimension": 768,
        "note": "Source code and natural-language -> code retrieval.",
        "identifiers": {
            # No Ollama tag exists for this model: on Ollama it must be used
            # through the hosted Jina API instead of being downloaded.
            "ollama": None,
            # llama-server is normally started with a single -m, so the id it
            # reports is the file it was pointed at.
            "llamacpp": "jina-code-embeddings-1.5b-Q8_0.gguf",
        },
        "downloads": {
            "llamacpp": {
                "repo": "jinaai/jina-code-embeddings-1.5b-GGUF",
                "filename": "jina-code-embeddings-1.5b-Q8_0.gguf",
                # Published by Jina themselves, and the GGUF metadata reports
                # architecture "qwen2" -- a plain decoder llama.cpp supports.
                "official": True,
                "note": "Official Jina GGUF (Q8_0).",
            },
        },
    },
    "BAAI/bge-m3": {
        "label": "BAAI bge-m3",
        "role": "document",
        "dimension": 1024,
        "note": "Multilingual documentation, Markdown and ADRs.",
        "identifiers": {
            "ollama": "bge-m3",
            # Usable, because the community GGUF below is a real file
            # llama-server can be pointed at. Its provenance is reported
            # separately as official=False, so the caveat travels with it
            # instead of being hidden by pretending the model is unavailable.
            "llamacpp": "bge-m3-Q8_0.gguf",
        },
        "downloads": {
            "ollama": {"tag": "bge-m3", "official": True},
            "llamacpp": {
                "repo": "gpustack/bge-m3-GGUF",
                "filename": "bge-m3-Q8_0.gguf",
                # A community conversion of an XLMRoberta encoder. Marked
                # unofficial so the UI can say so rather than presenting it as
                # interchangeable with the official weights.
                "official": False,
                "note": "Community GGUF conversion, not published by BAAI.",
            },
        },
    },
}


def _is_ollama_endpoint(base_url: str | None) -> bool:
    """Whether ``base_url`` points at an Ollama-compatible runtime.

    Ollama's OpenAI-compatible layer lives under /v1 on the same host as its
    native API. Recognising it is what lets one logical model be addressed by
    the name that runtime knows.
    """
    if not base_url:
        return False
    return "11434" in base_url or "ollama" in base_url.lower()


def _is_llamacpp_endpoint(base_url: str | None) -> bool:
    """Whether ``base_url`` points at a llama.cpp ``llama-server``.

    Deliberately checked only AFTER an Ollama match, and deliberately not a
    bare "is it on localhost" test: Ollama also listens on localhost, and that
    test would claim Ollama's port for llama.cpp and send a llama-server model
    name to a daemon that has never heard of it. What is left is an explicit
    port/name match; an unusual port is simply not auto-detected, and the
    operator can set the name explicitly instead.
    """
    if not base_url:
        return False
    lowered = base_url.lower()
    return "llama" in lowered or ":8080" in lowered


def api_model_name(model: str, base_url: str | None = None) -> str:
    """The identifier to send to ``base_url`` for the logical model ``model``.

    Resolves against the runtime the endpoint belongs to, so the same
    configured model reaches a hosted API, an Ollama daemon and a llama-server
    under the identifier each of them actually knows. Falls back to the
    configured name for a model outside the catalog: an unknown model is
    allowed (the operator may run something we have never heard of), it just
    gets no name translation.
    """
    entry = EMBEDDING_MODEL_CATALOG.get(model)
    if entry is None:
        return model
    identifiers = entry.get("identifiers") or {}
    # Ollama first: its port is distinctive, and llama.cpp's detection is the
    # looser of the two, so the specific test has to win.
    if _is_ollama_endpoint(base_url):
        tag = identifiers.get("ollama")
        if tag:
            return tag
    elif _is_llamacpp_endpoint(base_url):
        local = identifiers.get("llamacpp")
        if local:
            return local
    # A hosted endpoint uses the name its vendor documents, which is the
    # catalog's own name for these two models.
    return model


#: Configured model name -> spec, for dimension lookup. Filled lazily from
#: settings so the mapping follows configuration rather than hardcoding a
#: single pair of names.
MODEL_SPECS: dict[str, _ModelSpec] = {}


def _model_spec(model: str) -> _ModelSpec | None:
    if not MODEL_SPECS:
        MODEL_SPECS[settings.code_embedding_model] = CODE_MODEL_SPEC
        MODEL_SPECS[settings.document_embedding_model] = DOCUMENT_MODEL_SPEC
    return MODEL_SPECS.get(model)


def model_dimension(model: str) -> int:
    """The embedding dimension for ``model``, raising when unknowable.

    An unknown model is a configuration error the operator must fix (set
    CODE_EMBEDDING_MODEL / DOCUMENT_EMBEDDING_MODEL to a supported model), not
    something to guess: a wrong guess writes vectors no query will ever match.
    """
    spec = _model_spec(model)
    if spec is None:
        raise EmbeddingError(
            f"Unknown embedding model {model!r}. Configure CODE_EMBEDDING_MODEL "
            "or DOCUMENT_EMBEDDING_MODEL to a supported model."
        )
    return spec.dimension


class RemoteEmbeddingProvider:
    """An OpenAI-compatible /embeddings endpoint.

    Works with the Jina API (``https://api.jina.ai/v1``), a local inference
    server, or any endpoint that accepts ``{"model": ..., "input": [...]}``
    and returns ``{"data": [{"embedding": [...]}]}``. Credentials come only
    from the environment; nothing is hardcoded and nothing is stored.
    """

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        dimension: int | None = None,
        timeout: int = 60,
        batch_size: int | None = None,
    ) -> None:
        self._model = model
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.batch_size = max(1, batch_size or settings.embedding_batch_size)
        spec = _model_spec(model)
        self._dimension = dimension or (spec.dimension if spec else None)
        if not self.base_url:
            raise EmbeddingError(
                "No embedding endpoint configured. Set EMBEDDING_API_BASE_URL, "
                "or leave it unset to use the built-in local provider."
            )

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            raise EmbeddingError(
                f"The dimension of embedding model {self._model!r} is not known. "
                "Use a supported model or set its dimension explicitly."
            )
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # urllib is blocking; run the batches in a worker thread so the event
        # loop (and anything sharing it) keeps serving during the call.
        return await asyncio.to_thread(self._embed_all, texts)

    def _embed_all(self, texts: list[str]) -> list[list[float]]:
        # The wire name is resolved against the configured endpoint, so the
        # same configured model reaches a hosted API and a local Ollama
        # runtime under the identifier each of them actually knows.
        api_model = api_model_name(self._model, self.base_url)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            vectors.extend(self._embed_batch(api_model, batch))
        return vectors

    def _embed_batch(self, api_model: str, batch: list[str]) -> list[list[float]]:
        payload = {"model": api_model, "input": batch}
        request = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self.api_key:
            # Sent only to the configured endpoint, never stored anywhere.
            request.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise EmbeddingError(f"Embedding endpoint returned HTTP {exc.code}.") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise EmbeddingError(f"Could not reach embedding endpoint: {exc}") from exc

        data = body.get("data") or []
        if not isinstance(data, list) or len(data) != len(batch):
            raise EmbeddingError(
                f"Embedding endpoint returned {len(data)} vectors for {len(batch)} inputs."
            )
        vectors: list[list[float]] = []
        for item in data:
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(vector, list) or not vector:
                raise EmbeddingError("Embedding endpoint returned a malformed vector.")
            vectors.append([float(x) for x in vector])
        return vectors


class LocalEmbeddingProvider:
    """A deterministic, dependency-free fallback embedder.

    Produces fixed-dimension vectors from character-shingle hashes. It is
    *not* semantic: two synonyms do not land closer than two unrelated words.
    What it does guarantee is that identical content always yields the
    identical vector, that different content yields different vectors, and
    that cosine similarity behaves monotonically for shared vocabulary -- which
    is exactly what the indexing, storage and retrieval layers need to be
    correct and testable offline.

    Choosing it is explicit: it is used when no ``EMBEDDING_API_BASE_URL`` is
    configured, so a deployment is never silently downgraded to it.
    """

    def __init__(self, model: str, dimension: int | None = None) -> None:
        self._model = model
        spec = _model_spec(model)
        self._dimension = dimension or (spec.dimension if spec else None)
        if not self._dimension or self._dimension < 2:
            raise EmbeddingError(
                f"The local provider needs an explicit dimension for model {model!r}."
            )

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Pure in-process computation; nothing to await.
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        # 3-gram character shingles keep short identifiers and formatting
        # distinct while still overlapping on shared words, so similarity
        # tracks shared vocabulary.
        normalized = re.sub(r"\s+", " ", text).strip().lower()
        shingles = [normalized[i : i + 3] for i in range(max(1, len(normalized) - 2))]
        vector = [0.0] * self._dimension
        for shingle in shingles:
            digest = hashlib.sha256(f"{self._model}:{shingle}".encode("utf-8"))
            slot = int.from_bytes(digest.digest()[:8], "big") % self._dimension
            vector[slot] += 1.0
        return self._normalize(vector)

    @staticmethod
    def _normalize(vector: list[float]) -> list[float]:
        magnitude = math.sqrt(sum(x * x for x in vector))
        if magnitude == 0:
            return vector
        return [x / magnitude for x in vector]


def _build_provider(model: str) -> EmbeddingProvider:
    if settings.embedding_api_base_url:
        return RemoteEmbeddingProvider(
            model,
            base_url=settings.embedding_api_base_url,
            api_key=settings.embedding_api_key,
        )
    return LocalEmbeddingProvider(model)


#: Provider cache: one instance per model name, built on first use. Rebuilding
#: on every call would re-read settings and re-create sessions for nothing.
_PROVIDERS: dict[str, EmbeddingProvider] = {}


def code_provider() -> EmbeddingProvider:
    """The provider for source code (Jina Code Embeddings 1.5B by default)."""
    model = settings.code_embedding_model
    if model not in _PROVIDERS:
        _PROVIDERS[model] = _build_provider(model)
    return _PROVIDERS[model]


def document_provider() -> EmbeddingProvider:
    """The provider for documentation (BAAI/bge-m3 by default)."""
    model = settings.document_embedding_model
    if model not in _PROVIDERS:
        _PROVIDERS[model] = _build_provider(model)
    return _PROVIDERS[model]


def reset_providers() -> None:
    """Drop cached providers (used when settings change, e.g. in tests)."""
    _PROVIDERS.clear()
    MODEL_SPECS.clear()


def _empty_result(model: str) -> EmbeddingResult:
    return EmbeddingResult(vectors=[], model_name=model, dimension=model_dimension(model))


async def aembed_code(texts: list[str]) -> EmbeddingResult:
    """Embed code units with the configured code model (async protocol)."""
    if not texts:
        return _empty_result(settings.code_embedding_model)
    provider = code_provider()
    vectors = await provider.embed(texts)
    _validate(provider, vectors)
    return EmbeddingResult(vectors, provider.model_name, provider.dimension)


async def aembed_documents(texts: list[str]) -> EmbeddingResult:
    """Embed documentation chunks with the configured documentation model."""
    if not texts:
        return _empty_result(settings.document_embedding_model)
    provider = document_provider()
    vectors = await provider.embed(texts)
    _validate(provider, vectors)
    return EmbeddingResult(vectors, provider.model_name, provider.dimension)


def run_async(coro: Any) -> Any:
    """Run a coroutine to completion from synchronous code.

    The request path and the agent loop are synchronous; the embedding
    protocol is async (remote providers are I/O-bound). This bridge keeps one
    calling convention without forcing asyncio onto every caller.
    """
    return asyncio.run(coro)


def embed_code(texts: list[str]) -> EmbeddingResult:
    """Synchronous facade over :func:`aembed_code`."""
    return run_async(aembed_code(texts))


def embed_documents(texts: list[str]) -> EmbeddingResult:
    """Synchronous facade over :func:`aembed_documents`."""
    return run_async(aembed_documents(texts))


def _validate(provider: EmbeddingProvider, vectors: list[list[float]]) -> None:
    """Reject malformed or inconsistent provider output before it is stored.

    Every vector must have the provider's declared dimension; a shorter or
    longer vector would be written as an unusable row that can never match a
    query, and worse, would claim a model it does not correspond to.
    """
    expected = provider.dimension
    for index, vector in enumerate(vectors):
        if len(vector) != expected:
            raise EmbeddingDimensionError(
                f"Embedding {index} has dimension {len(vector)}, expected {expected} "
                f"for model {provider.model_name!r}."
            )
