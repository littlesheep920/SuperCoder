"""Tests for the job view's retry and live-stream wiring.

The two rules worth pinning:
  * retry is offered only for failed/dead-lettered jobs and requires `job:retry`;
  * a retry refreshes the row instead of assuming success, because the endpoint
    answers 202 (accepted, not done).

The stream is checked structurally: `EventSource` cannot carry an Authorization
header, so the implementation must not smuggle the token into the URL.
"""

from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
VIEW = ROOT / "apps" / "web" / "src" / "views" / "JobsView.vue"
CLIENT = ROOT / "apps" / "web" / "src" / "api" / "client.ts"


class RetryRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = VIEW.read_text(encoding="utf-8")

    def test_retry_requires_the_capability(self) -> None:
        self.assertIn('can("job:retry")', self.text)

    def test_retry_is_limited_to_failed_and_dead_letter(self) -> None:
        match = re.search(r"function canRetry\(job: Job\): boolean \{(.*?)\n\}", self.text, re.S)
        self.assertIsNotNone(match, "canRetry must exist")
        body = match.group(1)
        self.assertIn("failed", body)
        self.assertIn("dead_letter", body)

    def test_retry_reloads_rather_than_claiming_success(self) -> None:
        match = re.search(r"async function retry\(job: Job\).*?\n\}", self.text, re.S)
        self.assertIsNotNone(match, "retry must exist")
        body = match.group(0)
        self.assertIn("await load()", body)
        self.assertNotIn('"已成功"', body)
        self.assertIn("不表示已经成功", body)

    def test_terminal_states_block_cancel(self) -> None:
        match = re.search(r"const TERMINAL = \[(.*?)\]", self.text, re.S)
        self.assertIsNotNone(match)
        for state in ("succeeded", "failed", "cancelled", "dead_letter"):
            self.assertIn(f'"{state}"', match.group(1))


class StreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.view = VIEW.read_text(encoding="utf-8")
        cls.client = CLIENT.read_text(encoding="utf-8")

    def test_the_stream_path_exists_on_the_client(self) -> None:
        self.assertIn("jobEventsStreamPath", self.client)

    def test_the_token_is_not_put_in_the_stream_url(self) -> None:
        """Query-string tokens leak through proxy logs and browser history."""

        match = re.search(r"jobEventsStreamPath: \([^)]*\) =>(.+)", self.client)
        self.assertIsNotNone(match)
        line = match.group(1)
        self.assertNotIn("token", line.lower())

    def test_a_refused_stream_falls_back_to_the_event_list(self) -> None:
        """An unavailable stream must be reported, not rendered as 'no events'."""

        self.assertIn("streamUnavailable", self.view)
        self.assertIn("实时流不可用", self.view)

    def test_events_arrive_live_and_are_merged_with_the_fetched_history(self) -> None:
        self.assertIn("mergedEvents", self.view)
        self.assertIn("sequence_no", self.view)

    def test_the_stream_is_closed_when_leaving(self) -> None:
        self.assertIn("onUnmounted(stopStream)", self.view)


if __name__ == "__main__":
    unittest.main()
