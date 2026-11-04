"""The workflow console must show what a run pinned, not what the definition says today.

Two claims the screen makes that would be easy to break without noticing:

* the run list shows the snapshot hash, because that is the identity of what ran;
* a review shows what was recorded — a capability with no executions shows zero uses, and the
  screen never fills that in with a success.

These check the component source rather than rendering it, which is the same approach the other
view tests use.
"""

from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web" / "src"
VIEW = WEB / "views" / "WorkflowRunsView.vue"
CLIENT = WEB / "api" / "client.ts"
TYPES = WEB / "api" / "types.ts"
ROUTER = WEB / "router" / "index.ts"


class WorkflowViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.view = VIEW.read_text(encoding="utf-8")

    def test_the_view_exists(self) -> None:
        self.assertTrue(VIEW.exists())

    def test_the_route_is_registered(self) -> None:
        self.assertIn("/workflows", ROUTER.read_text(encoding="utf-8"))

    def test_the_sidebar_links_to_it(self) -> None:
        app = (WEB / "App.vue").read_text(encoding="utf-8")
        self.assertIn('/workflows', app)

    def test_the_snapshot_hash_is_shown(self) -> None:
        """The snapshot hash identifies what ran, so it belongs in the list."""

        self.assertIn("resolution_snapshot_hash", self.view)

    def test_hashes_are_shortened(self) -> None:
        self.assertIn("function shortHash", self.view)

    def test_a_run_with_no_definitions_explains_what_to_do(self) -> None:
        self.assertIn("还没有可运行的工作流", self.view)

    def test_a_run_list_that_is_empty_explains_itself(self) -> None:
        self.assertIn("还没有运行记录", self.view)

    def test_the_review_shows_usage_counts(self) -> None:
        self.assertIn("item.uses", self.view)
        self.assertIn("item.succeeded", self.view)
        self.assertIn("item.failed", self.view)

    def test_the_review_shows_the_digest(self) -> None:
        """A reader can tell whether what they are reading is what was recorded."""

        self.assertIn("review_digest", self.view)

    def test_a_capability_with_no_output_shows_a_dash_not_a_success(self) -> None:
        self.assertIn("output_hashes.length === 0", self.view)

    def test_the_review_notes_are_surfaced(self) -> None:
        """The server states when a run had no executions; hiding it would read as success."""

        self.assertIn("review.notes", self.view)

    def test_starting_a_run_carries_an_idempotency_key(self) -> None:
        """Without a key a double click would create two runs."""

        self.assertIn("Idempotency-Key", CLIENT.read_text(encoding="utf-8"))


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = CLIENT.read_text(encoding="utf-8")

    def test_the_endpoints_are_wrapped(self) -> None:
        for name in (
            "listWorkflows",
            "getWorkflow",
            "createRun",
            "listRuns",
            "getRun",
            "getRunReview",
            "decideRunApproval",
        ):
            with self.subTest(endpoint=name):
                self.assertIn(name, self.client)

    def test_the_approval_sends_if_match(self) -> None:
        """The server requires the header to agree with the body's row version."""

        self.assertIn("If-Match", self.client)

    def test_the_types_cover_the_review(self) -> None:
        types = TYPES.read_text(encoding="utf-8")
        for name in ("WorkflowDefinition", "WorkflowRunSummary", "WorkflowRunReview"):
            with self.subTest(type=name):
                self.assertIn(name, types)


if __name__ == "__main__":
    unittest.main()
