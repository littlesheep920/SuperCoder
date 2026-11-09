"""The Stage 5 duplicate dataset is what its spec says, valid, honest about its labels, and leak-free.

The dataset was frozen before the detector was written (see docs/STAGE5_ACCEPTANCE_REPORT.md). These
tests keep it that way: the generated files must equal what the spec produces, every record must pass
the evaluation schema, no resource may sit in both the dev and the test split, and every label must
keep saying that an AI assistant drafted it and no person has reviewed it yet.
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest
from collections import Counter

import jsonschema

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_duplicate_dataset as dataset  # noqa: E402

POSITIVE = {"exact_duplicate", "near_duplicate"}


def read_jsonl(relative: str) -> list[dict]:
    return [json.loads(line) for line in (ROOT / relative).read_text(encoding="utf-8").splitlines() if line]


class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = json.loads((ROOT / dataset.SPEC).read_text(encoding="utf-8"))
        cls.pairs = read_jsonl(dataset.PAIRS)
        cls.labels = read_jsonl(dataset.LABELS)
        cls.by_pair = {label["pair_id"]: label for label in cls.labels}
        cls.corpus = dataset.tracked_manifests(ROOT)

    def split(self, pair: dict) -> str:
        return pair["extensions"]["x-split"]

    def test_the_generated_files_are_exactly_what_the_spec_produces(self) -> None:
        self.assertEqual(dataset.stale(ROOT, dataset.build(ROOT)), [],
                         "regenerate with: python scripts/build_duplicate_dataset.py")

    def test_every_record_passes_the_evaluation_schema(self) -> None:
        schema = json.loads((ROOT / "evals/schemas/evaluation-records.schema.json").read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        for record in self.pairs + self.labels:
            with self.subTest(record=record.get("pair_id"), type=record["record_type"]):
                self.assertEqual([error.message for error in validator.iter_errors(record)], [])

    def test_ids_are_unique_and_every_label_joins_one_pair(self) -> None:
        pair_ids = [pair["pair_id"] for pair in self.pairs]
        self.assertEqual(len(pair_ids), len(set(pair_ids)))
        self.assertEqual(sorted(self.by_pair), sorted(pair_ids))
        self.assertEqual(len(self.labels), len({label["label_id"] for label in self.labels}))

    def test_each_unordered_pair_appears_once_in_canonical_order(self) -> None:
        keys = [(pair["left_resource"]["resource_id"], pair["right_resource"]["resource_id"]) for pair in self.pairs]
        self.assertTrue(all(left < right for left, right in keys), "left must sort before right (FORMAT.md §6.1)")
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_reference_is_a_tracked_resource_or_a_variant(self) -> None:
        variants = {f"{variant['kind']}/{variant['slug']}" for variant in self.spec["variants"]}
        for pair in self.pairs:
            for side in ("left_resource", "right_resource"):
                reference = pair[side]
                ref = f"{reference['kind']}/{reference['slug']}"
                with self.subTest(pair=pair["pair_id"], ref=ref):
                    self.assertIn(ref, set(self.corpus) | variants)
                    self.assertEqual(reference["resource_id"], f"{ref}@{reference['version']}")

    def test_no_resource_is_in_both_splits(self) -> None:
        splits: dict[str, set[str]] = {}
        for pair in self.pairs:
            for side in ("left_resource", "right_resource"):
                splits.setdefault(pair[side]["resource_id"], set()).add(self.split(pair))
        self.assertEqual({ref: s for ref, s in splits.items() if len(s) > 1}, {})

    def test_every_label_says_an_ai_assistant_drafted_it_and_nobody_reviewed_it(self) -> None:
        reviewed = set(self.spec.get("reviewed_pairs", []))
        for label in self.labels:
            with self.subTest(label=label["label_id"]):
                self.assertNotIn(label["judgment"]["assessor_type"], {"human", "expert-adjudicated"})
                self.assertEqual(label["extensions"]["x-label-author"], "ai-assistant")
                self.assertEqual(label["provenance"]["source_type"], "synthetic")
                if label["pair_id"] not in reviewed:
                    self.assertEqual(label["judgment"]["adjudication_status"], "draft")
                    self.assertEqual(label["extensions"]["x-human-review-status"], "pending")

    def test_the_variants_are_valid_resource_manifests_that_collide_with_nothing(self) -> None:
        schema = json.loads((ROOT / "schemas/resource.schema.json").read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        slugs = {ref.split("/", 1)[1] for ref in self.corpus}
        for path in sorted((ROOT / dataset.REGISTRY).glob("*/*/resource.yaml")):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            with self.subTest(variant=manifest["slug"]):
                self.assertEqual([error.message for error in validator.iter_errors(manifest)], [])
                self.assertNotIn(manifest["slug"], slugs)
                self.assertTrue((path.parent / manifest["entrypoint"]).is_file())

    def test_a_chinese_re_registration_does_not_reuse_its_base_slug(self) -> None:
        """A shared slug would hand a lexical detector the answer to the cross-language slice."""

        for variant in self.spec["variants"]:
            if variant["category"] == "cross-language":
                base_slug = variant["base"].split("/", 1)[1].removeprefix("example-")
                with self.subTest(variant=variant["slug"]):
                    self.assertNotIn(base_slug, variant["slug"])

    def test_the_dataset_has_the_size_and_balance_the_report_states(self) -> None:
        self.assertEqual(Counter(variant["category"] for variant in self.spec["variants"]),
                         Counter({"rewrite": 10, "cross-language": 8, "lookalike": 7, "copy": 3, "kind-change": 3, "reuse": 2}))
        self.assertEqual(len(self.pairs), 111)
        verdict = Counter()
        for pair in self.pairs:
            label = self.by_pair[pair["pair_id"]]["judgment"]["label"]
            kind = "uncertain" if label == "uncertain" else ("positive" if label in POSITIVE else "negative")
            verdict[(self.split(pair), kind)] += 1
        self.assertEqual(verdict, Counter({("dev", "positive"): 15, ("dev", "negative"): 37, ("dev", "uncertain"): 2,
                                           ("test", "positive"): 16, ("test", "negative"): 41}))

    def test_the_stage0_files_are_untouched(self) -> None:
        """The four Stage 0 pairs keep their own dataset version and their own file."""

        for record in read_jsonl("evals/duplicates/pairs.jsonl") + read_jsonl("evals/duplicates/labels.jsonl"):
            self.assertEqual(record["dataset_version"], "0.1.0")


if __name__ == "__main__":
    unittest.main()
