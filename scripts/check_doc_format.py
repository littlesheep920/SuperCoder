"""CI gate: documents must be structurally regular.

Content correctness is checked elsewhere (`check_docs.py`, `check_doc_consistency.py`). This
checks form, because inconsistent form makes a reader doubt everything: a document with two
titles, a table with ragged columns, or a heading with nothing under it signals that nobody
proofread it, and that doubt spreads to the claims.

The rules are stated in `docs/DOCUMENTATION_STANDARD.md`; this enforces them.

Two false-positive classes were found while writing this, and both are handled rather than
suppressed:

* a `#` inside a fenced code block is a shell comment, not a heading — `GIT_WORKFLOW.md`
  produced six false "multiple titles" findings before fenced blocks were excluded;
* a heading whose body is a command is not empty — blanking fenced blocks made those
  sections look contentless.

`docs/archive/` is exempt: those are closed stage records, kept as written.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = sorted(
    path for path in (ROOT / "docs").glob("*.md") if "archive" not in path.parts
)


def mask_code_blocks(text: str) -> str:
    """Replace fenced-block lines with a content marker.

    `#` inside a fence is not a heading, but the block itself is content. Both facts matter:
    the first prevents false title counts, the second prevents false empty sections.
    """

    kept: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("```"):
            inside = not inside
            kept.append("")
            continue
        kept.append("(code)" if inside else line)
    return "\n".join(kept)


def check_document(path: pathlib.Path) -> list[str]:
    problems: list[str] = []
    name = path.name
    raw = path.read_text(encoding="utf-8")
    text = mask_code_blocks(raw)
    lines = text.splitlines()

    first = next((line for line in lines if line.strip()), "")
    if not first.startswith("# "):
        problems.append(f"{name}: does not open with a level-1 heading")

    titles = [line for line in lines if line.startswith("# ")]
    if len(titles) > 1:
        problems.append(f"{name}: {len(titles)} level-1 headings; a document has one title")

    levels = [len(m.group(1)) for m in re.finditer(r"^(#{1,6}) ", text, re.MULTILINE)]
    for previous, current in zip(levels, levels[1:]):
        if current - previous > 1:
            problems.append(f"{name}: heading jumps from H{previous} to H{current}")

    # Tables: every contiguous run needs a separator and consistent columns.
    run: list[str] = []
    runs: list[list[str]] = []
    for line in lines:
        if line.strip().startswith("|"):
            run.append(line.strip())
        elif run:
            runs.append(run)
            run = []
    if run:
        runs.append(run)

    for table in runs:
        if not any(re.match(r"^\|[\s:|-]+\|$", row) for row in table):
            problems.append(f"{name}: a table has no header separator row")
        counts = {len(row.strip("|").split("|")) for row in table}
        if len(counts) > 1:
            problems.append(f"{name}: a table has inconsistent columns {sorted(counts)}")

    # Empty sections: a heading followed immediately by one of the same or higher level.
    for index, line in enumerate(lines[:-1]):
        match = re.match(r"^(#{2,6}) ", line)
        if not match:
            continue
        level = len(match.group(1))
        following = [entry for entry in lines[index + 1 :] if entry.strip()]
        if not following:
            problems.append(f"{name}: section {line.strip()[:40]!r} is a trailing empty heading")
            continue
        next_match = re.match(r"^(#{1,6}) ", following[0])
        if next_match and len(next_match.group(1)) <= level:
            problems.append(f"{name}: section {line.strip()[:40]!r} has no content")

    fences = raw.count("```")
    if fences % 2:
        problems.append(f"{name}: odd number of code fences ({fences}); one is unclosed")

    for number, line in enumerate(raw.splitlines(), 1):
        if "\t" in line:
            problems.append(f"{name}:{number}: contains a tab")
        if line != line.rstrip():
            problems.append(f"{name}:{number}: trailing whitespace")

    return problems


def main() -> int:
    problems: list[str] = []
    for path in DOCS:
        problems.extend(check_document(path))

    print(f"documents checked: {len(DOCS)}")
    print()

    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}")
        print()
        print("RESULT: FAIL")
        return 1

    print("[PASS] all documents follow docs/DOCUMENTATION_STANDARD.md")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
