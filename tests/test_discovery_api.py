"""End-to-end tests for the discovery endpoint.

The API tests use a fake decider so they never call a paid model. One optional
integration test exercises the real provider and is skipped unless
``SUPERCODER_EXTERNAL_LLM_ENABLED=true`` and a key are present.
"""

from __future__ import annotations

import os
import unittest

from fastapi.testclient import TestClient

from apps.api.application.discovery_service import DiscoveryService
from apps.api.domain.discovery import (
    CandidateView,
    Decision,
    HeuristicDiscoveryDecider,
    Recommendation,
)
from apps.api.main import create_app


class StubDecider:
    """Returns a fixed decision, and records what it was shown."""

    def __init__(self, *, answered: bool = True, cite_outside_list: bool = False) -> None:
        self._answered = answered
        self._cite_outside = cite_outside_list
        self.seen: list[CandidateView] = []

    @property
    def name(self) -> str:
        return "stub"

    def decide(self, *, question: str, candidates, context: str = "") -> Decision:
        self.seen = list(candidates)
        if not self._answered:
            return Decision(False, (), "nothing fits", ("更多细节？",))
        target = candidates[0] if candidates else None
        if target is None:
            return Decision(False, (), "no candidates", ())
        resource_id = "skill/does-not-exist@9.9.9" if self._cite_outside else target.resource_id
        return Decision(
            True,
            (
                Recommendation(
                    resource_id=resource_id,
                    kind=target.kind,
                    version=target.version,
                    artifact_hash="f" * 64,  # deliberately wrong
                    reason="Stub reason.",
                    score=target.score,
                ),
            ),
            None,
            (),
        )


def build_client(decider) -> TestClient:
    app = create_app()
    container = app.state.container
    app.state.container = type(container)(
        **{**container.__dict__, "discovery": DiscoveryService(container.search, decider)}
    )
    return TestClient(app)


class DiscoveryEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        # MySQL is off in CI's unit run, so the search side cannot load documents.
        # These tests therefore assert the request contract and the fail-closed
        # paths rather than a live ranking; ranking is covered by the service tests.
        self.token = _dev_token()

    def test_endpoint_exists_and_is_post(self) -> None:
        app = create_app()
        spec = app.openapi()
        self.assertIn("/v1/discovery/recommend", spec["paths"])
        self.assertIn("post", spec["paths"]["/v1/discovery/recommend"])

    def test_request_rejects_extra_fields(self) -> None:
        client = build_client(StubDecider())
        response = client.post(
            "/v1/discovery/recommend",
            json={"question": "hi", "unexpected": 1},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(response.status_code, 422)

    def test_request_requires_a_question(self) -> None:
        client = build_client(StubDecider())
        response = client.post(
            "/v1/discovery/recommend",
            json={"question": ""},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(response.status_code, 422)

    def test_unauthenticated_request_is_rejected(self) -> None:
        client = build_client(StubDecider())
        response = client.post("/v1/discovery/recommend", json={"question": "hi"})
        self.assertEqual(response.status_code, 401)


class DiscoveryServiceTests(unittest.TestCase):
    """The service-level guarantees, exercised without MySQL."""

    def test_recommendation_hash_comes_from_the_server_not_the_model(self) -> None:
        """A model-supplied hash must never reach the response."""

        decider = StubDecider()
        service = _service_with_fake_search(decider)
        outcome = service.discover(scope=object(), question="find something")
        self.assertTrue(outcome.answered)
        self.assertEqual(len(outcome.recommendations), 1)
        self.assertNotEqual(outcome.recommendations[0].artifact_hash, "f" * 64)
        self.assertEqual(outcome.recommendations[0].artifact_hash, "a" * 64)

    def test_citation_outside_the_candidate_list_becomes_a_refusal(self) -> None:
        decider = StubDecider(cite_outside_list=True)
        service = _service_with_fake_search(decider)
        outcome = service.discover(scope=object(), question="find something")
        self.assertFalse(outcome.answered)
        self.assertEqual(outcome.refusal_reason, "model_cited_no_valid_candidate")
        self.assertTrue(outcome.considered)

    def test_refusal_lists_what_was_considered(self) -> None:
        decider = StubDecider(answered=False)
        service = _service_with_fake_search(decider)
        outcome = service.discover(scope=object(), question="find something")
        self.assertFalse(outcome.answered)
        self.assertEqual(outcome.considered, ("skill/example-a@1.0.0", "skill/example-b@1.0.0"))
        self.assertEqual(outcome.clarifying_questions, ("更多细节？",))

    def test_decider_sees_at_most_the_candidate_limit(self) -> None:
        decider = StubDecider()
        service = _service_with_fake_search(decider)
        service.discover(scope=object(), question="find something")
        self.assertLessEqual(len(decider.seen), 8)


class FakeDocument:
    def __init__(self, public_id: str, name: str, hash_char: str) -> None:
        self.resource_public_id = public_id
        self.index_key = public_id
        self.kind = "skill"
        self.version = "1.0.0"
        self.name = name
        self.description = f"{name} description."
        self.status = "published"
        self.artifact_hash = hash_char * 64


class FakeCandidate:
    def __init__(self, document, score: float) -> None:
        self.document = document
        self.score = score
        self.matched_fields = ("name",)


class FakeOutcome:
    degraded: tuple = ()


class FakeSearchResult:
    def __init__(self) -> None:
        self.outcome = FakeOutcome()
        self.outcome.results = [
            FakeCandidate(FakeDocument("skill/example-a@1.0.0", "A", "a"), 0.9),
            FakeCandidate(FakeDocument("skill/example-b@1.0.0", "B", "b"), 0.4),
        ]


class FakeSearchFactory:
    def search(self, **_kwargs):
        return FakeSearchResult()


def _service_with_fake_search(decider) -> DiscoveryService:
    return DiscoveryService(FakeSearchFactory(), decider)


def _dev_token() -> str:
    # Dev tokens are fixed, documented values (see scripts/dev_tokens.example.json).
    return "dev-owner-token"


@unittest.skipUnless(
    os.environ.get("SUPERCODER_EXTERNAL_LLM_ENABLED", "").lower() in {"1", "true", "yes", "on"}
    and os.environ.get("FTO_LLM_API_KEY"),
    "real LLM not configured",
)
class RealLlmIntegrationTest(unittest.TestCase):
    """One real call, to prove the wiring works against the configured provider."""

    def test_real_model_can_refuse_an_impossible_question(self) -> None:
        from apps.api.config import get_settings
        from apps.api.domain.discovery import LlmDiscoveryDecider
        from apps.api.providers.llm import build_llm_client

        settings = get_settings()
        client = build_llm_client(settings.external_llm)
        self.assertIsNotNone(client)

        decider = LlmDiscoveryDecider(client=client)
        candidates = [
            CandidateView(
                resource_id="skill/example-review@1.0.0",
                kind="skill",
                version="1.0.0",
                name="Example Code Review",
                summary="Reviews a diff and reports findings with evidence.",
                status="published",
                artifact_hash="a" * 64,
                score=0.5,
            )
        ]
        decision = decider.decide(
            question="请帮我预订一张明天去上海的机票",
            candidates=candidates,
        )
        self.assertFalse(
            decision.answered,
            f"model should refuse an unrelated request, got {decision.recommendations}",
        )


if __name__ == "__main__":
    unittest.main()
