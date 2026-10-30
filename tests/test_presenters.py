"""Presenters must produce models the schema actually accepts.

The dependency mapping used to emit `{}` placeholders, which `ResolvedDependency`
rejects (extra="forbid", several required fields). The failure showed up only as a
500 on a version read, so it is tested here directly.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from datetime import datetime, timezone

from apps.api.domain.models import LifecycleState
from apps.api.presenters import to_version_detail
from apps.api.schemas.resources import ResolvedDependency

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


@dataclass
class StubVersion:
    public_id: str = "01HZZZZZZZZZZZZZZZZZZZZZZZ"
    resource_public_id: str = "01HYYYYYYYYYYYYYYYYYYYYYYY"
    identifier: object = field(default_factory=lambda: _Identifier())
    schema_version: str = "1"
    name: str = "Probe"
    description: str = "Example resource for the SuperCoder protocol."
    owner_team: str = "platform-ai"
    entrypoint: str = "SKILL.md"
    aliases: tuple = ()
    manifest: dict = field(default_factory=lambda: {"kind": "skill"})
    artifact_hash: str = "sha256:" + "a" * 64
    manifest_snapshot_hash: str = "sha256:" + "b" * 64
    status: LifecycleState = LifecycleState.DRAFT
    status_changed_at: object = field(default_factory=lambda: NOW)
    status_changed_by: str = "user-owner"
    status_reason: str = "created"
    row_version: int = 1
    created_by: str = "user-owner"
    created_at: object = field(default_factory=lambda: NOW)
    updated_at: object = field(default_factory=lambda: NOW)
    validated_at: object = None
    approved_at: object = None
    published_at: object = None
    deprecated_at: object = None
    retire_after: object = None
    retired_at: object = None
    replacement_version_public_id: object = None


class _Identifier:
    raw = "1.0.0"

    def __str__(self) -> str:  # pragma: no cover - defensive
        return self.raw




@dataclass
class StubDependency:
    dependency_kind: str = "skill"
    dependency_slug: str = "example-base"
    version_constraint: str = "^1.0.0"
    source: str = "internal"
    ordinal: int = 0
    is_optional: bool = False
    resolution_status: str = "resolved"
    row_version: int = 1
    purpose: str | None = "provides the base behaviour"
    resolved_version_public_id: str | None = "01HXXXXXXXXXXXXXXXXXXXXXXX"
    external_locator: str | None = None
    external_digest: str | None = None
    resolution_error_code: str | None = None


class DependencyPresentationTests(unittest.TestCase):
    def test_dependencies_are_presented_with_real_fields(self) -> None:
        version = to_version_detail(StubVersion(), dependencies=(StubDependency(),))
        self.assertEqual(len(version.dependencies), 1)
        dependency = version.dependencies[0]
        self.assertIsInstance(dependency, ResolvedDependency)
        self.assertEqual(dependency.kind, "skill")
        self.assertEqual(dependency.slug, "example-base")
        self.assertEqual(dependency.version, "^1.0.0")
        self.assertEqual(dependency.resolution_status, "resolved")
        self.assertEqual(dependency.ordinal, 0)

    def test_no_dependencies_is_still_valid(self) -> None:
        version = to_version_detail(StubVersion(), dependencies=())
        self.assertEqual(version.dependencies, [])


if __name__ == "__main__":
    unittest.main()
