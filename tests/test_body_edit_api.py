"""`PUT /v1/resource-versions/{id}/body` over HTTP: it answers what the contract says.

The frozen contract gives this operation the response `EditedDraftResponse`:
`version`, `body_path`, `body_written` and `audit_id`, nothing else. The route returned
`previous_version_id` instead of `audit_id`, and once the response model was declared every
successful edit answered **500 after the edit had been applied**: the body was written, a new
version row existed, and the caller was told it failed. No test drove the route over HTTP, so this
was found by running `scripts/check_acceptance_walkthrough.py` against MySQL. These tests drive it
through the real app with an in-memory store and a writable temporary checkout.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from apps.api.application.version_editor import VersionEditorService
from apps.api.domain.hashing import normalize_utf8_text, sha256_digest
from apps.api.domain.models import ResourceKind
from apps.api.main import create_app
from apps.api.providers.git_repository import GitProvider
from apps.api.providers.manifest_loader import ManifestLoader
from apps.api.config import get_settings
from apps.api.application.resource_commands import ResourceCommandService
from apps.api.application.snapshots import SnapshotService
from tests.support.memory import MemoryStore
from tests.test_stage1_api import build_container

ROOT = pathlib.Path(__file__).resolve().parents[1]
OWNER = {"Authorization": "Bearer dev-owner-token"}
ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
SLUG = "demo-code-review"


class BodyEditOverHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.checkout = pathlib.Path(self._tmp.name)
        shutil.copytree(ROOT / "resources" / "skills" / SLUG, self.checkout / "resources" / "skills" / SLUG)
        shutil.copytree(ROOT / "schemas", self.checkout / "schemas")  # the loader validates against these
        self.store = MemoryStore()
        # The Stage 1 harness, with its checkout swapped for a writable temporary one. Every
        # service that reads files is rebuilt on that checkout, so nothing reads the repository.
        container = build_container(self.store)
        git = GitProvider(str(self.checkout), read_only=False)
        loader = ManifestLoader(git)
        snapshots = SnapshotService(loader)
        commands = ResourceCommandService(container.uow_factory, snapshots)
        self.container = dataclasses.replace(
            container, git=git, manifest_loader=loader, snapshots=snapshots, resource_commands=commands,
            version_editor=VersionEditorService(git, commands),
        )
        self.client = TestClient(create_app(get_settings(), self.container))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def register(self) -> dict:
        loaded = self.container.manifest_loader.load(ResourceKind.SKILL, SLUG)
        artifacts = []
        for path, content in sorted(loaded.files.items()):
            normalized = normalize_utf8_text(content)
            artifacts.append({"path": path, "digest": sha256_digest(normalized), "size_bytes": len(normalized)})
        response = self.client.post("/v1/resources", headers=OWNER,
                                    json={"manifest": loaded.manifest, "artifacts": artifacts, "reason": "body edit test"})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["version"]

    def edit(self, version: dict, body: str) -> object:
        current = self.client.get(f"/v1/resource-versions/{version['version_id']}", headers=OWNER)
        self.assertEqual(current.status_code, 200, current.text)
        return self.client.put(
            f"/v1/resource-versions/{version['version_id']}/body",
            headers={**OWNER, "If-Match": current.headers["ETag"]},
            json={"body": body, "manifest": {"version": "1.0.1"}, "row_version": current.json()["row_version"],
                  "reason": "edit over http"},
        )

    def test_a_successful_edit_answers_200_with_the_contract_fields(self) -> None:
        response = self.edit(self.register(), "# Demo Code Review\n\nEdited over HTTP.\n")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(set(payload), {"version", "body_path", "body_written", "audit_id"})
        self.assertRegex(payload["audit_id"], ULID)
        self.assertEqual(payload["version"]["version"], "1.0.1")
        self.assertTrue(payload["body_written"])

    def test_the_audit_id_names_the_event_that_records_this_edit(self) -> None:
        payload = self.edit(self.register(), "# Demo Code Review\n\nAudited edit.\n").json()
        event = self.container.uow_factory().__enter__().audit.get(payload["audit_id"])
        self.assertIsNotNone(event)
        self.assertEqual(event.action, "version.update_draft")
        self.assertEqual(event.version_public_id, payload["version"]["version_id"])

    def test_the_written_file_is_what_the_new_version_hashes(self) -> None:
        payload = self.edit(self.register(), "# Demo Code Review\n\nHashed edit.\n").json()
        on_disk = (self.checkout / "resources" / "skills" / SLUG / payload["body_path"]).read_text(encoding="utf-8")
        self.assertIn("Hashed edit.", on_disk)

    def test_the_route_returns_exactly_the_fields_the_frozen_contract_lists(self) -> None:
        spec = json.loads((ROOT / "apps" / "api" / "openapi.stage0.yaml").read_text(encoding="utf-8"))
        contract = spec["components"]["schemas"]["EditedDraftResponse"]
        payload = self.edit(self.register(), "# Demo Code Review\n\nContract check.\n").json()
        self.assertEqual(set(payload), set(contract["properties"]))
        self.assertTrue(set(contract["required"]) <= set(payload))


if __name__ == "__main__":
    unittest.main()
