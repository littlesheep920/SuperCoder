"""CI gate: the documents must agree with each other and with the code.

`check_docs.py` verifies links and the PRD's figures. This catches the failure it cannot
see: two documents stating different things about the same fact. A documentation set feels
unreliable not when one number is wrong, but when two numbers disagree — a reader then has
no way to tell which to believe.

Every comparison is between a claim and its source, never between two prose passages, so
the check has no opinion about wording:

* a directory stated for an environment must match the adapter's actual directory;
* an environment with a working adapter must not be marked "未开始";
* warning codes the adapters emit must appear in ADAPTERS.md;
* a test count quoted in the acceptance report must be close to the real suite size.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DOCS = ROOT / "docs"


def main() -> int:
    from apps.api.domain.adapters import available_targets
    from tests.test_adapter_ir import FakeResource, clean_manifest

    prd = (DOCS / "PRD.md").read_text(encoding="utf-8")
    adapters_doc = (DOCS / "ADAPTERS.md").read_text(encoding="utf-8")
    report = (DOCS / "STAGE3_ACCEPTANCE_REPORT.md").read_text(encoding="utf-8")

    problems: list[str] = []
    targets = available_targets()

    # 1. A named environment must have its directory stated in the authoritative table row.
    #
    # Checking the whole document is too weak: the same fact appears in prose as well, so a
    # mutation that removes the table row still finds a match elsewhere and the gate stays
    # silent. The table row is what a reader consults, so that is what must exist.
    for target in targets:
        # The directory may sit inside backticks with a trailing path component, or stand
        # alone (ChatGPT's is `skills`, with no leading dot). Allow both.
        directory_row = re.search(
            rf"^\|\s*skills 目录\s*\|\s*`?{re.escape(target.skills_directory)}", adapters_doc, re.MULTILINE
        )
        package_row = re.search(
            rf"^\|\s*包结构\s*\|\s*`?{re.escape(target.skills_directory)}", adapters_doc, re.MULTILINE
        )
        if not directory_row and not package_row:
            problems.append(
                f"ADAPTERS.md states neither a 'skills 目录' nor a '包结构' entry for "
                f"{target.skills_directory} (target {target.identifier})"
            )
        if target.identifier not in prd and target.display_name not in prd:
            problems.append(f"PRD.md never names the {target.display_name} environment")

    # 2. A working adapter must not be described as not started.
    for target in targets:
        row = re.search(
            rf"^\|\s*{re.escape(target.display_name)}\s*\|[^|]*\|([^|\n]*)\|",
            prd,
            re.MULTILINE,
        )
        if row is None:
            problems.append(f"PRD.md has no status row for {target.display_name}")
            continue
        status = row.group(1).strip()
        if "已实现" not in status and "部分实现" not in status:
            problems.append(
                f"PRD.md reports {target.display_name} as {status!r}, "
                "but an adapter exists and is tested"
            )

    # 3. Every warning the adapters emit must appear in the lossy-mapping table.
    #
    # A mention in prose is not documentation: the table is where a reader looks up a code
    # they just received. Requiring the table row means a code cannot be added to an
    # adapter without being explained where it will be needed.
    cases = [
        FakeResource(kind="workflow", entrypoint="GRAPH.yaml"),
        FakeResource(manifest=clean_manifest(inputs=[{"name": "x"}])),
        FakeResource(manifest=clean_manifest(runtimes=["agent-skills", "kiro"])),
        FakeResource(kind="mcp", manifest=clean_manifest(mcp={"transport": "stdio"})),
    ]
    emitted: set[str] = set()
    for target in targets:
        for case in cases:
            for warning in target.export(case).warnings:
                emitted.add(warning.code)
    for code in sorted(emitted):
        if not re.search(rf"^\|\s*`{re.escape(code)}`", adapters_doc, re.MULTILINE):
            problems.append(f"ADAPTERS.md has no lossy-mapping table row for {code!r}")

    # 4. A quoted *total* test count must match the real suite.
    #
    # Only statements that present a count as the suite total are checked. A per-module
    # figure ("该模块 25 项测试") and a description of golden fixtures ("9 份") are not
    # totals, and comparing them against the suite would be a false alarm. The pattern
    # therefore requires the phrase "测试套件" or a bare count at the start of a line's
    # claim, and never matches a count that is part of a multiplication or a noun other
    # than tests.
    suite = sum(
        len(re.findall(r"^\s+def test_", path.read_text(encoding="utf-8"), re.MULTILINE))
        for path in (ROOT / "tests").glob("test_*.py")
    )
    for match in re.finditer(r"(\d+)\s*项(?:测试|通过)", report):
        start = max(0, match.start() - 12)
        context = report[start : match.start()]
        if "该模块" in context:
            continue
        value = int(match.group(1))
        if abs(value - suite) > 10:
            problems.append(
                f"STAGE3_ACCEPTANCE_REPORT.md quotes {value} as a test total; "
                f"the suite defines {suite}"
            )

    print(f"adapters documented:  {len(targets)}")
    print(f"warning codes emitted: {len(emitted)}")
    print(f"test methods:          {suite}")
    print()

    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}")
        print()
        print("RESULT: FAIL")
        return 1

    print("[PASS] documents agree with each other and with the code")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
