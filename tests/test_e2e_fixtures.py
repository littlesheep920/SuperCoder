"""End-to-end fixtures are written exactly, never overwrite, and always go away.

`scripts/e2e_fixtures.py` exists because the end-to-end checks left their fixtures in the
working tree (compose bind-mounts `./resources`), where the next unit test run counted them as
repository workflows. These tests pin the rules that prevent that from recurring:

* only fixture-shaped slugs (`...-<number>`) can be written, so cleanup can never reach a real
  resource such as `example-e2e-scenario`;
* an existing directory is refused, not replaced;
* cleanup removes exactly what the run created, also when the run fails, unless told to keep it;
* bytes are written as given, so the reported digest is the digest of the file on disk;
* a Docker failure is reported with the container's own message.
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from e2e_fixtures import FIXTURE_SLUG, FixtureError, Fixtures, artifact, leftover_fixtures  # noqa: E402

BODY = b"# E2E Plan\n\nExample resource.\n"


class SlugRuleTests(unittest.TestCase):
    def test_fixture_slugs_end_in_a_number(self) -> None:
        for slug in ("example-e2e-flow-12345", "example-e2e-plan-7", "example-acceptance-42"):
            with self.subTest(slug=slug):
                self.assertRegex(slug, FIXTURE_SLUG)

    def test_lookalike_names_are_not_fixtures(self) -> None:
        for slug in ("example-e2e-scenario", "example-acceptance-criteria", "example-e2e-", "demo-flow-1",
                     "example-e2e-flow-12a", "my-example-e2e-flow-1"):
            with self.subTest(slug=slug):
                self.assertIsNone(FIXTURE_SLUG.match(slug))

    def test_the_real_lookalikes_exist_and_are_never_reported_as_leftovers(self) -> None:
        """The first version matched by prefix and flagged these two real resources."""

        real = {"resources/skills/example-e2e-scenario", "resources/prompts/example-acceptance-criteria"}
        present = {path.relative_to(ROOT).as_posix() for path in (ROOT / "resources").glob("*/*")
                   if path.name in {"example-e2e-scenario", "example-acceptance-criteria"}}
        self.assertTrue(present, "the lookalike resources this test guards against are gone")
        self.assertFalse(real & set(leftover_fixtures(ROOT)))
        self.assertFalse(present & set(leftover_fixtures(ROOT)))


class LocalFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        (self.root / "resources" / "skills").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_bytes_are_written_unchanged_and_the_digest_matches_the_file(self) -> None:
        fixtures = Fixtures(self.root, "local")
        relative = fixtures.write("skill", "example-e2e-plan-1", {"SKILL.md": BODY})
        on_disk = (self.root / relative / "SKILL.md").read_bytes()
        self.assertEqual(on_disk, BODY)
        self.assertNotIn(b"\r\n", on_disk)
        self.assertEqual(artifact("SKILL.md", BODY)["digest"], "sha256:" + hashlib.sha256(on_disk).hexdigest())
        self.assertEqual(artifact("SKILL.md", BODY)["size_bytes"], len(on_disk))

    def test_an_existing_directory_is_refused_and_survives_cleanup(self) -> None:
        existing = self.root / "resources" / "skills" / "example-e2e-plan-2"
        existing.mkdir()
        (existing / "SKILL.md").write_bytes(b"someone else's file\n")
        fixtures = Fixtures(self.root, "local")
        with self.assertRaises(FixtureError):
            fixtures.write("skill", "example-e2e-plan-2", {"SKILL.md": BODY})
        fixtures.close()
        self.assertEqual((existing / "SKILL.md").read_bytes(), b"someone else's file\n")

    def test_cleanup_removes_only_what_the_run_created(self) -> None:
        neighbour = self.root / "resources" / "skills" / "example-e2e-plan-3"
        neighbour.mkdir()
        fixtures = Fixtures(self.root, "local")
        fixtures.write("skill", "example-e2e-plan-4", {"SKILL.md": BODY})
        fixtures.write("workflow", "example-e2e-flow-4", {"WORKFLOW.md": BODY})
        removed = fixtures.cleanup()
        self.assertEqual(set(removed), {"resources/skills/example-e2e-plan-4", "resources/workflows/example-e2e-flow-4"})
        self.assertTrue(neighbour.is_dir())
        self.assertEqual(leftover_fixtures(self.root), ["resources/skills/example-e2e-plan-3"])

    def test_a_failing_run_still_cleans_up(self) -> None:
        with self.assertRaises(RuntimeError):
            with Fixtures(self.root, "local") as fixtures:
                fixtures.write("skill", "example-e2e-plan-5", {"SKILL.md": BODY})
                raise RuntimeError("the check failed half way")
        self.assertEqual(leftover_fixtures(self.root), [])

    def test_keep_leaves_the_fixtures_in_place(self) -> None:
        with Fixtures(self.root, "local", keep=True) as fixtures:
            fixtures.write("skill", "example-e2e-plan-6", {"SKILL.md": BODY})
        self.assertEqual(leftover_fixtures(self.root), ["resources/skills/example-e2e-plan-6"])

    def test_a_slug_that_is_not_fixture_shaped_is_refused_before_anything_is_written(self) -> None:
        fixtures = Fixtures(self.root, "local")
        with self.assertRaises(FixtureError):
            fixtures.write("skill", "example-e2e-scenario", {"SKILL.md": BODY})
        self.assertFalse((self.root / "resources" / "skills" / "example-e2e-scenario").exists())
        self.assertEqual(fixtures.created, [])


class ComposeFixtureTests(unittest.TestCase):
    """The compose path, with `docker compose exec` replaced by a recorder."""

    def recorder(self, fail_on: str | None = None, stderr: bytes = b""):
        calls: list[tuple[list[str], bytes]] = []

        def run(command, *, cwd, input, capture_output):  # noqa: A002 - mirrors subprocess.run
            calls.append((command, input))
            inner = command[5:]
            if inner[:2] == ["test", "-e"]:
                return subprocess.CompletedProcess(command, 1, b"", b"")
            if fail_on and fail_on in " ".join(inner):
                return subprocess.CompletedProcess(command, 1, b"", stderr)
            return subprocess.CompletedProcess(command, 0, b"", b"")

        return calls, run

    def test_content_is_sent_on_stdin_unchanged(self) -> None:
        calls, run = self.recorder()
        Fixtures(ROOT, "compose", runner=run).write("skill", "example-e2e-plan-8", {"SKILL.md": BODY})
        writes = [(command, data) for command, data in calls if "cat > \"$1\"" in command]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][1], BODY)
        self.assertEqual(writes[0][0][-1], "/app/resources/skills/example-e2e-plan-8/SKILL.md")

    def test_a_docker_failure_carries_the_container_message(self) -> None:
        _, run = self.recorder(fail_on="mkdir", stderr=b'service "api" is not running\n')
        with self.assertRaises(FixtureError) as raised:
            Fixtures(ROOT, "compose", runner=run).write("skill", "example-e2e-plan-9", {"SKILL.md": BODY})
        self.assertIn('service "api" is not running', str(raised.exception))

    def test_cleanup_removes_inside_the_container_only_what_was_created(self) -> None:
        calls, run = self.recorder()
        fixtures = Fixtures(ROOT, "compose", runner=run)
        fixtures.write("workflow", "example-e2e-flow-10", {"WORKFLOW.md": BODY})
        fixtures.cleanup()
        removals = [command[5:] for command, _ in calls if command[5:6] == ["rm"]]
        self.assertEqual(removals, [["rm", "-rf", "--", "/app/resources/workflows/example-e2e-flow-10"]])


class ScriptWiringTests(unittest.TestCase):
    """Both scripts that write fixtures go through the module and clean up in `finally`."""

    def test_both_scripts_use_the_fixture_module_and_clean_up(self) -> None:
        for name in ("check_workflow_e2e.py", "check_acceptance_walkthrough.py"):
            with self.subTest(script=name):
                source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
                self.assertIn("from e2e_fixtures import", source)
                self.assertIn("add_arguments(parser)", source)
                self.assertIn("finally:\n        fixtures.close()", source)
                self.assertNotIn("rm -rf", source)
                self.assertNotIn("docker", source.split('"""', 2)[2])


if __name__ == "__main__":
    unittest.main()
