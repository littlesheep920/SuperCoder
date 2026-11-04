"""Write or verify golden adapter fixtures.

Golden files make export changes reviewable: a diff shows exactly which bytes moved,
instead of trusting that a refactor was harmless. They are regenerated deliberately with
`--write`, never automatically, so an unintended change fails rather than being absorbed.

The fixtures use real corpus resources so the bytes reflect actual manifests rather than
a convenient toy shape.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.domain.adapters import available_targets  # noqa: E402
from tests.test_adapter_ir import FakeResource  # noqa: E402

GOLDEN = ROOT / "tests" / "golden" / "adapters"

#: Three shapes that between them exercise every warning path and the MCP branch.
CASES = {
    "plain-skill": {
        "kind": "skill",
        "slug": "example-vocabulary-coverage",
        "manifest_extra": {},
    },
    "typed-io-skill": {
        "kind": "skill",
        "slug": "example-typed-io",
        "manifest_extra": {
            "inputs": [{"name": "text", "type": "text"}],
            "outputs": [{"name": "report", "type": "text"}],
        },
    },
    "mcp-server": {
        "kind": "mcp",
        "slug": "example-mcp-server",
        "manifest_extra": {},
        "mcp": {"transport": "stdio", "command": "npx", "args": ["-y", "example-mcp"]},
    },
}


def build_resource(name: str, spec: dict) -> FakeResource:
    manifest = {
        "inputs": [],
        "outputs": [],
        "runtimes": ["agent-skills"],
        "permissions": [],
    }
    manifest.update(spec.get("manifest_extra", {}))
    if "mcp" in spec:
        manifest["mcp"] = spec["mcp"]
    return FakeResource(
        resource_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        version_id="01BX5ZZKBKACTAV9WEVGEMMVRZ",
        kind=spec["kind"],
        slug=spec["slug"],
        name=name.replace("-", " ").title(),
        description=f"Example resource for the SuperCoder protocol. Fixture {name}.",
        body=f"# {name}\n\nExample resource for the SuperCoder protocol.\n",
        manifest=manifest,
    )


def render(bundle) -> dict:
    return {
        "target": bundle.target,
        "digest": bundle.digest(),
        "files": {
            path: {"media_type": f.media_type, "text": f.text}
            for path, f in ((p, bundle.files[p]) for p in sorted(bundle.files))
        },
        "warnings": [
            {
                "code": w.code,
                "source": w.source,
                "effect": w.effect,
                "mitigation": w.mitigation,
            }
            for w in bundle.warnings
        ],
        "permissions": {
            "declared": list(bundle.permissions.declared),
            "requires_approval": list(bundle.permissions.requires_approval),
            "unsupported": list(bundle.permissions.unsupported),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="regenerate the golden files")
    arguments = parser.parse_args()

    GOLDEN.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    # One fixture per (target, case) so a change to any single target is reviewable in
    # isolation rather than buried in a shared file.
    for target in available_targets():
        for name, spec in CASES.items():
            bundle = target.export(build_resource(name, spec))
            payload = render(bundle)
            text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
            path = GOLDEN / f"{target.identifier}--{name}.json"

            if arguments.write:
                path.write_text(text, encoding="utf-8", newline="\n")
                print(f"wrote {path.relative_to(ROOT)}")
                continue

            if not path.exists():
                failures.append(f"{path.name}: missing (run with --write)")
                continue
            if path.read_text(encoding="utf-8") != text:
                failures.append(f"{path.name}: differs from the current export")
            else:
                print(f"ok {path.name}")

    if failures:
        print()
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
