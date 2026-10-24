"""Tests for BM25 keyword scoring (acceptance item B1: ranking correctness).

These assert *ordering*, not just that a score is non-zero. A scorer that ranks
randomly would pass a "score > 0" check but fail every test here.
"""

from __future__ import annotations

import unittest

from apps.api.search import IndexField
from apps.api.search.bm25 import BM25Index, BM25Params
from apps.api.search.chunking import build_index_document


def _doc(
    *,
    resource_id: str,
    name: str,
    description: str = "",
    aliases: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
    body: str = "",
    version: str = "1.0.0",
    updated_at_epoch: float = 0.0,
) -> object:
    return build_index_document(
        resource_public_id=resource_id,
        version_public_id=f"{resource_id}-v1",
        kind="skill",
        slug=resource_id,
        name=name,
        version=version,
        status="published",
        owner_team="platform-ai",
        description=description,
        manifest={
            "aliases": list(aliases),
            "tags": list(tags),
            "categories": ["software-development"],
            "runtimes": ["kiro"],
        },
        type_spec={},
        files={"SKILL.md": f"# 说明\n\n{body}\n"} if body else {},
        artifact_hash=f"hash-{resource_id}",
        manifest_snapshot_hash=f"snap-{resource_id}",
        updated_at_epoch=updated_at_epoch,
    )


class RankingTests(unittest.TestCase):
    def test_name_match_outranks_body_match(self) -> None:
        """B1: field weighting must actually affect order."""

        by_name = _doc(resource_id="a", name="代码审查", body="其它内容")
        by_body = _doc(resource_id="b", name="其它工具", body="代码审查")
        index = BM25Index([by_name, by_body])

        results = index.search("代码审查")

        self.assertEqual([hit.document.resource_public_id for hit in results], ["a", "b"])

    def test_name_match_outranks_description_match(self) -> None:
        by_name = _doc(resource_id="a", name="数据库工具")
        by_desc = _doc(resource_id="b", name="其它", description="数据库工具")
        index = BM25Index([by_name, by_desc])

        results = index.search("数据库工具")

        self.assertEqual([hit.document.resource_public_id for hit in results], ["a", "b"])

    def test_higher_term_frequency_ranks_first(self) -> None:
        once = _doc(resource_id="a", name="甲", body="测试")
        twice = _doc(resource_id="b", name="乙", body="测试 测试")
        index = BM25Index([once, twice])

        results = index.search("测试")

        self.assertEqual([hit.document.resource_public_id for hit in results], ["b", "a"])

    def test_rarer_term_contributes_more_than_common_term(self) -> None:
        """IDF must discount terms that appear everywhere."""

        common = [_doc(resource_id=f"c{n}", name=f"工具{n}", body="通用词") for n in range(5)]
        rare = _doc(resource_id="rare", name="专用", body="罕见词")
        index = BM25Index([*common, rare])

        # Both terms appear in exactly one document except "通用词", which is in
        # five. A query containing only the rare term must clearly beat a query
        # with only the common one for the same single-document target.
        rare_hit = index.search("罕见词")[0]
        common_hit = index.search("通用词")[0]
        self.assertGreater(rare_hit.score, common_hit.score)

    def test_short_document_is_not_penalised_into_invisibility(self) -> None:
        short = _doc(resource_id="s", name="短", body="关键词")
        long = _doc(resource_id="l", name="长", body="填充 " * 200 + "关键词")
        index = BM25Index([short, long])

        results = index.search("关键词")

        self.assertEqual(results[0].document.resource_public_id, "s")

    def test_no_match_returns_empty(self) -> None:
        index = BM25Index([_doc(resource_id="a", name="代码审查")])

        self.assertEqual(index.search("完全无关的词汇"), [])

    def test_empty_query_returns_empty(self) -> None:
        index = BM25Index([_doc(resource_id="a", name="代码审查")])

        self.assertEqual(index.search(""), [])
        self.assertEqual(index.search("   "), [])

    def test_ties_are_broken_deterministically_by_resource_id(self) -> None:
        """B7: identical inputs must produce identical output ordering."""

        docs = [_doc(resource_id=name, name="相同名称") for name in ("m", "a", "z")]
        index = BM25Index(docs)

        first = [hit.document.resource_public_id for hit in index.search("相同名称")]
        second = [hit.document.resource_public_id for hit in index.search("相同名称")]

        self.assertEqual(first, second)
        self.assertEqual(first, ["a", "m", "z"])

    def test_repeated_query_term_does_not_dominate(self) -> None:
        """A caller repeating a word must not multiply its own weight."""

        index = BM25Index([_doc(resource_id="a", name="代码审查")])

        once = index.search("审查")
        thrice = index.search("审查 审查 审查")

        self.assertEqual(once[0].score, thrice[0].score)


class EvidenceTests(unittest.TestCase):
    def test_matched_fields_reports_the_actual_match_location(self) -> None:
        index = BM25Index([_doc(resource_id="a", name="代码审查", description="审查工具")])

        hit = index.search("审查")[0]

        self.assertIn(IndexField.NAME.value, hit.matched_fields)
        self.assertIn(IndexField.DESCRIPTION.value, hit.matched_fields)

    def test_field_scores_are_exposed_for_debugging(self) -> None:
        index = BM25Index([_doc(resource_id="a", name="代码审查")])

        hit = index.search("审查")[0]

        self.assertIn(IndexField.NAME.value, hit.field_scores)
        self.assertGreater(hit.field_scores[IndexField.NAME.value], 0.0)

    def test_hit_carries_the_source_artifact_hash(self) -> None:
        """B9: every scored hit must identify immutable content."""

        index = BM25Index([_doc(resource_id="a", name="代码审查")])

        self.assertEqual(index.search("审查")[0].document.artifact_hash, "hash-a")

    def test_scores_are_sorted_descending(self) -> None:
        docs = [
            _doc(resource_id="a", name="代码审查", body="审查"),
            _doc(resource_id="b", name="代码", body="审查 审查 审查"),
        ]
        index = BM25Index(docs)

        scores = [hit.score for hit in index.search("审查")]

        self.assertEqual(scores, sorted(scores, reverse=True))


class ChinesMatchingTests(unittest.TestCase):
    def test_chinese_bigram_query_matches_chinese_document(self) -> None:
        index = BM25Index([_doc(resource_id="a", name="后端代码审查")])

        self.assertTrue(index.search("代码审查"))

    def test_partial_chinese_overlap_still_matches(self) -> None:
        """Bigrams give partial recall, which is the point of this tokenizer."""

        index = BM25Index([_doc(resource_id="a", name="后端代码审查")])

        self.assertTrue(index.search("后端审查"))

    def test_identifier_is_searchable_in_snake_and_camel_case(self) -> None:
        index = BM25Index([_doc(resource_id="a", name="apiKey 管理", body="resourceVersionId")])

        self.assertTrue(index.search("resource_version_id"))
        self.assertTrue(index.search("resourceVersionId"))


class ParameterTests(unittest.TestCase):
    def test_custom_params_are_honoured(self) -> None:
        docs = [_doc(resource_id="a", name="代码审查")]
        strict = BM25Index(docs, BM25Params(k1=0.5, b=0.1))
        loose = BM25Index(docs, BM25Params(k1=2.0, b=0.9))

        # Both must still find the document; k1 changes magnitude, not recall.
        self.assertTrue(strict.search("审查"))
        self.assertTrue(loose.search("审查"))

    def test_index_is_immutable_after_construction(self) -> None:
        index = BM25Index([_doc(resource_id="a", name="代码审查")])

        before = index.search("审查")
        index.search("完全不同的查询")
        after = index.search("审查")

        self.assertEqual([hit.score for hit in before], [hit.score for hit in after])


if __name__ == "__main__":
    unittest.main()
