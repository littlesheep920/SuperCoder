"""Export service tests: only published versions leave, warnings travel with the artefact.

The corpus on disk is the source of truth for a resource's manifest and body, so the tests
create resources from that content rather than hand-writing a copy. A hand-written copy
would drift from the corpus and start testing a fiction.

The two rules asserted here were settled before implementation:
  1. an unpublished version is refused — exporting must not become a second way for
     unreviewed content to leave the platform;
  2. warnings are attached to the result — an export that silently drops a capability is
     the failure this feature exists to prevent.
"""

from __future__ import annotations

import hashlib
import io
import json
import pathlib
import unittest
import zipfile

from fastapi.testclient import TestClient

from apps.api.application.catalog import CatalogService
from apps.api.application.diffs import DiffService
from apps.api.application.export import ExportService
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
from apps.api.domain.adapters import available_targets
from apps.api.main import create_app
from apps.api.providers.git_repository import GitProvider
from apps.api.providers.manifest_loader import ManifestLoader
from tests.support.memory import MemoryStore, make_uow_factory

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORPUS = ROOT / "resources"

OWNER = {"Authorization": "Bearer dev-owner-token"}
REVIEWER = {"Authorization": "Bearer dev-reviewer-token"}
VALIDATOR = {"Authorization": "Bearer dev-validator-token"}
PUBLISHER = {"Authorization": "Bearer dev-publisher-token"}

#: A real corpus skill whose runtime list already includes claude-code.
SAMPLE = CORPUS / "skills" / "example-accessibility-audit"


def build_client() -> TestClient:
    """An app wired to in-memory repositories and the real Git provider.

    The export service is attached explicitly because `build_container` disables it when
    MySQL is off, which is correct in production but would leave this path untested.
    """

    settings = get_settings()
    git = GitProvider(settings.git.repository_root, read_only=True)
    loader = ManifestLoader(git)
    uow_factory = make_uow_factory(MemoryStore())
    snapshots = SnapshotService(loader)

    container = Container(
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
        export_service=ExportService(uow_factory, git, VersionQueryService(uow_factory)),
        mysql_engine=None,
    )

    app = create_app()
    app.state.container = container
    return TestClient(app)


def sample_manifest() -> dict:
    """The corpus manifest, with `status` reset to draft so it can be registered."""

    manifest = json.loads((SAMPLE / "resource.yaml").read_text(encoding="utf-8"))
    manifest["status"] = "draft"
    return manifest


def sample_artifact() -> dict:
    """The real SKILL.md digest and size.

    The create endpoint verifies the artifact against the file on disk, so an invented
    digest is refused with CONTENT_HASH_MISMATCH — which is the platform working as
    designed, not a test inconvenience.
    """

    import hashlib

    body = (SAMPLE / "SKILL.md").read_bytes()
    return {
        "path": "SKILL.md",
        "digest": "sha256:" + hashlib.sha256(body).hexdigest(),
        "size_bytes": len(body),
        "media_type": "text/markdown",
    }


class ExportTargetListTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = build_client()

    def test_targets_are_listed(self) -> None:
        response = self.client.get("/v1/export-targets", headers=OWNER)
        self.assertEqual(response.status_code, 200, response.text)
        identifiers = [item["identifier"] for item in response.json()["items"]]
        self.assertEqual(identifiers, [t.identifier for t in available_targets()])

    def test_each_target_states_its_directory(self) -> None:
        payload = self.client.get("/v1/export-targets", headers=OWNER).json()
        by_id = {item["identifier"]: item for item in payload["items"]}
        self.assertEqual(by_id["claude-code"]["skills_directory"], ".claude/skills")
        self.assertEqual(by_id["dsh"]["skills_directory"], ".dsh/skills")


class ExportFixture(unittest.TestCase):
    """Creates one registered version and offers a way to publish it."""

    def setUp(self) -> None:
        self.client = build_client()
        manifest = sample_manifest()
        created = self.client.post(
            "/v1/resources",
            json={
                "manifest": manifest,
                "artifacts": [sample_artifact()],
                "reason": "export test",
            },
            headers=OWNER,
        )
        self.assertEqual(created.status_code, 201, created.text)
        body = created.json()
        self.version_id = body["version"]["version_id"]
        self.resource_id = body["resource_id"]

    def transition(self, token: dict, target: str) -> None:
        current = self.client.get(
            f"/v1/resource-versions/{self.version_id}", headers=token
        ).json()
        self.client.post(
            f"/v1/resource-versions/{self.version_id}/transitions",
            json={
                "target_status": target,
                "reason": "export test",
                "row_version": current["row_version"],
            },
            headers={**token, "If-Match": f'"{self.version_id}:{current["row_version"]}"'},
        )

    def drive_to_published(self) -> None:
        self.transition(OWNER, "validating")
        self.transition(VALIDATOR, "pending_review")

        current = self.client.get(
            f"/v1/resource-versions/{self.version_id}", headers=OWNER
        ).json()
        review = self.client.post(
            f"/v1/resource-versions/{self.version_id}/reviews",
            json={
                "review_type": "governance",
                "decision": "approve",
                "reviewer_role": "reviewer",
                "artifact_hash": current["artifact_hash"],
                "manifest_snapshot_hash": current["manifest_snapshot_hash"],
                "runtime_scope": [
                    entry["runtime"] if isinstance(entry, dict) else entry
                    for entry in current["runtimes"]
                ],
                "validation_evidence_ref": f"version:{self.version_id}",
                "permission_summary": {},
                "dependency_summary": {},
                "evaluation_refs": [],
                "rationale": "export test",
            },
            headers=REVIEWER,
        )
        self.assertEqual(review.status_code, 201, review.text)

        self.transition(REVIEWER, "approved")
        self.transition(PUBLISHER, "published")

        state = self.client.get(
            f"/v1/resource-versions/{self.version_id}", headers=OWNER
        ).json()
        self.assertEqual(state["status"], "published", state)


class ExportRefusalTests(ExportFixture):
    """A version that has not been published must not be exported."""

    def test_a_draft_cannot_be_exported(self) -> None:
        response = self.client.get(
            f"/v1/resource-versions/{self.version_id}/export?target=claude-code", headers=OWNER
        )
        self.assertEqual(response.status_code, 409, response.text)

    def test_the_refusal_names_the_current_state(self) -> None:
        """A bare 409 leaves the caller guessing why their version is not exportable."""

        response = self.client.get(
            f"/v1/resource-versions/{self.version_id}/export?target=claude-code", headers=OWNER
        )
        self.assertIn("draft", response.json()["detail"])

    def test_an_unknown_target_is_not_found(self) -> None:
        response = self.client.get(
            f"/v1/resource-versions/{self.version_id}/export?target=nonexistent", headers=OWNER
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_a_missing_version_is_not_found(self) -> None:
        response = self.client.get(
            "/v1/resource-versions/01ARZ3NDEKTSV4RRFFQ69G5FAV/export?target=claude-code",
            headers=OWNER,
        )
        self.assertEqual(response.status_code, 404, response.text)


class ExportArchiveTests(ExportFixture):
    def export(self, target: str):
        response = self.client.get(
            f"/v1/resource-versions/{self.version_id}/export?target={target}", headers=OWNER
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def test_published_version_exports_a_zip(self) -> None:
        self.drive_to_published()
        response = self.export("claude-code")
        self.assertEqual(response.headers["content-type"], "application/zip")
        names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
        self.assertTrue(any(name.endswith("SKILL.md") for name in names), names)

    def test_the_archive_reports_its_digest(self) -> None:
        self.drive_to_published()
        response = self.export("claude-code")
        self.assertIn("x-export-digest", response.headers)
        self.assertTrue(response.headers["x-export-digest"].startswith("sha256:"))

    def test_two_exports_are_byte_identical(self) -> None:
        """A verifiable export must not vary between calls.

        Zip metadata is pinned to 1980-01-01 for this reason: a timestamp would make every
        archive different and the digest meaningless.
        """

        self.drive_to_published()
        self.assertEqual(self.export("dsh").content, self.export("dsh").content)

    def test_warnings_are_surfaced_in_a_header(self) -> None:
        self.drive_to_published()
        response = self.export("chatgpt")
        self.assertIn("x-export-warning-count", response.headers)
        # ChatGPT always reports the manual upload step, so at least one is guaranteed.
        self.assertGreaterEqual(int(response.headers["x-export-warning-count"]), 1)
        self.assertIn("upload_is_manual", response.headers.get("x-export-warnings", ""))

    def test_skill_lands_in_the_targets_directory(self) -> None:
        self.drive_to_published()
        for target in available_targets():
            with self.subTest(target=target.identifier):
                names = zipfile.ZipFile(io.BytesIO(self.export(target.identifier).content)).namelist()
                self.assertTrue(
                    any(n.startswith(target.skills_directory) for n in names), names
                )

    def test_the_exported_skill_matches_the_target_format(self) -> None:
        """The artefact a user downloads must pass the same check the adapter declares."""

        self.drive_to_published()
        archive = zipfile.ZipFile(io.BytesIO(self.export("claude-code").content))
        skill = next(n for n in archive.namelist() if n.endswith("SKILL.md"))
        text = archive.read(skill).decode("utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("name: example-accessibility-audit", text)


if __name__ == "__main__":
    unittest.main()
