"""Keep the PRD truthful about what the code actually does.

The PRD is what people read to decide what exists. It has drifted from reality
twice in this project: Qdrant/Redis were described as "disabled by default" when
no client code existed at all, and the metric table said "baseline to be
established" long after a real baseline had been measured.

These tests assert facts, never exact prose. They check that the numbers the PRD
quotes equal the archived baselines, that the counts it states equal the
repository, and that unimplemented components are not described as merely
switched off. Rewording the document freely is expected; contradicting the code
is not.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRD = ROOT / "docs" / "PRD.md"
OPENAPI = ROOT / "apps" / "api" / "openapi.stage0.yaml"


def baseline(profile: str) -> dict:
    path = ROOT / "evals" / "results" / f"BASELINE-stage2-retrieval-{profile}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def route_count() -> int:
    total = 0
    for source in (ROOT / "apps" / "api" / "routers").glob("*.py"):
        total += len(
            re.findall(r"@router\.(?:get|post|patch|put|delete)\(", source.read_text(encoding="utf-8"))
        )
    return total


class MetricDisclosureTests(unittest.TestCase):
    """§7 quotes measured numbers together with their limits."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = PRD.read_text(encoding="utf-8")

    def test_hybrid_hit_at_5_is_quoted(self) -> None:
        value = format(baseline("hybrid")["metrics"]["hit_at_5"], ".2f")
        self.assertIn(value, self.text)

    def test_keyword_contrast_is_quoted(self) -> None:
        """Keyword must appear as the low baseline that justifies semantics."""

        value = format(baseline("keyword")["metrics"]["hit_at_5"], ".2f")
        self.assertIn(value, self.text)
        self.assertIn("关键词", self.text)

    def test_latency_overrun_is_disclosed(self) -> None:
        """While P95 exceeds the target, the PRD must say it is not met."""

        hybrid = baseline("hybrid")["metrics"]["p95_latency_ms"]
        self.assertGreater(hybrid, 1500, "latency is now within target: update the PRD")
        self.assertRegex(self.text, r"未达|超出|超过")

    def test_small_sample_caveat_accompanies_the_score(self) -> None:
        """A 1.00 on six resources must never stand without its limits."""

        self.assertIn("6 项", self.text)
        self.assertRegex(self.text, r"不可作为能力宣传|不得作为|小样本")


class ContractCoverageTests(unittest.TestCase):
    """§11 states coverage; the numbers must match the repository."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = PRD.read_text(encoding="utf-8")
        cls.frozen = len(re.findall(r'"operationId"\s*:', OPENAPI.read_text(encoding="utf-8")))
        cls.routes = route_count()

    def test_frozen_operation_count_is_stated(self) -> None:
        self.assertRegex(self.text, rf"{self.frozen}\s*个操作")

    def test_implemented_route_count_is_stated(self) -> None:
        self.assertRegex(self.text, rf"{self.routes}\s*个路由")


class StatusHonestyTests(unittest.TestCase):
    """Guards against the specific drift modes found in review."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = PRD.read_text(encoding="utf-8")

    def test_qdrant_and_redis_are_marked_not_implemented(self) -> None:
        """Both have a config flag but no client code.

        Saying "disabled by default" invites the reader to assume an adapter
        exists behind the switch. The status table must say otherwise.
        """

        for component in ("Qdrant", "Redis"):
            with self.subTest(component=component):
                row = re.search(rf"^\|\s*{component}\s*\|([^|\n]*)\|", self.text, re.MULTILINE)
                self.assertIsNotNone(row, f"no status row found for {component}")
                self.assertIn("未实现", row.group(1))

    def test_no_client_library_is_imported(self) -> None:
        offenders = []
        for source in (ROOT / "apps").rglob("*.py"):
            if "__pycache__" in source.parts:
                continue
            for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"\b(import|from)\s+(qdrant_client|redis)\b", line):
                    offenders.append(f"{source.relative_to(ROOT)}:{number}")
        self.assertEqual(
            offenders, [], "a Qdrant/Redis client appeared; update the status table"
        )

    def test_status_matrix_covers_every_maturity_level(self) -> None:
        self.assertIn("实现状态", self.text)
        for level in ("已实现并验证", "未开始"):
            self.assertIn(level, self.text)

    def test_unstarted_domains_are_named(self) -> None:
        """The big gaps must be visible, not omitted."""

        for domain in ("能力发现 Agent", "工作流", "平台评测"):
            with self.subTest(domain=domain):
                self.assertIn(domain, self.text)

    def test_forbidden_phrases_are_listed(self) -> None:
        """The document itself must constrain how results are described."""

        self.assertIn("禁止这样说", self.text)


class DocumentShapeTests(unittest.TestCase):
    """The PRD is a specification, not a changelog."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = PRD.read_text(encoding="utf-8")

    def test_no_changelog_or_revision_summary(self) -> None:
        """A reader meeting this document fresh should not wade through history."""

        for phrase in ("变更记录", "修订摘要", "V2.1", "V2.2", "取代"):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, self.text)

    def test_core_sections_exist(self) -> None:
        for heading in (
            "## 1. 产品定义",
            "## 2. 核心设计原则",
            "## 4. 领域模型",
            "## 5. 功能范围",
            "## 7. 质量指标",
            "## 9. 安全要求",
            "## 11. 实现状态",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, self.text)


if __name__ == "__main__":
    unittest.main()
