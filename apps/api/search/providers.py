"""Embedding and rerank providers behind narrow, fail-closed interfaces.

Security contract (``docs/SECURITY_AND_CONFIGURATION.md``) requirements applied here:

* **Disabled by default.** No network call happens unless the operator sets the
  enable flag *and* supplies a key. A disabled provider never reads its secret.
* **No secret in logs or errors.** Provider errors carry a stable reason code and
  an HTTP status, never the key or the request body.
* **Explicit failure.** The caller decides how to degrade; this layer never
  silently returns a fake vector that would look like a real result.

Two implementations exist for each capability: a deterministic local one used
for offline development and as a *pipeline* baseline, and a remote one speaking
the OpenAI-compatible API. The remote implementation is the one that produces
meaningful semantic quality; the local one does not and must never be cited as
a quality result.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Protocol, Sequence

import httpx

__all__ = [
    "EmbeddingProvider",
    "ProviderError",
    "ProviderUnavailable",
    "HashingEmbeddingProvider",
    "RemoteEmbeddingProvider",
    "RemoteRerankProvider",
    "RerankProvider",
    "RerankResult",
]


class ProviderError(RuntimeError):
    """A provider failed. Carries a stable reason, never payload or secret."""

    def __init__(self, reason: str, *, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


class ProviderUnavailable(ProviderError):
    """The provider is disabled or not configured."""


@dataclass(frozen=True, slots=True)
class RerankResult:
    index: int
    score: float


class EmbeddingProvider(Protocol):
    @property
    def name(self) -> str:
        """Stable model/configuration identifier recorded with every index."""

    @property
    def available(self) -> bool: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class RerankProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def available(self) -> bool: ...

    def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> list[RerankResult]: ...


class HashingEmbeddingProvider:
    """Deterministic offline embedding.

    This is a **pipeline baseline**, not a semantic model: it maps text into a
    fixed vector space via hashed token counts, so it can only reflect lexical
    overlap. Any Hit@K produced with it measures the retrieval pipeline, never
    semantic quality — a distinction the PRD makes explicit (§3.3, §7.2).
    """

    def __init__(self, dimensions: int = 256) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self._dimensions = dimensions

    @property
    def name(self) -> str:
        return f"local-hash-{self._dimensions}"

    @property
    def available(self) -> bool:
        return True

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        from .tokenizer import tokenize

        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self._dimensions
            for token in tokenize(text):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                bucket = int.from_bytes(digest[:4], "big") % self._dimensions
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vector[bucket] += sign
            norm = math.sqrt(sum(value * value for value in vector))
            vectors.append([value / norm for value in vector] if norm else vector)
        return vectors


class RemoteEmbeddingProvider:
    """OpenAI-compatible embeddings endpoint (default: SiliconFlow).

    The model name and dimensions are configuration, never hard-coded at call
    sites, so switching providers is a config change.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        dimensions: int | None = None,
        timeout_seconds: float = 30.0,
        batch_size: int = 16,
        enabled: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        self._timeout = timeout_seconds
        self._batch_size = max(1, batch_size)
        self._enabled = enabled
        self._transport = transport

    @property
    def name(self) -> str:
        suffix = f"-d{self._dimensions}" if self._dimensions else ""
        return f"{self._model}{suffix}"

    @property
    def available(self) -> bool:
        # A disabled or keyless provider must report unavailable *without*
        # touching the secret, so callers can degrade before any I/O.
        return bool(self._enabled and self._api_key and self._model)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.available:
            raise ProviderUnavailable("embedding provider is disabled or unconfigured")
        cleaned = [text if text.strip() else " " for text in texts]
        vectors: list[list[float]] = []
        for start in range(0, len(cleaned), self._batch_size):
            batch = cleaned[start : start + self._batch_size]
            vectors.extend(self._embed_batch(batch))
        return vectors

    def _embed_batch(self, batch: Sequence[str]) -> list[list[float]]:
        payload: dict[str, object] = {"model": self._model, "input": list(batch)}
        if self._dimensions:
            payload["dimensions"] = self._dimensions
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/embeddings"
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as error:
            # Never forward the exception text: it can include the request.
            raise ProviderError(f"embedding transport error: {type(error).__name__}") from None
        if response.status_code != 200:
            raise ProviderError("embedding request failed", status=response.status_code)
        try:
            body = response.json()
            data = body["data"]
            ordered = sorted(data, key=lambda item: item.get("index", 0))
            return [[float(value) for value in item["embedding"]] for item in ordered]
        except (ValueError, KeyError, TypeError):
            raise ProviderError("embedding response was not in the expected shape") from None


class RemoteRerankProvider:
    """Cross-encoder rerank endpoint (default: BAAI/bge-reranker-v2-m3)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: float = 30.0,
        enabled: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._enabled = enabled
        self._transport = transport

    @property
    def name(self) -> str:
        return self._model

    @property
    def available(self) -> bool:
        return bool(self._enabled and self._api_key and self._model)

    def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> list[RerankResult]:
        if not self.available:
            raise ProviderUnavailable("rerank provider is disabled or unconfigured")
        if not documents:
            return []
        payload: dict[str, object] = {
            "model": self._model,
            "query": query,
            "documents": list(documents),
            "return_documents": False,
        }
        if top_n is not None:
            payload["top_n"] = top_n
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/rerank"
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as error:
            raise ProviderError(f"rerank transport error: {type(error).__name__}") from None
        if response.status_code != 200:
            raise ProviderError("rerank request failed", status=response.status_code)
        try:
            body = response.json()
            results = body["results"]
            return [
                RerankResult(index=int(item["index"]), score=float(item["relevance_score"]))
                for item in results
            ]
        except (ValueError, KeyError, TypeError):
            raise ProviderError("rerank response was not in the expected shape") from None
