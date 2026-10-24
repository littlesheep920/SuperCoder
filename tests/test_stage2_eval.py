"""Tests for the retrieval evaluation runner (acceptance B7, B8).

Two properties matter most and are easy to get wrong:

* **B8 raw archive** — the report must contain every query's ranked hits,
  judgments, and latency, not just aggregate numbers.
* **B7 reproducibility** — two runs over the same inputs must produce identical
  rankings and metrics.

A third property is about honesty: a run that returns nothing must not report a
perfect Hit@5. That exact mistake happened during development and is now a test.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_retrieval_eval.py"


def _run(profile: str, out_dir: Path) -> dict:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--profile", profile, "--out", str(out_dir)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"evaluation failed ({completed.returncode}):\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    files = sorted(out_dir.glob(f"stage2-retrieval-{profile}-*.json"))
    if not files:
        raise AssertionError(f"no report written to {out_dir}")
    return json.loads(files[-1].read_text(encoding="utf-8"))


class EvaluationRunnerTests(unittest.TestCase):
    def test_report_archives_every_query(self) -> None:
        """B8: per-query evidence, not only aggregates."""

        with tempfile.TemporaryDirectory() as tmp:
            report = _run("keyword", Path(tmp))

        self.assertEqual(len(report["per_query"]), report["dataset"]["query_count"])
        for entry in report["per_query"]:
            for key in (
                "query_id",
                "query",
                "ranked_ids",
                "relevant_ids",
                "graded",
                "first_relevant_rank",
                "reciprocal_rank",
                "ndcg_at_10",
                "took_ms",
            ):
                self.assertIn(key, entry, msg=f"{entry.get('query_id')} missing {key}")

    def test_report_records_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = _run("keyword", Path(tmp))

        dataset = report["dataset"]
        self.assertTrue(dataset["queries_sha256_16"])
        self.assertTrue(dataset["qrels_sha256_16"])
        config = report["config"]
        self.assertIn("embedding_provider", config)
        self.assertIn("seed", config)
        self.assertGreater(config["corpus_size"], 0)

    def test_two_runs_agree(self) -> None:
        """B7: same inputs must produce the same rankings and metrics."""

        with tempfile.TemporaryDirectory() as first_tmp, tempfile.TemporaryDirectory() as second_tmp:
            first = _run("keyword", Path(first_tmp))
            second = _run("keyword", Path(second_tmp))

        self.assertEqual(first["metrics"], second["metrics"])
        self.assertEqual(
            [(q["query_id"], q["ranked_ids"]) for q in first["per_query"]],
            [(q["query_id"], q["ranked_ids"]) for q in second["per_query"]],
        )

    def test_empty_results_never_score_as_hits(self) -> None:
        """Guard the defect found while building this: Hit@5 = 1.0 with 0 results."""

        with tempfile.TemporaryDirectory() as tmp:
            report = _run("keyword", Path(tmp))

        metrics = report["metrics"]
        if metrics.get("zero_result_queries"):
            hits_without_results = [
                q for q in report["per_query"] if not q["ranked_ids"] and q["reciprocal_rank"] > 0
            ]
            self.assertEqual(hits_without_results, [])
        self.assertLessEqual(metrics["hit_at_5"], 1.0)
        self.assertGreaterEqual(metrics["hit_at_5"], 0.0)

    def test_judgments_are_graded_and_joinable(self) -> None:
        """Every query must have at least one judgment, and IDs must join."""

        with tempfile.TemporaryDirectory() as tmp:
            report = _run("keyword", Path(tmp))

        for entry in report["per_query"]:
            self.assertTrue(entry["graded"], msg=f"{entry['query_id']} has no judgments")
            self.assertTrue(entry["relevant_ids"], msg=f"{entry['query_id']} has no relevant doc")
            # Any retrieved document that also has a judgment must use the same
            # identifier format, otherwise the join silently fails.
            for resource_id in entry["ranked_ids"]:
                if resource_id in entry["graded"]:
                    self.assertIn("@", resource_id)

    def test_honesty_flag_marks_offline_baseline(self) -> None:
        """An offline hashing provider must never be presented as semantic quality."""

        with tempfile.TemporaryDirectory() as tmp:
            report = _run("keyword", Path(tmp))

        honesty = report["honesty"]
        if not honesty["semantic_quality_claimed"]:
            self.assertIn("pipeline baseline", honesty["note"])


if __name__ == "__main__":
    unittest.main()
