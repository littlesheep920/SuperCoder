"""CI gate: the documents' claims about code must match the code.

Three separate stale claims were found by hand in one session — an adapter marked
unfinished, a diff endpoint marked unimplemented, and a test count that had moved twice.
Hand audits do not scale and each one found a different failure, so the checks are now a
gate.

It verifies the claims a document can make that are mechanically checkable:

* a status row's maturity marker must match whether the code provides it;
* an endpoint named in backticks must be a registered route;
* every document must be indexed by `docs/README.md`.

What it does not do is judge prose. A checker that guessed at meaning would produce
confident nonsense; these checks are exact, so a pass means something.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DOCS = sorted(path for path in (ROOT / "docs").glob("*.md") if "archive" not in path.parts)


def main() -> int:
    from apps.api.domain.adapters import available_targets
    from apps.api.main import create_app

    spec = create_app().openapi()["paths"]
    # Routes are registered under `/v1/...`; normalise the parameter names so a document
    # may write `{versionId}` or `{version_id}` and still match.
    implemented = {
        (method.upper(), re.sub(r"\{[^}]*\}", "{}", path))
        for path, methods in spec.items()
        for method in methods
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }

    def has_route(method: str, path: str) -> bool:
        return (method.upper(), re.sub(r"\{[^}]*\}", "{}", f"/v1{path}")) in implemented

    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "apps" / "api").rglob("*.py")
    )
    adapter_modules = list((ROOT / "apps/api/domain/adapters").glob("*.py"))

    # Capabilities the PRD's status table names, and where to look for each in the code.
    capability_evidence = {
        "运行环境适配器": len(adapter_modules) >= 4,
        "文件级历史差异": has_route("GET", "/resource-versions/{id}/diff"),
        # The schema and the resolver are domain modules, and this row is marked implemented
        # before any route exists, so the evidence is the modules rather than a route.
        "工作流定义与依赖解析": (
            (ROOT / "apps/api/domain/workflows.py").exists()
            and (ROOT / "apps/api/domain/workflow_resolution.py").exists()
        ),
        "平台评测接口": any("/evaluations" in path for _, path in implemented),
        "生产身份认证": "oidc" in sources.lower() or "saml" in sources.lower(),
        "重复资源识别": "detect_duplicate" in sources,
    }

    problems: list[str] = []

    # 1. Status markers against the code.
    prd = (ROOT / "docs/PRD.md").read_text(encoding="utf-8")
    section = prd.split("### 11.2")[1].split("### 11.3")[0]
    for name, has_code in capability_evidence.items():
        row = re.search(rf"^\|\s*{re.escape(name)}\s*\|([^|]*)\|", section, re.MULTILINE)
        if not row:
            problems.append(f"PRD.md: no status row for {name}")
            continue
        status = row.group(1).strip()
        claims_implemented = "已实现" in status or "部分实现" in status
        if claims_implemented != has_code:
            problems.append(
                f"PRD.md: {name} is marked {status!r} but code present = {has_code}"
            )

    # 2. Endpoints named in documents must exist.
    for document in DOCS:
        text = document.read_text(encoding="utf-8")
        for match in re.finditer(r"`(GET|POST|PUT|PATCH|DELETE)\s+(/v1/[^`\s]+)`", text):
            method = match.group(1)
            raw = match.group(2)
            path = re.sub(r"\{[^}]*\}", "{}", raw)
            if (method, path) not in implemented:
                line = text[: match.start()].count("\n") + 1
                problems.append(
                    f"{document.name}:{line}: names {method} {raw}, "
                    "which is not a registered route"
                )

    # 3. Every adapter must be documented with its directory.
    adapters_doc = (ROOT / "docs/ADAPTERS.md").read_text(encoding="utf-8")
    for target in available_targets():
        if target.identifier not in adapters_doc:
            problems.append(f"ADAPTERS.md: does not document target {target.identifier}")
        if target.skills_directory not in adapters_doc:
            problems.append(
                f"ADAPTERS.md: does not state {target.display_name}'s directory "
                f"{target.skills_directory}"
            )

    # 4. Every document must be findable from the index.
    readme = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    for document in DOCS:
        if document.name == "README.md":
            continue
        if document.name not in readme:
            problems.append(f"README.md: does not index {document.name}")

    print(f"documents scanned: {len(DOCS)}")
    print(f"capabilities checked: {len(capability_evidence)}")
    print(f"registered routes: {len(implemented)}")
    print()

    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}")
        print()
        print("RESULT: FAIL")
        return 1

    print("[PASS] documents describe the code that exists")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
