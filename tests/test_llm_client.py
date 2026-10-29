"""Secret handling in the LLM client.

The rule from the PRD: a disabled component must never read its secret, and a secret
must never appear in an error or a log line. The search providers have tests for
this; the LLM client did not, and it is the newer code path.
"""

from __future__ import annotations

import unittest

from apps.api.config import ExternalModelSettings, Secret
from apps.api.providers.llm import (
    LlmError,
    LlmUnavailable,
    OpenAiCompatibleClient,
    build_llm_client,
)


def settings(**overrides) -> ExternalModelSettings:
    base = dict(
        enabled=True,
        provider="deepseek",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        api_key=Secret("placeholder-value-not-a-key"),
        allow_project_data=False,
        request_timeout_seconds=30,
        max_output_tokens=4096,
        temperature=0.0,
    )
    base.update(overrides)
    return ExternalModelSettings(**base)


class DisabledClientTests(unittest.TestCase):
    def test_build_returns_none_when_disabled(self) -> None:
        self.assertIsNone(build_llm_client(settings(enabled=False)))

    def test_disabled_client_reports_unavailable(self) -> None:
        client = OpenAiCompatibleClient(settings(enabled=False))
        self.assertFalse(client.available)
        with self.assertRaises(LlmUnavailable):
            client.complete(system="s", user="u")

    def test_missing_key_is_unavailable_even_when_enabled(self) -> None:
        client = OpenAiCompatibleClient(settings(api_key=Secret(None)))
        self.assertFalse(client.available)

    def test_missing_model_is_unavailable(self) -> None:
        client = OpenAiCompatibleClient(settings(model=""))
        self.assertFalse(client.available)

    def test_name_reports_disabled_without_a_model(self) -> None:
        self.assertEqual(OpenAiCompatibleClient(settings(model="")).name, "disabled")


class SecretLeakTests(unittest.TestCase):
    def test_secret_repr_is_redacted(self) -> None:
        self.assertIn("redacted", repr(Secret("placeholder-value-not-a-key")))

    def test_client_repr_does_not_leak_the_key(self) -> None:
        """A dataclass repr or f-string must not print the key."""

        client = OpenAiCompatibleClient(settings())
        self.assertNotIn("placeholder-value-not-a-key", repr(client))
        self.assertNotIn("placeholder-value-not-a-key", str(client))

    def test_unavailable_error_carries_no_key(self) -> None:
        client = OpenAiCompatibleClient(settings(enabled=False))
        with self.assertRaises(LlmUnavailable) as caught:
            client.complete(system="s", user="u")
        self.assertNotIn("placeholder-value-not-a-key", str(caught.exception))

    def test_error_reason_is_a_stable_code(self) -> None:
        error = LlmError("llm_http_error", status=500)
        self.assertEqual(error.reason, "llm_http_error")
        self.assertEqual(error.status, 500)
        self.assertNotIn("placeholder-", str(error))


class WarmConnectionTests(unittest.TestCase):
    def test_warming_a_disabled_client_is_a_noop(self) -> None:
        """Warming must never break startup, even with a bad base URL."""

        client = OpenAiCompatibleClient(settings(enabled=False))
        client.warm_connection()  # must not raise


if __name__ == "__main__":
    unittest.main()
