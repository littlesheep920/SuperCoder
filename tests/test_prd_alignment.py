"""Keep the PRD truthful about what the code actually does.

The PRD is what people read to decide what exists. It has drifted from reality
several times in this project: Qdrant/Redis were once described as "disabled by
default" when no client code existed, the metric table once said "baseline to be
established" long after a real baseline was measured, and latency claims once
outlived the optimisation that changed them.

These tests assert facts, never exact prose. They check that numbers quoted in
the PRD equal the archived baselines, that counts equal the repository, and that
unimplemented components are not described as merely switched off. Rewording the
document freely is expected; contradicting the archived data is not.
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


def eval_size() -> tuple[int, int]:
    """Return (query count, judgment count) from the evaluation dataset."""

    retrieval = ROOT / "evals" / "retrieval"

    def count(name: str) -> int:
        text = (retrieval / name).read_text(encoding="utf-8")
        return sum(1 for line in text.splitlines() if line.strip())

    return count("stage2_queries.jsonl"), count("stage2_qrels.jsonl")


class MetricDisclosureTests(unittest.TestCase):
    """§7 quotes measured numbers together with their limits."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = PRD.read_text(encoding="utf-8")

    def test_quoted_metrics_match_the_archived_baseline(self) -> None:
        """Every quoted metric must round to the archived value at some precision."""

        for profile in ("hybrid", "keyword"):
            metrics = baseline(profile)["metrics"]
            with self.subTest(profile=profile):
                for key in ("hit_at_5", "mrr_at_10", "ndcg_at_10"):
                    # The table may quote 2 or 3 decimals; either is acceptable
                    # as long as it agrees with the archived value.
                    candidates = {format(metrics[key], f".{d}f") for d in (2, 3)}
                    self.assertTrue(
                        any(value in self.text for value in candidates),
                        f"{profile}.{key}={metrics[key]} not quoted "
                        f"(looked for {sorted(candidates)})",
                    )

    def test_baseline_describes_the_current_corpus(self) -> None:
        """The PRD must describe the corpus the archived baseline measured."""

        config = baseline("hybrid")["config"]
        corpus_size = config.get("corpus_size")
        self.assertIsInstance(corpus_size, int)
        self.assertIn(f"{corpus_size} 项", self.text)

    def test_latency_claim_is_present_and_caveated(self) -> None:
        """The quoted latency must match the archived measurement."""

        p95 = baseline("hybrid")["metrics"]["p95_latency_ms"]
        self.assertIn(str(p95), self.text)
        for caveat in ("预热", "尖峰"):
            with self.subTest(caveat=caveat):
                self.assertIn(caveat, self.text)

    def test_corpus_provenance_is_disclosed(self) -> None:
        """Example content must never be presented as real enterprise data."""

        self.assertRegex(self.text, r"示例|example")
        self.assertIn("单标注者", self.text)

    def test_query_gap_is_disclosed_when_present(self) -> None:
        """If single-answer queries remain, the PRD must disclose the share."""

        queries, qrels = eval_size()
        if queries < 300:
            self.assertRegex(self.text, r"未完全达成|未达|未达到")
        # Regardless of total size, the single-answer share must be stated.
        self.assertRegex(self.text, r"只有 1 个相关资源|单答案")


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
        self.assertEqual(offenders, [], "a Qdrant/Redis client appeared; update the status table")

    def test_status_matrix_covers_every_maturity_level(self) -> None:
        self.assertIn("实现状态", self.text)
        for level in ("已实现并验证", "未开始"):
            self.assertIn(level, self.text)

    def test_unstarted_domains_are_named(self) -> None:
        for domain in ("能力发现 Agent", "工作流", "平台评测"):
            with self.subTest(domain=domain):
                self.assertIn(domain, self.text)

    def test_forbidden_phrases_are_listed(self) -> None:
        self.assertIn("禁止这样说", self.text)


class DocumentShapeTests(unittest.TestCase):
    """The PRD is a specification, not a changelog."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = PRD.read_text(encoding="utf-8")

    def test_no_changelog_or_revision_summary(self) -> None:
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
