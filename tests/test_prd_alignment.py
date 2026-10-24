"""Keep the PRD honest about what the code actually does.

The PRD is the document people read to decide what exists. Twice during this
project it drifted from the code: Qdrant/Redis were described as "disabled by
default" when no client code existed at all, and metric tables said "baseline to
be established" long after a real baseline was measured.

These tests fail when a specific, checkable claim in the PRD no longer matches
the repository, so drift is caught at commit time instead of during a review.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRD = ROOT / "docs" / "PRD.md"
OPENAPI = ROOT / "apps" / "api" / "openapi.stage0.yaml"


class PrdMetricTests(unittest.TestCase):
    """§7.2 quotes measured numbers; they must equal the archived baselines."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = PRD.read_text(encoding="utf-8")

    def _baseline(self, profile: str) -> dict:
        path = ROOT / "evals" / "results" / f"BASELINE-stage2-retrieval-{profile}.json"
        self.assertTrue(path.is_file(), f"missing baseline file: {path.name}")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_hybrid_hit_at_5_matches_baseline(self) -> None:
        baseline = self._baseline("hybrid")
        hit = baseline["metrics"]["hit_at_5"]
        formatted = format(hit, ".2f")
        # The table emphasises the current value with bold: `| **1.00** |`.
        self.assertIn("检索 Hit@5（hybrid）", self.text)
        self.assertRegex(self.text, rf"\|\s*\*?\*?{re.escape(formatted)}\*?\*?\s*\|")

    def test_keyword_contrast_is_recorded(self) -> None:
        """The keyword baseline is the evidence that semantics matter."""

        keyword = self._baseline("keyword")["metrics"]["hit_at_5"]
        self.assertIn("关键词 Hit@5", self.text)
        self.assertIn(format(keyword, ".2f"), self.text)

    def test_latency_overrun_is_disclosed(self) -> None:
        """P95 exceeds the 1.5 s target, and the PRD must say so, not hide it."""

        hybrid = self._baseline("hybrid")["metrics"]["p95_latency_ms"]
        self.assertGreater(hybrid, 1500, "if latency is now within target, update the PRD")
        self.assertIn("未达", self.text)

    def test_small_sample_caveat_is_present(self) -> None:
        """A 1.00 score on 6 resources must never appear without its limits."""

        self.assertIn("不得作为能力声明", self.text)
        self.assertIn("6 项", self.text)


class PrdContractTests(unittest.TestCase):
    """§5 states contract coverage; the numbers must match the repository."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = PRD.read_text(encoding="utf-8")
        cls.frozen_operations = len(re.findall(r'"operationId"\s*:', OPENAPI.read_text(encoding="utf-8")))
        cls.routes = 0
        for source in (ROOT / "apps" / "api" / "routers").glob("*.py"):
            cls.routes += len(
                re.findall(r"@router\.(?:get|post|patch|put|delete)\(", source.read_text(encoding="utf-8"))
            )

    def test_frozen_operation_count_is_stated_correctly(self) -> None:
        self.assertIn(f"**{self.frozen_operations} 个操作**", self.text)

    def test_implemented_route_count_is_stated_correctly(self) -> None:
        self.assertIn(f"**{self.routes} 个路由**", self.text)


class PrdHonestyTests(unittest.TestCase):
    """Guards against the specific drift modes found during review."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = PRD.read_text(encoding="utf-8")

    def test_unimplemented_infrastructure_is_labelled_unimplemented(self) -> None:
        """Qdrant/Redis have configuration flags but no client code.

        Describing them as "disabled by default" invites the reader to assume a
        working adapter sits behind the flag. The PRD must say "not implemented".
        """

        for name in ("Qdrant", "Redis"):
            with self.subTest(component=name):
                self.assertRegex(
                    self.text,
                    rf"\*\*{name}\*\* \| ❌ \*\*完全未实现\*\*",
                    f"{name} must be explicitly labelled as not implemented",
                )

    def test_no_client_code_pretends_to_use_qdrant_or_redis(self) -> None:
        offenders: list[str] = []
        for source in (ROOT / "apps").rglob("*.py"):
            if "__pycache__" in source.parts:
                continue
            for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"'):
                    continue
                if re.search(r"\b(import|from)\s+(qdrant_client|redis)\b", stripped):
                    offenders.append(f"{source.relative_to(ROOT)}:{number}")
        self.assertEqual(
            offenders,
            [],
            "Qdrant/Redis client code appeared; the PRD's status table must be updated",
        )

    def test_implementation_status_matrix_exists(self) -> None:
        self.assertIn("## 12. 实现状态矩阵", self.text)
        self.assertIn("仅配置/骨架", self.text)

    def test_version_is_current(self) -> None:
        self.assertIn("V2.2（重置版）", self.text)


if __name__ == "__main__":
    unittest.main()
