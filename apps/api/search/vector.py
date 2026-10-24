"""In-process vector index backed by numpy.

PRD §3.3 sets an infrastructure threshold: Qdrant is only justified past ~2000
published versions. Below that, a dense matrix in memory is simpler, faster, and
has no operational footprint. This module is that implementation.

It is deliberately *not* a database. The matrix is rebuilt from index documents,
which are themselves derived from Git content plus relational facts. Losing it
costs a rebuild, never data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from . import IndexDocument
from .providers import EmbeddingProvider, ProviderError, ProviderUnavailable

__all__ = ["ScoredVector", "VectorIndex"]


@dataclass(frozen=True, slots=True)
class ScoredVector:
    document: IndexDocument
    similarity: float


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return matrix / norms


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
        cls, documents: Sequence[IndexDocument], provider: EmbeddingProvider
    ) -> "VectorIndex":
        """Embed every document's searchable text.

        Only called when the provider is available; callers must check
        ``provider.available`` first so a disabled provider causes degradation
        rather than an exception at build time.
        """

        if not documents:
            return cls([], model=getattr(provider, "name", ""))
        texts = [_document_text(document) for document in documents]
        vectors = np.asarray(provider.embed(texts), dtype=np.float32)
        return cls(documents, vectors, model=getattr(provider, "name", ""))

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


def _document_text(document: IndexDocument) -> str:
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
