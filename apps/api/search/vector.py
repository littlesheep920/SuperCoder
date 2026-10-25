"""In-process vector index backed by numpy.

PRD §3.3 sets an infrastructure threshold: Qdrant is only justified past ~2000
published versions. Below that, a dense matrix in memory is simpler, faster, and
has no operational footprint. This module is that implementation.

It is deliberately *not* a database. The matrix is rebuilt from index documents,
which are themselves derived from Git content plus relational facts. Losing it
costs a rebuild, never data.
"""

from __future__ import annotations

import hashlib
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from . import IndexDocument
from .providers import EmbeddingProvider, ProviderError, ProviderUnavailable

__all__ = ["DocumentVectorCache", "ScoredVector", "VectorIndex"]


@dataclass(frozen=True, slots=True)
class ScoredVector:
    document: IndexDocument
    similarity: float


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return matrix / norms


def document_text(document: IndexDocument) -> str:
    """Compose the text embedded for one document.

    The name and description lead because embedding models weight the beginning
    of the input more heavily; the body follows so chunk-level detail is still
    represented.
    """

    parts = [
        f"{document.name} ({document.kind})",
        document.description,
        " ".join(document.aliases),
        " ".join(document.tags),
        " ".join(document.categories),
        " ".join(document.runtimes),
        " ".join(chunk.text for chunk in document.chunks),
    ]
    return "\n".join(part for part in parts if part).strip()


class DocumentVectorCache:
    """Reuse document embeddings across requests, keyed by content and model.

    Without this, every search re-embedded the whole corpus, because the vector
    index lives on a per-request :class:`SearchService`. Profiling showed that
    as a batch provider call on every query.

    The key is the model name plus a digest of the exact embedded text, so:
    * editing a document changes its text and therefore misses;
    * switching models cannot reuse a vector from a different space;
    * a document that reverts to an earlier body hits again, which is correct
      because the bytes are identical.
    """

    def __init__(self, capacity: int = 5000) -> None:
        self._capacity = capacity
        self._entries: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key_for(model: str, text: str) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return f"{model}\x00{digest}"

    def get(self, key: str) -> tuple[float, ...] | None:
        with self._lock:
            if key not in self._entries:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return self._entries[key]

    def put(self, key: str, vector: Sequence[float]) -> None:
        if self._capacity == 0:
            return
        with self._lock:
            self._entries[key] = tuple(float(value) for value in vector)
            self._entries.move_to_end(key)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)

    def stats(self) -> dict[str, float | int]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "capacity": self._capacity,
                "size": len(self._entries),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 4) if total else 0.0,
            }

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.hits = 0
            self.misses = 0


class VectorIndex:
    """Cosine-similarity index over precomputed document vectors.

    Vectors are normalised once at build time, so a query is a single matrix
    multiply rather than a per-document cosine computation.
    """

    def __init__(
        self,
        documents: Sequence[IndexDocument],
        vectors: np.ndarray | None = None,
        *,
        model: str = "",
    ) -> None:
        self._documents = list(documents)
        self._model = model
        if vectors is None or len(self._documents) == 0:
            self._matrix = np.zeros((0, 0), dtype=np.float32)
        else:
            matrix = np.asarray(vectors, dtype=np.float32)
            if matrix.shape[0] != len(self._documents):
                raise ValueError(
                    "vector count does not match document count: "
                    f"{matrix.shape[0]} != {len(self._documents)}"
                )
            # A zero row would divide by one and silently become an all-zero
            # vector, which matches everything equally badly; keep it explicit.
            self._matrix = _normalize_rows(matrix)

    @property
    def model(self) -> str:
        return self._model

    @property
    def document_count(self) -> int:
        return len(self._documents)

    @property
    def dimensions(self) -> int:
        return int(self._matrix.shape[1]) if self._matrix.size else 0

    @classmethod
    def build(
        cls,
        documents: Sequence[IndexDocument],
        provider: EmbeddingProvider,
        *,
        cache: DocumentVectorCache | None = None,
    ) -> "VectorIndex":
        """Embed every document's searchable text.

        Only called when the provider is available; callers must check
        ``provider.available`` first so a disabled provider causes degradation
        rather than an exception at build time.

        With a ``cache``, only documents whose text is not already known are
        sent to the provider. The remaining texts go in one batch, so a cache
        that hits everything makes no provider call at all.
        """

        if not documents:
            return cls([], model=getattr(provider, "name", ""))

        model = getattr(provider, "name", "")
        texts = [document_text(document) for document in documents]

        if cache is None:
            vectors = np.asarray(provider.embed(texts), dtype=np.float32)
            return cls(documents, vectors, model=model)

        keys = [DocumentVectorCache.key_for(model, text) for text in texts]
        resolved: dict[str, tuple[float, ...]] = {}
        missing: list[str] = []
        for key in keys:
            if key in resolved or key in missing:
                continue
            cached = cache.get(key)
            if cached is None:
                missing.append(key)
            else:
                resolved[key] = cached

        if missing:
            # Map each missing key back to its text; duplicates share one slot.
            text_by_key = {key: text for key, text in zip(keys, texts)}
            fresh = provider.embed([text_by_key[key] for key in missing])
            for key, vector in zip(missing, fresh):
                cache.put(key, vector)
                resolved[key] = tuple(float(value) for value in vector)

        matrix = np.asarray([resolved[key] for key in keys], dtype=np.float32)
        return cls(documents, matrix, model=model)

    def search(
        self, query_vector: Sequence[float], *, limit: int | None = None
    ) -> list[ScoredVector]:
        """Return documents ordered by cosine similarity, descending."""

        if not self._documents or self._matrix.size == 0:
            return []
        query = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        if query.shape[0] != self._matrix.shape[1]:
            raise ValueError(
                f"query vector has {query.shape[0]} dimensions but the index has "
                f"{self._matrix.shape[1]}"
            )
        norm = float(np.linalg.norm(query))
        if norm == 0.0:
            return []
        similarities = self._matrix @ (query / norm)
        # Deterministic order: similarity desc, then resource id asc (B7).
        order = sorted(
            range(len(self._documents)),
            key=lambda index: (-float(similarities[index]), self._documents[index].resource_public_id),
        )
        if limit is not None:
            order = order[:limit]
        return [
            ScoredVector(document=self._documents[index], similarity=round(float(similarities[index]), 6))
            for index in order
            if math.isfinite(float(similarities[index]))
        ]

    def query(self, text: str, provider: EmbeddingProvider, *, limit: int | None = None) -> list[ScoredVector]:
        """Convenience wrapper that embeds *text* then searches."""

        if not provider.available:
            raise ProviderUnavailable("embedding provider is unavailable")
        if not text.strip():
            return []
        try:
            vector = provider.embed([text])[0]
        except ProviderError:
            raise
        return self.search(vector, limit=limit)
