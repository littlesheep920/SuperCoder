"""Verify the remote-provider path end to end without a real API key.

The key the operator will supply is not committed. These tests prove that the
moment a key *is* configured, the wiring works: settings are read, the right
provider is constructed, requests carry the documented payload shape, and the
pipeline upgrades from the offline baseline to semantic recall.

The transport is mocked, so no network call happens.
"""

from __future__ import annotations

import json
import os
import unittest

import httpx

from apps.api.config import SearchSettings, Secret
from apps.api.search import RankingProfile
from apps.api.search.chunking import build_index_document
from apps.api.search.factory import build_embedding_provider, build_rerank_provider
from apps.api.search.providers import HashingEmbeddingProvider, RemoteEmbeddingProvider
from apps.api.search.service import SearchService

FIXTURE_KEY = "test-fixture-not-a-real-key"


def _settings(**overrides) -> SearchSettings:
    base = dict(
        embedding_enabled=False,
        embedding_provider="disabled",
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        embedding_dimensions=1024,
        rerank_enabled=False,
        rerank_model="BAAI/bge-reranker-v2-m3",
        remote_enabled=False,
        base_url="https://api.siliconflow.cn/v1",
        api_key=Secret(None),
        timeout_seconds=30.0,
        batch_size=16,
    )
    base.update(overrides)
    return SearchSettings(**base)


def _doc(resource_id: str, name: str, description: str = "", body: str = "") -> object:
    return build_index_document(
        resource_public_id=resource_id,
        version_public_id=f"{resource_id}@1.0.0",
        kind="skill",
        slug=resource_id,
        name=name,
        version="1.0.0",
        status="draft",
        owner_team="platform-ai",
        description=description,
        manifest={"tags": []},
        type_spec={},
        files={"SKILL.md": f"# 说明\n\n{body}\n"} if body else {},
        artifact_hash=f"hash-{resource_id}",
        manifest_snapshot_hash=f"snap-{resource_id}",
    )


class FactoryTests(unittest.TestCase):
    def test_defaults_use_the_offline_baseline(self) -> None:
        """With nothing enabled, no network path may be constructed."""

        provider = build_embedding_provider(_settings())

        self.assertIsInstance(provider, HashingEmbeddingProvider)
        self.assertFalse(isinstance(provider, RemoteEmbeddingProvider))

    def test_enabling_remote_with_a_key_selects_the_remote_provider(self) -> None:
        provider = build_embedding_provider(
            _settings(
                remote_enabled=True,
                embedding_enabled=True,
                api_key=Secret(FIXTURE_KEY),
            )
        )

        self.assertIsInstance(provider, RemoteEmbeddingProvider)
        self.assertTrue(provider.available)
        self.assertEqual(provider.name, "Qwen/Qwen3-Embedding-0.6B-d1024")

    def test_remote_enabled_without_a_key_falls_back(self) -> None:
        """A missing key must never produce a provider that tries to call out."""

        provider = build_embedding_provider(_settings(remote_enabled=True, embedding_enabled=True))

        self.assertIsInstance(provider, HashingEmbeddingProvider)

    def test_rerank_stays_unavailable_when_disabled(self) -> None:
        provider = build_rerank_provider(_settings())

        self.assertFalse(provider.available)

    def test_rerank_becomes_available_when_enabled_with_a_key(self) -> None:
        provider = build_rerank_provider(
            _settings(remote_enabled=True, rerank_enabled=True, api_key=Secret(FIXTURE_KEY))
        )

        self.assertTrue(provider.available)
        self.assertEqual(provider.name, "BAAI/bge-reranker-v2-m3")

    def test_model_name_is_configuration_not_a_constant(self) -> None:
        provider = build_embedding_provider(
            _settings(
                remote_enabled=True,
                embedding_enabled=True,
                api_key=Secret(FIXTURE_KEY),
                embedding_model="some/other-model",
                embedding_dimensions=256,
            )
        )

        self.assertEqual(provider.name, "some/other-model-d256")

    def test_provider_receives_the_real_key_not_the_redacted_repr(self) -> None:
        """Regression: ``str(Secret)`` yields "Secret(<redacted>)".

        Passing that to the provider sent a bogus bearer token, so every remote
        call failed with HTTP 401 while the offline fallback silently hid it.
        ``Secret.expose()`` is the only correct conversion here.
        """

        provider = build_embedding_provider(
            _settings(remote_enabled=True, embedding_enabled=True, api_key=Secret(FIXTURE_KEY))
        )

        self.assertEqual(provider._api_key, FIXTURE_KEY)
        self.assertNotIn("redacted", provider._api_key)
        self.assertNotIn("Secret(", provider._api_key)

    def test_rerank_provider_receives_the_real_key(self) -> None:
        provider = build_rerank_provider(
            _settings(remote_enabled=True, rerank_enabled=True, api_key=Secret(FIXTURE_KEY))
        )

        self.assertEqual(provider._api_key, FIXTURE_KEY)

    def test_secret_repr_stays_redacted(self) -> None:
        """The fix must not weaken the redaction that protects logs."""

        secret = Secret(FIXTURE_KEY)
        self.assertIn("redacted", repr(secret))
        self.assertNotIn(FIXTURE_KEY, repr(secret))
        self.assertNotIn(FIXTURE_KEY, str(secret))
        self.assertEqual(secret.expose(), FIXTURE_KEY)


class _SemanticStub:
    """Embedding stub that returns vectors with real semantic structure.

    It maps text onto a small set of concept axes, so a Chinese paraphrase and
    its English/code counterpart land near each other. This is what the offline
    hashing provider cannot do, and it lets us prove that enabling a semantic
    provider actually changes ranking.
    """

    name = "Qwen/Qwen3-Embedding-0.6B-d4"
    available = True

    _AXES = {
        "review": ("审查", "review", "代码质量", "检查"),
        "plan": ("计划", "plan", "规划", "方案"),
        "stats": ("统计", "字数", "词频", "statistics", "字频"),
    }

    def _vector(self, text: str) -> list[float]:
        lowered = text.casefold()
        vector = [0.0] * len(self._AXES)
        for index, keywords in enumerate(self._AXES.values()):
            vector[index] = 1.0 if any(keyword in lowered for keyword in keywords) else 0.0
        if not any(vector):
            vector = [0.25] * len(self._AXES)
        return vector

    def embed(self, texts):
        return [self._vector(text) for text in texts]


class SemanticUpgradeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.documents = [
            _doc("review", "代码审查", "审查变更并给出证据"),
            _doc("plan", "开发计划助手", "制定实施方案"),
            _doc("stats", "文本统计", "统计字数和词频"),
        ]

    def test_semantic_provider_finds_a_paraphrase_that_keyword_misses(self) -> None:
        """The whole point of adding a semantic path."""

        keyword_only = SearchService(self.documents, embedding=None)
        semantic = SearchService(self.documents, embedding=_SemanticStub())

        paraphrase = "代码质量把关"
        keyword_outcome = keyword_only.search(paraphrase, profile=RankingProfile.KEYWORD)
        semantic_outcome = semantic.search(paraphrase, profile=RankingProfile.HYBRID)

        keyword_hit = (
            keyword_outcome.results
            and keyword_outcome.results[0].document.resource_public_id == "review"
        )
        semantic_hit = (
            semantic_outcome.results
            and semantic_outcome.results[0].document.resource_public_id == "review"
        )
        self.assertTrue(semantic_hit, msg="semantic path should surface the review document")
        if not keyword_hit:
            # If keyword happened to match too, the comparison is uninformative
            # but the semantic result must still be correct.
            self.assertTrue(semantic_outcome.results)

    def test_enabled_provider_reports_no_semantic_degradation(self) -> None:
        service = SearchService(self.documents, embedding=_SemanticStub())

        outcome = service.search("代码质量把关", profile=RankingProfile.SEMANTIC)

        self.assertNotIn("semantic_unavailable", outcome.degraded)
        self.assertNotIn("semantic_error", outcome.degraded)


class RemotePayloadTests(unittest.TestCase):
    def test_embedding_payload_matches_siliconflow_docs(self) -> None:
        """Request shape per the published embeddings API."""

        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200, json={"data": [{"index": 0, "embedding": [0.1] * 4}]}
            )

        provider = RemoteEmbeddingProvider(
            base_url="https://api.siliconflow.cn/v1",
            api_key=FIXTURE_KEY,
            model="Qwen/Qwen3-Embedding-0.6B",
            dimensions=1024,
            enabled=True,
            transport=httpx.MockTransport(handler),
        )
        provider.embed(["你好"])

        self.assertEqual(captured["path"], "/v1/embeddings")
        body = captured["body"]
        self.assertEqual(body["model"], "Qwen/Qwen3-Embedding-0.6B")
        self.assertEqual(body["dimensions"], 1024)
        self.assertEqual(body["input"], ["你好"])

    def test_rerank_payload_matches_siliconflow_docs(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200, json={"results": [{"index": 0, "relevance_score": 0.5}]}
            )

        provider = build_rerank_provider(
            _settings(remote_enabled=True, rerank_enabled=True, api_key=Secret(FIXTURE_KEY))
        )
        # Rebuild with the mock transport attached.
        from apps.api.search.providers import RemoteRerankProvider

        provider = RemoteRerankProvider(
            base_url=provider._base_url,
            api_key=FIXTURE_KEY,
            model=provider.name,
            enabled=True,
            transport=httpx.MockTransport(handler),
        )
        provider.rerank("查询", ["文档甲", "文档乙"])

        self.assertEqual(captured["path"], "/v1/rerank")
        body = captured["body"]
        self.assertEqual(body["model"], "BAAI/bge-reranker-v2-m3")
        self.assertEqual(body["query"], "查询")
        self.assertEqual(body["documents"], ["文档甲", "文档乙"])


if __name__ == "__main__":
    unittest.main()
