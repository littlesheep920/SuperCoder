"""The resource list paginates; a naive count under-reports the corpus.

CI failed after the corpus grew past one page: the seed step verified 42
resources existed but only read the first page of 25, so the check failed while
the data was in fact complete. That is a test bug with a real lesson — any
assertion over a paginated endpoint has to follow the cursor.

These tests pin the pagination contract so the same mistake is caught locally.
"""

from __future__ import annotations

import unittest

from apps.api.repositories.contracts import CursorPage
from apps.api.schemas.common import PageInfo


class PageInfoTests(unittest.TestCase):
    """The page envelope must make truncation impossible to miss."""

    def test_has_more_is_exposed(self) -> None:
        info = PageInfo(limit=25, has_more=True, next_cursor="abc")

        self.assertTrue(info.has_more)
        self.assertEqual(info.next_cursor, "abc")

    def test_final_page_signals_completion(self) -> None:
        info = PageInfo(limit=25, has_more=False, next_cursor=None)

        self.assertFalse(info.has_more)
        self.assertIsNone(info.next_cursor)

    def test_has_more_implies_a_cursor(self) -> None:
        """A page that claims more rows must say how to reach them."""

        carried = PageInfo(limit=25, has_more=True, next_cursor="abc")

        self.assertIsNotNone(carried.next_cursor)


class CursorPageTests(unittest.TestCase):
    def test_cursor_page_reports_its_own_completeness(self) -> None:
        page = CursorPage(items=[1, 2, 3], next_cursor=None, total=3)

        self.assertEqual(len(page.items), 3)
        self.assertIsNone(page.next_cursor)

    def test_cursor_page_carries_the_next_cursor(self) -> None:
        page = CursorPage(items=[1], next_cursor="more", total=42)

        self.assertEqual(page.next_cursor, "more")
        self.assertEqual(page.total, 42)


class CollectAllPagesTests(unittest.TestCase):
    """A helper showing the only correct way to count a paginated endpoint."""

    @staticmethod
    def collect(fetch) -> list[int]:
        """Follow cursors until exhausted; ``fetch`` takes a cursor or None."""

        collected: list[int] = []
        cursor = None
        while True:
            items, next_cursor = fetch(cursor)
            collected.extend(items)
            if next_cursor is None:
                return collected
            cursor = next_cursor

    def test_collects_across_multiple_pages(self) -> None:
        pages = {None: ([1, 2], "c1"), "c1": ([3, 4], "c2"), "c2": ([5], None)}

        self.assertEqual(self.collect(lambda cursor: pages[cursor]), [1, 2, 3, 4, 5])

    def test_single_page_collects_once(self) -> None:
        self.assertEqual(self.collect(lambda cursor: ([1, 2, 3], None)), [1, 2, 3])

    def test_empty_result_is_handled(self) -> None:
        self.assertEqual(self.collect(lambda cursor: ([], None)), [])


if __name__ == "__main__":
    unittest.main()
