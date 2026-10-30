"""Tests for previewing and committing a reviewed draft.

The safety properties:

* a commit without explicit confirmation is refused and writes nothing,
* the manifest is re-validated at write time, not trusted from earlier,
* a failure after writing removes what was written,
* status is forced to draft, so registration cannot publish,
* preview never writes.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import unittest

from apps.api.application.draft_commit import DraftCommitService, artifact_reference_for
from apps.api.domain.errors import ValidationError
from apps.api.domain.models import ResourceKind

ROOT = pathlib.Path(__file__).resolve().parents[1]

VALID_MANIFEST = {
    "schema_version": "1",
    "kind": "skill",
    "slug": "example-committed-skill",
    "name": "Example Committed Skill",
    "version": "1.0.0",
    "description": "Example resource for the SuperCoder protocol. Does a thing.",
    "aliases": [],
    "tags": ["example", "skill"],
    "categories": ["software-development/testing"],
    "runtimes": ["claude-code"],
    "inputs": [{"name": "request", "type": "text", "required": True, "description": "In."}],
    "outputs": [{"name": "result", "type": "text", "required": True, "description": "Out."}],
    "permissions": [],
    "dependencies": [],
    "entrypoint": "SKILL.md",
    "status": "draft",
    "owner_team": "qa-automation",
    "skill": {"scenarios": ["Does a thing."], "constraints": ["Report only."]},
}


class FakeGit:
    def __init__(self, *, read_only: bool = False) -> None:
        self.read_only = read_only
        self.files: dict[tuple[str, str, str], str] = {}

    def write_resource_file(self, kind, slug, relative_path, content) -> None:
        if self.read_only:
            raise RuntimeError("managed Git provider is read-only")
        self.files[(kind.value, slug, relative_path)] = content


class FakeVersion:
    public_id = "ver_1"
    # VersionRecord exposes the semantic version as ``identifier``, not ``version``.
    # The earlier fake invented ``version``, so the fake and the code agreed with
    # each other while both disagreed with the real record — and the mismatch only
    # appeared when the path ran against MySQL.
    identifier = "1.0.0"
    artifact_hash = "a" * 64


class FakeResource:
    public_id = "res_1"


class FakeCommands:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list = []

    def create_resource(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("database rejected the insert")
        return FakeResource(), FakeVersion()


class FakeActor:
    subject_id = "u1"
    display_name = "Demo Owner"


def service(*, git=None, commands=None) -> DraftCommitService:
    return DraftCommitService(git or FakeGit(), commands or FakeCommands(), ROOT)


class CommitTests(unittest.TestCase):
    def test_commit_writes_manifest_and_body_then_registers(self) -> None:
        git = FakeGit()
        commands = FakeCommands()
        result = service(git=git, commands=commands).commit(
            actor=FakeActor(),
            manifest=dict(VALID_MANIFEST),
            reason="registering a reviewed draft",
            correlation_id="c1",
        )
        written = set(git.files)
        self.assertIn(("skill", "example-committed-skill", "resource.yaml"), written)
        self.assertIn(("skill", "example-committed-skill", "SKILL.md"), written)
        self.assertEqual(len(commands.calls), 1)
        self.assertEqual(result.resource_id, "res_1")
        self.assertEqual(result.kind, "skill")

    def test_status_is_forced_to_draft(self) -> None:
        git = FakeGit()
        manifest = dict(VALID_MANIFEST)
        manifest["status"] = "published"
        service(git=git, commands=FakeCommands()).commit(
            actor=FakeActor(), manifest=manifest, reason="r", correlation_id="c"
        )
        stored = json.loads(git.files[("skill", "example-committed-skill", "resource.yaml")])
        self.assertEqual(stored["status"], "draft")

    def test_invalid_manifest_is_rejected_before_writing(self) -> None:
        git = FakeGit()
        manifest = dict(VALID_MANIFEST)
        manifest["version"] = "not-semver"
        with self.assertRaises(ValidationError):
            service(git=git, commands=FakeCommands()).commit(
                actor=FakeActor(), manifest=manifest, reason="r", correlation_id="c"
            )
        self.assertEqual(git.files, {}, "nothing may be written for an invalid draft")

    def test_unknown_kind_is_rejected(self) -> None:
        manifest = dict(VALID_MANIFEST)
        manifest["kind"] = "spaceship"
        with self.assertRaises(ValidationError):
            service(git=FakeGit(), commands=FakeCommands()).commit(
                actor=FakeActor(), manifest=manifest, reason="r", correlation_id="c"
            )

    def test_command_failure_removes_written_files(self) -> None:
        """A half-written resource would look real to the next scan."""

        git = FakeGit()
        with self.assertRaises(RuntimeError):
            service(git=git, commands=FakeCommands(fail=True)).commit(
                actor=FakeActor(), manifest=dict(VALID_MANIFEST), reason="r", correlation_id="c"
            )
        for (_, _, name), content in git.files.items():
            self.assertEqual(content, "", f"{name} was not cleaned up")

    def test_read_only_git_fails_without_writing(self) -> None:
        git = FakeGit(read_only=True)
        with self.assertRaises(RuntimeError):
            service(git=git, commands=FakeCommands()).commit(
                actor=FakeActor(), manifest=dict(VALID_MANIFEST), reason="r", correlation_id="c"
            )
        self.assertEqual(git.files, {})

    def test_each_kind_writes_its_own_entrypoint(self) -> None:
        expected = {
            "skill": "SKILL.md",
            "prompt": "PROMPT.md",
            "agent": "AGENT.md",
            "workflow": "WORKFLOW.md",
            "tool": "tool.py",
            "mcp": "MCP.md",
        }
        from apps.api.application.draft_commit import _ENTRYPOINT_BY_KIND

        self.assertEqual(
            {kind.value: name for kind, name in _ENTRYPOINT_BY_KIND.items()}, expected
        )


class ArtifactReferenceTests(unittest.TestCase):
    def test_digest_matches_the_content(self) -> None:
        content = "# Body\n"
        reference = artifact_reference_for("SKILL.md", content)
        self.assertEqual(reference.digest, "sha256:" + hashlib.sha256(content.encode()).hexdigest())
        self.assertEqual(reference.size_bytes, len(content.encode()))


class EndpointGateTests(unittest.TestCase):
    def test_commit_requires_confirmation(self) -> None:
        """The confirmation flag is what separates deciding from writing."""

        from fastapi.testclient import TestClient

        from apps.api.main import create_app

        app = create_app()
        client = TestClient(app)
        response = client.post(
            "/v1/registration/commit",
            json={"manifest": VALID_MANIFEST, "reason": "r", "confirmed": False},
            headers={"Authorization": "Bearer dev-owner-token"},
        )
        # ValidationError maps to 422 across this API; the important part is that
        # the request is refused before any write path is reached.
        self.assertEqual(response.status_code, 422)
        self.assertIn("confirmed", response.text.lower())

    def test_commit_fails_closed_without_a_write_capable_service(self) -> None:
        from fastapi.testclient import TestClient

        from apps.api.main import create_app

        app = create_app()
        self.assertIsNone(
            app.state.container.draft_commit,
            "commit service must be absent when MySQL or write access is missing",
        )
        client = TestClient(app)
        response = client.post(
            "/v1/registration/commit",
            json={"manifest": VALID_MANIFEST, "reason": "r", "confirmed": True},
            headers={"Authorization": "Bearer dev-owner-token"},
        )
        self.assertEqual(response.status_code, 503)

    def test_preview_does_not_write(self) -> None:
        from fastapi.testclient import TestClient

        from apps.api.main import create_app

        app = create_app()
        container = app.state.container
        before = dict(getattr(container.git, "files", {}) or {})
        client = TestClient(app)
        response = client.post(
            "/v1/registration/preview",
            json={"manifest": VALID_MANIFEST},
            headers={"Authorization": "Bearer dev-owner-token"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["will_write"])
        self.assertTrue(body["validation_valid"], body.get("validation_errors"))
        after = dict(getattr(container.git, "files", {}) or {})
        self.assertEqual(before, after)


# Kept separate so the parametrised assertion above stays readable.
_ENTRYPOINTS: dict = {}


if __name__ == "__main__":
    unittest.main()
