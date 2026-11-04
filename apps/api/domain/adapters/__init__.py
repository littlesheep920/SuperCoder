"""Runtime environment adapters.

The platform keeps capabilities as governed resources; an adapter turns one into the files
a specific environment consumes. The split matters: governance stays in one place, and the
environments are interchangeable views over the same facts.

Layering: adapters depend on the domain and nothing else — no repository, no database, no
clock. That is what makes them deterministic enough to assert against golden files.
"""

from __future__ import annotations

from .agent_skills import AgentSkillsTarget, SKILL_NAME_PATTERN
from .base import AdaptationTarget, ResourceForExport
from .ir import (
    BundleFile,
    ExportBundle,
    ExportWarning,
    PermissionReport,
    ValidationIssue,
    ValidationResult,
)
from .targets import (
    ChatGPTTarget,
    ClaudeCodeTarget,
    DshTarget,
    available_targets,
    find_target,
)

__all__ = [
    "AdaptationTarget",
    "AgentSkillsTarget",
    "BundleFile",
    "ChatGPTTarget",
    "ClaudeCodeTarget",
    "DshTarget",
    "ExportBundle",
    "ExportWarning",
    "PermissionReport",
    "ResourceForExport",
    "SKILL_NAME_PATTERN",
    "ValidationIssue",
    "ValidationResult",
    "available_targets",
    "find_target",
]
