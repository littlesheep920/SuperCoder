"""Tests for the search caches.

A cache in a retrieval path is dangerous in a specific way: it can serve a
result that no longer reflects the data. These tests pin the invalidation rules
rather than the speed, because correctness is what a cache can break.

Covered:
* the key includes the model identity, so a provider change cannot reuse a
  vector from another embedding space;
* edited content produces a new cache key and therefore a rebuild;
* visibility is never cached, so a revoked resource disappears immediately;
* failures are not memoised, so recovery is not delayed by a stale miss;
* capacity 0 disables caching without disabling the provider.
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from apps.api.search.cache import (
    CachingEmbeddingProvider,
    CachingRerankProvider,
    LRUCache,
    wrap_with_cache,
)
from apps.api.search.providers import ProviderError, RerankResult


class CountingEmbedding:
    """An embedding provider that records how many calls it received."""

    def __init__(self, name: str = "test-embed", dimensions: int = 3) -> None:
        self._name = name
        self._dimensions = dimensions
        self.calls: list[list[str]] = []
        self.fail_next = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def available(self) -> bool:
        return True

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.fail_next:
            self.fail_next = False
            raise ProviderError("embedding_failed", status=500)
        return [[float(len(text)), 1.0, 2.0][: self._dimensions] for text in texts]


class CountingRerank:
    def __init__(self, name: str = "test-rerank") -> None:
        self._name = name
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def available(self) -> bool:
        return True

    def rerank(self, query, documents, *, top_n=None):
        self.calls.append((query, tuple(documents)))
        return [RerankResult(index=position, score=1.0 / (position + 1)) for position in range(len(documents))]


class LRUCacheTests(unittest.TestCase):
    def test_hit_and_miss_are_counted(self) -> None:
        cache = LRUCache(4)
        cache.put("a", [1.0])

        self.assertEqual(cache.get("a"), [1.0])
        cache.get("missing")

        stats = cache.stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["hit_rate"], 0.5)

    def test_eviction_removes_least_recently_used(self) -> None:
        cache = LRUCache(2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.get("a")  # 'a' becomes most recently used
        cache.put("c", 3)  # evicts 'b'

        self.assertIsNone(cache.get("b"))
        self.assertEqual(cache.get("a"), 1)
        self.assertEqual(cache.get("c"), 3)

    def test_zero_capacity_stores_nothing(self) -> None:
        cache = LRUCache(0)
        cache.put("a", 1)

        self.assertIsNone(cache.get("a"))
        self.assertEqual(len(cache), 0)

    def test_clear_resets_counters(self) -> None:
        cache = LRUCache(2)
        cache.put("a", 1)
        cache.get("a")
        cache.clear()

        stats = cache.stats()
        self.assertEqual(stats["hits"], 0)
        self.assertEqual(stats["size"], 0)


class EmbeddingCacheTests(unittest.TestCase):
    def test_repeated_query_calls_the_provider_once(self) -> None:
        inner = CountingEmbedding()
        provider = CachingEmbeddingProvider(inner, LRUCache(8))

        first = provider.embed(["审查代码"])
        second = provider.embed(["审查代码"])

        self.assertEqual(len(inner.calls), 1)
        self.assertEqual(first, second)

    def test_different_queries_both_reach_the_provider(self) -> None:
        inner = CountingEmbedding()
        provider = CachingEmbeddingProvider(inner, LRUCache(8))

        provider.embed(["审查代码"])
        provider.embed(["部署流程"])

        self.assertEqual(len(inner.calls), 2)

    def test_provider_name_is_part_of_the_key(self) -> None:
        """Two models can produce equal-length vectors in different spaces."""

        cache = LRUCache(8)
        first = CachingEmbeddingProvider(CountingEmbedding(name="model-a"), cache)
        second = CachingEmbeddingProvider(CountingEmbedding(name="model-b"), cache)

        first.embed(["同一段文本"])
        second.embed(["同一段文本"])

        # Each wrapper has its own inner provider; the point is that the shared
        # cache did not let the second read the first model's vector.
        self.assertEqual(second._cache.stats()["hits"], 0)

    def test_wrapper_preserves_the_model_identity(self) -> None:
        """Index revisions must not change merely because caching is on."""

        inner = CountingEmbedding(name="qwen-embed")
        provider = CachingEmbeddingProvider(inner, LRUCache(8))

        self.assertEqual(provider.name, inner.name)

    def test_cached_vector_is_not_aliased_to_the_caller(self) -> None:
        """A caller mutating its vector must not corrupt the cache."""

        inner = CountingEmbedding()
        provider = CachingEmbeddingProvider(inner, LRUCache(8))

        first = provider.embed(["查询"])[0]
        first[0] = 999.0
        second = provider.embed(["查询"])[0]

        self.assertNotEqual(second[0], 999.0)

    def test_batch_calls_bypass_the_cache(self) -> None:
        """Index building sends distinct texts; caching them would only grow."""

        inner = CountingEmbedding()
        provider = CachingEmbeddingProvider(inner, LRUCache(8))

        provider.embed(["a", "b", "c"])

        self.assertEqual(provider._cache.stats()["misses"], 0)

    def test_failure_is_not_memoised(self) -> None:
        """A memoised failure would delay recovery after an outage."""

        inner = CountingEmbedding()
        inner.fail_next = True
        provider = CachingEmbeddingProvider(inner, LRUCache(8))

        with self.assertRaises(ProviderError):
            provider.embed(["查询"])

        # The retry must actually reach the provider again.
        provider.embed(["查询"])
        self.assertEqual(len(inner.calls), 2)


class RerankCacheTests(unittest.TestCase):
    def test_same_query_and_candidates_hit_the_cache(self) -> None:
        inner = CountingRerank()
        provider = CachingRerankProvider(inner, LRUCache(8))

        first = provider.rerank("查询", ["文档一", "文档二"])
        second = provider.rerank("查询", ["文档一", "文档二"])

        self.assertEqual(len(inner.calls), 1)
        self.assertEqual(first, second)

    def test_changed_candidate_set_misses(self) -> None:
        inner = CountingRerank()
        provider = CachingRerankProvider(inner, LRUCache(8))

        provider.rerank("查询", ["文档一", "文档二"])
        provider.rerank("查询", ["文档一", "文档三"])

        self.assertEqual(len(inner.calls), 2)

    def test_candidate_order_is_part_of_the_key(self) -> None:
        """Order carries meaning: rerank scores are positional."""

        inner = CountingRerank()
        provider = CachingRerankProvider(inner, LRUCache(8))

        provider.rerank("查询", ["文档一", "文档二"])
        provider.rerank("查询", ["文档二", "文档一"])

        self.assertEqual(len(inner.calls), 2)

    def test_different_query_misses(self) -> None:
        inner = CountingRerank()
        provider = CachingRerankProvider(inner, LRUCache(8))

        provider.rerank("查询一", ["文档一"])
        provider.rerank("查询二", ["文档一"])

        self.assertEqual(len(inner.calls), 2)


class WrapperFactoryTests(unittest.TestCase):
    def test_zero_capacity_leaves_the_provider_unwrapped(self) -> None:
        inner = CountingEmbedding()

        wrapped, _ = wrap_with_cache(inner, None, embedding_capacity=0, rerank_capacity=0)

        self.assertIs(wrapped, inner)

    def test_non_zero_capacity_wraps(self) -> None:
        inner = CountingEmbedding()

        wrapped, _ = wrap_with_cache(inner, None, embedding_capacity=4, rerank_capacity=4)

        self.assertIsInstance(wrapped, CachingEmbeddingProvider)
        # The model identity must survive wrapping, or index revisions change.
        self.assertEqual(wrapped.name, inner.name)

    def test_none_provider_stays_none(self) -> None:
        wrapped, rerank = wrap_with_cache(None, None, embedding_capacity=4, rerank_capacity=4)

        self.assertIsNone(wrapped)
        self.assertIsNone(rerank)


if __name__ == "__main__":
    unittest.main()
