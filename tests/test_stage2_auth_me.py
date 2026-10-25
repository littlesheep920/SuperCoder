"""Tests for GET /v1/auth/me.

The endpoint must:
* require authentication;
* return the caller's identity from the token alone, without touching MySQL, so
  the console can decide what to render even while the database is down;
* report capabilities that match what the server would actually allow.
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from apps.api.config import get_settings
from apps.api.domain.capabilities import Capability, capabilities_for
from apps.api.main import create_app

MEDIA = "application/json"


def _client() -> TestClient:
    return TestClient(create_app(get_settings()))


def _token(role: str) -> str:
    """Pick a development token that carries the requested governance role."""

    import json
    from pathlib import Path

    settings = get_settings()
    path = Path(settings.dev_auth_tokens_file)
    if not path.is_file():
        path = path.with_name(f"{path.stem}.example{path.suffix}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    for entry in payload["tokens"]:
        if role in entry.get("governance_roles", []):
            return entry["token"]
    raise AssertionError(f"no development token carries role {role!r}")


class AuthenticationTests(unittest.TestCase):
    def test_missing_token_is_rejected(self) -> None:
        response = _client().get("/v1/auth/me")

        self.assertEqual(response.status_code, 401)

    def test_unknown_token_is_rejected(self) -> None:
        response = _client().get(
            "/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
        )

        self.assertEqual(response.status_code, 401)


class IdentityPayloadTests(unittest.TestCase):
    def test_response_carries_identity_and_capabilities(self) -> None:
        client = _client()
        # Any known token works; availability of a role is verified separately.
        token = _token("owner")

        response = client.get(
            "/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        for field in (
            "subject_id",
            "display_name",
            "teams",
            "organization_member",
            "governance_roles",
            "capabilities",
        ):
            self.assertIn(field, body)

    def test_read_capabilities_are_always_present(self) -> None:
        token = _token("owner")

        body = _client().get(
            "/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        ).json()

        for capability in (
            Capability.RESOURCE_READ,
            Capability.SEARCH,
            Capability.AUDIT_READ,
        ):
            self.assertIn(capability.value, body["capabilities"])

    def test_capabilities_are_sorted_and_unique(self) -> None:
        token = _token("owner")

        caps = _client().get(
            "/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        ).json()["capabilities"]

        self.assertEqual(caps, sorted(caps))
        self.assertEqual(len(caps), len(set(caps)))

    def test_reported_capabilities_match_the_derivation(self) -> None:
        """A10: the endpoint must not post-process what the domain produces."""

        from apps.api.auth import DevTokenAuth

        settings = get_settings()
        auth = DevTokenAuth(settings.dev_auth_tokens_file)
        token = _token("owner")
        expected = sorted(
            capability.value for capability in capabilities_for(auth.resolve(token))
        )

        reported = _client().get(
            "/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        ).json()["capabilities"]

        self.assertEqual(reported, expected)

    def test_response_does_not_leak_the_token(self) -> None:
        """The identity payload must never echo the credential back."""

        token = _token("owner")

        text = _client().get(
            "/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        ).text

        self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
