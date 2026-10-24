"""Tests for tokenization and typed chunking (acceptance items B3, B4 support)."""

from __future__ import annotations

import unittest

from apps.api.search import IndexField
from apps.api.search.chunking import (
    FIELD_WEIGHTS,
    MAX_CHUNK_CHARS,
    build_index_document,
    chunk_resource,
)
from apps.api.search.tokenizer import tokenize

# --- tokenizer -------------------------------------------------------------


class TokenizerTests(unittest.TestCase):
    def test_chinese_uses_overlapping_bigrams(self) -> None:
        self.assertEqual(tokenize("代码审查"), ["代码", "码审", "审查"])

    def test_single_chinese_character_is_kept(self) -> None:
        """A lone character has no bigram partner but must still be searchable."""

        self.assertEqual(tokenize("审"), ["审"])

    def test_chinese_and_ascii_are_split_at_the_boundary(self) -> None:
        tokens = tokenize("审查PR")
        self.assertIn("审查", tokens)
        self.assertIn("pr", tokens)

    def test_snake_case_identifier_splits_into_parts(self) -> None:
        self.assertEqual(tokenize("resource_version_id"), ["resource", "version", "id"])

    def test_camel_case_matches_snake_case(self) -> None:
        """Identifiers are searchable regardless of the caller's casing style."""

        self.assertEqual(
            tokenize("resourceVersionId"), tokenize("resource_version_id")
        )

    def test_ascii_is_casefolded(self) -> None:
        self.assertEqual(tokenize("Spring Boot"), tokenize("spring boot"))

    def test_fullwidth_characters_are_normalized(self) -> None:
        """NFKC folds full-width ASCII, common in Chinese input methods."""

        self.assertEqual(tokenize("ＰＲ"), tokenize("PR"))

    def test_empty_and_symbol_only_input_yield_no_tokens(self) -> None:
        self.assertEqual(tokenize(""), [])
        self.assertEqual(tokenize("   !!! ###   "), [])

    def test_repeated_terms_are_preserved_for_term_frequency(self) -> None:
        tokens = tokenize("审查 审查")
        self.assertEqual(tokens.count("审查"), 2)


# --- chunking --------------------------------------------------------------


class FieldWeightTests(unittest.TestCase):
    def test_weights_are_strictly_ordered_by_importance(self) -> None:
        order = [
            IndexField.NAME,
            IndexField.ALIASES,
            IndexField.TAGS,
            IndexField.CATEGORIES,
            IndexField.RUNTIMES,
            IndexField.DESCRIPTION,
            IndexField.DEPENDENCIES,
            IndexField.BODY,
        ]
        weights = [FIELD_WEIGHTS[field] for field in order]
        self.assertEqual(weights, sorted(weights, reverse=True))

    def test_name_outranks_body(self) -> None:
        self.assertGreater(FIELD_WEIGHTS[IndexField.NAME], FIELD_WEIGHTS[IndexField.BODY])


class SkillChunkingTests(unittest.TestCase):
    def test_skill_splits_on_markdown_headings(self) -> None:
        entry = (
            "# 适用场景\n\n审查 Java 后端代码。\n\n"
            "# 执行步骤\n\n1. 读取 diff\n2. 分析风险\n\n"
            "# 输出格式\n\n返回 markdown 报告。\n"
        )
        chunks = chunk_resource("skill", {}, {"SKILL.md": entry})
        sections = [chunk.section for chunk in chunks]
        self.assertIn("适用场景", sections)
        self.assertIn("执行步骤", sections)
        self.assertIn("输出格式", sections)

    def test_text_before_the_first_heading_is_not_dropped(self) -> None:
        chunks = chunk_resource("skill", {}, {"SKILL.md": "前言内容\n\n# 步骤\n\n做事\n"})
        self.assertEqual(chunks[0].section, "overview")

    def test_ordinals_are_contiguous_from_zero(self) -> None:
        chunks = chunk_resource("skill", {}, {"SKILL.md": "# A\n\na\n\n# B\n\nb\n"})
        self.assertEqual([chunk.ordinal for chunk in chunks], list(range(len(chunks))))

    def test_oversized_section_is_split_on_sentence_boundaries(self) -> None:
        # Build well past MAX_CHUNK_CHARS so a split is actually required.
        sentence = "这是一个很长的句子用于测试切分逻辑。" * 150
        self.assertGreater(len(sentence), MAX_CHUNK_CHARS)
        chunks = chunk_resource("skill", {}, {"SKILL.md": f"# 段落\n\n{sentence}\n"})
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.text), MAX_CHUNK_CHARS)

    def test_short_section_is_not_split(self) -> None:
        """Splitting must not fragment text that already fits."""

        chunks = chunk_resource("skill", {}, {"SKILL.md": "# 短\n\n一句话。\n"})
        self.assertEqual(len(chunks), 1)

    def test_split_is_deterministic(self) -> None:
        """B7 requires byte-identical results across runs."""

        entry = "# A\n\n" + "内容。" * 400 + "\n"
        first = chunk_resource("skill", {}, {"SKILL.md": entry})
        second = chunk_resource("skill", {}, {"SKILL.md": entry})
        self.assertEqual(first, second)


class McpChunkingTests(unittest.TestCase):
    def test_mcp_produces_one_chunk_per_tool(self) -> None:
        spec = {
            "transport": "stdio",
            "tools": [
                {"name": "search_repos", "description": "搜索仓库"},
                {"name": "get_issue", "description": "读取 Issue"},
            ],
        }
        chunks = chunk_resource("mcp", spec, {})
        sections = [chunk.section for chunk in chunks]
        self.assertIn("overview", sections)
        self.assertIn("tool-search-repos", sections)
        self.assertIn("tool-get-issue", sections)

    def test_tool_parameters_stay_with_their_tool(self) -> None:
        spec = {
            "tools": [
                {"name": "alpha", "description": "甲工具", "input_schema": {"q": "query"}},
                {"name": "beta", "description": "乙工具", "input_schema": {"id": "issue"}},
            ]
        }
        chunks = {chunk.section: chunk.text for chunk in chunk_resource("mcp", spec, {})}
        self.assertIn("甲工具", chunks["tool-alpha"])
        self.assertNotIn("乙工具", chunks["tool-alpha"])


class OtherKindChunkingTests(unittest.TestCase):
    def test_agent_splits_by_configuration_concern(self) -> None:
        spec = {"role": "规划者", "tools": ["search"], "safety_boundaries": "不改生产"}
        sections = [chunk.section for chunk in chunk_resource("agent", spec, {})]
        self.assertIn("role", sections)
        self.assertIn("tools", sections)
        self.assertIn("safety-boundaries", sections)

    def test_workflow_splits_by_node(self) -> None:
        spec = {
            "goal": "审查流程",
            "nodes": [
                {"id": "extract", "type": "resource", "description": "提取变更"},
                {"id": "review", "type": "approval"},
            ],
        }
        sections = [chunk.section for chunk in chunk_resource("workflow", spec, {})]
        self.assertIn("goal", sections)
        self.assertIn("node-extract", sections)
        self.assertIn("node-review", sections)

    def test_tool_splits_by_invocation_contract(self) -> None:
        spec = {"entrypoint": "tool.py", "execution_mode": "python", "parameters": {"text": "输入"}}
        sections = [chunk.section for chunk in chunk_resource("tool", spec, {})]
        self.assertIn("entrypoint", sections)
        self.assertIn("parameters", sections)

    def test_prompt_splits_by_template_block(self) -> None:
        spec = {"system_role": "你是审查者", "task": "审查代码", "output_format": "markdown"}
        sections = [chunk.section for chunk in chunk_resource("prompt", spec, {})]
        self.assertEqual(sections, ["system-role", "task", "output-format"])

    def test_unknown_kind_still_yields_chunks(self) -> None:
        chunks = chunk_resource("mystery", {}, {"x.md": "# 标题\n\n正文\n"})
        self.assertTrue(chunks)


class IndexDocumentTests(unittest.TestCase):
    def _build(self, **overrides):
        base = dict(
            resource_public_id="res-1",
            version_public_id="ver-1",
            kind="skill",
            slug="demo",
            name="后端代码审查",
            version="1.0.0",
            status="published",
            owner_team="platform-ai",
            description="审查 Java 后端",
            manifest={
                "aliases": ["PR Review"],
                "tags": ["代码审查"],
                "categories": ["software-development"],
                "runtimes": ["kiro"],
                "dependencies": [{"kind": "mcp", "slug": "github-tools"}],
                "permissions": ["repository:read"],
            },
            type_spec={},
            files={"SKILL.md": "# 步骤\n\n审查\n"},
            artifact_hash="abc123",
            manifest_snapshot_hash="def456",
            updated_at_epoch=100.0,
        )
        base.update(overrides)
        return build_index_document(**base)

    def test_document_binds_to_the_content_hash(self) -> None:
        """B9: every hit must trace back to immutable content."""

        document = self._build()
        self.assertEqual(document.artifact_hash, "abc123")
        self.assertIn("abc123", document.index_key)

    def test_index_key_changes_when_content_hash_changes(self) -> None:
        self.assertNotEqual(self._build().index_key, self._build(artifact_hash="zzz").index_key)

    def test_missing_artifact_hash_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._build(artifact_hash="")

    def test_manifest_taxonomy_is_projected(self) -> None:
        document = self._build()
        self.assertEqual(document.aliases, ("PR Review",))
        self.assertEqual(document.tags, ("代码审查",))
        self.assertEqual(document.runtimes, ("kiro",))
        self.assertEqual(document.permission_names, ("repository:read",))
        self.assertEqual(document.dependency_labels, ("mcp github-tools",))

    def test_field_text_exposes_each_searchable_field(self) -> None:
        document = self._build()
        self.assertEqual(document.field_text(IndexField.NAME), "后端代码审查")
        self.assertEqual(document.field_text(IndexField.ALIASES), "PR Review")
        self.assertIn("审查", document.field_text(IndexField.BODY))


if __name__ == "__main__":
    unittest.main()
