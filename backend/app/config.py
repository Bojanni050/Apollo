"""Application settings.

All configuration is environment-driven so the app stays independent of any
particular deployment. Nothing here references Gaia runtime services.

Security posture
----------------
Three rules shape this module:

1. **Fail closed.** ``app_env`` defaults to ``production``. A deployment that
   forgot to configure authentication, CORS or workspace roots refuses to
   start rather than serving the filesystem to the world.
2. **Development is explicit.** Relaxed behaviour (no authentication, no
   workspace-root restriction) is only ever reachable from
   ``APP_ENV=development`` *and* the matching opt-in flag. It is never the
   default for production.
3. **Secrets are never defaulted.** Password hashes, session secrets and API
   tokens have no default value, so a missing secret is an error, not an
   empty string that trivially matches.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent

#: Origins the Vite dev server serves from. Used as the CORS default *only*
#: when ``APP_ENV=development``; production must configure its own origins.
#:
#: 5273 is the dev server's port (see frontend/vite.config.ts and
#: scripts/dev.ps1). It deliberately is not Vite's default of 5173, which is
#: often already taken by another project on the machine. Because the Vite
#: proxy keeps the browser same-origin, this list is a fallback rather than the
#: normal path.
DEV_CORS_ORIGINS = ["http://localhost:5273", "http://127.0.0.1:5273"]

#: Shortest session secret accepted in production. Long enough that an
#: offline brute force of the HMAC key is not realistic.
MIN_SESSION_SECRET_LENGTH = 32

#: Bounds on LLM_CONTEXT_TOKENS. The floor leaves room for a system prompt and
#: a real answer on a small local model; the ceiling catches a typo such as
#: 128_000_000, which would otherwise disable budgeting entirely by promising a
#: window the model does not have.
MIN_CONTEXT_TOKENS = 1_024
MAX_CONTEXT_TOKENS = 4_000_000


class SecurityConfigurationError(RuntimeError):
    """The configuration would expose the app if the server started."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Apollo"
    # Debug output must never be the default for a self-hosted deployment.
    debug: bool = False

    # Which set of rules applies. "production" is the default on purpose: a
    # deployment that never set APP_ENV gets the strict behaviour.
    app_env: Literal["development", "production"] = "production"

    # PostgreSQL is the production store. A sqlite file is supported purely so
    # the app can be started and tested without a running database server.
    #
    # Never hardcode a password here: this value comes from the environment and
    # must be a full connection URL supplied by the operator, e.g.
    #   postgresql+psycopg://gaia:<password>@db.internal:5432/gaia_docs
    database_url: str = "postgresql+psycopg://gaia:gaia@localhost:5432/gaia_docs"

    # Apply pending migrations during application startup. Off by default in
    # production: applying schema changes implicitly on boot is how two
    # instances race and how an unexpected migration reaches production. The
    # documented deployment step is `alembic upgrade head`. When off, startup
    # fails loudly if the schema is behind.
    db_migrate_on_startup: bool = False

    # Validate that DATABASE_URL is a PostgreSQL URL in production. SQLite is a
    # local-development convenience and is refused for a real deployment.
    db_require_postgres_in_production: bool = True

    # ---- Authentication -------------------------------------------------
    # Enforced server-side by app/security.py on every /api route except the
    # explicitly public ones (health, login, logout, status).
    auth_enabled: bool = True

    # The single local account. There is no registration: an operator creates
    # the account by setting these values in the environment.
    auth_username: str | None = None

    # Preferred: "pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>".
    # Generate one with: python -m app.security hash-password
    auth_password_hash: str | None = None

    # Convenience for local development only. Compared in constant time.
    # A password stored in the environment is readable by anyone who can read
    # the process environment, so prefer auth_password_hash.
    auth_password: str | None = None

    # Optional long-lived bearer token, for CLI use and smoke scripts.
    auth_api_token: str | None = None

    # HMAC key for the session cookie. Required in production.
    session_secret: str | None = None
    session_max_age_seconds: int = 12 * 60 * 60

    auth_cookie_name: str = "apollo_session"
    # None means "Secure whenever APP_ENV is production".
    auth_cookie_secure: bool | None = None

    # ---- CORS ----------------------------------------------------------
    # Origins allowed to call the API from a browser. Empty in production is a
    # startup error; empty in development falls back to the Vite dev server.
    cors_origins: list[str] = []
    # The session cookie must be sent, so credentialed requests are allowed --
    # which is exactly why a wildcard origin is not acceptable (see
    # validate_security).
    cors_allow_credentials: bool = True

    # ---- Filesystem access ---------------------------------------------
    # Directories that may be registered as workspace repositories. Any file
    # access outside these roots is rejected (see services/paths.py).
    allowed_workspace_roots: list[str] = []

    # Development-only escape hatch: treat an empty root list as "any existing
    # directory". Ignored -- and rejected as a misconfiguration -- in
    # production, where an empty list means no repository can be registered.
    allow_unrestricted_workspace_roots: bool = False

    # ---- Source repositories (architecture evidence) ---------------------
    # Root under which GitHub repository sources are cloned/pulled. Machine-
    # specific by design (never portable, never part of .sources.yaml), and
    # deliberately outside the workspace-root allow-list: these checkouts are
    # managed by the application, not registered by the operator.
    source_checkout_root: str = "./gaia_source_checkouts"

    # ---- Semantic indexing (embeddings + pgvector) ------------------------
    # Model names are configuration, never code: changing either one changes
    # which vectors are compatible with the index (see services/embeddings.py,
    # which detects the mismatch and requires a re-index).
    #
    # Default code model: Jina Code Embeddings 1.5B -- trained for
    # natural-language -> code retrieval. Default documentation model: BAAI
    # bge-m3, a strong multilingual general-purpose embedder.
    code_embedding_model: str = "jina-code-embeddings-1.5b"
    document_embedding_model: str = "BAAI/bge-m3"
    # How many texts are embedded per provider call. Larger batches amortize
    # request overhead; smaller batches keep memory bounded on big indexes.
    embedding_batch_size: int = Field(default=32, ge=1, le=512)
    # Default result limit for vector searches. Kept small on purpose: the
    # results are evidence for an AI to reason over, not a result page.
    vector_search_limit: int = Field(default=5, ge=1, le=50)
    # Optional base URL/key for a remote embedding API (OpenAI-compatible or
    # Jina-style). When unset, the deterministic local fallback provider is
    # used, so the application works with no paid external API.
    embedding_api_base_url: str | None = None
    embedding_api_key: str | None = None
    # Which local runtime manages embedding models: "ollama" (the daemon's own
    # pull API) or "llamacpp" (a models directory the app fills itself; there is
    # no pull API in llama.cpp). This decides where downloads go and which
    # identifier the model gets on the wire. It does NOT decide where embeddings
    # are served: that stays EMBEDDING_API_BASE_URL, because either runtime can
    # also be pointed at a hosted endpoint.
    embedding_runtime: str = Field(default="ollama", pattern="^(ollama|llamacpp)$")
    # Where llama.cpp weights are stored, and where EMBEDDING_API_BASE_URL
    # points when the operator is running llama-server locally. Resolved against
    # the backend directory when relative, like source_checkout_root.
    llamacpp_models_dir: str = "./models"
    llamacpp_base_url: str = "http://127.0.0.1:8080/v1"

    @property
    def effective_llamacpp_models_dir(self) -> str:
        """Absolute path to the llama.cpp models directory.

        Resolved against the backend directory so it does not depend on the
        process working directory, which differs between the dev server, the
        packaged desktop app and a service install.
        """
        from pathlib import PurePath

        value = self.llamacpp_models_dir or "./models"
        if PurePath(value).is_absolute():
            return value
        return str((BACKEND_DIR / value).resolve())

    @property
    def effective_source_checkout_root(self) -> str:
        """Resolve a relative checkout root against the backend directory, so
        it does not depend on whatever the current working directory is."""
        from pathlib import PurePath

        value = self.source_checkout_root or "./gaia_source_checkouts"
        if PurePath(value).is_absolute():
            return value
        return str((BACKEND_DIR / value).resolve())

    # ---- LLM provider (OpenAI-compatible) -------------------------------
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None

    # The TOTAL context window of the configured model, in tokens: the ceiling
    # for input *and* output combined, exactly as the model vendor states it.
    # It is enforced, not advisory -- see app/llm/context.py, which reserves
    # LLM_MAX_OUTPUT_TOKENS plus a safety margin and admits the rest of the
    # request (system prompt, tool definitions, history, tool results and the
    # current message) into what remains.
    #
    # It must describe the model you actually configured in LLM_MODEL. Too
    # large and every oversized request fails at the provider with a confusing
    # error; too small and the application needlessly discards history.
    llm_context_tokens: int = Field(default=128_000, ge=MIN_CONTEXT_TOKENS, le=MAX_CONTEXT_TOKENS)

    # Reserved from the context window for the model's reply. It is subtracted
    # from LLM_CONTEXT_TOKENS before anything else is budgeted, because a
    # request that leaves no room to answer is not a usable request.
    llm_max_output_tokens: int = Field(default=8_192, ge=256)

    llm_temperature: float = 0.2
    llm_timeout_seconds: int = 120

    # ---- Background LLM (cheap model for inventory and classification) ----
    # Falls back to the primary LLM settings when unset, so a single-model
    # setup keeps working unchanged. Set BACKGROUND_LLM_* to route bulk work
    # (structural digests, document classification in the inventory) to a
    # compact/cheap model while the chat keeps the strong one.
    background_llm_base_url: str | None = None
    background_llm_api_key: str | None = None
    background_llm_model: str | None = None
    background_llm_context_tokens: int | None = Field(default=None, ge=MIN_CONTEXT_TOKENS, le=MAX_CONTEXT_TOKENS)
    background_llm_max_output_tokens: int | None = Field(default=None, ge=256)

    @field_validator(
        "background_llm_base_url",
        "background_llm_api_key",
        "background_llm_model",
        "background_llm_context_tokens",
        "background_llm_max_output_tokens",
        "llm_base_url",
        "llm_api_key",
        "llm_model",
        mode="before",
    )
    @classmethod
    def _empty_llm_values_are_unset(cls, value):
        """Treat an empty string as "not configured".

        The settings screen persists the LLM keys to backend/.env, and an unset
        optional value is written as ``KEY=`` -- a blank line in the file. On
        the next start pydantic-settings feeds that literal empty string to
        the field validators, where the token counts are declared ``int`` and
        fail to parse, so a settings round-trip through the UI could leave the
        server unable to boot. Normalizing here keeps ``.env`` readable for
        humans (empty means unset, as everywhere else in the file) while the
        application sees a clean ``None``.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # ---- Derived behaviour ---------------------------------------------
    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def effective_cors_origins(self) -> list[str]:
        """The origins CORS should actually allow.

        Development falls back to the Vite dev server. Production returns the
        configured list unchanged -- including an empty one, which
        :meth:`validate_security` rejects at startup.
        """
        if self.cors_origins:
            return list(self.cors_origins)
        if self.is_development:
            return list(DEV_CORS_ORIGINS)
        return []

    @property
    def unrestricted_workspace_roots(self) -> bool:
        """Whether an empty ``allowed_workspace_roots`` means "anything".

        Only ever true in development, and only when explicitly requested.
        """
        return self.is_development and self.allow_unrestricted_workspace_roots

    @property
    def cookie_secure(self) -> bool:
        """Whether the session cookie carries the ``Secure`` attribute."""
        if self.auth_cookie_secure is not None:
            return self.auth_cookie_secure
        return self.is_production

    def has_password_credentials(self) -> bool:
        return bool(self.auth_password_hash or self.auth_password)

    @model_validator(mode="after")
    def _validate_llm_context(self) -> "Settings":
        """Reject a context window that leaves no room to answer.

        This is a cross-field rule, so it cannot be expressed on either field
        alone. Catching it at construction means the application never starts
        with a configuration in which every request is unsendable.
        """
        if self.llm_max_output_tokens >= self.llm_context_tokens:
            raise ValueError(
                f"LLM_MAX_OUTPUT_TOKENS ({self.llm_max_output_tokens:,}) must be "
                f"smaller than LLM_CONTEXT_TOKENS ({self.llm_context_tokens:,}): "
                "the output reserve is taken out of the context window, so a "
                "reserve at least as large as the window leaves no room for input."
            )
        return self

    def validate_security(self) -> None:
        """Refuse to serve with a configuration that would expose the app.

        Called during application startup. Development is intentionally
        forgiving; production must be fully configured. Raising here means a
        misconfigured deployment never accepts a request, which is far safer
        than serving an unauthenticated filesystem API.
        """
        if self.is_development:
            return

        problems: list[str] = []

        # -- authentication ------------------------------------------------
        if not self.auth_enabled:
            problems.append(
                "AUTH_ENABLED is false. Authentication cannot be disabled in "
                "production; set APP_ENV=development for local work."
            )
        if not self.auth_username:
            problems.append("AUTH_USERNAME is not set.")
        if not self.has_password_credentials():
            problems.append(
                "No password credentials. Set AUTH_PASSWORD_HASH (generate "
                "with `python -m app.security hash-password`)."
            )
        if not self.session_secret:
            problems.append(
                "SESSION_SECRET is not set. Generate one with "
                "`python -m app.security generate-secret`."
            )
        elif len(self.session_secret) < MIN_SESSION_SECRET_LENGTH:
            problems.append(
                f"SESSION_SECRET must be at least {MIN_SESSION_SECRET_LENGTH} "
                "characters."
            )

        # -- CORS ----------------------------------------------------------
        if not self.cors_origins:
            problems.append(
                "CORS_ORIGINS is empty. Production must name the exact origins "
                "allowed to call the API (the Vite dev server default applies "
                "only when APP_ENV=development)."
            )
        if "*" in self.cors_origins:
            problems.append(
                "CORS_ORIGINS must not contain '*'. Credentialed requests "
                "require explicit origins."
            )
        if self.cors_allow_credentials and "*" in self.cors_origins:
            problems.append(
                "CORS_ALLOW_CREDENTIALS cannot be combined with a wildcard "
                "origin."
            )

        # -- filesystem ----------------------------------------------------
        if not self.allowed_workspace_roots:
            problems.append(
                "ALLOWED_WORKSPACE_ROOTS is empty. At least one root is "
                "required, otherwise any directory on the host could be "
                "registered and read."
            )
        if self.allow_unrestricted_workspace_roots:
            problems.append(
                "ALLOW_UNRESTRICTED_WORKSPACE_ROOTS is only valid when "
                "APP_ENV=development."
            )

        # -- database ------------------------------------------------------
        dialect = self.database_url.split("://", 1)[0].split("+", 1)[0].lower()
        if self.db_require_postgres_in_production and dialect not in {
            "postgres",
            "postgresql",
        }:
            problems.append(
                f"DATABASE_URL is a {dialect!r} URL. Production requires "
                "PostgreSQL; SQLite is supported for local development only."
            )

        if problems:
            bullet = "\n  - "
            raise SecurityConfigurationError(
                "Refusing to start with an unsafe production configuration:"
                + bullet
                + bullet.join(problems)
                + "\n\nSet APP_ENV=development to run unauthenticated locally."
            )


settings = Settings()
