"""Tests for embedding providers and the in-process vector index.

No test here performs real network I/O: the remote providers are exercised
through ``httpx.MockTransport``. This keeps the suite hermetic and guarantees a
missing API key can never turn into a skipped or flaky test.
"""

from __future__ import annotations

import json
import unittest

import httpx
import numpy as np

from apps.api.search import IndexDocument
from apps.api.search.chunking import build_index_document
from apps.api.search.providers import (
    HashingEmbeddingProvider,
    ProviderError,
    ProviderUnavailable,
    RemoteEmbeddingProvider,
    RemoteRerankProvider,
)
from apps.api.search.vector import VectorIndex


def _doc(resource_id: str, name: str, description: str = "") -> IndexDocument:
    return build_index_document(
        resource_public_id=resource_id,
        version_public_id=f"{resource_id}-v1",
        kind="skill",
        slug=resource_id,
        name=name,
        version="1.0.0",
        status="published",
        owner_team="platform-ai",
        description=description,
        manifest={"tags": []},
        type_spec={},
        files={},
        artifact_hash=f"hash-{resource_id}",
        manifest_snapshot_hash=f"snap-{resource_id}",
    )


class HashingProviderTests(unittest.TestCase):
    def test_is_always_available_offline(self) -> None:
        self.assertTrue(HashingEmbeddingProvider().available)

    def test_vectors_are_deterministic_and_normalised(self) -> None:
        provider = HashingEmbeddingProvider(dimensions=64)
        first = provider.embed(["代码审查"])[0]
        second = provider.embed(["代码审查"])[0]
        self.assertEqual(first, second)
        self.assertAlmostEqual(sum(value * value for value in first), 1.0, places=6)

    def test_identical_text_yields_identical_vector(self) -> None:
        provider = HashingEmbeddingProvider()
        a, b = provider.embed(["same text", "same text"])
        self.assertEqual(a, b)

    def test_different_text_yields_different_vector(self) -> None:
        provider = HashingEmbeddingProvider()
        a, b = provider.embed(["代码审查", "数据库迁移"])
        self.assertNotEqual(a, b)

    def test_empty_text_yields_zero_vector(self) -> None:
        vector = HashingEmbeddingProvider().embed([""])[0]
        self.assertEqual(sum(abs(value) for value in vector), 0.0)

    def test_invalid_dimensions_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            HashingEmbeddingProvider(dimensions=0)


class RemoteEmbeddingDisabledTests(unittest.TestCase):
    def test_disabled_provider_reports_unavailable(self) -> None:
        provider = RemoteEmbeddingProvider(
            base_url="https://example.invalid", api_key="k", model="m", enabled=False
        )
        self.assertFalse(provider.available)

    def test_keyless_provider_reports_unavailable(self) -> None:
        provider = RemoteEmbeddingProvider(
            base_url="https://example.invalid", api_key=None, model="m", enabled=True
        )
        self.assertFalse(provider.available)

    def test_calling_a_disabled_provider_raises_without_network(self) -> None:
        """Fail closed: no request may be attempted when disabled."""

        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            calls.append(str(request.url))
            return httpx.Response(200, json={})

        provider = RemoteEmbeddingProvider(
            base_url="https://example.invalid",
            api_key="k",
            model="m",
            enabled=False,
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(ProviderUnavailable):
            provider.embed(["x"])
        self.assertEqual(calls, [])


class RemoteEmbeddingTests(unittest.TestCase):
    def _provider(self, handler, **kwargs) -> RemoteEmbeddingProvider:
        return RemoteEmbeddingProvider(
            base_url="https://api.example/v1",
            api_key="secret-key",
            model="Qwen/Qwen3-Embedding-0.6B",
            dimensions=4,
            enabled=True,
            transport=httpx.MockTransport(handler),
            **kwargs,
        )

    def test_request_shape_and_response_parsing(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization")
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 0, "embedding": [1.0, 0.0, 0.0, 0.0]},
                        {"index": 1, "embedding": [0.0, 1.0, 0.0, 0.0]},
                    ]
                },
            )

        vectors = self._provider(handler).embed(["a", "b"])

        self.assertEqual(captured["url"], "https://api.example/v1/embeddings")
        self.assertEqual(captured["auth"], "Bearer secret-key")
        self.assertEqual(captured["body"]["model"], "Qwen/Qwen3-Embedding-0.6B")
        self.assertEqual(captured["body"]["input"], ["a", "b"])
        self.assertEqual(captured["body"]["dimensions"], 4)
        self.assertEqual(len(vectors), 2)
        self.assertEqual(vectors[0], [1.0, 0.0, 0.0, 0.0])

    def test_results_are_ordered_by_index_even_if_server_reorders(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "embedding": [0.0, 1.0]},
                        {"index": 0, "embedding": [1.0, 0.0]},
                    ]
                },
            )

        vectors = self._provider(handler).embed(["first", "second"])

        self.assertEqual(vectors[0], [1.0, 0.0])
        self.assertEqual(vectors[1], [0.0, 1.0])

    def test_batching_splits_large_inputs(self) -> None:
        batch_sizes: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            batch_sizes.append(len(body["input"]))
            return httpx.Response(
                200,
                json={"data": [{"index": i, "embedding": [0.0]} for i in range(len(body["input"]))]},
            )

        provider = self._provider(handler, batch_size=2)
        provider.embed(["a", "b", "c", "d", "e"])

        self.assertEqual(batch_sizes, [2, 2, 1])

    def test_http_error_raises_without_leaking_the_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "bad key"})

        with self.assertRaises(ProviderError) as caught:
            self._provider(handler).embed(["x"])

        self.assertEqual(caught.exception.status, 401)
        self.assertNotIn("secret-key", str(caught.exception))

    def test_malformed_response_raises_provider_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": True})

        with self.assertRaises(ProviderError):
            self._provider(handler).embed(["x"])

    def test_transport_failure_is_wrapped(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        with self.assertRaises(ProviderError):
            self._provider(handler).embed(["x"])


class RemoteRerankTests(unittest.TestCase):
    def _provider(self, handler) -> RemoteRerankProvider:
        return RemoteRerankProvider(
            base_url="https://api.example/v1",
            api_key="secret-key",
            model="BAAI/bge-reranker-v2-m3",
            enabled=True,
            transport=httpx.MockTransport(handler),
        )

    def test_disabled_provider_raises(self) -> None:
        provider = RemoteRerankProvider(
            base_url="https://example.invalid", api_key=None, model="m", enabled=False
        )
        self.assertFalse(provider.available)
        with self.assertRaises(ProviderUnavailable):
            provider.rerank("q", ["d"])

    def test_response_is_parsed_into_index_and_score(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.assertEqual(body["model"], "BAAI/bge-reranker-v2-m3")
            self.assertEqual(body["query"], "审查")
            self.assertFalse(body["return_documents"])
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 1, "relevance_score": 0.9},
                        {"index": 0, "relevance_score": 0.2},
                    ]
                },
            )

        results = self._provider(handler).rerank("审查", ["doc-a", "doc-b"])

        self.assertEqual([(r.index, r.score) for r in results], [(1, 0.9), (0, 0.2)])

    def test_empty_documents_short_circuit_without_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("no request should be made for empty input")

        self.assertEqual(self._provider(handler).rerank("q", []), [])


class VectorIndexTests(unittest.TestCase):
    def test_cosine_similarity_ranks_correctly(self) -> None:
        docs = [_doc("a", "alpha"), _doc("b", "beta")]
        vectors = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        index = VectorIndex(docs, vectors)

        results = index.search([1.0, 0.0])

        self.assertEqual(results[0].document.resource_public_id, "a")
        self.assertAlmostEqual(results[0].similarity, 1.0, places=6)

    def test_vectors_are_normalised_so_magnitude_does_not_matter(self) -> None:
        docs = [_doc("a", "alpha")]
        index = VectorIndex(docs, np.array([[3.0, 0.0]], dtype=np.float32))

        self.assertAlmostEqual(index.search([10.0, 0.0])[0].similarity, 1.0, places=6)

    def test_ties_break_deterministically(self) -> None:
        docs = [_doc("z", "same"), _doc("a", "same")]
        vectors = np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)
        index = VectorIndex(docs, vectors)

        self.assertEqual(
            [r.document.resource_public_id for r in index.search([1.0, 0.0])], ["a", "z"]
        )

    def test_zero_vector_query_returns_nothing(self) -> None:
        index = VectorIndex([_doc("a", "alpha")], np.array([[1.0, 0.0]], dtype=np.float32))

        self.assertEqual(index.search([0.0, 0.0]), [])

    def test_dimension_mismatch_is_rejected(self) -> None:
        index = VectorIndex([_doc("a", "alpha")], np.array([[1.0, 0.0]], dtype=np.float32))

        with self.assertRaises(ValueError):
            index.search([1.0, 0.0, 0.0])

    def test_count_mismatch_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            VectorIndex([_doc("a", "alpha")], np.array([[1.0], [2.0]], dtype=np.float32))

    def test_empty_index_returns_nothing(self) -> None:
        index = VectorIndex([], None)

        self.assertEqual(index.search([1.0, 0.0]), [])
        self.assertEqual(index.document_count, 0)

    def test_build_uses_the_provider_and_records_its_name(self) -> None:
        docs = [_doc("a", "alpha"), _doc("b", "beta")]
        provider = HashingEmbeddingProvider(dimensions=32)

        index = VectorIndex.build(docs, provider)

        self.assertEqual(index.document_count, 2)
        self.assertEqual(index.dimensions, 32)
        self.assertEqual(index.model, provider.name)

    def test_local_hash_recall_is_lexical_only(self) -> None:
        """Documents the baseline's limitation rather than pretending it is semantic."""

        docs = [_doc("review", "代码审查", "审查 Java 代码"), _doc("db", "数据库迁移", "执行 SQL 迁移")]
        index = VectorIndex.build(docs, HashingEmbeddingProvider(dimensions=256))

        exact = index.query("代码审查", HashingEmbeddingProvider(dimensions=256))

        self.assertEqual(exact[0].document.resource_public_id, "review")


if __name__ == "__main__":
    unittest.main()
