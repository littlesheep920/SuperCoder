"""Tests for the audit list's filters and hash-chain check.

The chain check is the part with real consequences: a false "intact" would tell a
reader the record is trustworthy when it is not, and a false "broken" would train
them to ignore the warning. It is implemented in the view, so these tests exercise
the same rule against a small reference implementation and assert the arithmetic the
view documents.
"""

from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
VIEW = ROOT / "apps" / "web" / "src" / "views" / "AuditLogView.vue"


def chain_breaks(hashes: list[tuple[str, str | None]]) -> int:
    """Reference implementation of the view's rule.

    `hashes` is newest-first: (event_hash, previous_event_hash). Entry i's
    previous_event_hash must equal entry i+1's event_hash. The oldest loaded entry
    has no successor in the window, so it is not compared.
    """

    breaks = 0
    for index in range(len(hashes) - 1):
        if hashes[index][1] != hashes[index + 1][0]:
            breaks += 1
    return breaks


class ChainRuleTests(unittest.TestCase):
    def test_a_contiguous_chain_has_no_breaks(self) -> None:
        entries = [
            ("h3", "h2"),
            ("h2", "h1"),
            ("h1", None),
        ]
        self.assertEqual(chain_breaks(entries), 0)

    def test_a_missing_link_is_reported(self) -> None:
        entries = [
            ("h3", "h2"),
            ("h2", "hX"),  # points at something that is not the next entry
            ("h1", None),
        ]
        self.assertEqual(chain_breaks(entries), 1)

    def test_the_oldest_entry_is_not_compared(self) -> None:
        """Its predecessor may simply be outside the loaded window."""

        entries = [
            ("h2", "h1"),
            ("h1", "h0"),  # h0 is on the previous page; not a break
        ]
        self.assertEqual(chain_breaks(entries), 0)

    def test_a_single_entry_is_vacuously_intact(self) -> None:
        self.assertEqual(chain_breaks([("h1", None)]), 0)

    def test_an_empty_page_is_vacuously_intact(self) -> None:
        self.assertEqual(chain_breaks([]), 0)


class ViewWiringTests(unittest.TestCase):
    """The view must actually implement the rule and the filters it claims."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = VIEW.read_text(encoding="utf-8")

    def test_the_view_compares_adjacent_hashes(self) -> None:
        self.assertIn("previous_event_hash", self.text)
        self.assertIn("event_hash", self.text)

    def test_the_view_skips_the_oldest_entry(self) -> None:
        self.assertRegex(
            self.text,
            r"items\.length - 1",
            "the loop must stop before the oldest entry, which has no successor",
        )

    def test_the_view_filters_by_version(self) -> None:
        self.assertIn("version_public_id", self.text)

    def test_the_view_is_gated_on_audit_read(self) -> None:
        self.assertIn('can("audit:read")', self.text)

    def test_the_view_does_not_claim_more_than_it_checks(self) -> None:
        """The message must scope the claim to this page, not the whole log."""

        self.assertIn("本页", self.text)


class CapabilityRaceTests(unittest.TestCase):
    """The first load must not run before capabilities arrive.

    The identity request is asynchronous. The first version of this view guarded
    `load()` on `can("audit:read")` and triggered it from `onMounted`; at that moment
    the capability set was still empty, so the guard returned and nothing ever
    retried. The page rendered "0 条" and "没有匹配的审计记录" — indistinguishable
    from a genuinely empty log, which is the worst way for a governance screen to
    fail.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = VIEW.read_text(encoding="utf-8")

    def test_load_is_guarded_on_capability(self) -> None:
        self.assertRegex(self.text, r"if\s*\(!allowed\.value\)\s*return")

    def test_a_watcher_retries_once_capabilities_arrive(self) -> None:
        self.assertIn("watch(allowed", self.text)

    def test_the_mount_does_not_assume_capabilities_are_loaded(self) -> None:
        """`onMounted(load)` would fire the guard against an empty set."""

        self.assertNotIn("onMounted(load)", self.text)


class RouteWiringTests(unittest.TestCase):
    def test_the_audit_route_exists(self) -> None:
        router = (ROOT / "apps" / "web" / "src" / "router" / "index.ts").read_text(encoding="utf-8")
        self.assertIn('path: "/audit"', router)

    def test_the_navigation_links_to_it(self) -> None:
        app = (ROOT / "apps" / "web" / "src" / "App.vue").read_text(encoding="utf-8")
        self.assertIn('to="/audit"', app)

    def test_the_detail_page_embeds_a_scoped_audit_view(self) -> None:
        detail = (ROOT / "apps" / "web" / "src" / "views" / "ResourceDetailView.vue").read_text(
            encoding="utf-8"
        )
        self.assertIn("<AuditLogView", detail)
        self.assertIn("compact", detail)


if __name__ == "__main__":
    unittest.main()
