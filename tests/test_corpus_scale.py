"""Guard the expanded example corpus.

The corpus grew from 6 to 42 resources to check that the architecture still
behaves at a realistic size. These tests keep that expansion honest:

* the size stays in the intended band, so a silent deletion is noticed;
* all six kinds stay represented, so a "search" cannot succeed by only covering
  one type;
* every resource is clearly labelled as example content, so nothing here can be
  mistaken for a real enterprise asset (PRD §6);
* the evaluation set actually references the corpus, so metrics cannot be
  computed against resources that no longer exist.
"""

from __future__ import annotations

import json
import re
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "resources"
QUERIES = ROOT / "evals" / "retrieval" / "stage2_queries.jsonl"
QRELS = ROOT / "evals" / "retrieval" / "stage2_qrels.jsonl"

KINDS = {"skill", "mcp", "prompt", "agent", "workflow", "tool"}


def manifests() -> list[tuple[Path, dict]]:
    return [
        (path, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(RESOURCES.glob("*/*/resource.yaml"))
    ]


class CorpusSizeTests(unittest.TestCase):
    def test_corpus_size_is_in_the_intended_band(self) -> None:
        """The corpus must stay large enough for the metrics to mean something.

        The band widened from 30-50 to 150-400 when the evaluation set grew to
        several hundred queries: a corpus of forty cannot support that many
        distinct questions without them all pointing at the same resources.
        """

        count = len(manifests())
        self.assertGreaterEqual(count, 150, "corpus shrank below what the evaluation covers")
        self.assertLessEqual(count, 400, "corpus grew beyond what was evaluated")

    def test_all_six_kinds_are_represented(self) -> None:
        kinds = Counter(manifest["kind"] for _, manifest in manifests())
        self.assertEqual(set(kinds), KINDS)
        # Every kind should carry enough members for type filters to matter.
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.assertGreaterEqual(kinds[kind], 5, f"only {kinds[kind]} {kind} resources")

    def test_no_duplicate_slugs_across_kinds(self) -> None:
        """A slug is used in URLs and evaluation ids, so it must be unique."""

        slugs = [manifest["slug"] for _, manifest in manifests()]
        duplicates = {slug for slug in slugs if slugs.count(slug) > 1}
        self.assertEqual(duplicates, set())


class ExampleLabellingTests(unittest.TestCase):
    """Nothing in this corpus may masquerade as a real enterprise asset."""

    def test_expanded_resources_carry_the_example_prefix(self) -> None:
        for path, manifest in manifests():
            slug = manifest["slug"]
            if slug.startswith("demo-"):
                continue
            with self.subTest(slug=slug):
                self.assertTrue(
                    slug.startswith("example-"),
                    f"{slug} must be prefixed 'example-' or 'demo-' to signal its nature",
                )

    def test_expanded_resources_say_they_are_examples(self) -> None:
        """The description must state the nature, not rely on the slug alone."""

        for path, manifest in manifests():
            slug = manifest["slug"]
            if slug.startswith("demo-"):
                continue
            with self.subTest(slug=slug):
                description = manifest["description"].lower()
                self.assertIn("example", description)

    def test_expanded_resources_are_tagged_example(self) -> None:
        for path, manifest in manifests():
            if manifest["slug"].startswith("demo-"):
                continue
            with self.subTest(slug=manifest["slug"]):
                self.assertIn("example", manifest["tags"])


class EvaluationCoverageTests(unittest.TestCase):
    def _records(self, path: Path) -> list[dict]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_every_qrel_points_at_a_real_resource(self) -> None:
        """A judgment about a deleted resource would corrupt every metric."""

        known = {
            f"{manifest['kind']}/{manifest['slug']}@{manifest['version']}"
            for _, manifest in manifests()
        }
        unknown = [
            qrel["resource"]["resource_id"]
            for qrel in self._records(QRELS)
            if qrel["resource"]["resource_id"] not in known
        ]
        self.assertEqual(unknown, [], f"judgments reference missing resources: {unknown}")

    def test_every_query_has_at_least_one_judgment(self) -> None:
        judged = {qrel["query_id"] for qrel in self._records(QRELS)}
        missing = [query["query_id"] for query in self._records(QUERIES) if query["query_id"] not in judged]
        self.assertEqual(missing, [], f"queries without judgments: {missing}")

    def test_most_queries_have_multiple_relevant_resources(self) -> None:
        """Multiple correct answers is what makes ranking, not lookup, the task.

        The original six-resource set had ~1.1 relevant resources per query,
        which made Hit@5 nearly free.
        """

        relevant = Counter()
        for qrel in self._records(QRELS):
            if qrel["judgment"]["grade"] >= 2:
                relevant[qrel["query_id"]] += 1
        multi = sum(1 for count in relevant.values() if count >= 2)
        self.assertGreaterEqual(
            multi, 15, f"only {multi} queries have 2+ relevant resources; ranking is under-tested"
        )

    def test_query_ids_are_unique(self) -> None:
        ids = [query["query_id"] for query in self._records(QUERIES)]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
