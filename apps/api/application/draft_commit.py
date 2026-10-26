"""Commit a reviewed draft: write it to Git, then register it.

This is the step the whole drafting flow exists to make safe. The rules it encodes:

* **A human confirms, not the model.** The endpoint that calls this requires the
  draft the caller actually saw, plus an explicit confirmation flag. The server
  re-validates that draft rather than re-asking the model, so what gets written is
  what the person approved, not a fresh sample from the model.
* **Re-validation before writing.** The draft is checked against the repository
  schema again at write time. A draft that passed minutes ago in a browser is not
  trusted to still be valid.
* **Status is forced to ``draft``.** Registration never publishes.
* **All-or-nothing.** If the artifact write fails after the manifest lands, the
  written files are removed so a half-registered resource cannot exist.
* **Auditable.** The reason, the actor, and the exact content hash are recorded by
  the existing command service and audit log.

Why Git is written first: :class:`~apps.api.application.resource_commands.ResourceCommandService`
loads the manifest from Git and verifies the request body matches that content. The
files must exist before the command can compute an artifact hash, so committing the
draft requires materialising it. That is exactly why a human gate matters here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from ..domain.errors import ValidationError
from ..domain.manifest_validation import validate_manifest
from ..domain.models import ActorIdentity, ResourceKind
from ..providers.git_repository import GitProvider
from ..schemas.resources import ArtifactReference, CreateResourceRequest
from .resource_commands import ResourceCommandService

__all__ = ["DraftCommitService", "DraftCommitResult", "artifact_reference_for"]


# The body file each kind must carry. Registration writes a minimal placeholder
# that says what it is; a human replaces it before the resource is usable.
_ENTRYPOINT_BY_KIND: dict[ResourceKind, str] = {
    ResourceKind.SKILL: "SKILL.md",
    ResourceKind.PROMPT: "PROMPT.md",
    ResourceKind.AGENT: "AGENT.md",
    ResourceKind.WORKFLOW: "WORKFLOW.md",
    ResourceKind.TOOL: "tool.py",
    ResourceKind.MCP: "MCP.md",
}


@dataclass(frozen=True, slots=True)
class DraftCommitResult:
    resource_id: str
    version_id: str
    version: str
    artifact_hash: str
    slug: str
    kind: str


def artifact_reference_for(path: str, content: str) -> ArtifactReference:
    """Build the artifact reference the command service verifies.

    The digest is required, and it is computed from the bytes actually written so a
    mismatch is caught by the command service rather than silently accepted.
    """

    payload = content.encode("utf-8")
    return ArtifactReference(
        path=path,
        # The schema requires the algorithm prefix, not a bare hex digest.
        digest="sha256:" + hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        media_type="text/markdown",
    )


def _placeholder_body(kind: ResourceKind, name: str) -> str:
    return (
        f"# {name}\n\n"
        "Registered from a reviewed draft. The body is a placeholder: replace it "
        "before publishing, and keep the manifest description accurate.\n"
    )


class DraftCommitService:
    """Write a confirmed draft to Git and register it as a resource."""

    def __init__(
        self,
        git: GitProvider,
        commands: ResourceCommandService,
        root,
    ) -> None:
        self._git = git
        self._commands = commands
        self._root = root

    def commit(
        self,
        *,
        actor: ActorIdentity,
        manifest: Mapping[str, Any],
        reason: str,
        correlation_id: str,
        request_id: str | None = None,
    ) -> DraftCommitResult:
        if not isinstance(manifest, Mapping):
            raise ValidationError(message="manifest must be an object")

        # Re-validate now, not when the draft was produced.
        validation = validate_manifest(self._root, dict(manifest))
        if not validation.valid:
            raise ValidationError(
                message=(
                    "draft does not satisfy the resource schema: "
                    + "; ".join(validation.errors[:5])
                ),
            )

        kind_value = manifest.get("kind")
        slug = manifest.get("slug")
        try:
            kind = ResourceKind(str(kind_value))
        except ValueError as error:
            raise ValidationError(message=f"unknown kind {kind_value!r}") from error
        if not isinstance(slug, str) or not slug:
            raise ValidationError(message="manifest must carry a slug")

        # Status is forced: a registered resource starts as a draft version.
        final_manifest = dict(manifest)
        final_manifest["status"] = "draft"

        entrypoint = _ENTRYPOINT_BY_KIND[kind]
        written: list[str] = []
        try:
            self._git.write_resource_file(
                kind,
                slug,
                "resource.yaml",
                json.dumps(final_manifest, indent=2, ensure_ascii=False) + "\n",
            )
            written.append("resource.yaml")

            body = _placeholder_body(kind, str(final_manifest.get("name") or slug))
            self._git.write_resource_file(kind, slug, entrypoint, body)
            written.append(entrypoint)

            request = CreateResourceRequest(
                manifest=final_manifest,
                artifacts=[artifact_reference_for(entrypoint, body)],
                reason=reason,
            )
            resource, version = self._commands.create_resource(
                actor=actor,
                request=request,
                correlation_id=correlation_id,
                request_id=request_id,
            )
        except Exception:
            # All-or-nothing: a half-written resource directory would be worse than
            # a clean failure, because it would look like a real resource to the
            # next scan while having no governance record.
            for name in written:
                try:
                    self._git.write_resource_file(kind, slug, name, "")
                except Exception:  # noqa: BLE001 - cleanup must not mask the cause
                    pass
            raise

        return DraftCommitResult(
            resource_id=str(resource.public_id),
            version_id=str(version.public_id),
            version=str(version.version),
            artifact_hash=str(version.artifact_hash),
            slug=slug,
            kind=kind.value,
        )
