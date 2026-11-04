"""The adapter contract: three deterministic functions, one IR, no surprises.

PRD §5.6 commits to 导出 / 校验 / 描述权限 plus golden files. These tests hold that
commitment: the IR is deterministic, validation mirrors what the targets actually enforce,
and loss is always reported rather than dropped.

The rules asserted here come from the implementations, not from intuition:
  * Claude Code — https://code.claude.com/docs/en/skills.md
  * DSH — package `@deepseek-ai/dsh-skill-filesystem`, `parseSkillFile`
"""

from __future__ import annotations

import json
import pathlib
import unittest
from dataclasses import dataclass, field

from apps.api.domain.adapters import (
    AgentSkillsTarget,
    BundleFile,
    ExportBundle,
    SKILL_NAME_PATTERN,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def clean_manifest(**overrides: object) -> dict:
    manifest = {
        "inputs": [],
        "outputs": [],
        "runtimes": ["agent-skills"],
        "permissions": [],
    }
    manifest.update(overrides)
    return manifest


@dataclass
class FakeResource:
    """A minimal resource standing in for a version.

    The adapter only reads these fields; keeping the double narrow means a failure points
    at the adapter rather than at the fixture.
    """

    resource_id: str = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    version_id: str = "01BX5ZZKBKACTAV9WEVGEMMVRZ"
    version: str = "1.0.0"
    kind: str = "skill"
    slug: str = "example-vocabulary-coverage"
    name: str = "Vocabulary Coverage"
    description: str = "Example resource for the SuperCoder protocol. Reports vocabulary gaps."
    entrypoint: str = "SKILL.md"
    body: str = "# Vocabulary Coverage\n\nReport the gaps.\n"
    manifest: dict = field(default_factory=clean_manifest)
    tags: tuple[str, ...] = ("example",)


def target() -> AgentSkillsTarget:
    return AgentSkillsTarget()


def skill_text(bundle: ExportBundle) -> str:
    return next(iter(bundle.ordered_files())).text


class DeterminismTests(unittest.TestCase):
    def test_two_exports_produce_identical_bytes(self) -> None:
        resource = FakeResource()
        self.assertEqual(target().export(resource).digest(), target().export(resource).digest())

    def test_file_order_is_stable(self) -> None:
        resource = FakeResource()
        first = [f.path for f in target().export(resource).ordered_files()]
        second = [f.path for f in target().export(resource).ordered_files()]
        self.assertEqual(first, second)

    def test_content_does_not_depend_on_the_target_identifier(self) -> None:
        """Only placement changes between environments, never content.

        If content varied, two targets could disagree about what a skill says, and the
        shared base class would be pointless.
        """

        class Other(AgentSkillsTarget):
            _identifier = "other-target"
            _skills_directory = ".other/skills"

        base = skill_text(target().export(FakeResource()))
        other = skill_text(Other().export(FakeResource()))
        self.assertEqual(base, other)


class FrontmatterTests(unittest.TestCase):
    def test_fence_comes_first(self) -> None:
        text = skill_text(target().export(FakeResource()))
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("\n---\n", text)

    def test_name_and_description_are_present(self) -> None:
        text = skill_text(target().export(FakeResource()))
        self.assertIn("name: example-vocabulary-coverage", text)
        self.assertIn("description: Example resource", text)

    def test_body_follows_the_frontmatter(self) -> None:
        text = skill_text(target().export(FakeResource()))
        self.assertTrue(text.rstrip().endswith("Report the gaps."))

    def test_a_colon_in_the_description_is_quoted(self) -> None:
        """Unquoted YAML containing a colon parses as a nested map, which no target expects."""

        resource = FakeResource(description="Example resource: reports gaps.")
        text = skill_text(target().export(resource))
        self.assertIn('description: "Example resource: reports gaps."', text)

    def test_the_slug_becomes_the_skill_name(self) -> None:
        resource = FakeResource(slug="example-valid-slug")
        bundle = target().export(resource)
        self.assertIn(".agents/skills/example-valid-slug/SKILL.md", bundle.files)


class ValidationTests(unittest.TestCase):
    def _bundle(self, text: str | None = None) -> ExportBundle:
        files = {} if text is None else {"x/SKILL.md": BundleFile(path="x/SKILL.md", text=text)}
        return ExportBundle(
            target="t",
            resource_id="r",
            version_id="v",
            resource_version="1.0.0",
            files=files,
        )

    def test_a_produced_bundle_passes(self) -> None:
        result = target().validate(target().export(FakeResource()))
        self.assertTrue(result.ok, result.issues)

    def test_missing_name_fails(self) -> None:
        result = target().validate(self._bundle("---\ndescription: d\n---\n"))
        self.assertFalse(result.ok)
        self.assertTrue(any("requires `name`" in i.message for i in result.fatal))

    def test_missing_description_fails(self) -> None:
        result = target().validate(self._bundle("---\nname: ok\n---\n"))
        self.assertFalse(result.ok)
        self.assertTrue(any("requires `description`" in i.message for i in result.fatal))

    def test_a_non_kebab_name_fails(self) -> None:
        """DSH drops such a skill outright; Claude Code documents the same grammar."""

        result = target().validate(self._bundle("---\nname: Not_Kebab\ndescription: d\n---\n"))
        self.assertFalse(result.ok)

    def test_missing_fence_fails(self) -> None:
        self.assertFalse(target().validate(self._bundle("# no frontmatter\n")).ok)

    def test_unclosed_frontmatter_fails(self) -> None:
        self.assertFalse(target().validate(self._bundle("---\nname: ok\n")).ok)

    def test_overlong_description_is_reported_but_survives(self) -> None:
        result = target().validate(
            self._bundle(f"---\nname: ok\ndescription: {'d' * 1100}\n---\n")
        )
        self.assertTrue(result.ok, "the target still loads it, so it must not be fatal")
        self.assertTrue(any(not issue.fatal for issue in result.issues))

    def test_a_bundle_without_skills_fails(self) -> None:
        self.assertFalse(target().validate(self._bundle()).ok)


class LossIsReportedTests(unittest.TestCase):
    def _codes(self, resource: FakeResource) -> set[str]:
        return {w.code for w in target().export(resource).warnings}

    def test_typed_io_is_reported(self) -> None:
        resource = FakeResource(
            manifest=clean_manifest(inputs=[{"name": "text"}], outputs=[{"name": "out"}])
        )
        self.assertIn("typed_io_not_carried", self._codes(resource))

    def test_other_runtimes_are_reported(self) -> None:
        resource = FakeResource(manifest=clean_manifest(runtimes=["agent-skills", "kiro"]))
        self.assertIn("other_runtimes_not_exported", self._codes(resource))

    def test_a_workflow_is_reported_as_degraded(self) -> None:
        self.assertIn("workflow_degrades_to_instructions", self._codes(FakeResource(kind="workflow")))

    def test_a_renamed_entrypoint_is_reported(self) -> None:
        self.assertIn("entrypoint_renamed", self._codes(FakeResource(entrypoint="PROMPT.md")))

    def test_a_clean_skill_warns_about_nothing(self) -> None:
        self.assertEqual(self._codes(FakeResource()), set())

    def test_every_warning_explains_its_effect(self) -> None:
        """A code alone is not a disclosure; the reader must learn what changes."""

        resource = FakeResource(kind="workflow", entrypoint="GRAPH.yaml")
        for warning in target().export(resource).warnings:
            self.assertTrue(warning.effect, f"{warning.code} has no effect text")
            self.assertTrue(warning.source, f"{warning.code} has no source")


class PermissionTests(unittest.TestCase):
    def test_declared_permissions_are_reported(self) -> None:
        resource = FakeResource(
            manifest=clean_manifest(permissions=[{"scope": "repository:read"}])
        )
        self.assertEqual(target().describe_permissions(resource).declared, ("repository:read",))

    def test_no_permissions_yields_an_empty_report(self) -> None:
        self.assertEqual(target().describe_permissions(FakeResource()).declared, ())


class CorpusCompatibilityTests(unittest.TestCase):
    def test_every_corpus_slug_meets_the_target_grammar(self) -> None:
        """Platform and target slug rules must not diverge.

        If they did, an export would pass every check here and be rejected on the user's
        machine — the failure this test exists to prevent.
        """

        offenders = []
        for path in (ROOT / "resources").glob("*/*/resource.yaml"):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            slug = manifest.get("slug", "")
            if not SKILL_NAME_PATTERN.match(slug):
                offenders.append(slug)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
