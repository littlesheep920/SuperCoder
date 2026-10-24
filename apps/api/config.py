"""Typed, fail-closed application settings.

Settings are read from environment variables once at startup.  Disabled
components do not read their secrets; secret values are wrapped so they never
appear in ``repr``, logs, errors, or serialized state.  Unknown or malformed
values are rejected rather than coerced.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Final

__all__ = ["Secret", "Settings", "get_settings"]


class Secret:
    """Opaque runtime secret; equality is the only non-mutating operation."""

    __slots__ = ("_value",)

    def __init__(self, value: str | None) -> None:
        self._value = value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return "Secret(<redacted>)"

    def expose(self) -> str:
        if self._value is None:
            raise RuntimeError("secret is not configured")
        return self._value


_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"{name} must be a boolean, got {raw!r}")


def _int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from error
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _choice(name: str, default: str, allowed: frozenset[str]) -> str:
    value = _str(name, default)
    if value not in allowed:
        raise ValueError(f"{name} must be one of {sorted(allowed)}, got {value!r}")
    return value


def _secret(name: str) -> Secret:
    return Secret(os.environ.get(name))


@dataclass(frozen=True, slots=True)
class GitSettings:
    enabled: bool
    repository_root: str
    default_branch: str
    remote_name: str
    remote_url: str
    sync_enabled: bool
    read_only: bool
    token: Secret = field(default=Secret(None), repr=False)

    def token_if_enabled(self) -> Secret:
        if not self.sync_enabled:
            raise RuntimeError("git sync is disabled; its secret must not be read")
        return self.token


@dataclass(frozen=True, slots=True)
class MysqlSettings:
    enabled: bool
    host: str
    port: int
    database: str
    user: str
    password: Secret = field(repr=False)
    ssl_mode: str
    connect_timeout_seconds: int
    pool_size: int
    max_overflow: int

    def password_if_enabled(self) -> Secret:
        if not self.enabled:
            raise RuntimeError("mysql is disabled; its secret must not be read")
        return self.password

    def url(self) -> str:
        if not self.password:
            raise RuntimeError(
                "MySQL is enabled but SUPERCODER_MYSQL_PASSWORD is not set. "
                "Set the password in the environment (or in the compose file) "
                "before starting the API, worker, or seed step."
            )
        return (
            f"mysql+pymysql://{self.user}:{self.password.expose()}@"
            f"{self.host}:{self.port}/{self.database}?charset=utf8mb4"
        )


@dataclass(frozen=True, slots=True)
class ExternalModelSettings:
    enabled: bool
    provider: str
    base_url: str
    model: str
    api_key: Secret = field(repr=False)
    allow_project_data: bool
    request_timeout_seconds: int
    max_output_tokens: int
    temperature: float

    def api_key_if_enabled(self) -> Secret:
        if not self.enabled:
            raise RuntimeError("external model is disabled; its secret must not be read")
        return self.api_key


@dataclass(frozen=True, slots=True)
class SearchSettings:
    """Search pipeline configuration.

    The embedding and rerank providers are **disabled by default** and their API
    key is only read when explicitly enabled, matching the security contract:
    a component that is off must never cause its secret to be touched.

    Model names are configuration, never hard-coded at call sites, so a provider
    or model change is a config change rather than a code change.
    """

    embedding_enabled: bool
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    rerank_enabled: bool
    rerank_model: str
    remote_enabled: bool
    base_url: str
    api_key: Secret = field(repr=False)
    timeout_seconds: float = 30.0
    batch_size: int = 16

    def api_key_if_remote_enabled(self) -> Secret:
        if not self.remote_enabled:
            raise RuntimeError(
                "remote providers are disabled; the API key must not be read"
            )
        return self.api_key

    @property
    def remote_available(self) -> bool:
        """True when a remote call is both permitted and configured."""

        return bool(self.remote_enabled and self.api_key)


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    config_schema_version: str
    api_prefix: str
    cors_origins: tuple[str, ...]
    request_id_header: str
    cursor_key: str
    git: GitSettings
    mysql: MysqlSettings
    redis_enabled: bool
    qdrant_enabled: bool
    embedding_enabled: bool
    rerank_enabled: bool
    docker_enabled: bool
    external_llm: ExternalModelSettings
    dev_auth_tokens_file: str
    worker_poll_seconds: float
    worker_lease_seconds: int
    job_max_attempts: int
    search: SearchSettings


def _load() -> Settings:
    environment = _choice(
        "SUPERCODER_ENVIRONMENT", "local",
        frozenset({"local", "dev", "test", "staging", "production"}),
    )
    api_prefix = _str("SUPERCODER_API_PREFIX", "/v1")
    cors_raw = _str("SUPERCODER_CORS_ORIGINS", "http://localhost:5173")
    cors_origins = tuple(
        origin.strip() for origin in cors_raw.split(",") if origin.strip()
    )
    return Settings(
        environment=environment,
        config_schema_version=_str("SUPERCODER_CONFIG_SCHEMA_VERSION", "1"),
        api_prefix=api_prefix if api_prefix.startswith("/") else f"/{api_prefix}",
        cors_origins=cors_origins,
        request_id_header=_str("SUPERCODER_REQUEST_ID_HEADER", "X-Request-Id"),
        cursor_key=_str("SUPERCODER_CURSOR_KEY", "dev-cursor-key-change-me"),
        git=GitSettings(
            enabled=_bool("SUPERCODER_GIT_ENABLED", True),
            repository_root=_str("SUPERCODER_GIT_REPOSITORY_ROOT", "."),
            default_branch=_str("SUPERCODER_GIT_DEFAULT_BRANCH", "main"),
            remote_name=_str("SUPERCODER_GIT_REMOTE_NAME", "origin"),
            remote_url=_str("SUPERCODER_GIT_REMOTE_URL", ""),
            sync_enabled=_bool("SUPERCODER_GIT_SYNC_ENABLED", False),
            read_only=_bool("SUPERCODER_GIT_READ_ONLY", True),
            token=_secret("SUPERCODER_GIT_TOKEN"),
        ),
        mysql=MysqlSettings(
            enabled=_bool("SUPERCODER_MYSQL_ENABLED", False),
            host=_str("SUPERCODER_MYSQL_HOST", "127.0.0.1"),
            port=_int("SUPERCODER_MYSQL_PORT", 3306, minimum=1),
            database=_str("SUPERCODER_MYSQL_DATABASE", "supercoder"),
            user=_str("SUPERCODER_MYSQL_USER", "supercoder_local"),
            password=_secret("SUPERCODER_MYSQL_PASSWORD"),
            ssl_mode=_choice(
                "SUPERCODER_MYSQL_SSL_MODE", "disabled",
                frozenset({"disabled", "preferred", "required", "verify_ca", "verify_identity"}),
            ),
            connect_timeout_seconds=_int(
                "SUPERCODER_MYSQL_CONNECT_TIMEOUT_SECONDS", 5, minimum=1
            ),
            pool_size=_int("SUPERCODER_MYSQL_POOL_SIZE", 5, minimum=1),
            max_overflow=_int("SUPERCODER_MYSQL_MAX_OVERFLOW", 5, minimum=0),
        ),
        redis_enabled=_bool("SUPERCODER_REDIS_ENABLED", False),
        qdrant_enabled=_bool("SUPERCODER_QDRANT_ENABLED", False),
        embedding_enabled=_bool("SUPERCODER_EMBEDDING_ENABLED", False),
        rerank_enabled=_bool("SUPERCODER_RERANK_ENABLED", False),
        docker_enabled=_bool("SUPERCODER_DOCKER_ENABLED", False),
        external_llm=ExternalModelSettings(
            enabled=_bool("SUPERCODER_EXTERNAL_LLM_ENABLED", False),
            provider=_str("SUPERCODER_EXTERNAL_LLM_PROVIDER", "disabled"),
            base_url=_str("SUPERCODER_EXTERNAL_LLM_BASE_URL", ""),
            model=_str("SUPERCODER_EXTERNAL_LLM_MODEL", ""),
            api_key=_secret("SUPERCODER_LLM_API_KEY"),
            allow_project_data=_bool("SUPERCODER_EXTERNAL_LLM_ALLOW_PROJECT_DATA", False),
            request_timeout_seconds=_int(
                "SUPERCODER_EXTERNAL_LLM_REQUEST_TIMEOUT_SECONDS", 60, minimum=1
            ),
            max_output_tokens=_int(
                "SUPERCODER_EXTERNAL_LLM_MAX_OUTPUT_TOKENS", 2048, minimum=1
            ),
            temperature=float(_str("SUPERCODER_EXTERNAL_LLM_TEMPERATURE", "0")),
        ),
        dev_auth_tokens_file=_str(
            "SUPERCODER_DEV_AUTH_TOKENS_FILE", "scripts/dev_tokens.json"
        ),
        worker_poll_seconds=float(_str("SUPERCODER_WORKER_POLL_SECONDS", "1.0")),
        worker_lease_seconds=_int("SUPERCODER_WORKER_LEASE_SECONDS", 60, minimum=1),
        job_max_attempts=_int("SUPERCODER_JOB_MAX_ATTEMPTS", 3, minimum=1),
        search=SearchSettings(
            embedding_enabled=_bool("SUPERCODER_EMBEDDING_ENABLED", False),
            embedding_provider=_str("SUPERCODER_EMBEDDING_PROVIDER", "disabled"),
            # Defaults are the reviewed choice for this corpus: a small, fast,
            # Chinese-capable embedding model at a reduced dimension. Changing
            # them is a configuration change, never a code edit.
            embedding_model=_str(
                "SUPERCODER_SEARCH_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"
            ),
            embedding_dimensions=_int("SUPERCODER_SEARCH_EMBEDDING_DIMENSIONS", 1024, minimum=1),
            rerank_enabled=_bool("SUPERCODER_RERANK_ENABLED", False),
            rerank_model=_str("SUPERCODER_SEARCH_RERANK_MODEL", "BAAI/bge-reranker-v2-m3"),
            remote_enabled=_bool("SUPERCODER_SEARCH_REMOTE_ENABLED", False),
            base_url=_str("SUPERCODER_SEARCH_BASE_URL", "https://api.siliconflow.cn/v1"),
            api_key=_secret("SUPERCODER_SEARCH_API_KEY"),
            timeout_seconds=float(_str("SUPERCODER_SEARCH_TIMEOUT_SECONDS", "30")),
            batch_size=_int("SUPERCODER_SEARCH_BATCH_SIZE", 16, minimum=1),
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached process-wide settings, validating once."""

    return _load()
