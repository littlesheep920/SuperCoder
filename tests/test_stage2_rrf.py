"""Tests for Reciprocal Rank Fusion (acceptance item B2: hand-computed math).

Every numeric expectation below is derived by hand from the documented formula

    RRF(d) = Σ_i weight_i / (k + rank_i(d))

with the default k = 60. A test that only checked "ordering is plausible" would
pass for a wrong formula, so the exact values are asserted.
"""

from __future__ import annotations

import unittest

from apps.api.search.rrf import RRF_K, RankedList, fuse


class FormulaTests(unittest.TestCase):
    def test_single_list_ranks_by_formula(self) -> None:
        """1/(60+1), 1/(60+2), 1/(60+3) for the three positions."""

        results = fuse([RankedList(name="bm25", keys=("a", "b", "c"))])

        self.assertEqual([item.key for item in results], ["a", "b", "c"])
        self.assertAlmostEqual(results[0].score, 1 / 61, places=8)
        self.assertAlmostEqual(results[1].score, 1 / 62, places=8)
        self.assertAlmostEqual(results[2].score, 1 / 63, places=8)

    def test_document_in_both_lists_sums_both_contributions(self) -> None:
        """'a' is rank 1 in both lists: 1/61 + 1/61 = 2/61."""

        results = fuse(
            [
                RankedList(name="keyword", keys=("a", "b")),
                RankedList(name="semantic", keys=("a", "c")),
            ]
        )

        by_key = {item.key: item for item in results}
        self.assertAlmostEqual(by_key["a"].score, 2 / 61, places=8)
        self.assertAlmostEqual(by_key["b"].score, 1 / 62, places=8)
        self.assertAlmostEqual(by_key["c"].score, 1 / 62, places=8)

    def test_missing_document_contributes_nothing(self) -> None:
        results = fuse(
            [
                RankedList(name="keyword", keys=("only_in_keyword",)),
                RankedList(name="semantic", keys=("other",)),
            ]
        )

        by_key = {item.key: item for item in results}
        self.assertEqual(set(by_key), {"only_in_keyword", "other"})
        self.assertAlmostEqual(by_key["only_in_keyword"].score, 1 / 61, places=8)

    def test_weights_scale_contributions(self) -> None:
        """A 2.0 weight doubles that list's contribution."""

        results = fuse(
            [
                RankedList(name="heavy", keys=("a",), weight=2.0),
                RankedList(name="light", keys=("b",), weight=0.5),
            ]
        )

        by_key = {item.key: item for item in results}
        self.assertAlmostEqual(by_key["a"].score, 2.0 / 61, places=8)
        self.assertAlmostEqual(by_key["b"].score, 0.5 / 61, places=8)

    def test_custom_k_is_honoured(self) -> None:
        """With k=0 the score is exactly 1/rank."""

        results = fuse([RankedList(name="x", keys=("a", "b"))], k=0)

        self.assertAlmostEqual(results[0].score, 1.0, places=8)
        self.assertAlmostEqual(results[1].score, 0.5, places=8)


class OrderingTests(unittest.TestCase):
    def test_agreement_between_lists_wins(self) -> None:
        """A document ranked well by both lists beats one ranked by only one."""

        results = fuse(
            [
                RankedList(name="keyword", keys=("agreed", "keyword_only")),
                RankedList(name="semantic", keys=("agreed", "semantic_only")),
            ]
        )

        self.assertEqual(results[0].key, "agreed")

    def test_ties_are_broken_by_key_for_determinism(self) -> None:
        """B7: equal scores must order identically on every run."""

        results = fuse(
            [
                RankedList(name="k", keys=("z", "y")),
                RankedList(name="s", keys=("y", "z")),
            ]
        )

        # Both keys appear at ranks 1 and 2 across the two lists, so their fused
        # scores are identical; the key tie-break decides.
        self.assertEqual([item.key for item in results], ["y", "z"])
        self.assertAlmostEqual(results[0].score, results[1].score, places=9)

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(fuse([]), [])
        self.assertEqual(fuse([RankedList(name="k", keys=())]), [])

    def test_zero_and_negative_weights_are_ignored(self) -> None:
        results = fuse(
            [
                RankedList(name="zero", keys=("a",), weight=0.0),
                RankedList(name="negative", keys=("b",), weight=-1.0),
                RankedList(name="normal", keys=("c",), weight=1.0),
            ]
        )

        self.assertEqual([item.key for item in results], ["c"])


class RobustnessTests(unittest.TestCase):
    def test_duplicate_key_within_one_list_counts_once(self) -> None:
        """A duplicated entry must not be able to inflate its own score."""

        duplicated = fuse([RankedList(name="k", keys=("a", "a", "a"))])
        once = fuse([RankedList(name="k", keys=("a",))])

        self.assertAlmostEqual(duplicated[0].score, once[0].score, places=9)

    def test_contributions_are_reported_per_source(self) -> None:
        results = fuse(
            [
                RankedList(name="keyword", keys=("a",)),
                RankedList(name="semantic", keys=("a",)),
            ]
        )

        self.assertEqual(set(results[0].contributions), {"keyword", "semantic"})

    def test_negative_k_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            fuse([RankedList(name="k", keys=("a",))], k=-1)

    def test_default_k_matches_documented_constant(self) -> None:
        self.assertEqual(RRF_K, 60)


if __name__ == "__main__":
    unittest.main()
