"""The three concrete targets, each checked against its own specification.

A target is only correct relative to a documented rule. Every assertion here names the
source it comes from, so a future change can be judged against the spec rather than
against what the code happens to do:

* Claude Code — https://code.claude.com/docs/en/skills.md
* DSH — `@deepseek-ai/dsh-skill-filesystem`, `roots()` and `parseSkillFile`
* ChatGPT — https://help.openai.com/articles/20001066 (skills upload),
  https://help.openai.com/articles/20001256 (skill-only plugins, zip upload)
"""

from __future__ import annotations

import json
import unittest

from apps.api.domain.adapters import (
    ChatGPTTarget,
    ClaudeCodeTarget,
    DshTarget,
    available_targets,
    find_target,
)
from tests.test_adapter_ir import FakeResource, clean_manifest


class RegistryTests(unittest.TestCase):
    def test_exactly_three_targets_are_offered(self) -> None:
        self.assertEqual(
            [t.identifier for t in available_targets()], ["claude-code", "dsh", "chatgpt"]
        )

    def test_lookup_by_identifier(self) -> None:
        self.assertIsInstance(find_target("dsh"), DshTarget)
        self.assertIsNone(find_target("nonexistent"))

    def test_identifiers_are_unique(self) -> None:
        identifiers = [t.identifier for t in available_targets()]
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_every_target_declares_a_display_name(self) -> None:
        for target in available_targets():
            self.assertTrue(target.display_name, target.identifier)


class ClaudeCodeTests(unittest.TestCase):
    def test_skills_live_under_claude_skills(self) -> None:
        """Documented location: `.claude/skills/<name>/SKILL.md`."""

        target = ClaudeCodeTarget()
        self.assertEqual(target.skills_directory, ".claude/skills")
        bundle = target.export(FakeResource(slug="example-thing"))
        self.assertIn(".claude/skills/example-thing/SKILL.md", bundle.files)

    def test_mcp_servers_land_in_dot_mcp_json(self) -> None:
        """Claude Code reads project MCP servers from `.mcp.json`."""

        resource = FakeResource(
            kind="mcp",
            slug="example-server",
            manifest=clean_manifest(mcp={"transport": "stdio", "command": "npx"}),
        )
        bundle = ClaudeCodeTarget().export(resource)
        self.assertIn(".mcp.json", bundle.files)
        payload = json.loads(bundle.files[".mcp.json"].text)
        self.assertIn("example-server", payload["mcpServers"])

    def test_http_transport_is_accepted(self) -> None:
        resource = FakeResource(
            kind="mcp",
            manifest=clean_manifest(mcp={"transport": "http", "url": "https://example.test/mcp"}),
        )
        bundle = ClaudeCodeTarget().export(resource)
        self.assertIn(".mcp.json", bundle.files)

    def test_no_allowed_tools_are_emitted(self) -> None:
        """Pre-approving tools on import would decide security for the user.

        Claude Code supports `allowed-tools` in frontmatter, but writing it here would
        grant tool access without the recipient's consent.
        """

        bundle = ClaudeCodeTarget().export(FakeResource())
        self.assertNotIn("allowed-tools", next(iter(bundle.files.values())).text)


class DshTests(unittest.TestCase):
    def test_skills_live_under_dsh_skills(self) -> None:
        """`roots()` lists `.dsh/skills` before `.agents/skills`."""

        target = DshTarget()
        self.assertEqual(target.skills_directory, ".dsh/skills")
        bundle = target.export(FakeResource(slug="example-thing"))
        self.assertIn(".dsh/skills/example-thing/SKILL.md", bundle.files)

    def test_mcp_is_reported_rather_than_invented(self) -> None:
        """No file-based MCP layout is documented for DSH, so none is written.

        Producing a plausible-looking config the host ignores would be worse than stating
        the gap.
        """

        resource = FakeResource(kind="mcp", manifest=clean_manifest(mcp={"transport": "stdio"}))
        bundle = DshTarget().export(resource)
        self.assertEqual(
            [p for p in bundle.files if "mcp" in p.lower()], [],
        )
        self.assertIn("mcp_has_no_config_home", {w.code for w in bundle.warnings})

    def test_the_name_grammar_matches_the_loader(self) -> None:
        """DSH drops a skill whose name is not kebab-case; the slug already complies."""

        bundle = DshTarget().export(FakeResource(slug="example-kebab-name"))
        self.assertTrue(DshTarget().validate(bundle).ok)


class ChatGPTTests(unittest.TestCase):
    def test_the_skill_sits_under_a_single_root(self) -> None:
        """An uploaded zip needs one unambiguous root directory."""

        bundle = ChatGPTTarget().export(FakeResource(slug="example-thing"))
        self.assertIn("skills/example-thing/SKILL.md", bundle.files)

    def test_a_package_manifest_is_included(self) -> None:
        bundle = ChatGPTTarget().export(FakeResource(slug="example-thing"))
        self.assertIn("package.json", bundle.files)
        payload = json.loads(bundle.files["package.json"].text)
        self.assertEqual(payload["name"], "example-thing")
        self.assertIn("supercoder", payload)
        self.assertEqual(payload["supercoder"]["version_id"], FakeResource().version_id)

    def test_the_manual_upload_step_is_disclosed(self) -> None:
        """There is no public API for installing a skill into an account."""

        bundle = ChatGPTTarget().export(FakeResource())
        self.assertIn("upload_is_manual", {w.code for w in bundle.warnings})

    def test_the_bundle_still_validates(self) -> None:
        target = ChatGPTTarget()
        self.assertTrue(target.validate(target.export(FakeResource())).ok)


class CrossTargetTests(unittest.TestCase):
    def test_all_targets_produce_identical_skill_content(self) -> None:
        """Placement differs; the skill itself must not.

        If content diverged per target, the same governed resource would mean different
        things in different environments.
        """

        resource = FakeResource()
        texts = []
        for target in available_targets():
            bundle = target.export(resource)
            skill = next(f for p, f in bundle.files.items() if p.endswith("SKILL.md"))
            texts.append(skill.text)
        self.assertEqual(len(set(texts)), 1)

    def test_every_target_output_passes_its_own_validation(self) -> None:
        resource = FakeResource()
        for target in available_targets():
            with self.subTest(target=target.identifier):
                result = target.validate(target.export(resource))
                self.assertTrue(result.ok, result.issues)

    def test_every_target_reports_permissions(self) -> None:
        resource = FakeResource(manifest=clean_manifest(permissions=[{"scope": "repo:read"}]))
        for target in available_targets():
            with self.subTest(target=target.identifier):
                self.assertEqual(target.describe_permissions(resource).declared, ("repo:read",))


if __name__ == "__main__":
    unittest.main()
