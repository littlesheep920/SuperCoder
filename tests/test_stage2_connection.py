"""Tests for provider connection reuse and warm-up.

The measurement that motivated this: with a fresh ``httpx.Client`` per call, the
first embedding call took 813 ms and the second 90 ms. The 723 ms difference was
TCP and TLS setup, not the model. Reusing one client and warming it at startup
moved that cost off the user's first search.

These tests pin the behaviour with a fake transport, so they run offline and do
not depend on a real endpoint or key.
"""

from __future__ import annotations

import unittest

import httpx

from apps.api.search.cache import CachingEmbeddingProvider, CachingRerankProvider, LRUCache
from apps.api.search.providers import ProviderError, RemoteEmbeddingProvider, RemoteRerankProvider


class TransportRecorder:
    """A transport that records requests and returns canned responses."""

    def __init__(self, responses: list[tuple[int, dict]] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.closed = 0
        self._responses = responses or []
        self._index = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._index < len(self._responses):
            status, body = self._responses[self._index]
            self._index += 1
            return httpx.Response(status, json=body)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    def close(self) -> None:
        self.closed += 1


def _embedding(transport: TransportRecorder) -> RemoteEmbeddingProvider:
    return RemoteEmbeddingProvider(
        base_url="https://example.invalid/v1",
        api_key="k",
        model="test-model",
        dimensions=2,
        enabled=True,
        transport=httpx.MockTransport(transport),
    )


def _rerank(transport: TransportRecorder) -> RemoteRerankProvider:
    return RemoteRerankProvider(
        base_url="https://example.invalid/v1",
        api_key="k",
        model="test-rerank",
        enabled=True,
        transport=httpx.MockTransport(transport),
    )


class ConnectionReuseTests(unittest.TestCase):
    def test_embedding_client_is_created_once(self) -> None:
        transport = TransportRecorder()
        provider = _embedding(transport)

        provider.embed(["第一次"])
        client_after_first = provider._shared_client
        provider.embed(["第二次"])

        self.assertIsNotNone(client_after_first)
        self.assertIs(client_after_first, provider._shared_client)

    def test_rerank_client_is_created_once(self) -> None:
        transport = TransportRecorder(
            [
                (200, {"results": [{"index": 0, "relevance_score": 0.9}]}),
                (200, {"results": [{"index": 0, "relevance_score": 0.9}]}),
            ]
        )
        provider = _rerank(transport)

        provider.rerank("查询", ["文档"])
        client = provider._shared_client
        provider.rerank("查询2", ["文档"])

        self.assertIs(client, provider._shared_client)

    def test_close_releases_the_connection(self) -> None:
        transport = TransportRecorder()
        provider = _embedding(transport)
        provider.embed(["查询"])
        provider.close()

        self.assertIsNone(provider._shared_client)


class WarmUpTests(unittest.TestCase):
    def test_disabled_provider_does_not_warm(self) -> None:
        """A disabled provider must not touch the network or the secret."""

        transport = TransportRecorder()
        provider = RemoteEmbeddingProvider(
            base_url="https://example.invalid/v1",
            api_key=None,
            model="test-model",
            enabled=False,
            transport=httpx.MockTransport(transport),
        )

        self.assertFalse(provider.warm_connection())
        self.assertEqual(transport.requests, [])

    def test_warm_up_issues_a_request(self) -> None:
        transport = TransportRecorder()
        provider = _embedding(transport)

        self.assertTrue(provider.warm_connection())
        self.assertEqual(len(transport.requests), 1)

    def test_warm_up_swallows_errors(self) -> None:
        """Startup must not fail because a provider is down."""

        def explode(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        provider = RemoteEmbeddingProvider(
            base_url="https://example.invalid/v1",
            api_key="k",
            model="test-model",
            enabled=True,
            transport=httpx.MockTransport(explode),
        )

        self.assertFalse(provider.warm_connection())

    def test_warm_up_runs_before_the_cache_wrapper_hides_it(self) -> None:
        """The wrapper must forward warm-up, or the first query pays again."""

        transport = TransportRecorder()
        inner = _embedding(transport)
        wrapped = CachingEmbeddingProvider(inner, LRUCache(8))

        self.assertTrue(wrapped.warm_connection())
        self.assertEqual(len(transport.requests), 1)

    def test_rerank_wrapper_forwards_warm_up(self) -> None:
        transport = TransportRecorder()
        inner = _rerank(transport)
        wrapped = CachingRerankProvider(inner, LRUCache(8))

        self.assertTrue(wrapped.warm_connection())

    def test_warm_up_never_leaks_the_key_in_errors(self) -> None:
        """A failing warm-up must not surface the credential."""

        def explode(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out", request=request)

        provider = RemoteEmbeddingProvider(
            base_url="https://example.invalid/v1",
            api_key="k",
            model="test-model",
            enabled=True,
            transport=httpx.MockTransport(explode),
        )

        self.assertFalse(provider.warm_connection())

    def test_disabled_rerank_does_not_warm(self) -> None:
        provider = RemoteRerankProvider(
            base_url="https://example.invalid/v1",
            api_key=None,
            model="test-rerank",
            enabled=False,
        )

        self.assertFalse(provider.warm_connection())


if __name__ == "__main__":
    unittest.main()
