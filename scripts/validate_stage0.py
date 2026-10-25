#!/usr/bin/env python3
"""Validate the frozen SuperCoder Stage 0 contracts using only the standard library."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable
from urllib.parse import urlparse

LIFECYCLE_STATES = {
    "draft",
    "validating",
    "pending_review",
    "approved",
    "published",
    "deprecated",
    "retired",
}
RUNTIMES = {
    "kiro",
    "claude-code",
    "cursor",
    "dify",
    "openai-compatible-agent-runtime",
}
RESOURCE_KINDS = {"skill", "mcp", "prompt", "agent", "workflow", "tool"}
REQUIRED_DIRECTORIES = (
    "apps/api",
    "apps/web",
    "apps/worker",
    "resources/skills",
    "resources/mcp",
    "resources/prompts",
    "resources/agents",
    "resources/workflows",
    "resources/tools",
    "evals/retrieval",
    "evals/duplicates",
    "evals/recommendations",
    "evals/workflows",
    "evals/schemas",
    "infra",
    "infra/migrations",
    "schemas",
    "scripts",
    "docs",
)
CORE_TABLES = {
    "resource",
    "resource_version",
    "resource_tag",
    "resource_category",
    "resource_runtime",
    "resource_dependency",
    "resource_review",
    "resource_usage",
    "resource_index_status",
    "resource_duplicate_candidate",
    "resource_evaluation",
    "workflow_definition",
    "workflow_run",
    "job",
    "job_event",
    "audit_log",
}
JSONL_CONTRACTS = {
    "evals/retrieval/queries.jsonl": ("retrieval_query", "retrievalQuery", "query_id"),
    "evals/retrieval/qrels.jsonl": ("retrieval_qrel", "retrievalQrel", "qrel_id"),
    "evals/duplicates/pairs.jsonl": ("duplicate_pair", "duplicatePair", "pair_id"),
    "evals/duplicates/labels.jsonl": ("duplicate_label", "duplicateLabel", "label_id"),
    "evals/recommendations/requests.jsonl": (
        "recommendation_request",
        "recommendationRequest",
        "request_id",
    ),
    "evals/recommendations/human_labels.jsonl": (
        "recommendation_human_label",
        "recommendationHumanLabel",
        "label_id",
    ),
    "evals/workflows/cases.jsonl": ("workflow_case", "workflowCase", "case_id"),
    "evals/workflows/expected_outputs.jsonl": (
        "workflow_expected_output",
        "workflowExpectedOutput",
        "expectation_id",
    ),
}
COMMON_RESOURCE_FIELDS = {
    "schema_version",
    "kind",
    "slug",
    "name",
    "version",
    "description",
    "aliases",
    "tags",
    "categories",
    "runtimes",
    "inputs",
    "outputs",
    "permissions",
    "dependencies",
    "entrypoint",
    "status",
    "owner_team",
}
SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
SECRET_NAME_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?key|client[_-]?secret|secret|password|passwd|token|private[_-]?key)"
)
SECRET_ASSIGNMENT_RE = re.compile(
    # Leading boundary is ``(?<![A-Za-z0-9])`` rather than ``\b``: ``_`` is a
    # word character, so ``\b`` fails to match the most common credential
    # spellings (``DB_PASSWORD=``, ``MYSQL_PASSWORD=``, ``MY_API_KEY=``).
    # Trailing ``\b`` still prevents matching the bare word inside a sentence.
    r"(?i)[\"']?(?<![A-Za-z0-9])"
    r"(api[_-]?key|access[_-]?key|client[_-]?secret|secret|password|passwd|token|private[_-]?key)\b[\"']?"
    r"\s*[:=]\s*(?:(?P<quote>[\"'])(?P<quoted>[^\"'\r\n]+)(?P=quote)|(?P<bare>[^\s,;}#]+))"
)
HIGH_CONFIDENCE_SECRET_PATTERNS = (
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[opurs]_[A-Za-z0-9_]{30,255}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
)


REQUIREMENT_CHECKS = {
    "R1": (),
    "R2": ("repository boundary and Git status",),
    "R3": ("required structure", "targeted validator tests"),
    "R4": ("resource manifests and schema", "review blocker regressions", "targeted validator tests"),
    "R5": ("resource manifests and schema", "database model and indexes", "API endpoint and error contracts", "targeted validator tests"),
    "R6": ("resource manifests and schema", "Python compile and import", "targeted validator tests"),
    "R7": ("evaluation datasets", "targeted validator tests"),
    "R8": ("database model and indexes", "review blocker regressions", "targeted validator tests"),
    "R9": ("API endpoint and error contracts", "JSON-compatible contract files", "review blocker regressions", "targeted validator tests"),
    "R10": ("plaintext secret scan", "JSON-compatible contract files", "targeted validator tests"),
}
REQUIREMENT_SUMMARIES = {
    "R1": "Implementation-plan fidelity and complete-build boundary",
    "R2": "Repository/Git preservation evidence",
    "R3": "Formal Stage 0 directory structure",
    "R4": "Unified six-kind resource protocol",
    "R5": "Lifecycle, runtime, permission, version, and dependency governance",
    "R6": "Readable demo resource for every kind",
    "R7": "Evaluation schemas and parseable seed datasets",
    "R8": "Database model and MySQL migration contract",
    "R9": "OpenAPI and semantic API contract",
    "R10": "Configuration and security boundaries",
}
STAGE1_ENTRY_POINTS = (
    "API control plane — implement `apps.api.main:app` from the frozen OpenAPI operation IDs, with authentication, idempotency, ETag, transaction, and rejection-path tests.",
    "MySQL persistence — execute `infra/migrations/0001_stage0_contract.sql` on MySQL 8.0.16+ and add migrate/rollback, FK, CHECK, trigger, uniqueness, and concurrency tests.",
    "Registry and governance — implement Git discovery, canonical hashing, exact-version dependency resolution, review, publication, and retirement against the frozen resource protocol.",
    "Worker and Workflow runtime — implement durable claim/lease/retry/cancel, immutable resolution snapshots, approval gates, checkpoints, and recovery.",
    "Search and Evaluation — build real indexes and runners with fixed dataset/candidate/evaluator hashes, reproducible metrics, provenance, and hidden-label isolation.",
    "Runtime adapters and security — validate all five declared environments, fail closed on unsupported mappings, and prove disabled external services neither read secrets nor emit network traffic.",
)


class ValidationReport:
    """Collect named outcomes and render an evidence-complete acceptance report."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.checks: list[tuple[str, list[str]]] = []
        self.commands: list[tuple[str, int, str]] = []
        self.modified_files: list[str] | None = None

    def add(self, name: str, errors: Iterable[str] = ()) -> None:
        self.checks.append((name, list(errors)))

    def add_command(self, command: str, exit_status: int, output: str) -> None:
        self.commands.append((command, exit_status, output.strip() or "(no output)"))

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(not errors for _, errors in self.checks)

    @property
    def outcome(self) -> str:
        return "PASS" if self.passed else "HOLD"

    @property
    def errors(self) -> list[str]:
        return [f"{name}: {error}" for name, errors in self.checks for error in errors]

    def console_text(self) -> str:
        lines: list[str] = []
        for name, errors in self.checks:
            lines.append(f"[{'PASS' if not errors else 'HOLD'}] {name}")
            lines.extend(f"  - {error}" for error in errors)
        lines.append(f"RESULT: {self.outcome}")
        return "\n".join(lines)

    def _requirement_statuses(self) -> list[tuple[str, str, str]]:
        outcomes = {name: not errors for name, errors in self.checks}
        all_formal_checks_pass = self.passed
        rows: list[tuple[str, str, str]] = []
        for requirement, required_checks in REQUIREMENT_CHECKS.items():
            passed = all_formal_checks_pass if requirement == "R1" else all(
                outcomes.get(check, False) for check in required_checks
            )
            evidence = "all formal checks" if requirement == "R1" else ", ".join(required_checks)
            rows.append((requirement, "PASS" if passed else "HOLD", evidence))
        return rows

    def _managed_inventory(self) -> tuple[list[dict[str, Any]], str | None]:
        path = self.root / "stage0-manifest.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return [], f"Could not read managed inventory: {exc}"
        artifacts = manifest.get("artifacts", []) if isinstance(manifest, dict) else []
        if not isinstance(artifacts, list):
            return [], "stage0-manifest.json artifacts is not an array"
        return [item for item in artifacts if isinstance(item, dict)], None

    def markdown_text(self) -> str:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        lines = [
            "# SuperCoder Stage 0 Acceptance Report",
            "",
            f"- Generated: `{now}`",
            f"- Validation root: `{self.root}`",
            f"- Acceptance result: **{self.outcome}**",
            "- Scope: static Stage 0 contract acceptance only; this is not evidence that Stage 1 services are implemented or production-ready.",
            "",
            "## Formal checks",
            "",
        ]
        for name, errors in self.checks:
            mark = "x" if not errors else " "
            lines.append(f"- [{mark}] **{name}** — {'PASS' if not errors else 'HOLD'}")
            lines.extend(f"  - {error}" for error in errors)

        lines.extend([
            "",
            "## Stage 0 requirement completion",
            "",
            "| Requirement | Status | Formal evidence |",
            "|---|---|---|",
        ])
        for requirement, status, evidence in self._requirement_statuses():
            lines.append(
                f"| {requirement}: {REQUIREMENT_SUMMARIES[requirement]} | **{status}** | {evidence} |"
            )

        lines.extend(["", "## Actual modified-file inventory", ""])
        if self.modified_files is None:
            lines.append(
                "Git-derived modified-file inventory is unavailable because the validation root is not inside a Git worktree. This is an unresolved acceptance blocker."
            )
        elif self.modified_files:
            lines.extend(f"- `{line}`" for line in self.modified_files)
        else:
            lines.append("`git status --short` reported a clean worktree.")

        managed, inventory_error = self._managed_inventory()
        lines.extend(["", "### Managed Stage 0 artifact inventory", ""])
        if inventory_error:
            lines.append(inventory_error)
        else:
            lines.append(
                "The following is the complete managed delivery inventory from `stage0-manifest.json`; it is not a substitute for Git-derived modification provenance."
            )
            lines.extend([
                "",
                "| Path | Requirements | Authorship | Role |",
                "|---|---|---|---|",
            ])
            for artifact in managed:
                path = str(artifact.get("path", ""))
                requirements = ", ".join(str(item) for item in artifact.get("requirements", []))
                authorship = str(artifact.get("authorship", ""))
                role = str(artifact.get("role", "")).replace("|", "\\|")
                lines.append(f"| `{path}` | {requirements} | {authorship} | {role} |")

        lines.extend(["", "## Commands and observed results", ""])
        if not self.commands:
            lines.append("No command evidence was recorded.")
        for command, exit_status, output in self.commands:
            lines.extend([
                f"### Exit {exit_status}: `{command}`",
                "",
                "```text",
                output,
                "```",
                "",
            ])

        lines.extend(["## Unresolved issues", ""])
        if self.errors:
            lines.extend(f"- {error}" for error in self.errors)
        else:
            lines.append("None within the formal static checks.")

        lines.extend(["", "## Concrete Stage 1 entry points", ""])
        lines.extend(f"{index}. {entry}" for index, entry in enumerate(STAGE1_ENTRY_POINTS, 1))
        lines.extend([
            "",
            "## Acceptance rule",
            "",
            "PASS is emitted only when every formal check succeeds. Any failed check emits HOLD and the validator returns a nonzero exit status. Static PASS does not claim running services, production readiness, or completion of Stage 1.",
            "",
        ])
        return "\n".join(lines)


class JsonSchemaValidator:
    """Small Draft 2020-12 subset covering the checked-in Stage 0 schemas."""

    def __init__(self, root_schema: dict[str, Any]) -> None:
        self.root_schema = root_schema

    def resolve(self, ref: str) -> Any:
        if not ref.startswith("#/"):
            raise ValueError(f"external schema reference is not supported: {ref}")
        value: Any = self.root_schema
        for token in ref[2:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            value = value[token]
        return value

    def errors(self, instance: Any, schema: Any | None = None, path: str = "$") -> list[str]:
        return self._validate(instance, self.root_schema if schema is None else schema, path)

    def _validate(self, instance: Any, schema: Any, path: str) -> list[str]:
        if schema is True:
            return []
        if schema is False:
            return [f"{path}: value is prohibited"]
        if not isinstance(schema, dict):
            return [f"{path}: invalid schema node"]

        errors: list[str] = []
        if "$ref" in schema:
            try:
                errors.extend(self._validate(instance, self.resolve(schema["$ref"]), path))
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"{path}: cannot resolve {schema['$ref']!r}: {exc}")

        if "allOf" in schema:
            for child in schema["allOf"]:
                errors.extend(self._validate(instance, child, path))
        if "anyOf" in schema:
            matches = sum(not self._validate(instance, child, path) for child in schema["anyOf"])
            if matches == 0:
                errors.append(f"{path}: does not satisfy any allowed schema")
        if "oneOf" in schema:
            matches = sum(not self._validate(instance, child, path) for child in schema["oneOf"])
            if matches != 1:
                errors.append(f"{path}: must satisfy exactly one allowed schema (matched {matches})")
        if "not" in schema and not self._validate(instance, schema["not"], path):
            errors.append(f"{path}: matches a prohibited schema")
        if "if" in schema:
            branch = "then" if not self._validate(instance, schema["if"], path) else "else"
            if branch in schema:
                errors.extend(self._validate(instance, schema[branch], path))

        expected_type = schema.get("type")
        if expected_type is not None and not self._matches_type(instance, expected_type):
            errors.append(f"{path}: expected type {expected_type!r}, got {type(instance).__name__}")
            return errors
        if "const" in schema and instance != schema["const"]:
            errors.append(f"{path}: expected constant {schema['const']!r}")
        if "enum" in schema and instance not in schema["enum"]:
            errors.append(f"{path}: value {instance!r} is not in the allowed enumeration")

        if isinstance(instance, dict):
            required = schema.get("required", [])
            for key in required:
                if key not in instance:
                    errors.append(f"{path}: missing required property {key!r}")
            properties = schema.get("properties", {})
            for key, child_schema in properties.items():
                if key in instance:
                    errors.extend(self._validate(instance[key], child_schema, self._join(path, key)))
            if schema.get("additionalProperties") is False:
                for key in sorted(set(instance) - set(properties)):
                    errors.append(f"{self._join(path, key)}: additional property is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                child_schema = schema["additionalProperties"]
                for key in sorted(set(instance) - set(properties)):
                    errors.extend(self._validate(instance[key], child_schema, self._join(path, key)))
            if schema.get("unevaluatedProperties") is False:
                allowed = self._declared_properties(schema)
                for key in sorted(set(instance) - allowed):
                    errors.append(f"{self._join(path, key)}: unevaluated property is not allowed")
            if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
                errors.append(f"{path}: has more than {schema['maxProperties']} properties")
            if "minProperties" in schema and len(instance) < schema["minProperties"]:
                errors.append(f"{path}: has fewer than {schema['minProperties']} properties")
            if "propertyNames" in schema:
                for key in instance:
                    errors.extend(self._validate(key, schema["propertyNames"], self._join(path, key)))

        if isinstance(instance, list):
            if "minItems" in schema and len(instance) < schema["minItems"]:
                errors.append(f"{path}: has fewer than {schema['minItems']} items")
            if "maxItems" in schema and len(instance) > schema["maxItems"]:
                errors.append(f"{path}: has more than {schema['maxItems']} items")
            if schema.get("uniqueItems"):
                seen: set[str] = set()
                for index, value in enumerate(instance):
                    marker = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                    if marker in seen:
                        errors.append(f"{path}[{index}]: duplicate array item")
                    seen.add(marker)
            if isinstance(schema.get("items"), (dict, bool)):
                for index, value in enumerate(instance):
                    errors.extend(self._validate(value, schema["items"], f"{path}[{index}]"))

        if isinstance(instance, str):
            if "minLength" in schema and len(instance) < schema["minLength"]:
                errors.append(f"{path}: string is shorter than {schema['minLength']}")
            if "maxLength" in schema and len(instance) > schema["maxLength"]:
                errors.append(f"{path}: string is longer than {schema['maxLength']}")
            if "pattern" in schema:
                try:
                    if re.search(schema["pattern"], instance) is None:
                        errors.append(f"{path}: string does not match {schema['pattern']!r}")
                except re.error as exc:
                    errors.append(f"{path}: invalid schema regex: {exc}")
            if "format" in schema and not self._valid_format(instance, schema["format"]):
                errors.append(f"{path}: invalid {schema['format']} value")

        if self._is_number(instance):
            if "minimum" in schema and instance < schema["minimum"]:
                errors.append(f"{path}: number is below minimum {schema['minimum']}")
            if "maximum" in schema and instance > schema["maximum"]:
                errors.append(f"{path}: number is above maximum {schema['maximum']}")
        return errors

    def _declared_properties(self, schema: Any, seen: set[int] | None = None) -> set[str]:
        if not isinstance(schema, dict):
            return set()
        seen = set() if seen is None else seen
        marker = id(schema)
        if marker in seen:
            return set()
        seen.add(marker)
        declared = set(schema.get("properties", {}))
        if "$ref" in schema:
            try:
                declared.update(self._declared_properties(self.resolve(schema["$ref"]), seen))
            except (KeyError, TypeError, ValueError):
                pass
        for keyword in ("allOf", "anyOf", "oneOf"):
            for child in schema.get(keyword, []):
                declared.update(self._declared_properties(child, seen))
        return declared

    @staticmethod
    def _matches_type(instance: Any, expected: Any) -> bool:
        expected_types = expected if isinstance(expected, list) else [expected]
        return any(
            {
                "null": instance is None,
                "boolean": isinstance(instance, bool),
                "object": isinstance(instance, dict),
                "array": isinstance(instance, list),
                "string": isinstance(instance, str),
                "integer": isinstance(instance, int) and not isinstance(instance, bool),
                "number": JsonSchemaValidator._is_number(instance),
            }.get(item, False)
            for item in expected_types
        )

    @staticmethod
    def _is_number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    @staticmethod
    def _join(path: str, key: str) -> str:
        return f"{path}.{key}" if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) else f"{path}[{key!r}]"

    @staticmethod
    def _valid_format(value: str, format_name: str) -> bool:
        if format_name == "date-time":
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed.tzinfo is not None
            except ValueError:
                return False
        if format_name == "uri":
            parsed = urlparse(value)
            return bool(parsed.scheme and (parsed.netloc or parsed.scheme == "urn"))
        if format_name == "uri-reference":
            return " " not in value and "\n" not in value and "\r" not in value
        return True


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, 1):
                line = raw_line.strip()
                if not line:
                    errors.append(f"{path.relative_to(path.parents[2])}:{line_number}: blank lines are not allowed")
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append(f"{path.name}:{line_number}: invalid JSON: {exc.msg}")
                    continue
                if not isinstance(value, dict):
                    errors.append(f"{path.name}:{line_number}: record must be an object")
                    continue
                value["__line__"] = line_number
                records.append(value)
    except OSError as exc:
        errors.append(f"{path}: cannot read JSONL: {exc}")
    return records, errors


def _without_line(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "__line__"}


def _resource_identity(reference: dict[str, Any]) -> str | None:
    kind = reference.get("kind")
    slug = reference.get("slug")
    version = reference.get("version")
    if not all(isinstance(value, str) for value in (kind, slug, version)):
        return None
    return f"{kind}/{slug}@{version}"


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().strip("\"'").lower()
    if not normalized:
        return True
    if (normalized.startswith("${") and normalized.endswith("}")) or (
        normalized.startswith("%") and normalized.endswith("%")
    ):
        return True
    if normalized.startswith("<") and normalized.endswith(">"):
        return True
    markers = (
        "example",
        "placeholder",
        "replace_me",
        "replace-me",
        "change_me",
        "change-me",
        "changeme",
        "your_",
        "your-",
        "dummy",
        "redacted",
        "masked",
        "not-set",
        "disabled",
        "none",
        "null",
        "***",
    )
    return any(marker in normalized for marker in markers)


def _is_symbolic_constant(value: str) -> bool:
    """Return True when *value* is an identifier-like constant, not a credential.

    ``INVALID_TOKEN = "INVALID_TOKEN"`` and ``ERROR_SECRET = "SECRET"`` are enum
    members whose value mirrors their name. Real credentials are never a single
    UPPER_SNAKE token equal to (or derived from) their own field name.
    """

    text = value.strip().strip("\"'")
    if not text or len(text) > 64:
        return False
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", text):
        return False
    return True


def _is_development_fixture_secret(name: str, value: str) -> bool:
    """Return True for a declared development-fixture *token* value.

    ``scripts/dev_tokens.json`` holds server-owned opaque bearer tokens that map
    to fixed demo identities. They are intentionally committed so the local
    stack and its tests can authenticate; they grant nothing outside a local
    environment and are declared non-production in the file itself.

    The allowance is deliberately narrow: it applies only to a ``token`` field
    and only to an explicit development prefix. A ``password``, ``secret``, or
    ``api_key`` value is never exempt, even when it starts with ``dev-``, and a
    high-confidence credential pattern (GitHub, AWS, OpenAI, private key) is
    still reported before this check runs.
    """

    field = name.strip().strip("\"'").lower().replace("-", "_")
    if field != "token":
        return False
    normalized = value.strip().strip("\"'").lower()
    if "fixture" in normalized:
        return True
    return any(
        normalized.startswith(prefix)
        for prefix in ("dev_", "dev-", "test_", "test-")
    )


def check_managed_artifacts(root: Path) -> list[str]:
    """Validate the explicit Stage 0 inventory without policing unrelated user files."""
    manifest_path = root / "stage0-manifest.json"
    try:
        manifest = _load_json(manifest_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot parse stage0-manifest.json: {exc}"]
    if not isinstance(manifest, dict):
        return ["stage0-manifest.json: top-level value must be an object"]

    errors: list[str] = []
    if manifest.get("schema_version") != "1.0":
        errors.append("stage0-manifest.json: schema_version must be '1.0'")
    if manifest.get("stage") != "stage-0":
        errors.append("stage0-manifest.json: stage must be 'stage-0'")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return errors + ["stage0-manifest.json: artifacts must be a non-empty array"]

    seen_paths: set[str] = set()
    listed: dict[str, dict[str, Any]] = {}
    for index, artifact in enumerate(artifacts):
        location = f"stage0-manifest.json: artifacts[{index}]"
        if not isinstance(artifact, dict):
            errors.append(f"{location} must be an object")
            continue
        relative = artifact.get("path")
        if not isinstance(relative, str) or not relative:
            errors.append(f"{location}.path must be a non-empty string")
            continue
        if (
            "\\" in relative
            or relative.startswith("/")
            or relative.startswith("./")
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or Path(relative).is_absolute()
        ):
            errors.append(f"{location}.path must be a normalized repository-relative POSIX path: {relative!r}")
            continue
        if relative in seen_paths:
            errors.append(f"{location}.path duplicates {relative!r}")
            continue
        seen_paths.add(relative)
        listed[relative] = artifact

        requirements = artifact.get("requirements")
        if (
            not isinstance(requirements, list)
            or not requirements
            or any(not isinstance(item, str) or re.fullmatch(r"R(?:10|[1-9])", item) is None for item in requirements)
            or len(set(requirements)) != len(requirements)
        ):
            errors.append(f"{location}.requirements must contain unique requirement IDs R1 through R10")
        role = artifact.get("role")
        if not isinstance(role, str) or not role.strip():
            errors.append(f"{location}.role must be a non-empty string")
        authorship = artifact.get("authorship")
        if authorship not in {"hand-authored", "generated"}:
            errors.append(f"{location}.authorship must be 'hand-authored' or 'generated'")
        if authorship != "generated" and not (root / relative).is_file():
            errors.append(f"missing managed artifact: {relative}")

    required_inventory_entries = {
        "stage0-manifest.json": "hand-authored",
    }
    for relative, authorship in required_inventory_entries.items():
        artifact = listed.get(relative)
        if artifact is None:
            errors.append(f"stage0-manifest.json must list {relative}")
        elif artifact.get("authorship") != authorship:
            errors.append(
                f"stage0-manifest.json: {relative} must have authorship {authorship!r}"
            )

    # The Stage 0 acceptance report is a generated process record. It may be
    # listed at its active path or under docs/archive/ once the stage closed;
    # either way the manifest must declare it, so the evidence trail is explicit.
    report_names = {"docs/STAGE0_ACCEPTANCE_REPORT.md", "docs/archive/STAGE0_ACCEPTANCE_REPORT.md"}
    report_entries = [listed[name] for name in report_names if name in listed]
    if not report_entries:
        errors.append(
            "stage0-manifest.json must list docs/STAGE0_ACCEPTANCE_REPORT.md "
            "(active or under docs/archive/)"
        )
    elif all(entry.get("authorship") != "generated" for entry in report_entries):
        errors.append(
            "stage0-manifest.json: the Stage 0 acceptance report must have authorship 'generated'"
        )
    return errors


def check_required_structure(root: Path) -> list[str]:
    errors = [f"missing required directory: {relative}" for relative in REQUIRED_DIRECTORIES if not (root / relative).is_dir()]
    required_files = (
        "schemas/resource.schema.json",
        "evals/schemas/evaluation-records.schema.json",
        "evals/FORMAT.md",
        "docs/RESOURCE_PROTOCOL.md",
        "docs/RESOURCE_GOVERNANCE.md",
        "docs/DATABASE_MODEL.md",
        "docs/API_CONTRACT.md",
        "docs/SECURITY_AND_CONFIGURATION.md",
        "docs/ARCHITECTURE_FREEZE.md",
        "infra/migrations/0001_stage0_contract.sql",
        "infra/config.schema.json",
        ".env.example",
        "apps/api/openapi.stage0.yaml",
    )
    errors.extend(f"missing required file: {relative}" for relative in required_files if not (root / relative).is_file())
    errors.extend(check_managed_artifacts(root))
    return errors


def _duplicate_named_values(items: Any, field: str = "name") -> list[str]:
    if not isinstance(items, list):
        return []
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get(field), str):
            continue
        value = item[field]
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _managed_dependency(dependency: Any) -> bool:
    if not isinstance(dependency, dict) or dependency.get("kind") == "external":
        return False
    return dependency.get("source", "managed") != "external"


def _resource_cross_field_errors(manifests: dict[str, dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    identities_by_pair: dict[tuple[Any, Any], set[str]] = {}
    for identity, manifest in manifests.items():
        pair = (manifest.get("kind"), manifest.get("slug"))
        identities_by_pair.setdefault(pair, set()).add(identity)

    dependency_graph: dict[tuple[Any, Any], set[tuple[Any, Any]]] = {
        pair: set() for pair in identities_by_pair
    }
    for identity, manifest in sorted(manifests.items()):
        source_pair = (manifest.get("kind"), manifest.get("slug"))

        for field_name in ("inputs", "outputs"):
            duplicates = _duplicate_named_values(manifest.get(field_name))
            if duplicates:
                errors.append(
                    f"{identity}: duplicate {field_name} field names: {', '.join(duplicates)}"
                )
        if manifest.get("kind") == "prompt":
            duplicates = _duplicate_named_values(manifest.get("prompt", {}).get("variables"))
            if duplicates:
                errors.append(
                    f"{identity}: duplicate prompt variable names: {', '.join(duplicates)}"
                )

        dependencies = manifest.get("dependencies", [])
        seen_dependencies: set[tuple[Any, Any]] = set()
        managed_references: list[tuple[str, dict[str, Any]]] = []
        if isinstance(dependencies, list):
            for index, dependency in enumerate(dependencies):
                if not isinstance(dependency, dict):
                    continue
                dependency_pair = (dependency.get("kind"), dependency.get("slug"))
                if dependency_pair in seen_dependencies:
                    errors.append(
                        f"{identity}: duplicate dependency {dependency_pair[0]}/{dependency_pair[1]}"
                    )
                seen_dependencies.add(dependency_pair)
                if _managed_dependency(dependency):
                    managed_references.append((f"dependencies[{index}]", dependency))

        if manifest.get("kind") == "agent":
            declared_tools = manifest.get("agent", {}).get("tools", [])
            tool_dependencies = {
                dependency.get("slug")
                for dependency in dependencies
                if _managed_dependency(dependency)
                and dependency.get("kind") in {"mcp", "tool"}
            }
            if isinstance(declared_tools, list):
                for tool_slug in declared_tools:
                    if tool_slug not in tool_dependencies:
                        errors.append(
                            f"{identity}: agent tool {tool_slug!r} has no managed MCP or Tool dependency"
                        )

        if manifest.get("kind") == "workflow":
            workflow = manifest.get("workflow", {})
            nodes = workflow.get("nodes", []) if isinstance(workflow, dict) else []
            node_ids: set[str] = set()
            node_types: dict[str, Any] = {}
            duplicate_node_ids: set[str] = set()
            if isinstance(nodes, list):
                for index, node in enumerate(nodes):
                    if not isinstance(node, dict):
                        continue
                    node_id = node.get("id")
                    if isinstance(node_id, str):
                        if node_id in node_ids:
                            duplicate_node_ids.add(node_id)
                        node_ids.add(node_id)
                        node_types[node_id] = node.get("type")
                    dependency = node.get("resource")
                    if _managed_dependency(dependency):
                        managed_references.append((f"workflow.nodes[{index}].resource", dependency))
            if duplicate_node_ids:
                errors.append(
                    f"{identity}: duplicate workflow node IDs: {', '.join(sorted(duplicate_node_ids))}"
                )
            edges = workflow.get("edges", []) if isinstance(workflow, dict) else []
            if isinstance(edges, list):
                for index, edge in enumerate(edges):
                    if not isinstance(edge, dict):
                        continue
                    for endpoint in ("from", "to"):
                        node_id = edge.get(endpoint)
                        if isinstance(node_id, str) and node_id not in node_ids:
                            errors.append(
                                f"{identity}: workflow.edges[{index}].{endpoint} references unknown node {node_id!r}"
                            )
            approval_gates = workflow.get("approval_gates", []) if isinstance(workflow, dict) else []
            if isinstance(approval_gates, list):
                for gate_id in approval_gates:
                    if gate_id not in node_ids:
                        errors.append(
                            f"{identity}: approval gate {gate_id!r} references an unknown node"
                        )
                    elif node_types.get(gate_id) != "approval":
                        errors.append(
                            f"{identity}: approval gate {gate_id!r} must reference a node of type 'approval'"
                        )

        for location, dependency in managed_references:
            target_pair = (dependency.get("kind"), dependency.get("slug"))
            if target_pair == source_pair:
                errors.append(
                    f"{identity}: {location} creates a prohibited self-dependency"
                )
                continue
            if target_pair not in identities_by_pair:
                errors.append(
                    f"{identity}: {location} does not resolve: {target_pair[0]}/{target_pair[1]}"
                )
                continue
            version = dependency.get("version")
            if isinstance(version, str) and SEMVER_RE.fullmatch(version):
                target_identity = f"{target_pair[0]}/{target_pair[1]}@{version}"
                if target_identity not in manifests:
                    errors.append(
                        f"{identity}: {location} exact version does not resolve: {target_identity}"
                    )
                    continue
            dependency_graph[source_pair].add(target_pair)

    state: dict[tuple[Any, Any], int] = {}
    stack: list[tuple[Any, Any]] = []
    reported_cycles: set[tuple[tuple[Any, Any], ...]] = set()

    def visit(node: tuple[Any, Any]) -> None:
        state[node] = 1
        stack.append(node)
        for target in sorted(dependency_graph.get(node, set()), key=lambda item: str(item)):
            if state.get(target, 0) == 0:
                visit(target)
            elif state.get(target) == 1:
                start = stack.index(target)
                cycle = stack[start:] + [target]
                core = cycle[:-1]
                rotations = [tuple(core[index:] + core[:index]) for index in range(len(core))]
                canonical = min(rotations, key=lambda item: str(item))
                if canonical not in reported_cycles:
                    reported_cycles.add(canonical)
                    rendered = " -> ".join(f"{kind}/{slug}" for kind, slug in cycle)
                    errors.append(f"managed resource dependency cycle detected: {rendered}")
        stack.pop()
        state[node] = 2

    for node in sorted(dependency_graph, key=lambda item: str(item)):
        if state.get(node, 0) == 0:
            visit(node)
    return errors


def check_resource_contracts(root: Path, context: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    schema_path = root / "schemas/resource.schema.json"
    try:
        schema = _load_json(schema_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot parse {schema_path}: {exc}"]
    validator = JsonSchemaValidator(schema)
    defs = schema.get("$defs", {})
    if set(defs.get("lifecycleStatus", {}).get("enum", [])) != LIFECYCLE_STATES:
        errors.append("resource schema must enumerate exactly all seven lifecycle states")
    if set(defs.get("runtime", {}).get("enum", [])) != RUNTIMES:
        errors.append("resource schema must enumerate exactly all five supported runtimes")
    if set(schema.get("properties", {}).get("kind", {}).get("enum", [])) != RESOURCE_KINDS:
        errors.append("resource schema must enumerate exactly all six resource kinds")
    if set(schema.get("required", [])) != COMMON_RESOURCE_FIELDS:
        missing = sorted(COMMON_RESOURCE_FIELDS - set(schema.get("required", [])))
        extra = sorted(set(schema.get("required", [])) - COMMON_RESOURCE_FIELDS)
        errors.append(f"resource schema common required fields differ; missing={missing}, extra={extra}")

    manifests: dict[str, dict[str, Any]] = {}
    kinds_seen: set[str] = set()
    for manifest_path in sorted((root / "resources").glob("*/*/resource.yaml")):
        relative = manifest_path.relative_to(root).as_posix()
        try:
            manifest = _load_json(manifest_path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{relative}: not JSON-compatible YAML: {exc}")
            continue
        if not isinstance(manifest, dict):
            errors.append(f"{relative}: manifest must be an object")
            continue
        errors.extend(f"{relative}: {error}" for error in validator.errors(manifest))
        kind = manifest.get("kind")
        slug = manifest.get("slug")
        version = manifest.get("version")
        if kind not in RESOURCE_KINDS:
            continue
        kinds_seen.add(kind)
        expected_directory = f"resources/{kind}s" if kind != "mcp" else "resources/mcp"
        if manifest_path.parents[1].relative_to(root).as_posix() != expected_directory:
            errors.append(f"{relative}: kind {kind!r} is stored under the wrong resource directory")
        if slug != manifest_path.parent.name:
            errors.append(f"{relative}: slug must match containing directory name")
        if kind not in manifest or not isinstance(manifest.get(kind), dict):
            errors.append(f"{relative}: missing required type-specific object {kind!r}")
        for other_kind in RESOURCE_KINDS - {kind}:
            if other_kind in manifest:
                errors.append(f"{relative}: contains unrelated type-specific object {other_kind!r}")
        if not isinstance(version, str) or SEMVER_RE.fullmatch(version) is None:
            errors.append(f"{relative}: version must be semantic version text")
        for index, dependency in enumerate(manifest.get("dependencies", [])):
            if not isinstance(dependency, dict) or not isinstance(dependency.get("version"), str):
                errors.append(f"{relative}: dependencies[{index}].version must be a string")
        entrypoint = manifest.get("entrypoint")
        if isinstance(entrypoint, str):
            resolved = (manifest_path.parent / entrypoint).resolve()
            resource_root = manifest_path.parent.resolve()
            try:
                resolved.relative_to(resource_root)
            except ValueError:
                errors.append(f"{relative}: entrypoint escapes its resource directory")
            else:
                if not resolved.is_file():
                    errors.append(f"{relative}: entrypoint does not resolve to a file: {entrypoint}")
        identity = _resource_identity(manifest)
        if identity:
            if identity in manifests:
                errors.append(f"{relative}: duplicate resource identity {identity}")
            manifests[identity] = manifest

    missing_kinds = sorted(RESOURCE_KINDS - kinds_seen)
    if missing_kinds:
        errors.append(f"missing valid demo manifests for resource kinds: {', '.join(missing_kinds)}")

    errors.extend(_resource_cross_field_errors(manifests))
    context["manifests"] = manifests
    context["resource_schema"] = schema
    return errors


def check_evaluation_datasets(root: Path, context: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    schema_path = root / "evals/schemas/evaluation-records.schema.json"
    try:
        schema = _load_json(schema_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot parse {schema_path}: {exc}"]
    validator = JsonSchemaValidator(schema)
    defs = schema.get("$defs", {})
    if set(defs.get("lifecycleStatus", {}).get("enum", [])) != LIFECYCLE_STATES:
        errors.append("evaluation schema lifecycle enumeration differs from the seven-state contract")
    if set(defs.get("runtime", {}).get("enum", [])) != RUNTIMES:
        errors.append("evaluation schema runtime enumeration differs from the five-runtime contract")

    records_by_type: dict[str, list[dict[str, Any]]] = {}
    ids_by_type: dict[str, set[str]] = {}
    for relative, (record_type, definition_name, id_field) in JSONL_CONTRACTS.items():
        path = root / relative
        if not path.is_file():
            errors.append(f"missing evaluation dataset: {relative}")
            continue
        records, load_errors = _load_jsonl(path)
        errors.extend(load_errors)
        if not records:
            errors.append(f"{relative}: must contain at least one example record")
        seen: set[str] = set()
        for record in records:
            line = record.get("__line__", "?")
            value = _without_line(record)
            if value.get("record_type") != record_type:
                errors.append(f"{relative}:{line}: expected record_type {record_type!r}")
            definition = defs.get(definition_name)
            if definition is None:
                errors.append(f"evaluation schema is missing $defs/{definition_name}")
                continue
            errors.extend(f"{relative}:{line}: {error}" for error in validator.errors(value, definition))
            record_id = value.get(id_field)
            if isinstance(record_id, str):
                if record_id in seen:
                    errors.append(f"{relative}:{line}: duplicate {id_field} {record_id!r}")
                seen.add(record_id)
        records_by_type[record_type] = records
        ids_by_type[record_type] = seen

    manifests: dict[str, dict[str, Any]] = context.get("manifests", {})
    for record_type, records in records_by_type.items():
        for record in records:
            line = record.get("__line__", "?")
            for candidate in _walk_dicts(_without_line(record)):
                if {"resource_id", "kind", "slug", "version"} <= set(candidate):
                    expected = _resource_identity(candidate)
                    if expected != candidate.get("resource_id"):
                        errors.append(f"{record_type}:{line}: inconsistent resource reference {candidate.get('resource_id')!r}")
                    if expected not in manifests:
                        errors.append(f"{record_type}:{line}: unknown resource reference {expected!r}")

    _check_foreign_keys(
        errors,
        records_by_type.get("retrieval_qrel", []),
        "query_id",
        ids_by_type.get("retrieval_query", set()),
        "retrieval query",
    )
    _check_foreign_keys(
        errors,
        records_by_type.get("duplicate_label", []),
        "pair_id",
        ids_by_type.get("duplicate_pair", set()),
        "duplicate pair",
    )
    _check_foreign_keys(
        errors,
        records_by_type.get("recommendation_human_label", []),
        "request_id",
        ids_by_type.get("recommendation_request", set()),
        "recommendation request",
    )
    _check_foreign_keys(
        errors,
        records_by_type.get("workflow_expected_output", []),
        "case_id",
        ids_by_type.get("workflow_case", set()),
        "workflow case",
    )

    parent_child_pairs = (
        ("retrieval_query", "query_id", "retrieval_qrel"),
        ("duplicate_pair", "pair_id", "duplicate_label"),
        ("recommendation_request", "request_id", "recommendation_human_label"),
        ("workflow_case", "case_id", "workflow_expected_output"),
    )
    for parent_type, key, child_type in parent_child_pairs:
        child_ids = {item.get(key) for item in records_by_type.get(child_type, [])}
        for parent_id in ids_by_type.get(parent_type, set()):
            if parent_id not in child_ids:
                errors.append(f"{parent_type} {parent_id!r} has no {child_type} record")

    cases = {item.get("case_id"): item for item in records_by_type.get("workflow_case", [])}
    for expected in records_by_type.get("workflow_expected_output", []):
        case = cases.get(expected.get("case_id"))
        if not case:
            continue
        workflow_ref = case.get("workflow", {})
        workflow_manifest = manifests.get(_resource_identity(workflow_ref) or "", {})
        node_ids = {
            item.get("id")
            for item in workflow_manifest.get("workflow", {}).get("nodes", [])
            if isinstance(item, dict)
        }
        if expected.get("node_id") and expected.get("node_id") not in node_ids:
            errors.append(
                f"workflow expectation {expected.get('expectation_id')!r} references unknown node {expected.get('node_id')!r}"
            )
        assertion = expected.get("assertion", {})
        assertion_expected = assertion.get("expected") if isinstance(assertion, dict) else None
        if isinstance(assertion_expected, dict) and isinstance(assertion_expected.get("reason"), str):
            reason = assertion_expected["reason"]
            preconditions = case.get("preconditions", [])
            if not any(reason in item for item in preconditions if isinstance(item, str)):
                errors.append(
                    f"workflow case {case.get('case_id')!r} does not specify asserted approval reason {reason!r}"
                )
    context["evaluation_records"] = records_by_type
    return errors


def _check_foreign_keys(
    errors: list[str],
    records: Iterable[dict[str, Any]],
    field: str,
    targets: set[str],
    target_name: str,
) -> None:
    for record in records:
        if record.get(field) not in targets:
            errors.append(
                f"{record.get('record_type')}:{record.get('__line__', '?')}: {field} {record.get(field)!r} does not reference a {target_name}"
            )


def check_plaintext_secrets(root: Path) -> list[str]:
    errors: list[str] = []
    allowed_suffixes = {".json", ".yaml", ".yml", ".md", ".py", ".sql", ".env", ".example", ".txt"}
    # The scanner's own regression tests contain deliberate fake credentials as
    # fixtures; scanning them would always report the fixtures themselves.
    excluded_names = {"test_stage0_validator.py"}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path.name in excluded_names:
            continue
        if path.suffix.lower() not in allowed_suffixes and path.name != ".env.example":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(root).as_posix()
        for label, pattern in HIGH_CONFIDENCE_SECRET_PATTERNS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                errors.append(f"{relative}:{line}: likely plaintext {label}")
        for line_number, line_text in enumerate(text.splitlines(), 1):
            match = SECRET_ASSIGNMENT_RE.search(line_text)
            if not match:
                continue
            value = (match.group("quoted") or match.group("bare") or "").strip()
            if match.group("quote") is None and path.suffix.lower() in {".py", ".sql"}:
                continue
            if _is_placeholder(value):
                continue
            if _is_development_fixture_secret(match.group(1), value):
                continue
            # ``${VAR:-default}`` is an environment interpolation with a local
            # development fallback, not a literal credential baked into a file.
            if value.startswith("${") and value.endswith("}"):
                continue
            # The bare-value group stops at ``}``, so ``${VAR:-default}`` arrives
            # as ``${VAR:-default`` (no closing brace). Treat that as an
            # interpolation too, rather than as a literal credential.
            if value.startswith("${"):
                continue
            # Shell command substitution (``TOKEN=$(...)`` / backticks) is
            # resolved at run time; it is not a credential stored in the file.
            if value.startswith("$(") or value.startswith("`"):
                continue
            # Reference to another variable (``TOKEN=$OTHER``) is also not a
            # literal value.
            if re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]*", value):
                continue
            # A bare UPPER_SNAKE identifier is a constant/enum name being
            # compared or referenced (e.g. ``INVALID_TOKEN = "INVALID_TOKEN"``),
            # not a credential value.
            if _is_symbolic_constant(value):
                continue
            if value.lower() in {"true", "false", "0", "1"}:
                continue
            if len(value) >= 8:
                errors.append(
                    f"{relative}:{line_number}: likely plaintext secret assigned to {match.group(1)!r}"
                )
    return sorted(set(errors))


def check_database_contract(root: Path) -> list[str]:
    errors: list[str] = []
    document_path = root / "docs/DATABASE_MODEL.md"
    migration_path = root / "infra/migrations/0001_stage0_contract.sql"
    try:
        document = document_path.read_text(encoding="utf-8")
        migration = migration_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"cannot read database contract: {exc}"]
    documented_tables = {
        match.group(1).lower()
        for match in re.finditer(r"(?m)^#{1,6}\s+[^\n`]*`([a-z][a-z0-9_]*)`", document)
    }
    migration_tables = {
        match.group(1).lower()
        for match in re.finditer(r"(?i)\bCREATE\s+TABLE\s+`?([a-z][a-z0-9_]*)`?", migration)
    }
    for table in sorted(CORE_TABLES):
        if table not in documented_tables:
            errors.append(f"database model is missing a dedicated section for table {table}")
        if table not in migration_tables:
            errors.append(f"migration is missing core table {table}")
    named_indexes = {
        match.group(1)
        for match in re.finditer(r"(?i)\b(?:UNIQUE\s+)?KEY\s+`?(idx_[a-z0-9_]+)`?\s*\(", migration)
    }
    if not named_indexes:
        errors.append("migration does not declare named secondary indexes")
    for index_name in sorted(named_indexes):
        if re.search(rf"\b{re.escape(index_name)}\b", document, re.IGNORECASE) is None:
            errors.append(f"database model does not document migration index {index_name}")
    return errors


def _resolve_local_schema(schemas: dict[str, Any], schema: Any) -> Any:
    if not isinstance(schema, dict) or set(schema) != {"$ref"}:
        return schema
    prefix = "#/components/schemas/"
    ref = schema.get("$ref")
    return schemas.get(ref[len(prefix):], schema) if isinstance(ref, str) and ref.startswith(prefix) else schema


def _closed_allof_composition_errors(schemas: dict[str, Any]) -> list[str]:
    """Reject sibling allOf fields hidden by an additionalProperties:false branch."""
    errors: list[str] = []
    for schema_name, schema in schemas.items():
        if not isinstance(schema, dict) or not isinstance(schema.get("allOf"), list):
            continue
        branches = [_resolve_local_schema(schemas, branch) for branch in schema["allOf"]]
        declared_sets = [
            set(branch.get("properties", {})) if isinstance(branch, dict) else set()
            for branch in branches
        ]
        for index, branch in enumerate(branches):
            if not isinstance(branch, dict) or branch.get("additionalProperties") is not False:
                continue
            sibling_fields = set().union(*(fields for position, fields in enumerate(declared_sets) if position != index))
            rejected = sorted(sibling_fields - declared_sets[index])
            if rejected:
                errors.append(
                    f"OpenAPI schema {schema_name} is unsatisfiable: allOf branch with additionalProperties:false rejects sibling fields {', '.join(rejected)}"
                )
    return errors


def _doc_path(root: Path, name: str) -> Path:
    """Resolve a document that may live either in docs/ or in docs/archive/.

    Process records (traceability, implementation notes, review reports) are
    archived once their stage closes, but the checks that reference them stay
    live. Resolving both locations keeps the historical record tidy without
    deleting the evidence a check depends on.
    """

    active = root / "docs" / name
    if active.is_file():
        return active
    return root / "docs" / "archive" / name


def check_review_blocker_regressions(root: Path) -> list[str]:
    errors: list[str] = []
    paths = {
        "openapi": root / "apps/api/openapi.stage0.yaml",
        "api_document": root / "docs/API_CONTRACT.md",
        "migration": root / "infra/migrations/0001_stage0_contract.sql",
        "traceability": _doc_path(root, "STAGE0_SCOPE_AND_TRACEABILITY.md"),
    }
    try:
        spec = _load_json(paths["openapi"])
        api_document = paths["api_document"].read_text(encoding="utf-8")
        migration = paths["migration"].read_text(encoding="utf-8")
        traceability = paths["traceability"].read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read review-regression contract: {exc}"]
    if re.search(r"NEW\.replacement_version_id\s*=\s*OLD\.id", migration, re.IGNORECASE):
        errors.append("resource_version UPDATE self-reference trigger compares with OLD.id instead of NEW.id")
    if re.search(r"NEW\.parent_job_id\s*=\s*OLD\.id", migration, re.IGNORECASE):
        errors.append("job UPDATE self-reference trigger compares with OLD.id instead of NEW.id")
    if len(re.findall(r"NEW\.replacement_version_id\s*=\s*NEW\.id", migration, re.IGNORECASE)) < 2:
        errors.append("resource_version INSERT and UPDATE triggers must both reject self-reference against NEW.id")
    if len(re.findall(r"NEW\.parent_job_id\s*=\s*NEW\.id", migration, re.IGNORECASE)) < 2:
        errors.append("job INSERT and UPDATE triggers must both reject self-reference against NEW.id")
    serialized_spec = json.dumps(spec, sort_keys=True)
    if "replaces_version_id" in serialized_spec or "replaces_version_id" in api_document:
        errors.append("resource-version contracts must use replacement_version_id, never replaces_version_id")
    schemas = spec.get("components", {}).get("schemas", {}) if isinstance(spec, dict) else {}
    if not isinstance(schemas, dict):
        errors.append("OpenAPI components.schemas must be an object")
    else:
        errors.extend(_closed_allof_composition_errors(schemas))
        for name in ("ResourceDetail", "ResourceVersion", "ResolvedDependency"):
            schema = schemas.get(name, {})
            if not isinstance(schema, dict) or schema.get("unevaluatedProperties") is not False:
                errors.append(f"OpenAPI schema {name} must close composed fields with unevaluatedProperties:false")
    if re.search(r"taskrunner_main|TASK_[0-9A-Za-z]+", traceability, re.IGNORECASE):
        errors.append("traceability commands must not hard-code a temporary task-runner path")
    return errors


def check_api_contract(root: Path) -> list[str]:
    errors: list[str] = []
    spec_path = root / "apps/api/openapi.stage0.yaml"
    try:
        spec = _load_json(spec_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"OpenAPI file is not JSON-compatible YAML: {exc}"]
    if not isinstance(spec, dict) or not str(spec.get("openapi", "")).startswith("3.1"):
        errors.append("OpenAPI contract must declare version 3.1.x")
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return errors + ["OpenAPI contract has no paths object"]
    groups = {
        "resources": lambda path: path == "/resources",
        "versions": lambda path: path.startswith("/resource-versions/") or path == "/resources/{resourceId}/versions",
        "search": lambda path: path.startswith("/search/"),
        "agent registration": lambda path: path.startswith("/agent-registrations"),
        "workflows": lambda path: path.startswith("/workflows") or path.startswith("/workflow-runs"),
        "jobs": lambda path: path.startswith("/jobs"),
        "evaluations": lambda path: path.startswith("/evaluations"),
    }
    for name, predicate in groups.items():
        if not any(predicate(path) for path in paths):
            errors.append(f"OpenAPI contract is missing the {name} endpoint group")
    operation_ids: set[str] = set()
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            errors.append(f"OpenAPI path {path!r} must be an object")
            continue
        for method in ("get", "post", "put", "patch", "delete"):
            if method not in path_item:
                continue
            operation = path_item[method]
            if not isinstance(operation, dict):
                errors.append(f"{method.upper()} {path}: operation must be an object")
                continue
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id:
                errors.append(f"{method.upper()} {path}: operationId is required")
            elif operation_id in operation_ids:
                errors.append(f"duplicate operationId {operation_id!r}")
            else:
                operation_ids.add(operation_id)
            if not isinstance(operation.get("responses"), dict) or not operation["responses"]:
                errors.append(f"{method.upper()} {path}: responses are required")
    components = spec.get("components", {})
    schemas = components.get("schemas", {}) if isinstance(components, dict) else {}
    for name in ("Error", "ErrorDetail"):
        if name not in schemas:
            errors.append(f"OpenAPI components.schemas is missing {name}")
    error_schema = schemas.get("Error", {})
    required_error_fields = {
        "type",
        "title",
        "status",
        "code",
        "message",
        "request_id",
        "correlation_id",
        "retryable",
        "details",
    }
    missing_error_fields = required_error_fields - set(error_schema.get("required", [])) if isinstance(error_schema, dict) else required_error_fields
    if missing_error_fields:
        errors.append(f"Error schema is missing required fields: {', '.join(sorted(missing_error_fields))}")
    responses = components.get("responses", {}) if isinstance(components, dict) else {}
    required_responses = {
        "BadRequest",
        "Unauthorized",
        "Forbidden",
        "NotFound",
        "Conflict",
        "ValidationFailed",
        "UnexpectedError",
    }
    missing_responses = required_responses - set(responses)
    if missing_responses:
        errors.append(f"OpenAPI reusable error responses are missing: {', '.join(sorted(missing_responses))}")
    api_document = root / "docs/API_CONTRACT.md"
    try:
        document_text = api_document.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"cannot read API semantic contract: {exc}")
    else:
        for term in ("resource", "version", "search", "agent", "workflow", "job", "evaluation", "error"):
            if re.search(rf"\b{term}s?\b", document_text, re.IGNORECASE) is None:
                errors.append(f"API semantic contract does not discuss {term} semantics")
    return errors


def check_python_files(root: Path) -> list[str]:
    errors: list[str] = []
    python_files = sorted(
        path for path in root.rglob("*.py") if ".git" not in path.parts and "__pycache__" not in path.parts
    )
    if not python_files:
        return ["no Stage 0 Python files were found"]
    python_paths: list[Path] = []
    for path in python_files:
        relative = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            compile(source, str(path), "exec")
            python_paths.append(path)
        except (OSError, SyntaxError, UnicodeError) as exc:
            errors.append(f"{relative}: compile failed: {exc}")
    old_dont_write = sys.dont_write_bytecode
    old_sys_path = sys.path.copy()
    imported_before = set(sys.modules)
    sys.dont_write_bytecode = True
    root_string = str(root)
    if root_string not in sys.path:
        sys.path.insert(0, root_string)
    try:
        for index, path in enumerate(python_paths):
            relative = path.relative_to(root).as_posix()
            module_name = _module_name_for(root, path)
            try:
                if module_name is None:
                    # Not an importable package member (e.g. a standalone
                    # script): loading it by path is the only meaningful check.
                    script_name = f"_supercoder_stage0_script_{index}"
                    spec = importlib.util.spec_from_file_location(script_name, path)
                    if spec is None or spec.loader is None:
                        raise ImportError("cannot create module specification")
                    module = importlib.util.module_from_spec(spec)
                    assert isinstance(module, ModuleType)
                    # Register before executing: stdlib ``dataclasses`` (and other
                    # introspection machinery) resolves the defining module via
                    # ``sys.modules`` while the class body runs. Without this, any
                    # script using @dataclass fails with a misleading
                    # "'NoneType' object has no attribute '__dict__'".
                    sys.modules[script_name] = module
                    try:
                        spec.loader.exec_module(module)
                    finally:
                        sys.modules.pop(script_name, None)
                else:
                    # Import by dotted name so intra-package relative imports
                    # (``from .models import ...``) resolve against the real
                    # parent package instead of failing spuriously.
                    importlib.import_module(module_name)
            except BaseException as exc:  # Import failures include deliberate SystemExit.
                errors.append(f"{relative}: import failed: {type(exc).__name__}: {exc}")
    finally:
        _purge_modules(imported_before, root)
        sys.path[:] = old_sys_path
        sys.dont_write_bytecode = old_dont_write
    return errors


def _module_name_for(root: Path, path: Path) -> str | None:
    """Return the dotted import name for *path*, or None if it is not importable.

    A file is importable when every directory between the repository root and
    the file is a package (contains ``__init__.py``).  A ``__init__.py`` maps to
    its package name rather than a module inside it.
    """

    try:
        parts = path.relative_to(root).with_suffix("").parts
    except ValueError:
        return None
    if path.name == "__init__.py":
        parts = parts[:-1]
    if not parts:
        return None
    current = root
    for part in parts[:-1]:
        current = current / part
        if not (current / "__init__.py").is_file():
            return None
    if current is not root and not (current / "__init__.py").is_file():
        return None
    return ".".join(parts)


def _purge_modules(before: set[str], root: Path) -> None:
    """Drop modules imported from under *root* so the check leaves no residue."""

    prefix = f"{root.name}."
    for name in list(sys.modules):
        if name in before:
            continue
        module = sys.modules.get(name)
        origin = getattr(module, "__file__", None) or ""
        if name == root.name or name.startswith(prefix) or origin.startswith(str(root)):
            sys.modules.pop(name, None)


def check_json_contract_files(root: Path) -> list[str]:
    errors: list[str] = []
    for relative in ("infra/config.schema.json", "apps/api/openapi.stage0.yaml"):
        path = root / relative
        try:
            value = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{relative}: invalid JSON-compatible document: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"{relative}: top-level value must be an object")
    return errors


def check_targeted_validator_tests(root: Path, context: dict[str, Any]) -> list[str]:
    command = [sys.executable, "-m", "unittest", "tests.test_stage0_validator", "-v"]
    display = f'"{Path(sys.executable).resolve()}" -m unittest tests.test_stage0_validator -v'
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        context["test_evidence"] = [(display, 127, str(exc))]
        return [f"cannot execute targeted validator tests: {exc}"]
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    context["test_evidence"] = [
        (display, completed.returncode, output or "(no output)")
    ]
    if completed.returncode != 0:
        return [f"targeted validator tests exited {completed.returncode}"]
    return []


def check_repository_boundary(root: Path, context: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    evidence: list[tuple[str, int, str]] = []

    def run_git(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
        display = "git -C \"{}\" {}".format(root, " ".join(arguments))
        try:
            completed = subprocess.run(
                ["git", "-C", str(root), *arguments],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            evidence.append((display, 127, str(exc)))
            errors.append(f"cannot execute Git: {exc}")
            return None
        output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
        evidence.append((display, completed.returncode, output or "(no output)"))
        return completed

    top_level = run_git(["rev-parse", "--show-toplevel"])
    if top_level is None:
        context["git_evidence"] = evidence
        return errors
    if top_level.returncode != 0:
        errors.append("validation root is not inside a Git worktree; modification provenance cannot be verified")
        context["git_evidence"] = evidence
        context["modified_files"] = None
        return errors
    resolved_top = Path(top_level.stdout.strip()).resolve()
    if resolved_top != root.resolve():
        errors.append(f"validation root is not the Git worktree root: Git reports {resolved_top}")

    status = run_git(["status", "--short"])
    if status is None or status.returncode != 0:
        errors.append("git status --short failed; modified-file inventory is unavailable")
        context["modified_files"] = None
    else:
        context["modified_files"] = [line for line in status.stdout.splitlines() if line.strip()]

    diff_check = run_git(["diff", "--check"])
    if diff_check is None or diff_check.returncode != 0:
        errors.append("git diff --check failed")

    context["git_evidence"] = evidence
    return errors


def run_validation(root: Path | str) -> ValidationReport:
    """Run every Stage 0 check and return a report without writing files."""
    root_path = Path(root).expanduser().resolve()
    report = ValidationReport(root_path)
    if not root_path.is_dir():
        report.add("root", [f"validation root is not a directory: {root_path}"])
        return report
    context: dict[str, Any] = {}
    checks = (
        ("repository boundary and Git status", lambda: check_repository_boundary(root_path, context)),
        ("required structure", lambda: check_required_structure(root_path)),
        ("resource manifests and schema", lambda: check_resource_contracts(root_path, context)),
        ("evaluation datasets", lambda: check_evaluation_datasets(root_path, context)),
        ("plaintext secret scan", lambda: check_plaintext_secrets(root_path)),
        ("database model and indexes", lambda: check_database_contract(root_path)),
        ("API endpoint and error contracts", lambda: check_api_contract(root_path)),
        ("review blocker regressions", lambda: check_review_blocker_regressions(root_path)),
        ("JSON-compatible contract files", lambda: check_json_contract_files(root_path)),
        ("Python compile and import", lambda: check_python_files(root_path)),
        ("targeted validator tests", lambda: check_targeted_validator_tests(root_path, context)),
    )
    for name, check in checks:
        try:
            report.add(name, check())
        except Exception as exc:  # Keep independent checks running for a complete report.
            report.add(name, [f"validator internal error: {type(exc).__name__}: {exc}"])
    report.modified_files = context.get("modified_files")
    for evidence_key in ("git_evidence", "test_evidence"):
        for command, exit_status, output in context.get(evidence_key, []):
            report.add_command(command, exit_status, output)
    return report


def validate_stage0(root: Path | str) -> ValidationReport:
    """Compatibility alias for programmatic callers and targeted tests."""
    return run_validation(root)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=default_root,
        help=f"Stage 0 repository root (default: {default_root})",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Optional Markdown report path. Parent directories are created if needed.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root_path = args.root.expanduser().resolve()
    report_path = args.report.expanduser().resolve() if args.report is not None else None
    if report_path is not None and not report_path.exists():
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                "# SuperCoder Stage 0 Acceptance Report\n\nPending final validation.\n",
                encoding="utf-8",
                newline="\n",
            )
        except OSError as exc:
            print(f"ERROR: could not initialize report {report_path}: {exc}", file=sys.stderr)
            return 2

    report = run_validation(root_path)
    normalized_command = f'"{Path(sys.executable).resolve()}" "{Path(__file__).resolve()}" --root "{root_path}"'
    if report_path is not None:
        normalized_command += f' --report "{report_path}"'
    predicted_exit = 0 if report.passed else 1
    observed_console = report.console_text()
    report.add_command(normalized_command, predicted_exit, observed_console)
    print(observed_console)

    if report_path is not None:
        try:
            report_path.write_text(report.markdown_text(), encoding="utf-8", newline="\n")
        except OSError as exc:
            print(f"ERROR: could not write report {report_path}: {exc}", file=sys.stderr)
            return 2
        print(f"REPORT: {report_path}")
    return predicted_exit


if __name__ == "__main__":
    raise SystemExit(main())
