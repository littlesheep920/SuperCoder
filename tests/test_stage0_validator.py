"""Targeted, dependency-free tests for the Stage 0 validator."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import validate_stage0 as validator


ROOT = Path(__file__).resolve().parents[1]


class TemporaryRootTestCase(unittest.TestCase):
    """Provide an isolated root populated only from project-local fixtures."""

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary_directory.name)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def write_text(self, relative: str, content: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        return path

    def write_json(self, relative: str, value: object) -> Path:
        return self.write_text(relative, json.dumps(value, indent=2) + "\n")


class PythonImportTests(TemporaryRootTestCase):
    """The import check must understand real package names and relative imports.

    Regression: loading each file as a synthetic top-level module made every
    ``from .sibling import x`` fail with "attempted relative import with no known
    parent package", so a healthy package reported ~20 false failures.
    """

    def test_relative_imports_resolve_inside_a_real_package(self) -> None:
        self.write_text("pkg/__init__.py", "")
        self.write_text("pkg/models.py", "VALUE = 1\n")
        self.write_text(
            "pkg/service.py",
            "from __future__ import annotations\n\nfrom .models import VALUE\n\n__all__ = ['VALUE']\n",
        )

        self.assertEqual(validator.check_python_files(self.root), [])

    def test_standalone_script_is_loaded_by_path(self) -> None:
        self.write_text("scripts/tool.py", "print('standalone')\n")

        self.assertEqual(validator.check_python_files(self.root), [])

    def test_standalone_script_using_dataclass_imports_cleanly(self) -> None:
        """Regression: @dataclass in a path-loaded script raised AttributeError.

        Loading a module without registering it in ``sys.modules`` makes
        ``dataclasses`` fail with a misleading "'NoneType' object has no
        attribute '__dict__'", which looked like a defect in the script.
        """

        self.write_text(
            "scripts/uses_dataclass.py",
            "from dataclasses import dataclass\n\n"
            "@dataclass\nclass Item:\n    name: str\n\n"
            "print(Item('x'))\n",
        )

        self.assertEqual(validator.check_python_files(self.root), [])

    def test_path_loaded_script_does_not_leak_into_sys_modules(self) -> None:
        self.write_text("scripts/leaky.py", "VALUE = 1\n")

        before = set(sys.modules)
        validator.check_python_files(self.root)
        leaked = {
            name
            for name in set(sys.modules) - before
            if name.startswith("_supercoder_stage0_script_")
        }

        self.assertEqual(leaked, set())

    def test_syntax_error_in_a_package_member_is_still_reported(self) -> None:
        self.write_text("pkg/__init__.py", "")
        self.write_text("pkg/broken.py", "def oops(:\n")

        errors = validator.check_python_files(self.root)

        self.assertTrue(any("pkg/broken.py" in error and "compile failed" in error for error in errors))

    def test_importing_a_missing_sibling_is_reported(self) -> None:
        self.write_text("pkg/__init__.py", "")
        self.write_text("pkg/service.py", "from .absent import VALUE\n")

        errors = validator.check_python_files(self.root)

        self.assertTrue(any("pkg/service.py" in error and "import failed" in error for error in errors))


class ManagedArtifactManifestTests(TemporaryRootTestCase):
    def manifest(self, extra_artifacts: list[dict[str, object]] | None = None) -> dict[str, object]:
        artifacts: list[dict[str, object]] = [
            {
                "path": "stage0-manifest.json",
                "requirements": ["R1"],
                "role": "Managed inventory.",
                "authorship": "hand-authored",
            },
            {
                "path": "docs/STAGE0_ACCEPTANCE_REPORT.md",
                "requirements": ["R1"],
                "role": "Generated acceptance evidence.",
                "authorship": "generated",
            },
        ]
        artifacts.extend(extra_artifacts or [])
        return {
            "schema_version": "1.0",
            "stage": "stage-0",
            "artifacts": artifacts,
        }

    def test_missing_listed_artifact_is_rejected(self) -> None:
        self.write_text("docs/STAGE0_ACCEPTANCE_REPORT.md", "generated\n")
        self.write_json(
            "stage0-manifest.json",
            self.manifest([
                {
                    "path": "managed/missing.txt",
                    "requirements": ["R3"],
                    "role": "Required managed fixture.",
                    "authorship": "hand-authored",
                }
            ]),
        )

        errors = validator.check_managed_artifacts(self.root)

        self.assertIn("missing managed artifact: managed/missing.txt", errors)

    def test_unlisted_user_file_is_ignored(self) -> None:
        self.write_text("docs/STAGE0_ACCEPTANCE_REPORT.md", "generated\n")
        self.write_text("user-notes.txt", "unrelated user content\n")
        self.write_json("stage0-manifest.json", self.manifest())

        self.assertEqual(validator.check_managed_artifacts(self.root), [])


class ManifestValidationTests(unittest.TestCase):
    def test_project_demo_manifests_parse_and_cover_all_resource_kinds(self) -> None:
        manifests = [
            validator._load_json(path)
            for path in sorted((ROOT / "resources").glob("*/*/resource.yaml"))
        ]

        self.assertEqual({manifest["kind"] for manifest in manifests}, validator.RESOURCE_KINDS)
        self.assertTrue(all(manifest["slug"] for manifest in manifests))
        context: dict[str, object] = {}
        self.assertEqual(validator.check_resource_contracts(ROOT, context), [])
        self.assertEqual(set(context["manifests"]), {
            validator._resource_identity(manifest) for manifest in manifests
        })

    def test_lifecycle_and_runtime_enumerations_are_enforced(self) -> None:
        schema = validator._load_json(ROOT / "schemas/resource.schema.json")
        manifest = validator._load_json(
            ROOT / "resources/skills/demo-code-review/resource.yaml"
        )
        manifest["status"] = "active"
        manifest["runtimes"] = ["unsupported-runtime"]

        errors = validator.JsonSchemaValidator(schema).errors(manifest)

        self.assertTrue(any("$.status" in error and "allowed enumeration" in error for error in errors))
        self.assertTrue(any("$.runtimes[0]" in error and "allowed enumeration" in error for error in errors))


class CrossFieldContractTests(unittest.TestCase):
    def _project_manifests(self) -> dict[str, dict[str, object]]:
        manifests: dict[str, dict[str, object]] = {}
        for path in sorted((ROOT / "resources").glob("*/*/resource.yaml")):
            manifest = validator._load_json(path)
            manifests[validator._resource_identity(manifest)] = manifest
        return manifests

    def test_duplicate_interfaces_agent_tools_workflow_refs_and_dependencies_are_rejected(self) -> None:
        manifests = self._project_manifests()
        skill_key = next(key for key in manifests if key.startswith("skill/"))
        agent_key = next(key for key in manifests if key.startswith("agent/"))
        workflow_key = next(key for key in manifests if key.startswith("workflow/"))

        manifests[skill_key]["inputs"].append(dict(manifests[skill_key]["inputs"][0]))
        manifests[agent_key]["agent"]["tools"].append("missing-tool")
        manifests[workflow_key]["workflow"]["edges"][0]["to"] = "missing-node"
        manifests[workflow_key]["workflow"]["approval_gates"].append("missing-gate")
        manifests[skill_key]["dependencies"].append({
            "kind": manifests[skill_key]["kind"],
            "slug": manifests[skill_key]["slug"],
            "version": manifests[skill_key]["version"],
            "optional": False,
            "source": "managed",
        })

        errors = validator._resource_cross_field_errors(manifests)

        self.assertTrue(any("duplicate inputs field names" in error for error in errors))
        self.assertTrue(any("agent tool 'missing-tool'" in error for error in errors))
        self.assertTrue(any("references unknown node 'missing-node'" in error for error in errors))
        self.assertTrue(any("approval gate 'missing-gate'" in error for error in errors))
        self.assertTrue(any("prohibited self-dependency" in error for error in errors))

    def test_managed_dependency_cycle_is_rejected(self) -> None:
        manifests = {
            "skill/alpha@1.0.0": {
                "kind": "skill", "slug": "alpha", "inputs": [], "outputs": [],
                "dependencies": [{"kind": "tool", "slug": "beta", "version": "1.0.0", "source": "managed"}],
            },
            "tool/beta@1.0.0": {
                "kind": "tool", "slug": "beta", "inputs": [], "outputs": [],
                "dependencies": [{"kind": "skill", "slug": "alpha", "version": "1.0.0", "source": "managed"}],
            },
        }

        errors = validator._resource_cross_field_errors(manifests)

        self.assertTrue(any("dependency cycle detected" in error for error in errors))


class ReviewRegressionTests(unittest.TestCase):
    def test_project_contracts_clear_known_review_blockers(self) -> None:
        self.assertEqual(validator.check_review_blocker_regressions(ROOT), [])

    def test_closed_allof_branch_rejecting_sibling_fields_is_detected(self) -> None:
        schemas = {
            "Base": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"id": {"type": "string"}},
            },
            "Broken": {
                "allOf": [
                    {"$ref": "#/components/schemas/Base"},
                    {"type": "object", "properties": {"extra": {"type": "string"}}},
                ]
            },
        }

        errors = validator._closed_allof_composition_errors(schemas)

        self.assertTrue(any("rejects sibling fields extra" in error for error in errors))


class EvaluationJoinTests(TemporaryRootTestCase):
    def setUp(self) -> None:
        super().setUp()
        shutil.copytree(ROOT / "evals", self.root / "evals")
        self.manifests = {}
        for path in sorted((ROOT / "resources").glob("*/*/resource.yaml")):
            manifest = validator._load_json(path)
            self.manifests[validator._resource_identity(manifest)] = manifest

    def test_project_evaluation_records_have_complete_valid_joins(self) -> None:
        errors = validator.check_evaluation_datasets(
            self.root, {"manifests": self.manifests}
        )

        self.assertEqual(errors, [])

    def test_evaluation_join_rejects_unknown_parent_id(self) -> None:
        qrels_path = self.root / "evals/retrieval/qrels.jsonl"
        lines = qrels_path.read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["query_id"] = "rq-missing-parent"
        lines[0] = json.dumps(first, separators=(",", ":"))
        qrels_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

        errors = validator.check_evaluation_datasets(
            self.root, {"manifests": self.manifests}
        )

        self.assertTrue(any(
            "query_id 'rq-missing-parent' does not reference a retrieval query" in error
            for error in errors
        ))


class SecretDetectionTests(TemporaryRootTestCase):
    def test_secret_scan_ignores_placeholders_and_detects_assigned_values(self) -> None:
        self.write_text("config/example.env", "API_KEY=replace_me\n")
        self.write_text("config/local.env", "TOKEN=live-token-value-123456\n")

        errors = validator.check_plaintext_secrets(self.root)

        self.assertTrue(any("config/local.env:1" in error for error in errors))
        self.assertFalse(any("config/example.env" in error for error in errors))

    def test_secret_scan_ignores_development_fixture_tokens(self) -> None:
        """`dev-*` tokens are declared non-production fixtures, not credentials."""

        self.write_text(
            "scripts/dev_tokens.json",
            '{"tokens": [{"token": "dev-owner-token"}, {"token": "test-reviewer-token"}]}\n',
        )

        errors = validator.check_plaintext_secrets(self.root)

        self.assertEqual(errors, [])

    def test_secret_scan_still_detects_prefixed_real_password(self) -> None:
        """The fixture allowance must not become a blanket bypass for real keys."""

        self.write_text(
            "config/leak.env",
            "API_KEY=placeholder\n"
            "GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789\n"
            "MYSQL_PASSWORD=dev-not-a-fixture-supersecret123\n",
        )

        errors = validator.check_plaintext_secrets(self.root)

        joined = "\n".join(errors)
        self.assertIn("GitHub token", joined)
        # ``\b`` would not match ``MYSQL_PASSWORD`` because ``_`` is a word
        # character; the prefixed-name blind spot must stay closed.
        self.assertTrue(any("PASSWORD" in error and "leak.env:3" in error for error in errors))

    def test_secret_scan_ignores_environment_interpolation(self) -> None:
        self.write_text(
            "docker-compose.yml",
            "services:\n  db:\n    environment:\n      MYSQL_PASSWORD: ${DB_PASSWORD:-localdev}\n",
        )

        self.assertEqual(validator.check_plaintext_secrets(self.root), [])

    def test_secret_scan_ignores_symbolic_enum_constants(self) -> None:
        self.write_text("pkg/errors.py", 'INVALID_TOKEN = "INVALID_TOKEN"\nSECRET = "SECRET"\n')

        self.assertEqual(validator.check_plaintext_secrets(self.root), [])

    def test_secret_scan_ignores_runtime_shell_substitution(self) -> None:
        """``TOKEN=$(...)`` is resolved at run time, not stored in the file."""

        self.write_text(
            ".github/workflows/ci.yml",
            "jobs:\n  x:\n    steps:\n      - run: |\n"
            "          TOKEN=$(python -c \"print('x')\")\n"
            "          API_KEY=`security find-generic-password`\n"
            "          SECRET=$OTHER_VAR\n",
        )

        self.assertEqual(validator.check_plaintext_secrets(self.root), [])

    def test_secret_scan_detects_literal_token_next_to_a_substitution(self) -> None:
        """The substitution allowance must not hide a hard-coded sibling value."""

        self.write_text(
            ".github/workflows/ci.yml",
            "steps:\n  - run: |\n"
            "      TOKEN=$(cat /dev/null)\n"
            "      PASSWORD: \"hunter2-literal-value\"\n",
        )

        errors = validator.check_plaintext_secrets(self.root)

        self.assertTrue(any("PASSWORD" in error for error in errors))


class DatabaseContractTests(TemporaryRootTestCase):
    def _write_database_contract(self, omitted_migration_table: str | None = None) -> None:
        document_sections = [
            f"## `{table}`\n\nStage 0 contract for `{table}`."
            for table in sorted(validator.CORE_TABLES)
        ]
        document_sections.append("The migration index `idx_resource_id` supports resource lookup.")
        self.write_text("docs/DATABASE_MODEL.md", "\n\n".join(document_sections) + "\n")

        statements = []
        for table in sorted(validator.CORE_TABLES):
            if table == omitted_migration_table:
                continue
            index = ", KEY `idx_resource_id` (`id`)" if table == "resource" else ""
            statements.append(
                f"CREATE TABLE `{table}` (`id` BIGINT NOT NULL PRIMARY KEY{index});"
            )
        self.write_text(
            "infra/migrations/0001_stage0_contract.sql", "\n".join(statements) + "\n"
        )

    def test_required_table_detection_accepts_complete_contract(self) -> None:
        self._write_database_contract()

        self.assertEqual(validator.check_database_contract(self.root), [])

    def test_required_table_detection_reports_missing_migration_table(self) -> None:
        self._write_database_contract(omitted_migration_table="audit_log")

        errors = validator.check_database_contract(self.root)

        self.assertIn("migration is missing core table audit_log", errors)


class ApiContractTests(TemporaryRootTestCase):
    def _valid_openapi(self) -> dict[str, object]:
        paths = {
            "/resources": "listResources",
            "/resources/{resourceId}/versions": "listResourceVersions",
            "/search/resources": "searchResources",
            "/agent-registrations": "registerAgent",
            "/workflows": "listWorkflows",
            "/jobs": "listJobs",
            "/evaluations": "listEvaluations",
        }
        required_error_fields = [
            "type",
            "title",
            "status",
            "code",
            "message",
            "request_id",
            "correlation_id",
            "retryable",
            "details",
        ]
        reusable_responses = {
            name: {"description": name}
            for name in (
                "BadRequest",
                "Unauthorized",
                "Forbidden",
                "NotFound",
                "Conflict",
                "ValidationFailed",
                "UnexpectedError",
            )
        }
        return {
            "openapi": "3.1.0",
            "info": {"title": "Test contract", "version": "0.0.0"},
            "paths": {
                path: {
                    "get": {
                        "operationId": operation_id,
                        "responses": {"200": {"description": "OK"}},
                    }
                }
                for path, operation_id in paths.items()
            },
            "components": {
                "schemas": {
                    "Error": {
                        "type": "object",
                        "required": required_error_fields,
                        "properties": {name: {} for name in required_error_fields},
                    },
                    "ErrorDetail": {"type": "object"},
                },
                "responses": reusable_responses,
            },
        }

    def _write_api_contract(self, spec: dict[str, object]) -> None:
        self.write_json("apps/api/openapi.stage0.yaml", spec)
        self.write_text(
            "docs/API_CONTRACT.md",
            "Resource and version semantics. Search and agent registration semantics. "
            "Workflow, job, evaluation, and error semantics.\n",
        )

    def test_endpoint_detection_accepts_every_required_group(self) -> None:
        self._write_api_contract(self._valid_openapi())

        self.assertEqual(validator.check_api_contract(self.root), [])

    def test_endpoint_detection_reports_missing_group(self) -> None:
        spec = self._valid_openapi()
        del spec["paths"]["/jobs"]
        self._write_api_contract(spec)

        errors = validator.check_api_contract(self.root)

        self.assertIn("OpenAPI contract is missing the jobs endpoint group", errors)


class ValidationReportTests(unittest.TestCase):
    def test_report_pass_and_hold_gate_logic(self) -> None:
        accepted = validator.ValidationReport(ROOT)
        accepted.add("contracts")
        held = validator.ValidationReport(ROOT)
        held.add("contracts", ["contract mismatch"])

        self.assertEqual("PASS" if accepted.passed else "HOLD", "PASS")
        self.assertEqual("PASS" if held.passed else "HOLD", "HOLD")
        self.assertIn("RESULT: PASS", accepted.console_text())
        self.assertIn("RESULT: HOLD", held.console_text())
        self.assertIn("contract mismatch", held.console_text())
        self.assertIn("contracts: contract mismatch", held.errors)
        markdown = held.markdown_text()
        for heading in (
            "## Stage 0 requirement completion",
            "## Actual modified-file inventory",
            "## Commands and observed results",
            "## Unresolved issues",
            "## Concrete Stage 1 entry points",
        ):
            self.assertIn(heading, markdown)


if __name__ == "__main__":
    unittest.main()
