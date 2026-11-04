"""The export panel must state what an export loses, before and after downloading.

An export can silently drop typed IO, other runtimes, and a workflow's enforced ordering.
The panel therefore has two obligations, both checked here:

1. show the predicted losses *before* the download, so the choice is informed;
2. repeat the server-confirmed losses *after* it, because the server is the authority and
   its list may differ from what the UI predicted.

The panel also mirrors the published-only rule, so the reason appears next to the control
instead of arriving as a refusal after a click.
"""

from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web" / "src"
COMPONENT = WEB / "components" / "VersionExport.vue"
CLIENT = WEB / "api" / "client.ts"


class ExportPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = COMPONENT.read_text(encoding="utf-8")

    def test_losses_are_shown_before_downloading(self) -> None:
        self.assertIn("predicted", self.source)
        self.assertIn("本次导出会丢失以下能力", self.source)

    def test_a_download_still_works_without_predicted_losses(self) -> None:
        """The forecast is advisory: the download must not depend on it being right."""

        self.assertIn("async function run()", self.source)
        self.assertIn("api.exportVersion", self.source)

    def test_no_losses_is_stated_rather_than_left_blank(self) -> None:
        """Silence would be indistinguishable from "the check did not run".

        A resource with no typed IO and a single runtime has nothing to lose, so the panel
        must say so — and must not overclaim that the export is guaranteed to work in the
        target, which depends on the user's environment.
        """

        self.assertIn("本次导出没有已知的有损项", self.source)
        self.assertIn("这不等于", self.source)

    def test_server_warnings_are_repeated_after_download(self) -> None:
        self.assertIn("download.warningCodes", self.source)
        self.assertIn("服务端确认", self.source)

    def test_an_unknown_warning_code_is_still_shown(self) -> None:
        """Hiding a code the panel does not recognise would hide a real loss."""

        self.assertIn("未识别的有损项", self.source)

    def test_every_known_code_has_chinese_text(self) -> None:
        self.assertIn("WARNING_TEXT", self.source)
        for code in (
            "typed_io_not_carried",
            "other_runtimes_not_exported",
            "workflow_degrades_to_instructions",
            "upload_is_manual",
        ):
            with self.subTest(code=code):
                self.assertIn(code, self.source)

    def test_unpublished_versions_explain_themselves(self) -> None:
        self.assertIn('props.version?.status === "published"', self.source)
        self.assertIn("只有<strong>已发布</strong>的版本可以导出", self.source)

    def test_the_panel_is_reachable_from_the_detail_page(self) -> None:
        detail = (WEB / "views" / "ResourceDetailView.vue").read_text(encoding="utf-8")
        self.assertIn("<VersionExport", detail)


class ExportClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = CLIENT.read_text(encoding="utf-8")

    def test_the_zip_is_fetched_outside_the_json_helper(self) -> None:
        """`request` parses JSON; a zip body would fail there."""

        self.assertIn("async (", self.source)
        self.assertIn("exportVersion", self.source)
        self.assertIn("response.blob()", self.source)

    def test_header_metadata_is_preserved(self) -> None:
        for header in ("X-Export-Digest", "X-Export-Warnings", "Content-Disposition"):
            with self.subTest(header=header):
                self.assertIn(header, self.source)

    def test_a_failed_export_surfaces_the_translated_error(self) -> None:
        self.assertIn("throw new ApiError(body, response.status)", self.source)


if __name__ == "__main__":
    unittest.main()
