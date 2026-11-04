"""The inventory and detail pages must stay findable as the corpus grows.

Two problems were fixed here, and both would silently come back without a test:

1. The inventory offered only dropdowns. With hundreds of resources, finding one meant
   scrolling, so a filter box was added.
2. The detail page rendered a full read-only editor for non-draft versions, which occupied
   roughly a third of the page and implied an action that was not available. It now
   collapses to one line stating why.

These check the behaviour a reader depends on, not the markup: the filter narrows the list,
counts are shown, and a non-draft version does not render editable fields.
"""

from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web" / "src"
LIST = WEB / "views" / "ResourceListView.vue"
DETAIL = WEB / "views" / "ResourceDetailView.vue"
EDITOR = WEB / "components" / "VersionEditor.vue"
THEME = WEB / "styles" / "theme.css"


class InventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = LIST.read_text(encoding="utf-8")

    def test_a_filter_box_exists(self) -> None:
        """Dropdowns alone cannot find one resource among hundreds."""

        self.assertIn('v-model="query"', self.source)
        self.assertIn("按名称或团队筛选", self.source)

    def test_the_filter_matches_slug_and_team(self) -> None:
        self.assertIn("item.slug.toLowerCase().includes(needle)", self.source)
        self.assertIn("owner_team", self.source)

    def test_the_filter_is_case_insensitive_and_trimmed(self) -> None:
        self.assertIn("query.value.trim().toLowerCase()", self.source)

    def test_counts_are_shown_per_status(self) -> None:
        """25 identical rows say less than one line of counts."""

        self.assertIn("statusCounts", self.source)
        self.assertIn('class="stat"', self.source)

    def test_the_total_is_shown(self) -> None:
        self.assertIn("{{ items.length }} 项", self.source)

    def test_an_empty_result_explains_itself(self) -> None:
        """A blank table is indistinguishable from a failed load."""

        self.assertIn("没有匹配", self.source)
        self.assertIn("清除筛选", self.source)

    def test_a_failed_load_can_be_retried(self) -> None:
        self.assertIn("@click=\"load\"", self.source)
        self.assertIn("重试", self.source)

    def test_the_repeated_per_row_links_are_gone(self) -> None:
        """Three links on every row competed with the resource name."""

        self.assertNotIn("/dependencies\">依赖</router-link>", self.source)


class DetailPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = DETAIL.read_text(encoding="utf-8")

    def test_the_digest_is_shortened(self) -> None:
        """A 64-character hash per row forces wrapping and breaks alignment."""

        self.assertIn("function shortHash", self.source)
        self.assertIn("shortHash(summary?.artifact_hash)", self.source)

    def test_the_digest_can_be_copied(self) -> None:
        self.assertIn("navigator.clipboard.writeText", self.source)
        self.assertIn("复制", self.source)

    def test_a_published_version_leads_with_export(self) -> None:
        """Section order follows the task that dominates the state."""

        self.assertIn("summary?.status === 'published'", self.source)
        published_branch = self.source.split("summary?.status === 'published'")[1]
        self.assertLess(
            published_branch.find("<VersionExport"),
            published_branch.find("<VersionLifecyclePanel"),
            "for a published version the export panel should come first",
        )


class EditorCollapseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = EDITOR.read_text(encoding="utf-8")

    def test_a_non_draft_version_renders_no_editable_fields(self) -> None:
        """The read-only editor occupied a third of the page and implied an action."""

        # The field grid must sit in the draft branch, after `v-else`.
        draft_branch = self.source.split("v-else>\n      <p v-if=\"!mayEdit\"")[-1]
        self.assertIn("field-grid", draft_branch)
        non_draft = self.source.split("<template v-else-if=\"!isDraft\">")[1].split("</template>")[0]
        self.assertNotIn("field-grid", non_draft)
        self.assertNotIn("<textarea", non_draft)

    def test_the_reason_is_stated(self) -> None:
        self.assertIn("不可编辑", self.source)
        self.assertIn("退回草稿", self.source)


class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = THEME.read_text(encoding="utf-8")

    def test_colour_is_defined_by_role(self) -> None:
        """Roles, not isolated hex values, per the design guidance followed here."""

        for token in (
            "--surface-0",
            "--surface-1",
            "--text-1",
            "--text-2",
            "--accent",
            "--ok",
            "--warn",
            "--danger",
            "--focus",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.source)

    def test_spacing_uses_a_scale(self) -> None:
        """Arbitrary pixel values are how a layout drifts."""

        for step in ("--space-1", "--space-4", "--space-6"):
            with self.subTest(step=step):
                self.assertIn(step, self.source)

    def test_a_focus_ring_is_defined(self) -> None:
        self.assertIn(":focus-visible", self.source)

    def test_badges_carry_a_border_not_only_colour(self) -> None:
        """Colour alone cannot carry state, for colour-blind readers."""

        self.assertIn("border: 1px solid transparent", self.source)
        self.assertIn(".badge.published", self.source)


if __name__ == "__main__":
    unittest.main()
