"""Parse, schema-validate, and cross-check resource manifests.

The manifest provider parses only the frozen JSON-compatible YAML/JSON subset,
validates against the Draft 2020-12 resource Schema, loads the entrypoint and
every managed artifact within the resource root, and performs the frozen
cross-field checks (directory/slug/kind agreement, entrypoint presence,
interface-name uniqueness, agent-tool dependency binding, workflow reference
integrity, dependency validity, and a conservative credential scan).

Provider output is evidence consumed by application/domain code; it never
decides authorization, lifecycle, approval, or publication.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import jsonschema

from ..domain.dependencies import validate_dependency
from ..domain.hashing import (
    calculate_resource_hashes,
    normalize_json_value,
    parse_json_compatible_document,
)
from ..domain.models import (
    ArtifactHash,
    ManifestSnapshotHash,
    ResourceKind,
    VersionIdentifier,
)
from ..domain.permissions import normalize_permission, PermissionDeclaration
from ..domain.validation import validate_slug
from .git_repository import GitProvider, KIND_DIRECTORIES

__all__ = ["LoadedResource", "ManifestLoader", "ManifestLoadError", "scan_for_secrets"]


class ManifestLoadError(ValueError):
    """Structured manifest load failure carrying every diagnostic."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = tuple(errors)


@dataclass(frozen=True, slots=True)
class LoadedResource:
    manifest: dict[str, object]
    kind: ResourceKind
    slug: str
    version: VersionIdentifier
    entrypoint: str
    files: dict[str, str]
    artifact_hash: ArtifactHash
    manifest_snapshot_hash: ManifestSnapshotHash
    tags: tuple[str, ...]
    categories: tuple[str, ...]
    runtimes: tuple[str, ...]
    permissions: tuple[PermissionDeclaration, ...]
    dependencies: tuple[dict[str, object], ...]


_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{36,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[bap]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|password|passwd|secret|token)\s*[:=]\s*[\"']?[A-Za-z0-9+/=_-]{12,}"),
)


def scan_for_secrets(text: str) -> list[str]:
    """Return human-safe descriptions of any detected credential patterns."""

    findings: list[str] = []
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            findings.append(f"credential pattern matched: {pattern.pattern[:40]}...")
    return findings


@lru_cache(maxsize=1)
def _load_schema(schema_path: str) -> dict[str, object]:
    raw = Path(schema_path).read_text(encoding="utf-8")
    return json.loads(raw)


class ManifestLoader:
    """Loads and validates one resource directory against the frozen Schema."""

    def __init__(self, git: GitProvider) -> None:
        self._git = git

    @property
    def schema_path(self) -> Path:
        return self._git.repository_root / "schemas" / "resource.schema.json"

    def load(self, kind: ResourceKind, slug: str) -> LoadedResource:
        errors: list[str] = []
        files = self._git.read_resource_files(kind, slug)
        if "resource.yaml" not in files:
            raise ManifestLoadError(["resource.yaml is missing from the resource directory"])

        manifest = self._parse_manifest(files["resource.yaml"], errors)
        schema = _load_schema(str(self.schema_path))
        self._validate_schema(manifest, schema, errors)

        # Cross-field checks.
        manifest_kind = manifest.get("kind")
        if manifest_kind != kind.value:
            errors.append(f"manifest kind {manifest_kind!r} does not match directory kind {kind.value!r}")
        manifest_slug = manifest.get("slug")
        if manifest_slug != slug:
            errors.append(f"manifest slug {manifest_slug!r} does not match directory name {slug!r}")

        entrypoint = manifest.get("entrypoint")
        if isinstance(entrypoint, str) and entrypoint not in files:
            errors.append(f"entrypoint {entrypoint!r} is not present in the resource directory")

        self._cross_check(manifest, files, errors)

        secrets: list[str] = []
        secrets.extend(scan_for_secrets(files["resource.yaml"]))
        for path, content in files.items():
            if path == "resource.yaml":
                continue
            secrets.extend(scan_for_secrets(content))
        if secrets:
            errors.append(f"credential scan failed: {', '.join(secrets)}")

        if errors:
            raise ManifestLoadError(errors)

        version = VersionIdentifier.parse(str(manifest["version"]))
        artifact_files = {path: content for path, content in files.items() if path != "resource.yaml"}
        hashes = calculate_resource_hashes(manifest, artifact_files)

        permissions = tuple(
            normalize_permission(value) for value in manifest.get("permissions", [])
        )
        return LoadedResource(
            manifest=manifest,
            kind=kind,
            slug=slug,
            version=version,
            entrypoint=str(entrypoint),
            files=artifact_files,
            artifact_hash=hashes.artifact_hash,
            manifest_snapshot_hash=hashes.manifest_snapshot_hash,
            tags=tuple(manifest.get("tags", [])),
            categories=tuple(manifest.get("categories", [])),
            runtimes=tuple(manifest.get("runtimes", [])),
            permissions=permissions,
            dependencies=tuple(manifest.get("dependencies", [])),
        )

    def _parse_manifest(self, content: str, errors: list[str]) -> dict[str, object]:
        try:
            parsed = parse_json_compatible_document(content)
        except ValueError as error:
            errors.append(f"manifest is not valid JSON-compatible YAML/JSON: {error}")
            return {}
        if not isinstance(parsed, dict):
            errors.append("manifest top level must be an object")
            return {}
        return parsed

    def _validate_schema(
        self, manifest: dict[str, object], schema: dict[str, object], errors: list[str]
    ) -> None:
        validator = jsonschema.Draft202012Validator(schema)
        for error in validator.iter_errors(manifest):
            path = "/".join(str(part) for part in error.absolute_path) or "<root>"
            errors.append(f"{path}: {error.message}")

    def _cross_check(
        self, manifest: dict[str, object], files: dict[str, str], errors: list[str]
    ) -> None:
        self._check_interface_names(manifest, errors)
        self._check_dependencies(manifest, errors)
        self._check_agent_tools(manifest, errors)
        self._check_workflow_refs(manifest, errors)

    def _check_interface_names(self, manifest: dict[str, object], errors: list[str]) -> None:
        for section in ("inputs", "outputs"):
            names = [str(item.get("name")) for item in manifest.get(section, []) if isinstance(item, dict)]
            duplicates = {name for name in names if names.count(name) > 1}
            if duplicates:
                errors.append(f"{section} interface names are not unique: {sorted(duplicates)}")
        prompt = manifest.get("prompt")
        if isinstance(prompt, dict):
            names = [str(item.get("name")) for item in prompt.get("variables", []) if isinstance(item, dict)]
            duplicates = {name for name in names if names.count(name) > 1}
            if duplicates:
                errors.append(f"prompt variable names are not unique: {sorted(duplicates)}")

    def _check_dependencies(self, manifest: dict[str, object], errors: list[str]) -> None:
        seen: set[tuple[str, str]] = set()
        kind = str(manifest.get("kind", ""))
        slug = str(manifest.get("slug", ""))
        for item in manifest.get("dependencies", []):
            if not isinstance(item, dict):
                continue
            dep_kind = str(item.get("kind", ""))
            dep_slug = str(item.get("slug", ""))
            if (dep_kind, dep_slug) in seen:
                errors.append(f"duplicate dependency: {dep_kind}/{dep_slug}")
            seen.add((dep_kind, dep_slug))
            try:
                validate_dependency(
                    dep_kind,
                    dep_slug,
                    str(item.get("version", "")),
                    source=item.get("source"),
                    is_optional=bool(item.get("optional", False)),
                    parent_kind=kind,
                    parent_slug=slug,
                )
            except ValueError as error:
                errors.append(f"dependency {dep_kind}/{dep_slug} is invalid: {error}")

    def _check_agent_tools(self, manifest: dict[str, object], errors: list[str]) -> None:
        if manifest.get("kind") != "agent":
            return
        agent = manifest.get("agent")
        if not isinstance(agent, dict):
            return
        tools = set(str(tool) for tool in agent.get("tools", []))
        dependency_slugs = {
            str(item.get("slug"))
            for item in manifest.get("dependencies", [])
            if isinstance(item, dict) and str(item.get("kind", "")) in ("mcp", "tool")
        }
        for tool in tools:
            if tool not in dependency_slugs:
                errors.append(
                    f"agent tool {tool!r} has no matching mcp/tool dependency declaration"
                )

    def _check_workflow_refs(self, manifest: dict[str, object], errors: list[str]) -> None:
        if manifest.get("kind") != "workflow":
            return
        workflow = manifest.get("workflow")
        if not isinstance(workflow, dict):
            return
        node_ids = {str(node.get("id")) for node in workflow.get("nodes", []) if isinstance(node, dict)}
        for edge in workflow.get("edges", []):
            if not isinstance(edge, dict):
                continue
            if str(edge.get("from")) not in node_ids:
                errors.append(f"workflow edge references unknown node: {edge.get('from')!r}")
            if str(edge.get("to")) not in node_ids:
                errors.append(f"workflow edge references unknown node: {edge.get('to')!r}")
        approval_nodes = {
            str(node.get("id"))
            for node in workflow.get("nodes", [])
            if isinstance(node, dict) and node.get("type") == "approval"
        }
        for gate in workflow.get("approval_gates", []):
            if str(gate) not in approval_nodes:
                errors.append(f"approval gate {gate!r} does not reference an approval node")
    def load_snapshot(
        self,
        kind: ResourceKind,
        slug: str,
        manifest: dict[str, object],
        files: dict[str, str],
    ) -> LoadedResource:
        """Validate and hash a manifest patch over an existing read-only snapshot."""

        errors: list[str] = []
        normalized = normalize_json_value(manifest)
        if not isinstance(normalized, dict):
            raise ManifestLoadError(["manifest top level must be an object"])
        schema = _load_schema(str(self.schema_path))
        self._validate_schema(normalized, schema, errors)
        if normalized.get("kind") != kind.value:
            errors.append(f"manifest kind {normalized.get('kind')!r} does not match directory kind {kind.value!r}")
        if normalized.get("slug") != slug:
            errors.append(f"manifest slug {normalized.get('slug')!r} does not match directory name {slug!r}")
        entrypoint = normalized.get("entrypoint")
        if isinstance(entrypoint, str) and entrypoint not in files:
            errors.append(f"entrypoint {entrypoint!r} is not present in the resource directory")
        self._cross_check(normalized, files, errors)
        secrets = scan_for_secrets(json.dumps(normalized, ensure_ascii=False))
        for path, content in files.items():
            secrets.extend(scan_for_secrets(content))
        if secrets:
            errors.append(f"credential scan failed: {', '.join(secrets)}")
        if errors:
            raise ManifestLoadError(errors)

        version = VersionIdentifier.parse(str(normalized["version"]))
        hashes = calculate_resource_hashes(normalized, files)
        permissions = tuple(normalize_permission(value) for value in normalized.get("permissions", []))
        return LoadedResource(
            manifest=normalized,
            kind=kind,
            slug=slug,
            version=version,
            entrypoint=str(entrypoint),
            files=dict(files),
            artifact_hash=hashes.artifact_hash,
            manifest_snapshot_hash=hashes.manifest_snapshot_hash,
            tags=tuple(normalized.get("tags", [])),
            categories=tuple(normalized.get("categories", [])),
            runtimes=tuple(normalized.get("runtimes", [])),
            permissions=permissions,
            dependencies=tuple(normalized.get("dependencies", [])),
        )


def build_manifest_loader(repository_root: str | Path, *, read_only: bool = True) -> ManifestLoader:
    """Compose a Git provider and manifest loader for one managed root."""

    return ManifestLoader(GitProvider(repository_root, read_only=read_only))
