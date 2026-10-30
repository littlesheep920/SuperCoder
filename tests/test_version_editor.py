"""Tests for the draft body editor.

The contract under test: editing a body creates a NEW version row, so the manifest
version must change. That rule was discovered the hard way — a body-only edit
violated the unique constraint on (resource, version) and returned a 500 from MySQL.
The tests below pin both the rule and the reason for it.
"""

from __future__ import annotations

import unittest

from apps.api.application.version_editor import VersionEditorService
from apps.api.domain.errors import ConflictError, PreconditionFailedError, ValidationError
from apps.api.domain.models import LifecycleState


class FakeGit:
    """In-memory stand-in for the managed checkout."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files = dict(files or {})
        self.writes: list[tuple[str, str, str]] = []

    def read_resource_files(self, kind, slug) -> dict[str, str]:
        return dict(self.files)

    def write_resource_file(self, kind, slug, name: str, content: str) -> None:
        self.writes.append((kind.value if hasattr(kind, "value") else str(kind), slug, name))
        self.files[name] = content


class FakeCommands:
    """Captures the update call and can be made to fail."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    def update_draft(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise ConflictError(message="row version is stale")

        class Updated:
            public_id = "ver_new"
            identifier = "1.0.1"
            artifact_hash = "sha256:" + "b" * 64

        return Updated()


class FakeVersion:
    public_id = "ver_1"
    identifier = "1.0.0"
    row_version = 3
    status = LifecycleState.DRAFT
    manifest = {
        "kind": "skill",
        "slug": "example-probe",
        "entrypoint": "SKILL.md",
        "schema_version": "1",
        "version": "1.0.0",
    }


def service(git: FakeGit, commands: FakeCommands) -> VersionEditorService:
    return VersionEditorService(git, commands)  # type: ignore[arg-type]


def edit(service: VersionEditorService, **overrides):
    kwargs = dict(
        actor=object(),
        version=FakeVersion(),
        body="# New body\n",
        manifest_patch={"version": "1.0.1"},
        reason="edit the body",
        correlation_id="corr-1",
    )
    kwargs.update(overrides)
    return service.edit(**kwargs)


class BodyWriteTests(unittest.TestCase):
    def test_body_is_written_to_git_and_a_new_version_recorded(self) -> None:
        git = FakeGit({"SKILL.md": "# Old\n"})
        commands = FakeCommands()
        result = edit(service(git, commands))

        self.assertTrue(result.body_written)
        self.assertEqual(git.files["SKILL.md"], "# New body\n")
        self.assertEqual(len(commands.calls), 1)
        self.assertEqual(result.body_path, "SKILL.md")
        self.assertEqual(result.version, "1.0.1")

    def test_the_digest_matches_what_was_written(self) -> None:
        """The artifact must describe the file on disk, not the request body."""

        git = FakeGit({"SKILL.md": "# Old\n"})
        commands = FakeCommands()
        edit(service(git, commands))

        artifacts = commands.calls[0]["artifacts"]
        self.assertIsNotNone(artifacts)
        self.assertEqual(artifacts[0].path, "SKILL.md")
        self.assertEqual(artifacts[0].size_bytes, len("# New body\n".encode("utf-8")))

    def test_manifest_only_edit_writes_no_file(self) -> None:
        git = FakeGit({"SKILL.md": "# Body\n"})
        commands = FakeCommands()
        result = edit(
            service(git, commands), body=None, manifest_patch={"description": "New text"}
        )

        self.assertFalse(result.body_written)
        self.assertEqual(git.writes, [])
        self.assertIsNone(commands.calls[0]["artifacts"])

    def test_a_failed_update_restores_the_previous_body(self) -> None:
        """The checkout must keep matching the digest of the version that exists."""

        git = FakeGit({"SKILL.md": "# Original\n"})
        commands = FakeCommands(fail=True)

        with self.assertRaises(ConflictError):
            edit(service(git, commands))

        self.assertEqual(git.files["SKILL.md"], "# Original\n")


class VersionBumpRuleTests(unittest.TestCase):
    """Editing a body requires a new manifest version.

    The reason is structural: `update_draft` appends a row rather than mutating one,
    and (resource, version) is unique. Without a bump the insert fails with a MySQL
    integrity error, which is not a message any caller should have to decode.
    """

    def test_a_body_edit_without_a_version_bump_is_refused(self) -> None:
        git = FakeGit({"SKILL.md": "# Old\n"})
        commands = FakeCommands()

        with self.assertRaises(ValidationError) as caught:
            edit(service(git, commands), manifest_patch=None)

        self.assertIn("version", str(caught.exception))
        self.assertEqual(git.writes, [], "nothing may be written when the request is refused")
        self.assertEqual(commands.calls, [])

    def test_an_unchanged_version_is_refused(self) -> None:
        git = FakeGit({"SKILL.md": "# Old\n"})
        with self.assertRaises(ValidationError) as caught:
            edit(service(git, FakeCommands()), manifest_patch={"version": "1.0.0"})
        self.assertIn("differ", str(caught.exception))
        self.assertEqual(git.writes, [])

    def test_a_manifest_only_edit_does_not_need_a_bump(self) -> None:
        git = FakeGit({"SKILL.md": "# Body\n"})
        result = edit(
            service(git, FakeCommands()), body=None, manifest_patch={"description": "changed"}
        )
        self.assertFalse(result.body_written)

    def test_status_cannot_be_changed_through_an_edit(self) -> None:
        with self.assertRaises(ConflictError):
            edit(service(FakeGit({}), FakeCommands()), manifest_patch={"status": "published"})


class OptimisticLockTests(unittest.TestCase):
    """A stale expectation must be refused before anything is written.

    The first implementation passed `version.row_version` (the record it had just
    read) into the command service's check, so the check compared the record to
    itself and always passed. A save with row_version=100 against a row at 1 was
    accepted and created a version. These tests pin the corrected behaviour.
    """

    def test_a_stale_expectation_is_refused(self) -> None:
        git = FakeGit({"SKILL.md": "# Old\n"})
        commands = FakeCommands()
        with self.assertRaises(PreconditionFailedError):
            edit(service(git, commands), expected_row_version=FakeVersion.row_version + 99)
        self.assertEqual(git.writes, [], "a refused save must not touch the checkout")
        self.assertEqual(commands.calls, [])

    def test_a_matching_expectation_is_forwarded_to_the_command_service(self) -> None:
        commands = FakeCommands()
        current = FakeVersion.row_version
        edit(service(FakeGit({"SKILL.md": "# Old\n"}), commands), expected_row_version=current)
        self.assertEqual(
            commands.calls[0]["row_version"],
            current,
            "the caller's value must reach the command service, not the record's",
        )

    def test_omitting_the_expectation_falls_back_to_the_record(self) -> None:
        commands = FakeCommands()
        edit(service(FakeGit({"SKILL.md": "# Old\n"}), commands))
        self.assertEqual(commands.calls[0]["row_version"], FakeVersion.row_version)


class GuardTests(unittest.TestCase):
    def test_only_draft_versions_may_be_edited(self) -> None:
        class Validating(FakeVersion):
            status = LifecycleState.VALIDATING

        with self.assertRaises(ConflictError) as caught:
            edit(service(FakeGit({"SKILL.md": ""}), FakeCommands()), version=Validating())
        self.assertIn("draft", str(caught.exception))

    def test_unknown_kind_is_rejected(self) -> None:
        class BadKind(FakeVersion):
            manifest = {"kind": "not-a-kind", "slug": "x", "entrypoint": "SKILL.md"}

        with self.assertRaises(ValidationError):
            edit(service(FakeGit({}), FakeCommands()), version=BadKind())

    def test_missing_entrypoint_is_rejected(self) -> None:
        class NoEntrypoint(FakeVersion):
            manifest = {"kind": "skill", "slug": "x", "version": "1.0.0"}

        with self.assertRaises(ValidationError):
            edit(service(FakeGit({}), FakeCommands()), version=NoEntrypoint())

    def test_a_version_without_a_slug_is_rejected(self) -> None:
        class NoSlug(FakeVersion):
            manifest = {"kind": "skill", "entrypoint": "SKILL.md", "version": "1.0.0"}

        with self.assertRaises(ValidationError):
            edit(service(FakeGit({}), FakeCommands()), version=NoSlug())


if __name__ == "__main__":
    unittest.main()
