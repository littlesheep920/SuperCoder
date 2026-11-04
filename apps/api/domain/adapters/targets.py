"""Concrete adaptation targets.

Every target here reads `SKILL.md`: Claude Code and DSH from a project directory, ChatGPT
from an uploaded zip. They therefore share `AgentSkillsTarget` and override only placement,
plus whatever config file their environment reads.

Evidence for each rule is recorded next to the override that depends on it, because an
adapter is only as trustworthy as the specification behind it:

* Claude Code — https://code.claude.com/docs/en/skills.md
  (skills live in `.claude/skills/<name>/SKILL.md`; project, nested, and personal roots;
  `.mcp.json` carries MCP servers)
* DSH — package `@deepseek-ai/dsh-skill-filesystem`, `roots()`
  (`.dsh/skills` and `.agents/skills`, project then user; MCP in `settings/mcp.json`)
* ChatGPT — https://help.openai.com/articles/20001066 (skills upload / download),
  https://help.openai.com/articles/20001256 (plugins may be skills only, zip upload),
  https://developers.openai.com/cookbook/examples/skills_in_api (SKILL.md + folder, zip)
"""

from __future__ import annotations

from .base import ResourceForExport
from .ir import BundleFile, ExportBundle, ExportWarning, PermissionReport
from .agent_skills import AgentSkillsTarget

__all__ = ["ChatGPTTarget", "ClaudeCodeTarget", "DshTarget", "available_targets", "find_target"]


class ClaudeCodeTarget(AgentSkillsTarget):
    """Claude Code: project skills under `.claude/skills`, MCP servers in `.mcp.json`.

    Also supports a personal root (`~/.claude/skills`) and nested roots per subdirectory.
    The project root is the default here because an exported resource is committed with
    the repository it applies to.
    """

    _identifier = "claude-code"
    _display_name = "Claude Code"
    _skills_directory = ".claude/skills"
    #: Claude Code launches stdio servers and connects to HTTP/SSE endpoints.
    _mcp_transports = ("stdio", "http", "sse")

    def mcp_config_path(self) -> str | None:
        return ".mcp.json"

    def describe_permissions(self, resource: ResourceForExport) -> PermissionReport:
        """Claude Code prompts per tool unless a rule pre-approves it.

        A skill can carry `allowed-tools`, but that frontmatter field is not emitted here:
        it would pre-approve tools on the user's behalf without their consent, which is a
        security decision the platform has no mandate to make on an import path.
        """

        declared = tuple(
            str(entry.get("scope") or entry.get("name") or entry)
            for entry in (resource.manifest.get("permissions") or [])
        )
        return PermissionReport(
            declared=declared,
            requires_approval=declared,
            unsupported=(),
        )


class DshTarget(AgentSkillsTarget):
    """DeepSeek Harness: `.dsh/skills` first, `.agents/skills` as the shared location.

    `roots()` in `dsh-skill-filesystem` lists `.dsh/skills` before `.agents/skills`, so the
    DSH-specific directory wins when both exist. Writing to `.dsh/skills` is the narrower
    choice: it does not claim a shared location another tool may also read.
    """

    _identifier = "dsh"
    _display_name = "DeepSeek Harness"
    _skills_directory = ".dsh/skills"
    #: DSH's filesystem provider reads YAML frontmatter; MCP support is configured by the
    #: host, and no file layout is documented, so MCP resources are reported, not written.
    _mcp_transports = ()

    def mcp_config_path(self) -> str | None:
        return None


class ChatGPTTarget(AgentSkillsTarget):
    """ChatGPT: the same `SKILL.md` package, delivered as an uploadable zip.

    ChatGPT has no project directory to write into — a skill is uploaded (or created in the
    web editor) and stored by the account. The bundle is therefore placed under `skills/`
    so the archive has one unambiguous root, and `package.json` records what the upload is,
    following the plugin packaging used for skill-only plugins.

    Evidence: "Upload a skill: Go to Skills, select Create, then select Upload from your
    computer" (help.openai.com/articles/20001066), and skill packaging from a folder or zip
    containing `SKILL.md` plus supporting files.
    """

    _identifier = "chatgpt"
    _display_name = "ChatGPT"
    _skills_directory = "skills"
    #: MCP reaches ChatGPT through connected apps, not a file in the package.
    _mcp_transports = ()

    def mcp_config_path(self) -> str | None:
        return None

    def export(self, resource: ResourceForExport):  # type: ignore[override]
        bundle = super().export(resource)
        files = dict(bundle.files)

        # ChatGPT receives a package rather than a directory tree, so the archive needs a
        # manifest of its own. It also makes the upload's provenance inspectable without
        # unzipping: a reviewer can see which governed version produced it.
        package = (
            "{\n"
            f'  "name": {_json_string(resource.slug)},\n'
            f'  "description": {_json_string(resource.description)},\n'
            f'  "supercoder": {{\n'
            f'    "resource_id": {_json_string(resource.resource_id)},\n'
            f'    "version_id": {_json_string(resource.version_id)},\n'
            f'    "semver": {_json_string(resource.version)}\n'
            "  }\n"
            "}\n"
        )
        files["package.json"] = BundleFile(
            path="package.json", text=package, media_type="application/json"
        )

        warnings = list(bundle.warnings)
        warnings.append(
            ExportWarning(
                code="upload_is_manual",
                source="chatgpt distribution",
                effect=(
                    "The package must be uploaded through ChatGPT's Skills page; there is no "
                    "public API for installing a skill into an account. Availability depends "
                    "on workspace plan and administrator settings."
                ),
                mitigation="Upload via Skills > Create > Upload from your computer.",
            )
        )

        from .ir import ExportBundle

        return ExportBundle(
            target=bundle.target,
            resource_id=bundle.resource_id,
            version_id=bundle.version_id,
            resource_version=bundle.resource_version,
            files=files,
            warnings=tuple(warnings),
            permissions=bundle.permissions,
        )


def _json_string(value: str) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


#: Registry order is the order the console lists them in: the two environments that read a
#: project directory first, then the one that takes an upload.
_TARGETS: tuple[AgentSkillsTarget, ...] = (
    ClaudeCodeTarget(),
    DshTarget(),
    ChatGPTTarget(),
)


def available_targets() -> tuple[AgentSkillsTarget, ...]:
    return _TARGETS


def find_target(identifier: str) -> AgentSkillsTarget | None:
    for target in _TARGETS:
        if target.identifier == identifier:
            return target
    return None
