"""Wire search settings into concrete providers.

Kept separate from ``config`` so the security rule is visible in one place:

* A provider is constructed only from settings.
* The remote provider is constructed with ``enabled=False`` whenever remote
  access is off, so it reports ``available == False`` and never reads the key.
* No default here ever makes a network call.
"""

from __future__ import annotations

from ..config import SearchSettings
from .providers import (
    EmbeddingProvider,
    HashingEmbeddingProvider,
    RemoteEmbeddingProvider,
    RemoteRerankProvider,
    RerankProvider,
)

__all__ = ["build_embedding_provider", "build_rerank_provider"]


def build_embedding_provider(settings: SearchSettings) -> EmbeddingProvider:
    """Return the configured embedding provider.

    When remote access is disabled the offline hashing provider is used. That is
    a **pipeline** baseline and cannot measure semantic quality; callers that
    report quality metrics must check ``provider.name`` and label results
    accordingly.
    """

    if settings.remote_enabled and settings.remote_available and settings.embedding_enabled:
        return RemoteEmbeddingProvider(
            base_url=settings.base_url,
            # ``expose()`` is required: ``str(Secret)`` yields its repr
            # ("Secret(<redacted>)"), which would be sent as the bearer token and
            # rejected with 401. Redacting on str is correct for logs; this is
            # the one place that legitimately needs the value.
            api_key=settings.api_key_if_remote_enabled().expose(),
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            timeout_seconds=settings.timeout_seconds,
            batch_size=settings.batch_size,
            enabled=True,
        )
    return HashingEmbeddingProvider()


def build_rerank_provider(settings: SearchSettings) -> RerankProvider:
    """Return the configured rerank provider, or a permanently unavailable one."""

    if settings.remote_enabled and settings.remote_available and settings.rerank_enabled:
        return RemoteRerankProvider(
            base_url=settings.base_url,
            api_key=settings.api_key_if_remote_enabled().expose(),
            model=settings.rerank_model,
            timeout_seconds=settings.timeout_seconds,
            enabled=True,
        )
    # Disabled by construction: it reports unavailable and makes no request.
    return RemoteRerankProvider(
        base_url=settings.base_url,
        api_key=None,
        model=settings.rerank_model,
        enabled=False,
    )
