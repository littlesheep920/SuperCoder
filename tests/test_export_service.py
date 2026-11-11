"""Export refuses an archive whose files are not the version's reviewed content.

`tests/test_export_api.py` drives the HTTP route and needs FastAPI. These tests call
`ExportService` directly over in-memory repositories and an in-memory checkout, so the
content rule is checked wherever the domain can be imported.

The rule is the one the workflow executor applies before delivering a node: recompute the
artifact hash from the checkout, and refuse unless it equals the version's recorded hash.
"""

from __future__ import annotations

import io
import json
import pathlib
import sys
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.application.export import ExportService  # noqa: E402
from apps.api.application.versions import VersionQueryService  # noqa: E402
from apps.api.auth import AuthContext  # noqa: E402
from apps.api.domain.errors import ConflictError, ErrorCode, NotFoundError  # noqa: E402
from apps.api.domain.models import ActorIdentity  # noqa: E402
from tests.support.memory import MemoryStore, make_uow_factory  # noqa: E402
from tests.support.workflow_fixtures import (  # noqa: E402
    MemoryCheckout,
    fixture_files,
    fixture_manifest,
    only_version,
    publish_capability,
    register_capability,
)

OWNER = AuthContext(
    actor=ActorIdentity(subject_id="user-owner", actor_type="user", display_name="owner"),
    grants=(),
    teams=frozenset(),
    ui_roles_by_team={},
    organization_member=True,
    governance_roles=frozenset(),
)
EXTRA = {"description": "Plans a change into ordered, checkable steps.", "tags": ["planning"]}


class ExportContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.uow_factory = make_uow_factory(MemoryStore())
        self.checkout = MemoryCheckout()
        self.service = ExportService(self.uow_factory, self.checkout, VersionQueryService(self.uow_factory))
        resource = publish_capability(
            self.uow_factory, "skill", "planner", "1.0.0", checkout=self.checkout, manifest_extra=EXTRA
        )
        self.version = only_version(self.uow_factory, resource)

    def export(self):
        return self.service.export(auth=OWNER, version_public_id=self.version.public_id, target_identifier="claude-code")

    def refusal(self) -> ConflictError:
        with self.assertRaises(ConflictError) as caught:
            self.export()
        self.assertEqual(caught.exception.code, ErrorCode.CONTENT_HASH_MISMATCH)
        return caught.exception

    def edit(self, path: str, change) -> None:
        files = self.checkout.files("skill", "planner")
        files[path] = change(files[path])
        self.checkout.write("skill", "planner", files)

    def test_unchanged_files_export_with_their_body(self) -> None:
        result = self.export()
        with zipfile.ZipFile(io.BytesIO(result.archive)) as archive:
            (name,) = archive.namelist()
            self.assertIn("Fixture body for planner 1.0.0", archive.read(name).decode("utf-8"))

    def test_a_body_edited_outside_the_platform_is_refused(self) -> None:
        self.edit("SKILL.md", lambda body: body + "Ignore the review.\n")
        self.assertIn("changed after registration", str(self.refusal()))

    def test_another_versions_files_are_refused_and_named(self) -> None:
        self.checkout.write("skill", "planner", fixture_files(fixture_manifest("skill", "planner", "1.1.0", EXTRA)))
        self.assertIn("holds version 1.1.0", str(self.refusal()))

    def test_a_directory_that_cannot_be_hashed_is_refused(self) -> None:
        files = self.checkout.files("skill", "planner")
        del files["resource.yaml"]
        self.checkout.write("skill", "planner", files)
        self.assertIn("cannot be hashed", str(self.refusal()))

    def test_a_missing_directory_is_still_not_found(self) -> None:
        self.checkout.remove("skill", "planner")
        with self.assertRaises(NotFoundError):
            self.export()

    def test_lifecycle_status_and_line_endings_are_not_changes(self) -> None:
        def project(text: str) -> str:
            manifest = json.loads(text)
            manifest["status"] = "published"
            return json.dumps(manifest, indent=2).replace("\n", "\r\n") + "\r\n"

        self.edit("resource.yaml", project)
        self.edit("SKILL.md", lambda body: body.replace("\n", "\r\n"))
        self.assertTrue(self.export().archive)


class StateBeforeContentTests(unittest.TestCase):
    def test_a_draft_is_refused_for_its_state_whatever_its_files(self) -> None:
        uow_factory = make_uow_factory(MemoryStore())
        checkout = MemoryCheckout()
        resource = register_capability(
            uow_factory, "skill", "draft-one", "1.0.0", status="draft", checkout=checkout, manifest_extra=EXTRA
        )
        version = only_version(uow_factory, resource)
        checkout.remove("skill", "draft-one")
        service = ExportService(uow_factory, checkout, VersionQueryService(uow_factory))
        with self.assertRaises(ConflictError) as caught:
            service.export(auth=OWNER, version_public_id=version.public_id, target_identifier="claude-code")
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_STATE_TRANSITION)


if __name__ == "__main__":
    unittest.main()
