"""Keep the documentation tree navigable.

Two problems motivated these tests. Process records (acceptance reports,
correction reviews, per-stage architecture notes) accumulated next to live
contracts, so a newcomer could not tell which documents were normative. And
archived files kept being referenced from active ones, leaving dangling links.

The rules below are the smallest set that keeps the tree honest: contracts stay
active, closed-stage records live under docs/archive/, links resolve, and a
document that the validators read is still findable after archiving.
"""

from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ARCHIVE = DOCS / "archive"

# Documents that define how the system must behave. They may never be archived.
FROZEN_CONTRACTS = {
    "RESOURCE_PROTOCOL.md",
    "RESOURCE_GOVERNANCE.md",
    "DATABASE_MODEL.md",
    "API_CONTRACT.md",
    "SECURITY_AND_CONFIGURATION.md",
    "ARCHITECTURE_FREEZE.md",
}

LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


class ArchiveLayoutTests(unittest.TestCase):
    def test_frozen_contracts_are_not_archived(self) -> None:
        """A contract that moved to the archive would stop being authoritative."""

        for name in FROZEN_CONTRACTS:
            with self.subTest(document=name):
                self.assertTrue(
                    (DOCS / name).is_file(),
                    f"{name} must stay under docs/, not in the archive",
                )
                self.assertFalse((ARCHIVE / name).exists())

    def test_product_specification_is_active(self) -> None:
        self.assertTrue((DOCS / "PRD.md").is_file())
        self.assertFalse((ARCHIVE / "PRD.md").exists())

    def test_closed_stage_records_live_in_the_archive(self) -> None:
        """Stage 0 and 1 are closed; their process records belong in the archive."""

        for name in (
            "STAGE0_ACCEPTANCE_REPORT.md",
            "STAGE0_SCOPE_AND_TRACEABILITY.md",
            "STAGE1_ARCHITECTURE.md",
            "STAGE1_CONTRACT_INVENTORY.md",
            "STAGE1_IMPLEMENTATION.md",
            "CORRECTION_REVIEW.md",
        ):
            with self.subTest(document=name):
                self.assertTrue((ARCHIVE / name).is_file(), f"{name} should be archived")
                self.assertFalse((DOCS / name).exists(), f"{name} must not sit next to live docs")

    def test_archive_has_an_index(self) -> None:
        index = (ARCHIVE / "README.md").read_text(encoding="utf-8")
        self.assertIn("仍然有效的文档", index)

    def test_docs_have_a_reading_guide(self) -> None:
        guide = (DOCS / "README.md").read_text(encoding="utf-8")
        for anchor in ("PRD.md", "冻结契约", "archive/"):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, guide)


class LinkIntegrityTests(unittest.TestCase):
    def test_every_relative_link_resolves(self) -> None:
        broken: list[str] = []
        for path in ROOT.rglob("*.md"):
            if any(part in {".git", "node_modules"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in LINK.finditer(text):
                target = match.group(2).strip()
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                target = target.split("#", 1)[0]
                if target and not (path.parent / target).resolve().exists():
                    line = text[: match.start()].count("\n") + 1
                    broken.append(f"{path.relative_to(ROOT)}:{line} -> {target}")
        self.assertEqual(broken, [], "broken relative links:\n" + "\n".join(broken))

    def test_active_documents_do_not_link_to_archived_files_in_place(self) -> None:
        """A live document must point at docs/archive/..., not the bare filename."""

        off_limits = {name for name in ARCHIVE.glob("*.md")}
        offenders: list[str] = []
        for path in DOCS.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            for match in LINK.finditer(text):
                target = match.group(2).strip()
                if "archive/" in target:
                    continue
                if (path.parent / target.split("#")[0]).name in {p.name for p in off_limits}:
                    offenders.append(f"{path.name} -> {target}")
        self.assertEqual(offenders, [], "links should use docs/archive/: " + str(offenders))


class ValidatorReachabilityTests(unittest.TestCase):
    """The Stage 0 validator reads a traceability document; archiving must not hide it."""

    def test_traceability_is_found_in_either_location(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        from scripts.validate_stage0 import _doc_path

        resolved = _doc_path(ROOT, "STAGE0_SCOPE_AND_TRACEABILITY.md")
        self.assertTrue(resolved.is_file(), f"not found at {resolved}")
        self.assertIn(resolved.parent.name, {"docs", "archive"})


if __name__ == "__main__":
    unittest.main()
