"""Caching wrappers for the embedding and rerank providers.

Why this exists
---------------
Profiling a hybrid search on the Stage 2 corpus split the latency like this:

    query embedding   ~666 ms   (remote provider call)
    rerank            ~703 ms   (remote provider call)
    local compute      ~19 ms   (BM25, matrix multiply, fusion)

Almost all of the time is two network round trips, and the same query text is
embedded identically every time. Caching the query vector and the rerank result
removes a round trip without changing what the pipeline computes.

Correctness rules
-----------------
* The cache key includes the **provider name**, so switching models or
  dimensions can never serve a vector from a different space.
* The embedding key is the exact text embedded; a document edit changes the
  text and therefore misses.
* Only successful results are cached. A failure is not memoised: the next
  request retries, which is what an operator recovering from an outage expects.
* The cache is bounded (LRU). It is a performance aid, never a source of truth;
  clearing it costs latency, never correctness.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Sequence

from .providers import EmbeddingProvider, ProviderError, RerankProvider, RerankResult

__all__ = ["CachingEmbeddingProvider", "CachingRerankProvider", "LRUCache"]


class LRUCache:
    """A small thread-safe LRU map with hit/miss accounting.

    ``functools.lru_cache`` cannot be used because the wrapped values are lists
    and the wrapped callables are methods on instances created per request; an
    explicit map keeps the ownership obvious and exposes counters so the hit
    rate can be reported as evidence rather than assumed.
    """

    def __init__(self, capacity: int) -> None:
        if capacity < 0:
            raise ValueError("capacity must not be negative")
        self._capacity = capacity
        self._entries: OrderedDict[str, object] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, key: str) -> object | None:
        with self._lock:
            if key not in self._entries:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return self._entries[key]

    def put(self, key: str, value: object) -> None:
        if self._capacity == 0:
            return
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.hits = 0
            self.misses = 0

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


def _digest(*parts: str) -> str:
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part.encode("utf-8"))
        # A separator prevents ('ab','c') and ('a','bc') colliding.
        hasher.update(b"\x00")
    return hasher.hexdigest()


class CachingEmbeddingProvider:
    """Cache query embeddings; document embeddings pass through uncached.

    Only single-text calls are cached. A batch call comes from index building,
    where the texts are distinct per revision and caching them would grow the
    map without repeating any key.
    """

    def __init__(self, inner: EmbeddingProvider, cache: LRUCache) -> None:
        self._inner = inner
        self._cache = cache

    @property
    def name(self) -> str:
        # The wrapper must not change the recorded model identity, or the
        # index revision would differ purely because caching is enabled.
        return self._inner.name

    @property
    def available(self) -> bool:
        return self._inner.available

    @property
    def cache_stats(self) -> dict[str, float | int]:
        return self._cache.stats()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if len(texts) != 1:
            return self._inner.embed(texts)

        text = texts[0]
        key = _digest(self._inner.name, text)
        cached = self._cache.get(key)
        if cached is not None:
            # Copy so a caller mutating its vector cannot corrupt the cache.
            return [list(cached)]

        vectors = self._inner.embed([text])
        if vectors:
            self._cache.put(key, list(vectors[0]))
        return vectors


class CachingRerankProvider:
    """Cache rerank results keyed by the query and the exact candidate set.

    The key covers the ordered document texts, so it only hits when the same
    query is asked against the same head. That is the common case for repeated
    or replayed queries, including evaluation runs.
    """

    def __init__(self, inner: RerankProvider, cache: LRUCache) -> None:
        self._inner = inner
        self._cache = cache

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def available(self) -> bool:
        return self._inner.available

    @property
    def cache_stats(self) -> dict[str, float | int]:
        return self._cache.stats()

    def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> list[RerankResult]:
        key = _digest(self._inner.name, query, str(top_n), *documents)
        cached = self._cache.get(key)
        if cached is not None:
            return list(cached)

        results = self._inner.rerank(query, documents, top_n=top_n)
        if results:
            self._cache.put(key, list(results))
        return results


def wrap_with_cache(
    embedding: EmbeddingProvider | None,
    rerank: RerankProvider | None,
    *,
    embedding_capacity: int,
    rerank_capacity: int,
) -> tuple[EmbeddingProvider | None, RerankProvider | None]:
    """Attach LRU caches to the providers that support them.

    A capacity of 0 leaves the provider unwrapped, which is how an operator
    turns caching off without disabling the provider itself.
    """

    wrapped_embedding = (
        CachingEmbeddingProvider(embedding, LRUCache(embedding_capacity))
        if embedding is not None and embedding_capacity > 0
        else embedding
    )
    wrapped_rerank = (
        CachingRerankProvider(rerank, LRUCache(rerank_capacity))
        if rerank is not None and rerank_capacity > 0
        else rerank
    )
    return wrapped_embedding, wrapped_rerank
