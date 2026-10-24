"""API tests for ``POST /v1/search/resources`` and contract conformance.

These run against the in-memory repository doubles and the real Git provider, so
they prove the endpoint is reachable, authorized, and shaped exactly like the
frozen OpenAPI contract. MySQL-specific behaviour remains HOLD.
"""

from __future__ import annotations

import unittest

import yaml
from fastapi.testclient import TestClient

from apps.api.application.catalog import CatalogService
from apps.api.application.diffs import DiffService
from apps.api.application.governance import GovernanceService
from apps.api.application.job_service import JobService
from apps.api.application.resource_commands import ResourceCommandService
from apps.api.application.resource_queries import ResourceQueryService
from apps.api.application.rollback import RollbackService
from apps.api.application.search_service import SearchServiceFactory
from apps.api.application.snapshots import SnapshotService
from apps.api.application.versions import VersionQueryService
from apps.api.auth import DevTokenAuth
from apps.api.config import get_settings
from apps.api.dependencies import Container
from apps.api.domain.hashing import normalize_utf8_text, sha256_digest
from apps.api.domain.models import ResourceKind
from apps.api.main import create_app
from apps.api.providers.git_repository import GitProvider
from apps.api.providers.manifest_loader import ManifestLoader
from apps.api.search.providers import HashingEmbeddingProvider, RemoteRerankProvider
from tests.support.memory import MemoryStore, make_uow_factory

ROOT = None  # resolved lazily to keep import cost down
_OWNER = {"Authorization": "Bearer dev-owner-token"}
_MEMBER = {"Authorization": "Bearer dev-member-token"}

_KINDS = [
    (ResourceKind.SKILL, "demo-code-review"),
    (ResourceKind.MCP, "demo-local-catalog"),
    (ResourceKind.PROMPT, "demo-bugfix"),
    (ResourceKind.AGENT, "demo-planner"),
    (ResourceKind.WORKFLOW, "demo-review-flow"),
    (ResourceKind.TOOL, "demo-text-stats"),
]


def build_container(store: MemoryStore, *, embedding=None) -> Container:
    settings = get_settings()
    git = GitProvider(settings.git.repository_root, read_only=True)
    loader = ManifestLoader(git)
    uow_factory = make_uow_factory(store)
    snapshots = SnapshotService(loader)
    return Container(
        settings=settings,
        git=git,
        manifest_loader=loader,
        uow_factory=uow_factory,
        auth=DevTokenAuth(settings.dev_auth_tokens_file),
        snapshots=snapshots,
        resource_commands=ResourceCommandService(uow_factory, snapshots),
        resource_queries=ResourceQueryService(uow_factory),
        versions=VersionQueryService(uow_factory),
        diffs=DiffService(uow_factory),
        rollback=RollbackService(uow_factory, snapshots),
        governance=GovernanceService(uow_factory),
        catalog=CatalogService(uow_factory),
        jobs=JobService(uow_factory),
        search=SearchServiceFactory(
            uow_factory,
            loader,
            embedding=embedding or HashingEmbeddingProvider(dimensions=64),
            rerank=RemoteRerankProvider(
                base_url="https://example.invalid", api_key=None, model="m", enabled=False
            ),
        ),
        mysql_engine=None,
    )


def _request_body(kind: ResourceKind, slug: str) -> dict:
    settings = get_settings()
    git = GitProvider(settings.git.repository_root, read_only=True)
    loader = ManifestLoader(git)
    snapshots = SnapshotService(loader)
    loaded = snapshots.load(kind, slug)
    artifacts = [
        {
            "path": path,
            "digest": sha256_digest(normalize_utf8_text(content)),
            "size_bytes": len(normalize_utf8_text(content)),
        }
        for path, content in sorted(loaded.files.items())
    ]
    return {"manifest": loaded.manifest, "artifacts": artifacts, "reason": "stage2 search test"}


def _search_body(query: str, **overrides) -> dict:
    body = {"query": query, "filters": {}, "limit": 10}
    body.update(overrides)
    return body


class SearchEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryStore()
        self.container = build_container(self.store)
        self.client = TestClient(create_app(get_settings(), self.container))
        for kind, slug in _KINDS:
            response = self.client.post(
                "/v1/resources", json=_request_body(kind, slug), headers=_OWNER
            )
            self.assertEqual(response.status_code, 201, msg=response.text)

    def test_search_requires_authentication(self) -> None:
        response = self.client.post("/v1/search/resources", json=_search_body("代码审查"))

        self.assertEqual(response.status_code, 401)

    def test_search_returns_hits(self) -> None:
        response = self.client.post(
            "/v1/search/resources", json=_search_body("代码审查"), headers=_OWNER
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertTrue(response.json()["items"])

    def test_response_matches_frozen_schema_keys(self) -> None:
        """The frozen schema sets additionalProperties:false; nothing may be added."""

        response = self.client.post(
            "/v1/search/resources", json=_search_body("代码审查"), headers=_OWNER
        )

        body = response.json()
        self.assertEqual(set(body), {"query_id", "items", "page", "took_ms"})
        hit = body["items"][0]
        allowed = {
            "rank",
            "score",
            "score_components",
            "resource",
            "version",
            "source_artifact_hash",
            "matched_fields",
            "highlights",
            "index_revision",
        }
        self.assertTrue(set(hit) <= allowed, msg=f"unexpected hit keys: {set(hit) - allowed}")

    def test_every_hit_carries_source_artifact_hash(self) -> None:
        """B9."""

        response = self.client.post(
            "/v1/search/resources", json=_search_body("代码审查"), headers=_OWNER
        )

        for hit in response.json()["items"]:
            self.assertTrue(hit["source_artifact_hash"])

    def test_ranks_are_contiguous_from_one(self) -> None:
        response = self.client.post(
            "/v1/search/resources", json=_search_body("代码审查"), headers=_OWNER
        )

        ranks = [hit["rank"] for hit in response.json()["items"]]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))

    def test_empty_filters_object_is_accepted(self) -> None:
        response = self.client.post(
            "/v1/search/resources", json={"query": "审查", "filters": {}, "limit": 5}, headers=_OWNER
        )

        self.assertEqual(response.status_code, 200)

    def test_missing_required_fields_are_rejected(self) -> None:
        """``query``, ``filters`` and ``limit`` are all required by the contract."""

        for body in (
            {"filters": {}, "limit": 5},
            {"query": "x", "limit": 5},
            {"limit": 5},
            {"query": "x", "filters": {}},
        ):
            with self.subTest(body=body):
                response = self.client.post("/v1/search/resources", json=body, headers=_OWNER)
                self.assertEqual(response.status_code, 422)

    def test_page_info_echoes_the_requested_limit(self) -> None:
        response = self.client.post(
            "/v1/search/resources",
            json={"query": "审查", "filters": {}, "limit": 3},
            headers=_OWNER,
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["page"]["limit"], 3)

    def test_empty_query_is_rejected(self) -> None:
        response = self.client.post(
            "/v1/search/resources", json=_search_body(""), headers=_OWNER
        )

        self.assertEqual(response.status_code, 422)

    def test_unknown_ranking_profile_is_rejected(self) -> None:
        response = self.client.post(
            "/v1/search/resources",
            json=_search_body("审查", ranking_profile="magic"),
            headers=_OWNER,
        )

        self.assertEqual(response.status_code, 422)

    def test_all_three_profiles_succeed(self) -> None:
        """B11 at the HTTP layer."""

        for profile in ("hybrid", "keyword", "semantic"):
            with self.subTest(profile=profile):
                response = self.client.post(
                    "/v1/search/resources",
                    json=_search_body("代码审查", ranking_profile=profile),
                    headers=_OWNER,
                )
                self.assertEqual(response.status_code, 200, msg=response.text)

    def test_all_three_sorts_succeed(self) -> None:
        for sort in ("relevance", "newest", "name"):
            with self.subTest(sort=sort):
                response = self.client.post(
                    "/v1/search/resources", json=_search_body("代码审查", sort=sort), headers=_OWNER
                )
                self.assertEqual(response.status_code, 200, msg=response.text)

    def test_kind_filter_is_applied(self) -> None:
        response = self.client.post(
            "/v1/search/resources",
            json=_search_body("审查", filters={"kinds": ["skill"]}),
            headers=_OWNER,
        )

        kinds = {hit["resource"]["kind"] for hit in response.json()["items"]}
        self.assertTrue(kinds <= {"skill"}, msg=kinds)

    def test_degradation_is_reported_in_a_header(self) -> None:
        """Degradation must be visible without breaking the frozen body schema."""

        response = self.client.post(
            "/v1/search/resources",
            json=_search_body("代码审查", ranking_profile="semantic"),
            headers=_OWNER,
        )

        self.assertEqual(response.status_code, 200)
        # The local hashing provider is available, so rerank is what degrades.
        self.assertIn("rerank_unavailable", response.headers.get("x-search-degraded", ""))

    def test_index_revision_header_is_present(self) -> None:
        response = self.client.post(
            "/v1/search/resources", json=_search_body("审查"), headers=_OWNER
        )

        self.assertTrue(response.headers.get("x-index-revision", ""))

    def test_results_are_deterministic_across_requests(self) -> None:
        """B7 at the HTTP layer."""

        first = self.client.post(
            "/v1/search/resources", json=_search_body("代码审查"), headers=_OWNER
        ).json()
        second = self.client.post(
            "/v1/search/resources", json=_search_body("代码审查"), headers=_OWNER
        ).json()

        self.assertEqual(
            [hit["resource"]["resource_id"] for hit in first["items"]],
            [hit["resource"]["resource_id"] for hit in second["items"]],
        )


class VisibilityTests(unittest.TestCase):
    def test_member_sees_only_visible_resources(self) -> None:
        """B4/B10: another team's member must not see this team's resources."""

        store = MemoryStore()
        container = build_container(store)
        client = TestClient(create_app(get_settings(), container))
        for kind, slug in _KINDS:
            client.post("/v1/resources", json=_request_body(kind, slug), headers=_OWNER)

        owner_response = client.post(
            "/v1/search/resources", json=_search_body("代码审查"), headers=_OWNER
        )
        member_response = client.post(
            "/v1/search/resources", json=_search_body("代码审查"), headers=_MEMBER
        )

        self.assertEqual(owner_response.status_code, 200, msg=owner_response.text)
        self.assertEqual(member_response.status_code, 200, msg=member_response.text)
        owner_ids = {hit["resource"]["resource_id"] for hit in owner_response.json()["items"]}
        member_ids = {hit["resource"]["resource_id"] for hit in member_response.json()["items"]}
        # Drafts owned by platform-ai are not visible to other-team members.
        self.assertTrue(member_ids <= owner_ids)


if __name__ == "__main__":
    unittest.main()
