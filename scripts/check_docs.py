"""CI gate: documentation must be consistent, and numbers must be reproducible.

Two failure modes have already happened in this repository, and both were found by luck
rather than by a gate:

1. A relative link pointed at a file that did not exist, so the document promised evidence
   a reader could not reach.
2. The PRD quoted a route count that no longer matched the code, so the stated status was
   wrong while every other check stayed green.

This script fails on either. It is deliberately narrow: it checks links and the figures the
tests already derive, not prose. Judging prose automatically would produce confident
nonsense; checking that a number matches its source is exact.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Documents a reader is expected to trust. Process records under archive/ are excluded:
#: they describe a moment in the past and are allowed to reference removed files.
EXPECTED_DOCS = [
    "docs/README.md",
    "docs/PRD.md",
    "docs/API_CONTRACT.md",
    "docs/ARCHITECTURE_FREEZE.md",
    "docs/DATABASE_MODEL.md",
    "docs/RESOURCE_GOVERNANCE.md",
    "docs/RESOURCE_PROTOCOL.md",
    "docs/SECURITY_AND_CONFIGURATION.md",
    "docs/GIT_WORKFLOW.md",
    "docs/ADAPTERS.md",
    "docs/DOCUMENTATION_STANDARD.md",
    "docs/STAGE3_ACCEPTANCE_REPORT.md",
]

LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
FENCE = re.compile(r"^```", re.MULTILINE)


def documents() -> list[pathlib.Path]:
    return sorted(
        path
        for path in ROOT.glob("docs/**/*.md")
        if "archive" not in path.parts
    )


def check_links() -> list[str]:
    errors: list[str] = []
    for document in documents():
        text = document.read_text(encoding="utf-8")
        for target in LINK.findall(text):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path = target.split("#")[0].strip()
            if not path:
                continue
            resolved = (document.parent / path).resolve()
            if not resolved.exists():
                errors.append(f"{document.relative_to(ROOT)}: link target not found: {target}")
    return errors


def check_fences_balanced() -> list[str]:
    """An unclosed code fence silently swallows the rest of a document."""

    errors: list[str] = []
    for document in documents():
        fences = len(FENCE.findall(document.read_text(encoding="utf-8")))
        if fences % 2:
            errors.append(f"{document.relative_to(ROOT)}: odd number of code fences ({fences})")
    return errors


def check_required_documents() -> list[str]:
    return [f"missing document: {name}" for name in EXPECTED_DOCS if not (ROOT / name).exists()]


def check_prd_figures() -> list[str]:
    """The PRD's stated figures must match what the code and contract contain."""

    errors: list[str] = []
    prd = (ROOT / "docs" / "PRD.md").read_text(encoding="utf-8")

    spec = json.loads((ROOT / "apps/api/openapi.stage0.yaml").read_text(encoding="utf-8"))
    operations = sum(
        1
        for methods in spec["paths"].values()
        for method in methods
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    )
    routes = sum(
        len(re.findall(r'@router\.(?:get|post|patch|put|delete)\(\s*"', source.read_text(encoding="utf-8")))
        for source in (ROOT / "apps" / "api" / "routers").glob("*.py")
    )

    if not re.search(rf"\*\*{operations} 个操作\*\*", prd):
        errors.append(f"PRD does not state the contract's {operations} operations")
    if not re.search(rf"\*\*{routes} 个路由\*\*", prd):
        errors.append(f"PRD does not state the {routes} implemented routes")

    # Exports are documented as a stage-3 capability; if the code exists the PRD must say so.
    for marker in ("运行环境适配", "导出"):
        if marker not in prd:
            errors.append(f"PRD does not mention {marker}")

    return errors


def check_adapter_docs() -> list[str]:
    """Every shipped adapter must be documented, with its directory."""

    from apps.api.domain.adapters import available_targets

    path = ROOT / "docs" / "ADAPTERS.md"
    if not path.exists():
        return ["docs/ADAPTERS.md is missing"]
    text = path.read_text(encoding="utf-8")

    errors: list[str] = []
    for target in available_targets():
        if target.identifier not in text:
            errors.append(f"ADAPTERS.md does not document target {target.identifier!r}")
        if target.skills_directory not in text:
            errors.append(
                f"ADAPTERS.md does not state {target.display_name}'s directory "
                f"({target.skills_directory})"
            )
    return errors


def main() -> int:
    checks = {
        "required documents present": check_required_documents(),
        "relative links resolve": check_links(),
        "code fences balanced": check_fences_balanced(),
        "PRD figures match the code": check_prd_figures(),
        "adapter documentation complete": check_adapter_docs(),
    }

    failed = False
    for name, errors in checks.items():
        if errors:
            failed = True
            print(f"[FAIL] {name}")
            for error in errors:
                print(f"    {error}")
        else:
            print(f"[PASS] {name}")

    print()
    print("RESULT:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
