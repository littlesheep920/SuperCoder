"""Tests for the highlight/excerpt mapping in the search endpoint.

Regression context: highlights were computed by splitting the query on
whitespace. A Chinese business query has no spaces, so the whole sentence became
one "term" that could never appear in a document. Every hit rendered
"（无摘要）" in the console even when the query matched semantically, which made
a working search look broken.
"""

from __future__ import annotations

import unittest

from apps.api.search.chunking import build_index_document
from apps.api.routers.search import _highlights, _summary_excerpt


def _doc(name: str, description: str = "", body: str = "") -> object:
    return build_index_document(
        resource_public_id="res-1",
        version_public_id="ver-1",
        kind="skill",
        slug="demo",
        name=name,
        version="1.0.0",
        status="published",
        owner_team="platform-ai",
        description=description,
        manifest={},
        type_spec={},
        files={"SKILL.md": f"# 说明\n\n{body}\n"} if body else {},
        artifact_hash="hash-1",
        manifest_snapshot_hash="snap-1",
    )


class HighlightTests(unittest.TestCase):
    def test_chinese_query_without_spaces_still_matches(self) -> None:
        """The regression: no whitespace must not mean no match."""

        document = _doc("代码审查", body="审查代码改动并给出证据")

        highlights = _highlights(document, "检查代码审查")

        self.assertTrue(highlights, "a Chinese query must produce a highlight")

    def test_excerpt_comes_from_the_matching_chunk(self) -> None:
        document = _doc("甲", body="这一段讲数据库迁移")

        highlights = _highlights(document, "数据库迁移")

        self.assertTrue(any("数据库" in excerpt for excerpt in highlights))

    def test_most_relevant_chunk_ranks_first(self) -> None:
        document = build_index_document(
            resource_public_id="res-2",
            version_public_id="ver-2",
            kind="skill",
            slug="demo",
            name="多段落",
            version="1.0.0",
            status="published",
            owner_team="platform-ai",
            description="",
            manifest={},
            type_spec={},
            files={
                "SKILL.md": (
                    "# 第一节\n\n无关内容\n\n"
                    "# 第二节\n\n文本统计与字频分析\n\n"
                    "# 第三节\n\n文本统计\n"
                )
            },
            artifact_hash="hash-2",
            manifest_snapshot_hash="snap-2",
        )

        highlights = _highlights(document, "文本统计 字频分析")

        self.assertTrue(highlights)
        # The chunk sharing both terms must beat the one sharing a single term.
        self.assertIn("字频", highlights[0])

    def test_no_match_returns_empty(self) -> None:
        document = _doc("甲", body="数据库迁移")

        self.assertEqual(_highlights(document, "完全无关的英文词汇 zzzz"), [])

    def test_blank_query_returns_empty(self) -> None:
        document = _doc("甲", body="内容")

        self.assertEqual(_highlights(document, "   "), [])

    def test_highlights_are_deterministic(self) -> None:
        """B7: replaying the same query must give the same excerpt."""

        document = _doc("甲", body="审查代码改动并给出证据")

        first = _highlights(document, "审查代码")
        second = _highlights(document, "审查代码")

        self.assertEqual(first, second)

    def test_excerpt_length_is_bounded(self) -> None:
        document = _doc("甲", body="审查" * 2000)

        for excerpt in _highlights(document, "审查"):
            self.assertLessEqual(len(excerpt), 200)


class SummaryExcerptTests(unittest.TestCase):
    def test_description_is_preferred(self) -> None:
        document = _doc("甲", description="这是资源描述", body="正文内容")

        self.assertEqual(_summary_excerpt(document), "这是资源描述")

    def test_falls_back_to_first_chunk(self) -> None:
        document = _doc("甲", description="", body="正文内容")

        self.assertIn("正文内容", _summary_excerpt(document))

    def test_returns_empty_when_nothing_exists(self) -> None:
        document = _doc("甲", description="", body="")

        self.assertEqual(_summary_excerpt(document), "")


if __name__ == "__main__":
    unittest.main()
