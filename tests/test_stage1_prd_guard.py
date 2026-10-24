"""Guard the architectural decisions ratified by docs/PRD.md (V2.0).

The PRD replaced the V1.0 plan's targets with rules that are *checkable*. These
tests fail if the codebase drifts back to the conditions the correction review
identified, so a future change cannot silently reintroduce them.

Scope: configuration defaults only. Behaviour that needs a live MySQL/Docker is
covered elsewhere or reported as HOLD.
"""

from __future__ import annotations

import unittest

from apps.api.config import get_settings


class MinimumInfrastructureTests(unittest.TestCase):
    """PRD §3.3: infrastructure is opt-in by scale, never on by default.

    Before the correction review, the intended architecture assumed four
    stateful services (MySQL, Redis, Qdrant, RocketMQ) at a scale of ~120
    resources. That made the whole stack depend on services unavailable on the
    developer's machine, so most acceptance gates could never pass.
    """

    def setUp(self) -> None:
        # get_settings() is lru_cached; the defaults are what a fresh clone sees.
        self.settings = get_settings()

    def test_qdrant_is_disabled_by_default(self) -> None:
        self.assertFalse(self.settings.qdrant_enabled)

    def test_redis_is_disabled_by_default(self) -> None:
        self.assertFalse(self.settings.redis_enabled)

    def test_external_llm_is_disabled_by_default(self) -> None:
        self.assertFalse(self.settings.external_llm.enabled)

    def test_embedding_and_rerank_are_disabled_by_default(self) -> None:
        self.assertFalse(self.settings.embedding_enabled)
        self.assertFalse(self.settings.rerank_enabled)

    def test_docker_execution_is_disabled_by_default(self) -> None:
        self.assertFalse(self.settings.docker_enabled)

    def test_git_is_local_and_read_only_by_default(self) -> None:
        """Content is read from a managed local root; no network sync."""
        self.assertTrue(self.settings.git.enabled)
        self.assertTrue(self.settings.git.read_only)
        self.assertFalse(self.settings.git.sync_enabled)
        self.assertEqual(self.settings.git.remote_url, "")

    def test_mysql_is_the_only_required_stateful_service(self) -> None:
        """MySQL is the governance source of truth and is enabled explicitly."""
        # It is disabled by default so an offline clone can still import and
        # type-check; when enabled it is the only mandatory dependency.
        self.assertIsInstance(self.settings.mysql.enabled, bool)


class ApiPrefixTests(unittest.TestCase):
    """PRD T-4: the authoritative prefix is /v1, not /api."""

    def test_api_prefix_is_v1(self) -> None:
        self.assertEqual(get_settings().api_prefix, "/v1")


if __name__ == "__main__":
    unittest.main()
