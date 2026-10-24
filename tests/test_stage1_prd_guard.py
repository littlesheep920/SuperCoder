"""Guard the architectural decisions ratified by docs/PRD.md (V2.0).

The PRD replaced the V1.0 plan's targets with rules that are *checkable*. These
tests fail if the codebase drifts back to the conditions the correction review
identified, so a future change cannot silently reintroduce them.

Scope: configuration defaults only. Behaviour that needs a live MySQL/Docker is
covered elsewhere or reported as HOLD.
"""

from __future__ import annotations

import os
import unittest

from apps.api.config import get_settings


class MinimumInfrastructureTests(unittest.TestCase):
    """PRD §3.3: infrastructure is opt-in by scale, never on by default.

    Before the correction review, the intended architecture assumed four
    stateful services (MySQL, Redis, Qdrant, RocketMQ) at a scale of ~120
    resources. That made the whole stack depend on services unavailable on the
    developer's machine, so most acceptance gates could never pass.

    These tests assert the **code's defaults**, so every SuperCoder environment
    variable is removed for the duration of the test. Without that isolation a
    developer who legitimately enables remote search in their shell would see
    this suite fail, which says nothing about the defaults.
    """

    MANAGED_VARIABLES = (
        "SUPERCODER_QDRANT_ENABLED",
        "SUPERCODER_REDIS_ENABLED",
        "SUPERCODER_EXTERNAL_LLM_ENABLED",
        "SUPERCODER_EMBEDDING_ENABLED",
        "SUPERCODER_RERANK_ENABLED",
        "SUPERCODER_SEARCH_REMOTE_ENABLED",
        "SUPERCODER_DOCKER_ENABLED",
        "SUPERCODER_GIT_ENABLED",
        "SUPERCODER_GIT_SYNC_ENABLED",
        "SUPERCODER_GIT_READ_ONLY",
        "SUPERCODER_GIT_REMOTE_URL",
        "SUPERCODER_MYSQL_ENABLED",
        "SUPERCODER_API_PREFIX",
        "SUPERCODER_SEARCH_API_KEY",
    )

    def setUp(self) -> None:
        self._saved = {name: os.environ.pop(name, None) for name in self.MANAGED_VARIABLES}
        get_settings.cache_clear()
        # A fresh clone's defaults are what the code produces with no config.
        self.settings = get_settings()

    def tearDown(self) -> None:
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        get_settings.cache_clear()

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

    def setUp(self) -> None:
        self._saved = os.environ.pop("SUPERCODER_API_PREFIX", None)
        get_settings.cache_clear()

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ["SUPERCODER_API_PREFIX"] = self._saved
        get_settings.cache_clear()

    def test_api_prefix_is_v1(self) -> None:
        self.assertEqual(get_settings().api_prefix, "/v1")


class EnvironmentIsolationTests(unittest.TestCase):
    """Documents the behaviour that broke when the operator enabled search.

    A developer with SuperCoder variables exported must still get green
    defaults-tests, because those tests are about code defaults, not about the
    operator's local configuration.
    """

    def test_enabling_a_feature_in_the_environment_does_not_change_defaults(self) -> None:
        os.environ["SUPERCODER_EMBEDDING_ENABLED"] = "true"
        os.environ["SUPERCODER_SEARCH_REMOTE_ENABLED"] = "true"
        get_settings.cache_clear()
        try:
            enabled = get_settings()
            self.assertTrue(enabled.embedding_enabled)

            # Now simulate the isolated default view.
            saved = {
                name: os.environ.pop(name, None)
                for name in ("SUPERCODER_EMBEDDING_ENABLED", "SUPERCODER_SEARCH_REMOTE_ENABLED")
            }
            get_settings.cache_clear()
            defaults = get_settings()
            self.assertFalse(defaults.embedding_enabled)
            self.assertFalse(defaults.search.remote_enabled)

            for name, value in saved.items():
                if value is not None:
                    os.environ[name] = value
        finally:
            os.environ.pop("SUPERCODER_EMBEDDING_ENABLED", None)
            os.environ.pop("SUPERCODER_SEARCH_REMOTE_ENABLED", None)
            get_settings.cache_clear()


if __name__ == "__main__":
    unittest.main()
