"""The adaptation contract every runtime environment implements.

PRD §5.6 fixes three deterministic functions. This module states them as a protocol so an
adapter cannot half-exist: a class that omits one fails to satisfy the type and, more
usefully, fails the conformance test in `tests/test_adapter_contract.py`.

What an adapter may decide: where files land, how the manifest is laid out for that
environment, and which MCP transports it accepts.

What an adapter may NOT decide: what the resource says, whether a version is publishable,
or whether a warning gets shown. Adapters transform placement and report loss; they do not
gate governance.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .ir import ExportBundle, PermissionReport, ValidationResult

__all__ = ["AdaptationTarget", "ResourceForExport"]


@runtime_checkable
class ResourceForExport(Protocol):
    """The minimum an adapter needs to know about a version.

    Deliberately narrow: adapters read these fields and nothing else, which is what keeps
    a new adapter from reaching into the repository or the manifest in ways the contract
    does not describe.
    """

    resource_id: str
    version_id: str
    version: str
    kind: str
    slug: str
    name: str
    description: str
    entrypoint: str
    body: str
    manifest: dict
    tags: tuple[str, ...]


@runtime_checkable
class AdaptationTarget(Protocol):
    """A runtime environment a resource can be exported to."""

    @property
    def identifier(self) -> str:
        """Stable name used in URLs and file names, for example `claude-code`."""
        ...

    @property
    def display_name(self) -> str:
        """Human-facing name, shown in the console."""
        ...

    @property
    def skills_directory(self) -> str:
        """Where the target reads skills from, relative to a project root."""
        ...

    def export(self, resource: ResourceForExport) -> ExportBundle:
        """Produce the files this environment consumes, plus what was lost."""
        ...

    def validate(self, bundle: ExportBundle) -> ValidationResult:
        """Check a bundle against this environment's static requirements.

        Static only: whether the environment starts, or whether its tools are available,
        depends on the user's machine and is not something the platform can promise.
        """
        ...

    def describe_permissions(self, resource: ResourceForExport) -> PermissionReport:
        """State what the exported artefact needs, including what cannot be expressed."""
        ...
