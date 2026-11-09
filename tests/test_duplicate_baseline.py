"""The archived duplicate-detection baseline reproduces, pair by pair, in any process.

`evals/results/BASELINE-stage5-duplicates.json` is the evidence behind the Stage 5 figures. A figure
is only worth quoting if anyone can regenerate it, so these tests rerun the evaluation and compare
every pair's verdict and score with the archive. One of them reruns it in a child process with a
different hash seed: the first version of the detector broke ties in its evidence by set iteration
order, which changes from process to process, and only a cross-process comparison caught that.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import run_duplicate_eval as evaluation  # noqa: E402
from apps.api.search import duplicates  # noqa: E402


class BaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.baseline = json.loads(evaluation.BASELINE.read_text(encoding="utf-8"))

    def test_rerunning_the_evaluation_reproduces_every_pair(self) -> None:
        self.assertEqual(evaluation.baseline_differences(evaluation.build_report(ROOT), self.baseline), [])

    def test_it_reproduces_in_a_process_with_another_hash_seed(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_duplicate_eval.py"), "--check-baseline"],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=300,
            env={**os.environ, "PYTHONHASHSEED": "12345", "PYTHONIOENCODING": "utf-8"},
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("RESULT: PASS", completed.stdout)

    def test_the_archived_metrics_follow_from_the_archived_predictions(self) -> None:
        for split, result in self.baseline["splits"].items():
            with self.subTest(split=split):
                self.assertEqual(evaluation.score(result["predictions"]), result["metrics"])

    def test_the_archive_records_the_constants_the_detector_uses(self) -> None:
        detector = self.baseline["detector"]
        self.assertEqual(detector["weights"], duplicates.WEIGHTS)
        self.assertEqual(detector["duplicate_threshold"], duplicates.DUPLICATE_THRESHOLD)
        self.assertEqual(detector["text_floor"], duplicates.TEXT_FLOOR)
        self.assertEqual(detector["template_min_resources"], duplicates.TEMPLATE_MIN_RESOURCES)

    def test_the_archive_says_its_labels_are_drafts(self) -> None:
        self.assertIn("not reviewed by a person", self.baseline["label_status"])

    def test_the_archive_is_committed_like_the_other_baselines(self) -> None:
        self.assertRegex(evaluation.BASELINE.name, r"^BASELINE-.+\.json$")
        ignored = subprocess.run(["git", "check-ignore", "-q", str(evaluation.BASELINE)], cwd=str(ROOT))
        self.assertEqual(ignored.returncode, 1, "evals/results/*.json is ignored; baselines must not be")


if __name__ == "__main__":
    unittest.main()
