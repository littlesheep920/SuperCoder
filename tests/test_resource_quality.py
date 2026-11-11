"""The resource quality check-up: each rule, the boilerplate rule, and the corpus report.

What a reader of a quality report relies on:

* every rule fails the case it describes and passes its fix (`RuleTableTests`);
* boilerplate is decided by one count over the corpus, the same rule duplicate detection uses,
  with the threshold where it says (`BoilerplateTests`);
* the report is a report: no governance or lifecycle code imports it (`ReportOnlyTests`);
* the archived corpus report reproduces in another process (`CorpusTests`).
"""

from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.search.duplicates import TEMPLATE_MIN_RESOURCES  # noqa: E402
from apps.api.search.quality import (  # noqa: E402
    CHECKS,
    WARNING,
    CorpusContext,
    QualityResource,
    assess,
    assess_corpus,
)

GOOD_BODY = (
    "# Planner\n\n"
    "Split the objective into ordered steps with one owner each.\n"
    "Mark every assumption the input does not support.\n"
    "End with the validation each step needs before the next starts.\n"
)


def resource(kind: str = "skill", body: str = GOOD_BODY, **manifest) -> QualityResource:
    base = {
        "kind": kind,
        "slug": "planner",
        "name": "Planner",
        "version": "1.0.0",
        "description": "Turns a bounded engineering objective into an ordered, checkable plan.",
        "aliases": ["计划拆解"],
        "tags": ["planning", "engineering"],
        "inputs": [{"name": "objective", "type": "text", "description": "The objective, with its known constraints."}],
        "outputs": [{"name": "plan", "type": "text", "description": "Ordered steps, each with an owner and a check."}],
        "permissions": [],
        "dependencies": [],
        "entrypoint": "BODY.md",
        "skill": {"scenarios": ["Planning a change."], "constraints": ["Never edits files."]},
    }
    base.update(manifest)
    return QualityResource(manifest=base, files={"BODY.md": body})


def verdict(item: QualityResource, check_id: str, context: CorpusContext | None = None):
    for finding in assess(item, context).findings:
        if finding.check_id == check_id:
            return finding
    return None


class RuleTableTests(unittest.TestCase):
    def test_a_well_written_skill_passes_everything_that_applies(self) -> None:
        report = assess(resource())
        self.assertEqual([f.check_id for f in report.failed], [])
        self.assertEqual(report.passed_weight, report.applicable_weight)

    def test_each_rule_fails_its_case(self) -> None:
        tool = {"execution_mode": "python", "deterministic": True, "parameters_schema": {}, "returns_schema": {}}
        prompt = {"prompt_type": "template", "models": ["any"],
                  "variables": [{"name": "diff", "type": "text", "description": "The unified diff under review."}]}
        cases = [
            ("description.specific", resource(description="Planner")),
            ("description.specific", resource(description="Short one.")),
            ("aliases.present", resource(aliases=[])),
            ("tags.informative", resource(tags=["skill", "example", "planning"])),
            ("interface.described", resource(inputs=[{"name": "x", "type": "text"}])),
            ("interface.described", resource(inputs=[{"name": "x", "type": "text", "description": "input"}])),
            ("body.substantive", resource(body="# Planner\n\nPlans things.\n")),
            # Three sentences, every one copied from the description: length without content.
            ("body.substantive", resource(
                description="Plans a change. Orders the steps. Names an owner for each.",
                body="# Planner\n\nPlans a change. Orders the steps. Names an owner for each.\n")),
            # Declared but unused, and used but undeclared, each on its own.
            ("prompt.variables_match", resource("prompt", body=GOOD_BODY, prompt=prompt)),
            ("prompt.variables_match", resource("prompt", body=GOOD_BODY + "Review {{diff}} in {{context}}.\n",
                                                prompt=prompt)),
            ("skill.constraints", resource(skill={"scenarios": ["Planning."], "constraints": []})),
            ("tool.timeout", resource("tool", tool=tool)),
            ("tool.has_tests", resource("tool", tool=dict(tool, timeout_seconds=5))),
            ("permissions.network_declared", resource(
                "mcp", mcp={"transport": "streamable-http", "url": "https://x.invalid", "tools": [], "config_schema": {}})),
            ("dependencies.bounded", resource(dependencies=[{"kind": "mcp", "slug": "a", "version": "*", "purpose": "p"}])),
            ("dependencies.bounded", resource(dependencies=[{"kind": "mcp", "slug": "a", "version": ">=1.0.0", "purpose": "p"}])),
            ("dependencies.purpose", resource(dependencies=[{"kind": "mcp", "slug": "a", "version": "^1.0.0"}])),
        ]
        for check_id, item in cases:
            with self.subTest(check_id):
                finding = verdict(item, check_id)
                self.assertIsNotNone(finding, "the rule did not apply")
                self.assertFalse(finding.passed)
                self.assertTrue(finding.evidence, "a failed rule must say what it found")

    def test_each_fix_passes(self) -> None:
        tool = {"execution_mode": "python", "deterministic": True, "parameters_schema": {}, "returns_schema": {},
                "timeout_seconds": 5}
        prompt = {"prompt_type": "template", "models": ["any"],
                  "variables": [{"name": "diff", "type": "text", "description": "The unified diff under review."}]}
        fixed_tool = QualityResource(
            manifest=resource("tool", tool=tool).manifest,
            files={"BODY.md": GOOD_BODY, "test_tool.py": "def test_ok():\n    assert True\n"},
        )
        cases = [
            ("prompt.variables_match", resource("prompt", body=GOOD_BODY + "Review {{diff}}.\n", prompt=prompt)),
            ("tool.has_tests", fixed_tool),
            ("permissions.network_declared", resource(
                "mcp", mcp={"transport": "streamable-http", "url": "https://x.invalid", "tools": [], "config_schema": {}},
                permissions=[{"name": "network:access", "effect": "allow", "reason": "reads the advisory feed"}])),
            ("dependencies.bounded", resource(dependencies=[
                {"kind": "mcp", "slug": "a", "version": ">=1.0.0 <2.0.0", "purpose": "p"},
                {"kind": "mcp", "slug": "b", "version": "^1.2.0", "purpose": "p"},
                {"kind": "mcp", "slug": "c", "version": "1.0.0", "purpose": "p"},
            ])),
        ]
        for check_id, item in cases:
            with self.subTest(check_id):
                self.assertTrue(verdict(item, check_id).passed)

    def test_a_denied_network_permission_is_not_a_declaration_of_access(self) -> None:
        item = resource("mcp", mcp={"transport": "sse", "tools": [], "config_schema": {}},
                        permissions=[{"name": "network:access", "effect": "deny"}])
        self.assertFalse(verdict(item, "permissions.network_declared").passed)

    def test_rules_apply_only_where_they_mean_something(self) -> None:
        skill = {f.check_id for f in assess(resource()).findings}
        self.assertNotIn("prompt.variables_match", skill)
        self.assertNotIn("tool.has_tests", skill)
        self.assertNotIn("permissions.network_declared", skill)
        self.assertNotIn("dependencies.bounded", skill)
        local_mcp = resource("mcp", mcp={"transport": "stdio", "command": ["x"], "tools": [], "config_schema": {}})
        self.assertIsNone(verdict(local_mcp, "permissions.network_declared"))

    def test_every_rule_has_an_explanation_and_a_fix(self) -> None:
        self.assertEqual(len({check.check_id for check in CHECKS}), len(CHECKS))
        for check in CHECKS:
            with self.subTest(check.check_id):
                self.assertTrue(check.title and check.why and check.fix)

    def test_weights_follow_severity(self) -> None:
        report = assess(resource(aliases=[], body="# P\n"))
        expected = sum(2 if f.severity == WARNING else 1 for f in report.findings if f.passed)
        self.assertEqual(report.passed_weight, expected)


class BoilerplateTests(unittest.TestCase):
    STOCK = "Report only what the input supports."

    def corpus(self, copies: int) -> CorpusContext:
        items = [
            resource(slug=f"r{i}", body=f"# R{i}\n\n{self.STOCK}\nStep one for {i}.\n") for i in range(copies)
        ]
        return CorpusContext.from_resources(items)

    def test_the_threshold_is_the_duplicate_detectors(self) -> None:
        below = self.corpus(TEMPLATE_MIN_RESOURCES - 1)
        at = self.corpus(TEMPLATE_MIN_RESOURCES)
        stock = self.STOCK.casefold()
        self.assertNotIn(stock, below.boilerplate_segments)
        self.assertIn(stock, at.boilerplate_segments)

    def test_boilerplate_does_not_count_towards_a_body(self) -> None:
        stock = "\n".join([self.STOCK, "Keep the answer short.", "Cite the input."])
        item = resource(body=f"# P\n\n{stock}\n")
        context = CorpusContext.from_resources(
            [resource(slug=f"r{i}", body=f"# R\n\n{stock}\n") for i in range(TEMPLATE_MIN_RESOURCES)]
        )
        self.assertTrue(verdict(item, "body.substantive").passed)
        self.assertFalse(verdict(item, "body.substantive", context).passed)

    def test_a_stock_field_description_is_no_description(self) -> None:
        stock = "Caller-supplied input for this example resource."
        items = [resource(slug=f"r{i}", inputs=[{"name": "request", "type": "text", "description": stock}])
                 for i in range(TEMPLATE_MIN_RESOURCES)]
        context = CorpusContext.from_resources(items)
        finding = verdict(items[0], "interface.described", context)
        self.assertFalse(finding.passed)
        self.assertIn("通用套话", finding.evidence[0])


class ReportOnlyTests(unittest.TestCase):
    def test_no_governance_code_imports_the_check_up(self) -> None:
        """PRD §7.1: quality is reported, never a condition of a lifecycle transition."""

        importers = []
        for path in (ROOT / "apps").rglob("*.py"):
            if path.name == "quality.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("quality"):
                    importers.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(importers, [])


class CorpusTests(unittest.TestCase):
    def test_input_order_does_not_change_any_verdict(self) -> None:
        items = [resource(slug=f"r{i}", aliases=[] if i % 2 else ["x"]) for i in range(6)]
        forward = assess_corpus(items).reports
        backward = assess_corpus(list(reversed(items))).reports
        self.assertEqual(forward, backward)

    def test_the_archived_report_reproduces_in_another_process(self) -> None:
        env = dict(os.environ, PYTHONHASHSEED="4242", PYTHONIOENCODING="utf-8")
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "assess_resource_quality.py"), "--check-baseline"],
            capture_output=True, encoding="utf-8", env=env, cwd=str(ROOT), timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("baseline reproduced", completed.stdout)


if __name__ == "__main__":
    unittest.main()
