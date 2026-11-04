"""Three refinements that make the console answer questions it previously raised.

Each of these fixed a case where the interface was technically correct and practically
unhelpful:

1. The lifecycle panel named a capability (`lifecycle:publish`) when the reader's question
   was "whose job is this?".
2. The search screen showed a blank box before the first query, giving no starting point.
3. The audit log listed fifty rows with no anchor, so a reader could not see where one day's
   activity ended and the next began.
"""

from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web" / "src"

PANEL = WEB / "components" / "VersionLifecyclePanel.vue"
DOMAIN = WEB / "domain" / "lifecycle.ts"
DISCOVERY = WEB / "views" / "DiscoveryView.vue"
AUDIT = WEB / "views" / "AuditLogView.vue"


class LifecycleRoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.domain = DOMAIN.read_text(encoding="utf-8")
        cls.panel = PANEL.read_text(encoding="utf-8")

    def test_every_capability_has_a_role_name(self) -> None:
        """A capability without a name would render an empty hint."""

        import re

        block = self.domain.split("CAPABILITY_OWNER")[1].split("};")[0]
        for capability in (
            "lifecycle:submit",
            "lifecycle:validate",
            "lifecycle:review",
            "lifecycle:publish",
            "lifecycle:reverse_deprecation",
            "resource:edit",
        ):
            with self.subTest(capability=capability):
                self.assertIn(capability, block)

    def test_the_panel_uses_role_names_not_identifiers(self) -> None:
        self.assertIn("blockedRoleNames", self.panel)
        self.assertIn("CAPABILITY_OWNER", self.panel)

    def test_the_raw_capability_list_is_no_longer_shown_to_users(self) -> None:
        """`需要的权限为 lifecycle:publish` answers a question nobody asked."""

        self.assertNotIn("需要的权限为", self.panel)

    def test_the_hint_says_who_acts(self) -> None:
        self.assertIn("执行", self.panel)


class SearchEmptyStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = DISCOVERY.read_text(encoding="utf-8")

    def test_examples_are_offered_before_the_first_search(self) -> None:
        self.assertIn("EXAMPLE_QUERIES", self.source)
        self.assertIn("useExample", self.source)

    def test_an_example_runs_the_search(self) -> None:
        """An example that only fills the box is a hint; one that runs is a start."""

        block = self.source.split("function useExample")[1].split("}")[0]
        self.assertIn("runSearch()", block)

    def test_no_results_differs_from_no_search_yet(self) -> None:
        """The two states used to look alike: an empty result area either way."""

        self.assertIn("没有匹配的能力", self.source)
        self.assertIn("输入一句业务语言的需求", self.source)

    def test_the_debug_strip_waits_for_a_search(self) -> None:
        """Before a search it shows dashes and 0 ms, which reads as a failure."""

        self.assertIn('v-if="searched" class="debug panel"', self.source)


class AuditGroupingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = AUDIT.read_text(encoding="utf-8")

    def test_entries_are_grouped_by_day(self) -> None:
        self.assertIn("const groups = computed", self.source)
        self.assertIn("toLocaleDateString()", self.source)

    def test_groups_preserve_incoming_order(self) -> None:
        """Records arrive newest-first; regrouping must not reorder within a day."""

        block = self.source.split("const groups = computed")[1].split("});")[0]
        self.assertIn("buckets.push", block)
        self.assertNotIn("sort(", block)

    def test_the_day_header_shows_a_count(self) -> None:
        self.assertIn("group.entries.length", self.source)

    def test_the_chain_check_still_reads_the_flat_list(self) -> None:
        """Grouping is presentation; the chain must still compare adjacent records."""

        self.assertIn("const chain = computed", self.source)
        self.assertIn("entries.value", self.source.split("const chain = computed")[1].split("});")[0])


if __name__ == "__main__":
    unittest.main()
