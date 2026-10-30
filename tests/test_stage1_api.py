"""End-to-end API tests using in-memory repositories and the real Git provider.

These prove the control plane is runnable and enforce the governance/authority
rules.  They use in-memory test doubles for MySQL and therefore cannot prove
MySQL collation, locking, or concurrency; that evidence is reported HOLD.
"""

from __future__ import annotations

import unittest

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
from tests.support.memory import MemoryStore, make_uow_factory

_OWNER = {"Authorization": "Bearer dev-owner-token"}
_REVIEWER = {"Authorization": "Bearer dev-reviewer-token"}
_PUBLISHER = {"Authorization": "Bearer dev-publisher-token"}
_VALIDATOR = {"Authorization": "Bearer dev-validator-token"}
_MEMBER = {"Authorization": "Bearer dev-member-token"}

_KINDS = [
    (ResourceKind.SKILL, "demo-code-review"),
    (ResourceKind.MCP, "demo-local-catalog"),
    (ResourceKind.PROMPT, "demo-bugfix"),
    (ResourceKind.AGENT, "demo-planner"),
    (ResourceKind.WORKFLOW, "demo-review-flow"),
    (ResourceKind.TOOL, "demo-text-stats"),
]


def build_container(store: MemoryStore) -> Container:
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
        search=SearchServiceFactory(uow_factory, loader),
        mysql_engine=None,
    )


def _request_body(kind: ResourceKind, slug: str) -> dict:
    loader = ManifestLoader(GitProvider(".", read_only=True))
    loaded = loader.load(kind, slug)
    artifacts = []
    for path, content in sorted(loaded.files.items()):
        normalized = normalize_utf8_text(content)
        artifacts.append(
            {"path": path, "digest": sha256_digest(normalized), "size_bytes": len(normalized)}
        )
    return {"manifest": loaded.manifest, "artifacts": artifacts, "reason": "stage1 test registration"}


class Stage1ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        store = MemoryStore()
        self.container = build_container(store)
        self.client = TestClient(create_app(get_settings(), self.container))
        self.registered = {}

    def test_health(self):
        response = self.client.get("/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertIn("components", response.json())

    def test_register_all_demo_resources(self):
        for kind, slug in _KINDS:
            response = self.client.post("/v1/resources", json=_request_body(kind, slug), headers=_OWNER)
            self.assertEqual(response.status_code, 201, msg=f"{slug}: {response.text}")
            body = response.json()
            self.registered[slug] = body
            self.assertEqual(body["slug"], slug)
        listing = self.client.get("/v1/resources", headers=_OWNER)
        self.assertEqual(listing.status_code, 200)
        self.assertGreaterEqual(len(listing.json()["items"]), 6)

    def test_duplicate_identity_conflict(self):
        kind, slug = _KINDS[0]
        self.client.post("/v1/resources", json=_request_body(kind, slug), headers=_OWNER)
        response = self.client.post("/v1/resources", json=_request_body(kind, slug), headers=_OWNER)
        self.assertEqual(response.status_code, 409)

    def test_full_lifecycle_flow(self):
        kind, slug = _KINDS[-1]  # demo-text-stats tool
        created = self.client.post("/v1/resources", json=_request_body(kind, slug), headers=_OWNER).json()
        resource_id = created["resource_id"]
        version_id = created["version"]["version_id"]
        version = created["version"]

        # owner submits validation
        resp = self.client.post(
            f"/v1/resource-versions/{version_id}/transitions",
            json={"target_status": "validating", "reason": "run validation", "row_version": version["row_version"], "evidence_refs": []},
            headers={**_OWNER, "If-Match": f'"{version_id}:{version["row_version"]}"', "Idempotency-Key": f"validate-{slug}"},
        )
        self.assertEqual(resp.status_code, 202, msg=resp.text)
        job_id = resp.json()["job"]["job_id"]
        self.assertIsNotNone(job_id)

        # worker completes validation
        from apps.api.domain.models import ActorIdentity, ActorType
        from apps.api.auth import AuthContext
        from apps.api.schemas.resources import LifecycleTransitionRequest

        validator_auth = AuthContext(
            actor=ActorIdentity("worker", ActorType.WORKER),
            grants=(), teams=frozenset(), ui_roles_by_team={},
            organization_member=True, governance_roles=frozenset({"validator"}),
        )
        validating_version = self.container.versions.get(validator_auth.actor, version_id, validator_auth.visibility_scope())
        result = self.container.governance.transition(
            auth=validator_auth,
            version_public_id=version_id,
            request=LifecycleTransitionRequest(
                target_status="pending_review",
                reason="validation passed",
                row_version=validating_version.row_version,
                evidence_refs=[],
            ),
            correlation_id="test",
        )
        self.assertEqual(result.version.status.value, "pending_review")

        # reviewer approves
        detail = self.client.get(f"/v1/resource-versions/{version_id}", headers=_REVIEWER).json()
        review = self.client.post(
            f"/v1/resource-versions/{version_id}/reviews",
            json={
                "review_type": "governance",
                "decision": "approve",
                "reviewer_role": "reviewer",
                "artifact_hash": detail["artifact_hash"],
                "manifest_snapshot_hash": detail["manifest_snapshot_hash"],
                "runtime_scope": ["kiro"],
                "validation_evidence_ref": "evidence/test",
                "permission_summary": {"declared": []},
                "dependency_summary": {"resolved": True, "count": 0},
                "evaluation_refs": [],
                "rationale": "demo approval",
            },
            headers={**_REVIEWER, "Idempotency-Key": f"review-{slug}"},
        )
        self.assertEqual(review.status_code, 201, msg=review.text)

        # reviewer transitions pending_review -> approved
        pending = self.client.get(f"/v1/resource-versions/{version_id}", headers=_REVIEWER).json()
        approved = self.client.post(
            f"/v1/resource-versions/{version_id}/transitions",
            json={"target_status": "approved", "reason": "review approved", "row_version": pending["row_version"], "evidence_refs": []},
            headers={**_REVIEWER, "If-Match": f'"{version_id}:{pending["row_version"]}"', "Idempotency-Key": f"approve-{slug}"},
        )
        self.assertEqual(approved.status_code, 200, msg=approved.text)

        # publisher publishes
        approved_detail = self.client.get(f"/v1/resource-versions/{version_id}", headers=_PUBLISHER).json()
        published = self.client.post(
            f"/v1/resource-versions/{version_id}/transitions",
            json={"target_status": "published", "reason": "publish", "row_version": approved_detail["row_version"], "evidence_refs": []},
            headers={**_PUBLISHER, "If-Match": f'"{version_id}:{approved_detail["row_version"]}"', "Idempotency-Key": f"publish-{slug}"},
        )
        self.assertEqual(published.status_code, 200, msg=published.text)
        self.assertEqual(published.json()["version"]["status"], "published")

        # deprecate
        pub_detail = self.client.get(f"/v1/resource-versions/{version_id}", headers=_PUBLISHER).json()
        deprecated = self.client.post(
            f"/v1/resource-versions/{version_id}/transitions",
            json={"target_status": "deprecated", "reason": "superseded", "row_version": pub_detail["row_version"], "evidence_refs": []},
            headers={**_PUBLISHER, "If-Match": f'"{version_id}:{pub_detail["row_version"]}"', "Idempotency-Key": f"deprecate-{slug}"},
        )
        self.assertEqual(deprecated.status_code, 200, msg=deprecated.text)
        self.assertEqual(deprecated.json()["version"]["status"], "deprecated")

    def test_member_cannot_publish(self):
        kind, slug = _KINDS[0]
        created = self.client.post("/v1/resources", json=_request_body(kind, slug), headers=_OWNER).json()
        version_id = created["version"]["version_id"]
        detail = self.client.get(f"/v1/resource-versions/{version_id}", headers=_MEMBER).json()
        resp = self.client.post(
            f"/v1/resource-versions/{version_id}/transitions",
            json={"target_status": "validating", "reason": "try", "row_version": detail["row_version"], "evidence_refs": []},
            headers={**_MEMBER, "If-Match": f'"{version_id}:{detail["row_version"]}"'},
        )
        self.assertIn(resp.status_code, (403, 404))

    def test_audit_is_appended(self):
        kind, slug = _KINDS[0]
        self.client.post("/v1/resources", json=_request_body(kind, slug), headers=_OWNER)
        response = self.client.get("/v1/audit", headers=_OWNER)
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json()["items"]), 1)
    def test_stale_lifecycle_row_version_has_no_side_effect(self):
        kind, slug = _KINDS[0]
        created = self.client.post("/v1/resources", json=_request_body(kind, slug), headers=_OWNER).json()
        version = created["version"]
        stale = version["row_version"] + 1
        response = self.client.post(
            f"/v1/resource-versions/{version['version_id']}/transitions",
            json={"target_status": "validating", "reason": "stale request", "row_version": stale, "evidence_refs": []},
            headers={**_OWNER, "X-Correlation-Id": "stale-request", "If-Match": f'"{version["version_id"]}:{stale}"'},
        )
        self.assertEqual(response.status_code, 412, msg=response.text)
        jobs = self.client.get("/v1/jobs?correlation_id=stale-request", headers=_OWNER)
        self.assertEqual(jobs.status_code, 200)
        self.assertEqual(jobs.json()["items"], [])

    def test_cursor_pagination_traverses_without_duplicates(self):
        for kind, slug in _KINDS[:3]:
            response = self.client.post("/v1/resources", json=_request_body(kind, slug), headers=_OWNER)
            self.assertEqual(response.status_code, 201, msg=response.text)
        first = self.client.get("/v1/resources?limit=2", headers=_OWNER)
        self.assertEqual(first.status_code, 200)
        first_ids = {item["resource_id"] for item in first.json()["items"]}
        cursor = first.json()["page"]["next_cursor"]
        self.assertIsNotNone(cursor)

        second = self.client.get(f"/v1/resources?limit=2&cursor={cursor}", headers=_OWNER)
        self.assertEqual(second.status_code, 200)
        second_ids = {item["resource_id"] for item in second.json()["items"]}
        self.assertTrue(first_ids.isdisjoint(second_ids))
        self.assertEqual(len(first_ids | second_ids), 3)

        malformed = self.client.get("/v1/resources?cursor=not-a-valid-cursor", headers=_OWNER)
        self.assertEqual(malformed.status_code, 400)

    def test_update_draft_applies_manifest_patch(self):
        """A manifest-only edit with the same version number updates in place.

        The earlier implementation always appended a version row, so this case
        collided with uq_rv_resource_version and failed with a MySQL integrity error
        (a 500 in production). The version number is what decides: unchanged means
        "correct this draft", changed means "new version".
        """

        kind, slug = _KINDS[0]
        created = self.client.post("/v1/resources", json=_request_body(kind, slug), headers=_OWNER).json()
        old_version = created["version"]
        version_id = old_version["version_id"]
        response = self.client.patch(
            f"/v1/resource-versions/{version_id}",
            json={
                "manifest": {"description": "patched description"},
                "row_version": old_version["row_version"],
                "reason": "update draft description",
            },
            headers={**_OWNER, "If-Match": f'"{version_id}:{old_version["row_version"]}"'},
        )
        self.assertEqual(response.status_code, 200, msg=response.text)
        patched = response.json()

        # Same version row, new content and a bumped row_version.
        self.assertEqual(patched["version_id"], version_id)
        self.assertEqual(patched["version"], old_version["version"])
        self.assertEqual(patched["description"], "patched description")
        self.assertNotEqual(patched["artifact_hash"], old_version["artifact_hash"])
        self.assertEqual(patched["row_version"], old_version["row_version"] + 1)

        # The change is persisted, not just echoed back.
        current = self.client.get(f"/v1/resource-versions/{version_id}", headers=_OWNER)
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json()["description"], "patched description")

    def test_update_draft_with_a_new_version_creates_a_version_row(self):
        """A different version number still produces a new, separate version."""

        kind, slug = _KINDS[0]
        created = self.client.post("/v1/resources", json=_request_body(kind, slug), headers=_OWNER).json()
        old_version = created["version"]
        version_id = old_version["version_id"]
        response = self.client.patch(
            f"/v1/resource-versions/{version_id}",
            json={
                "manifest": {"version": "1.0.1"},
                "row_version": old_version["row_version"],
                "reason": "bump the version",
            },
            headers={**_OWNER, "If-Match": f'"{version_id}:{old_version["row_version"]}"'},
        )
        self.assertEqual(response.status_code, 200, msg=response.text)
        bumped = response.json()
        self.assertNotEqual(bumped["version_id"], version_id)
        self.assertEqual(bumped["version"], "1.0.1")

        # The original stays as immutable history.
        original = self.client.get(f"/v1/resource-versions/{version_id}", headers=_OWNER)
        self.assertEqual(original.status_code, 200)
        self.assertEqual(original.json()["version"], old_version["version"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
