"""Localisation and next-step hints for server refusals.

A governance rule surfacing as `an independent valid approval review is required` reads
as a crash, not a rule. The console is Chinese; the API is English. Translating at the
client means one place holds the mapping and every screen benefits.

These tests check the contract: known refusals translate, unknown ones pass through
unchanged rather than being swallowed, and the lifecycle panel points at the screen that
resolves the refusal.
"""

from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web" / "src"
CLIENT = WEB / "api" / "client.ts"
PANEL = WEB / "components" / "VersionLifecyclePanel.vue"


class ClientTranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = CLIENT.read_text(encoding="utf-8")

    def test_explainer_is_exported(self) -> None:
        self.assertIn("export function explainServerDetail", self.source)

    def test_known_gate_refusals_are_translated(self) -> None:
        """Every gate the state machine can refuse must have a Chinese explanation."""

        cases = {
            "independent valid approval": "独立审核",
            "validation": "校验",
            "unresolved": "依赖",
            "at least one runtime": "运行环境",
            "owner role": "所有者",
            "version already exists": "版本号",
            "only draft versions can be modified": "草稿",
        }
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.source.lower())
                self.assertIn(expected, self.source)

    def test_status_codes_have_fallbacks(self) -> None:
        for status in ("401", "403", "404", "409", "412", "422", "503"):
            with self.subTest(status=status):
                self.assertIn(f"status === {status}", self.source)

    def test_unknown_detail_passes_through(self) -> None:
        """An unrecognised message must not be replaced by a generic apology."""

        self.assertIn("return detail;", self.source)

    def test_the_original_message_is_preserved(self) -> None:
        """Translation must not destroy the detail a bug report needs."""

        self.assertIn("serverDetail", self.source)

    def test_message_is_translated_not_merely_wrapped(self) -> None:
        # The constructor must call the explainer; assigning body.detail directly is
        # the defect this guards against.
        self.assertIn("explainServerDetail(raw, status)", self.source)
        self.assertNotIn("super(body.detail ?? body.code);", self.source)


class LifecyclePanelHintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PANEL.read_text(encoding="utf-8")

    def test_refusals_link_to_the_resolving_screen(self) -> None:
        self.assertIn("function nextStepFor", self.source)
        self.assertIn("route: \"reviews\"", self.source)
        self.assertIn("route: \"jobs\"", self.source)

    def test_the_link_is_rendered(self) -> None:
        self.assertIn("blockedBy", self.source)
        self.assertIn("router-link", self.source)

    def test_translation_is_not_duplicated_here(self) -> None:
        """The panel adds a link; it must not carry a second copy of the message map."""

        self.assertNotIn("function explainRefusal", self.source)
        # Reading the raw detail is how it decides which link to show.
        self.assertIn("serverDetail", self.source)


class ReviewScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (WEB / "views" / "ReviewsView.vue").read_text(encoding="utf-8")

    def test_review_says_whether_a_valid_approval_exists(self) -> None:
        """Creating a review is useless unless the user knows to press approve next."""

        self.assertIn("hasValidApproval", self.source)
        self.assertIn("回到资源详情页", self.source)

    def test_real_evidence_reference_is_used(self) -> None:
        """A constant would satisfy the required field while pointing at nothing."""

        self.assertNotIn('validation_evidence_ref: "evidence/stage1"', self.source)
        self.assertIn("validation_evidence_ref: `version:", self.source)


if __name__ == "__main__":
    unittest.main()
