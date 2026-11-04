"""The Agent Skills open standard, as the shared base for every skills-based target.

Claude Code and DSH both follow https://agentskills.io/specification: a directory holding
`SKILL.md` with YAML frontmatter, plus optional supporting files. They differ in *where*
that directory lives and in which MCP transports they accept — not in the file format.

So the format is implemented once, here, and each concrete target overrides placement.
A target that needed a different file layout would override `render_skill_md` instead of
re-implementing the whole export, keeping the shared rules in one place.

The rules encoded below are not assumptions; they were read from the implementations:

* Claude Code: https://code.claude.com/docs/en/skills.md — "follow the Agent Skills open
  standard", frontmatter fields and the kebab-case name requirement.
* DSH: `dsh-skill-filesystem`, whose `parseSkillFile` drops any skill whose `name` fails
  `^[a-z0-9]+(?:-[a-z0-9]+)*$` or that lacks `name`/`description`.
"""

from __future__ import annotations

import json
import re

from .base import ResourceForExport
from .ir import (
    BundleFile,
    ExportBundle,
    ExportWarning,
    PermissionReport,
    ValidationIssue,
    ValidationResult,
)

__all__ = ["AgentSkillsTarget", "SKILL_NAME_PATTERN", "render_frontmatter"]

#: The grammar both Claude Code and DSH enforce for a skill name.
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: The standard caps a description here; longer text is truncated by the loader or ignored.
MAX_DESCRIPTION = 1024
MAX_NAME = 64


def render_frontmatter(fields: dict[str, object]) -> str:
    """Render YAML frontmatter with strings quoted only when they must be.

    A hand-rolled writer is used instead of a YAML library so the output is byte-stable
    across PyYAML versions. The values here are strings, booleans, and string maps, which
    is all the standard allows.
    """

    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, dict):
            lines.append(f"{key}:")
            for sub_key, sub_value in value.items():
                lines.append(f"  {sub_key}: {_scalar(sub_value)}")
        else:
            lines.append(f"{key}: {_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _scalar(value: object) -> str:
    text = str(value)
    if text and not re.search(r"[:#\n\"'&*!|>%@`{}\[\],]", text) and text == text.strip():
        return text
    return json.dumps(text, ensure_ascii=False)


class AgentSkillsTarget:
    """Format shared by every environment that reads `SKILL.md`."""

    #: Filled by subclasses; the base class is abstract in practice.
    _identifier = "agent-skills"
    _display_name = "Agent Skills"
    _skills_directory = ".agents/skills"
    #: Transports this environment can actually launch.
    _mcp_transports: tuple[str, ...] = ("stdio", "http")

    @property
    def identifier(self) -> str:
        return self._identifier

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def skills_directory(self) -> str:
        return self._skills_directory

    # ---------------------------------------------------------------- export

    def render_skill_md(self, resource: ResourceForExport) -> str:
        """Compose the SKILL.md file: frontmatter plus the resource body.

        `name` comes from the slug because both environments key the skill on it, and the
        slug is already constrained to the same grammar. `description` comes from the
        manifest, which the governance flow guarantees is non-empty.
        """

        fields: dict[str, object] = {
            "name": resource.slug,
            "description": resource.description,
        }
        metadata = self.skill_metadata(resource)
        if metadata:
            fields["metadata"] = metadata
        return render_frontmatter(fields) + "\n" + resource.body.rstrip("\n") + "\n"

    def skill_metadata(self, resource: ResourceForExport) -> dict[str, str]:
        """Extra metadata recorded under the standard's `metadata` map.

        Provenance is recorded so an exported file can be traced back to the exact version
        that produced it. Values must be strings per the standard.
        """

        return {
            "supercoder-resource": resource.resource_id,
            "supercoder-version": resource.version_id,
            "supercoder-semver": resource.version,
        }

    def export(self, resource: ResourceForExport) -> ExportBundle:
        files: dict[str, BundleFile] = {}

        skill_path = f"{self._skills_directory}/{resource.slug}/SKILL.md"
        files[skill_path] = BundleFile(path=skill_path, text=self.render_skill_md(resource))

        warnings = list(self._capability_warnings(resource))
        mcp_files, mcp_warnings = self._render_mcp(resource)
        files.update(mcp_files)
        warnings.extend(mcp_warnings)

        return ExportBundle(
            target=self.identifier,
            resource_id=resource.resource_id,
            version_id=resource.version_id,
            resource_version=resource.version,
            files=files,
            warnings=tuple(warnings),
            permissions=self.describe_permissions(resource),
        )

    def _capability_warnings(self, resource: ResourceForExport) -> list[ExportWarning]:
        """Features that do not survive the trip to a plain skill file."""

        warnings: list[ExportWarning] = []
        manifest = dict(resource.manifest or {})

        # Skills carry instructions, not executable contracts. A workflow expressed as a
        # skill becomes advice the model may or may not follow, which is a real
        # capability loss rather than a formatting detail.
        if resource.kind == "workflow":
            warnings.append(
                ExportWarning(
                    code="workflow_degrades_to_instructions",
                    source="kind=workflow",
                    effect=(
                        "The workflow graph is exported as prose instructions. Step order, "
                        "gating, and retries are not enforced by the target."
                    ),
                    mitigation="Keep the governed definition as the source of orchestration.",
                )
            )

        # Typed inputs/outputs have no representation in a skill file.
        inputs = manifest.get("inputs") or []
        outputs = manifest.get("outputs") or []
        if inputs or outputs:
            warnings.append(
                ExportWarning(
                    code="typed_io_not_carried",
                    source="inputs/outputs",
                    effect=(
                        f"{len(inputs)} input and {len(outputs)} output declarations are "
                        "described in the body but not machine-validated by the target."
                    ),
                    mitigation="Document the contract in the body so a reader can honour it.",
                )
            )

        # Runtime declarations are a platform concept; a skill has no such field.
        runtimes = manifest.get("runtimes") or []
        others = [r for r in runtimes if r != self.identifier]
        if others:
            warnings.append(
                ExportWarning(
                    code="other_runtimes_not_exported",
                    source="runtimes",
                    effect=(
                        "This resource also declares "
                        + ", ".join(sorted(others))
                        + "; only the current target is exported."
                    ),
                    mitigation=f"Export separately for each target: {', '.join(sorted(others))}.",
                )
            )

        # Entrypoints other than SKILL.md cannot be the skill's entry on the standard.
        if resource.entrypoint and resource.entrypoint != "SKILL.md":
            warnings.append(
                ExportWarning(
                    code="entrypoint_renamed",
                    source=f"entrypoint={resource.entrypoint}",
                    effect=(
                        f"The resource's entrypoint {resource.entrypoint} becomes SKILL.md, "
                        "because that filename is what the target loads."
                    ),
                    mitigation=None,
                )
            )

        if len(resource.description) > MAX_DESCRIPTION:
            warnings.append(
                ExportWarning(
                    code="description_truncated",
                    source="description",
                    effect=(
                        f"The description is {len(resource.description)} characters; the "
                        f"standard caps it at {MAX_DESCRIPTION}."
                    ),
                    mitigation="Shorten the description in the manifest.",
                )
            )

        return warnings

    # ---------------------------------------------------------------- mcp

    def mcp_config_path(self) -> str | None:
        """Where this environment reads MCP servers from, or None if it has no file."""

        return None

    def _render_mcp(self, resource: ResourceForExport) -> tuple[dict[str, BundleFile], list[ExportWarning]]:
        """Emit an MCP server definition when the resource is one.

        Returning a warning instead of a file is the honest outcome when the target cannot
        launch the declared transport: an MCP entry the environment silently ignores is
        worse than a stated omission.
        """

        if resource.kind != "mcp":
            return {}, []
        config_path = self.mcp_config_path()
        if config_path is None:
            return {}, [
                ExportWarning(
                    code="mcp_has_no_config_home",
                    source="kind=mcp",
                    effect=(
                        f"{self.display_name} has no file-based MCP configuration, so the "
                        "server definition is not exported."
                    ),
                    mitigation="Register the server through the environment's own UI.",
                )
            ]

        spec = dict(resource.manifest.get("mcp") or resource.manifest.get(resource.kind) or {})
        transport = str(spec.get("transport") or "stdio")
        if transport not in self._mcp_transports:
            return {}, [
                ExportWarning(
                    code="mcp_transport_unsupported",
                    source=f"mcp.transport={transport}",
                    effect=(
                        f"{self.display_name} supports only "
                        f"{', '.join(self._mcp_transports)}; this server uses {transport}."
                    ),
                    mitigation="Run the server over a supported transport, or register it manually.",
                )
            ]

        entry: dict[str, object] = {"transport": transport}
        for key in ("command", "url"):
            if spec.get(key):
                entry[key] = spec[key]
        if spec.get("tools"):
            entry["tools"] = spec["tools"]

        payload = {"mcpServers": {resource.slug: entry}}
        text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        return {config_path: BundleFile(path=config_path, text=text, media_type="application/json")}, []

    # ---------------------------------------------------------------- validate

    def validate(self, bundle: ExportBundle) -> ValidationResult:
        """Apply the static rules both environments enforce.

        Each issue names the file it concerns, so a caller can show it next to the
        artefact rather than as a loose error list.
        """

        issues: list[ValidationIssue] = []
        skill_files = [f for f in bundle.files.values() if f.path.endswith("/SKILL.md")]

        if not skill_files:
            issues.append(
                ValidationIssue(
                    path="(bundle)",
                    message="no SKILL.md was produced; the target would load nothing",
                )
            )

        for entry in skill_files:
            issues.extend(self._validate_skill_file(entry))

        return ValidationResult(issues=tuple(issues))

    def _validate_skill_file(self, entry: BundleFile) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        text = entry.text

        if not text.startswith("---\n"):
            issues.append(
                ValidationIssue(
                    path=entry.path,
                    message="file must begin with a `---` frontmatter fence",
                )
            )
            return issues

        end = text.find("\n---", 4)
        if end < 0:
            issues.append(
                ValidationIssue(path=entry.path, message="frontmatter is not closed with `---`")
            )
            return issues

        block = text[4:end]
        fields = _parse_simple_yaml(block)

        name = fields.get("name")
        if not name:
            issues.append(
                ValidationIssue(
                    path=entry.path,
                    message="frontmatter requires `name`; the target ignores a skill without it",
                )
            )
        elif not SKILL_NAME_PATTERN.match(str(name)):
            issues.append(
                ValidationIssue(
                    path=entry.path,
                    message=(
                        f"name {name!r} must match "
                        "^[a-z0-9]+(?:-[a-z0-9]+)*$; the target drops it otherwise"
                    ),
                )
            )
        elif len(str(name)) > MAX_NAME:
            issues.append(
                ValidationIssue(
                    path=entry.path, message=f"name exceeds {MAX_NAME} characters"
                )
            )

        description = fields.get("description")
        if not description:
            issues.append(
                ValidationIssue(
                    path=entry.path,
                    message="frontmatter requires `description`; the target ignores a skill without it",
                )
            )
        elif len(str(description)) > MAX_DESCRIPTION:
            issues.append(
                ValidationIssue(
                    path=entry.path,
                    message=(
                        f"description is {len(str(description))} characters; the standard "
                        f"caps it at {MAX_DESCRIPTION}"
                    ),
                    fatal=False,
                )
            )

        return issues

    # ---------------------------------------------------------------- permissions

    def describe_permissions(self, resource: ResourceForExport) -> PermissionReport:
        """Report declared permissions and which of them the target cannot enforce."""

        declared = tuple(
            str(entry.get("scope") or entry.get("name") or entry)
            for entry in (resource.manifest.get("permissions") or [])
        )
        return PermissionReport(
            declared=declared,
            requires_approval=declared,
            unsupported=(),
        )


def _parse_simple_yaml(block: str) -> dict[str, object]:
    """Parse the flat frontmatter this module writes.

    Only the shapes `render_frontmatter` emits are supported: `key: value` and one level of
    `key:\n  sub: value`. Parsing more than we write would invite the validator to accept
    frontmatter that no adapter produces.
    """

    fields: dict[str, object] = {}
    current_map: dict[str, str] | None = None
    for raw in block.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("  ") and current_map is not None:
            key, _, value = raw.strip().partition(":")
            current_map[key.strip()] = _unquote(value.strip())
            continue
        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()
        if not value:
            current_map = {}
            fields[key] = current_map
        else:
            current_map = None
            fields[key] = _unquote(value)
    return fields


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
    return value
