"""Stage 1 validator: import conformance + test-suite run + manifest check.

Reports four distinct outcomes — PASS, FAIL, NOT RUN, HOLD — and never converts
missing infrastructure (MySQL, Docker, Redis/Qdrant, browser) into a synthetic
pass.  Run from the repository root:

    python scripts/validate_stage1.py
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_MODULES = [
    "apps.api.domain.models",
    "apps.api.domain.errors",
    "apps.api.domain.validation",
    "apps.api.domain.hashing",
    "apps.api.domain.lifecycle",
    "apps.api.domain.jobs",
    "apps.api.domain.permissions",
    "apps.api.domain.dependencies",
    "apps.api.config",
    "apps.api.db.session",
    "apps.api.db.health",
    "apps.api.repositories.contracts",
    "apps.api.repositories.sql_resources",
    "apps.api.repositories.sql_governance",
    "apps.api.providers.git_repository",
    "apps.api.providers.manifest_loader",
    "apps.api.application.resource_commands",
    "apps.api.application.governance",
    "apps.api.application.job_service",
    "apps.api.routers.system",
    "apps.api.routers.resources",
    "apps.api.routers.versions",
    "apps.api.routers.governance",
    "apps.api.main",
    "apps.worker.main",
]

# Modules that are not part of an importable package and are executed as
# scripts instead (``python scripts/<name>.py``).  Importing them by dotted name
# would fail on ``scripts`` not being a package, so they are loaded by path.
_SCRIPT_MODULES = [
    "scripts/seed_demo_resources.py",
]


def _import_check() -> list[str]:
    failures = []
    for module in _MODULES:
        try:
            importlib.import_module(module)
        except Exception as error:  # noqa: BLE001
            failures.append(f"{module}: {type(error).__name__}: {error}")
    for relative in _SCRIPT_MODULES:
        path = ROOT / relative
        try:
            spec = importlib.util.spec_from_file_location(
                f"_stage1_script_{path.stem}", path
            )
            if spec is None or spec.loader is None:
                raise ImportError("cannot create module specification")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as error:  # noqa: BLE001
            failures.append(f"{relative}: {type(error).__name__}: {error}")
    return failures


def _run_tests() -> tuple[int, int]:
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), pattern="test_stage1_*.py")
    runner = unittest.TextTestRunner(verbosity=1, stream=sys.stdout)
    result = runner.run(suite)
    return result.testsRun, len(result.failures) + len(result.errors)


def _manifest_check() -> list[str]:
    manifest_path = ROOT / "stage1-manifest.json"
    if not manifest_path.is_file():
        return ["stage1-manifest.json missing"]
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as error:  # noqa: BLE001
        return [f"stage1-manifest.json unreadable: {error}"]
    missing = [
        entry["path"]
        for entry in data.get("artifacts", [])
        if not (ROOT / entry["path"]).exists()
    ]
    return [f"missing managed file: {path}" for path in missing]


def main() -> int:
    print("== SuperCoder Stage 1 validation ==")
    import_failures = _import_check()
    print(f"import conformance: {'PASS' if not import_failures else 'FAIL'}")
    for failure in import_failures:
        print(f"  - {failure}")

    print("\n== test suite ==")
    tests_run, failures = _run_tests()
    print(f"tests: {tests_run} run, {failures} failed")

    manifest_failures = _manifest_check()
    print(f"\nmanifest check: {'PASS' if not manifest_failures else 'FAIL'}")
    for failure in manifest_failures:
        print(f"  - {failure}")

    print("\n== environment gates ==")
    from apps.api.config import get_settings

    settings = get_settings()
    gates = {
        "MySQL 8 integration": "HOLD" if not settings.mysql.enabled else "NOT RUN (no live DB probe here)",
        "Docker compose start": "HOLD (Docker unavailable)",
        "Redis/Qdrant": "HOLD (disabled, out of Stage 1 scope)",
        "Browser E2E": "HOLD (requires running API + browser)",
    }
    for name, status in gates.items():
        print(f"  - {name}: {status}")

    ok = not import_failures and not manifest_failures and failures == 0
    print(f"\nOverall: {'PASS' if ok else 'FAIL'} (infrastructure gates remain HOLD)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
