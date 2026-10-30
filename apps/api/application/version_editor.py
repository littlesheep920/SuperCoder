"""Edit a draft version: write the body to Git, then save a new version row.

Why an edit produces a new version
----------------------------------
`update_draft` does not mutate the row it finds. It appends a **new** version whose
identifier comes from the manifest's ``version`` field, then invalidates any prior
approvals. Existing drafts stay in the record as immutable history.

That design is deliberate — an approved draft's content must never change under an
approval — but it has a consequence the caller must satisfy: the manifest version has
to differ from the one being edited, or the insert violates
``uq_rv_resource_version``. The unique constraint is what surfaced this: a body-only
edit returned a 500 from MySQL rather than a usable error.

So this service requires an explicit ``version`` bump whenever the body changes, and
refuses the request up front with an explanation if it is missing.

Why the write happens first
---------------------------
The command service reloads the manifest from Git and verifies the artifact digests
match the files. Nothing else can produce hashes that agree with a file being
changed, so the file is written first, then recorded. If recording fails, the
previous body is restored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain.errors import (
    ConflictError,
    ErrorCode,
    PreconditionFailedError,
    ValidationError,
)
from ..domain.models import ActorIdentity, LifecycleState, ResourceKind
from ..providers.git_repository import GitProvider
from ..schemas.resources import ArtifactReference, UpdateDraftVersionRequest
from .draft_commit import artifact_reference_for
from .resource_commands import ResourceCommandService

__all__ = ["VersionEditorService", "EditedVersion"]


@dataclass(frozen=True, slots=True)
class EditedVersion:
    """Outcome of an edit, including what changed on disk."""

    version_id: str
    version: str
    artifact_hash: str
    body_path: str
    body_written: bool
    previous_version_id: str


class VersionEditorService:
    """Write a draft body to Git and record the new digest."""

    def __init__(self, git: GitProvider, commands: ResourceCommandService) -> None:
        self._git = git
        self._commands = commands

    def edit(
        self,
        *,
        actor: ActorIdentity,
        version,
        body: str | None,
        manifest_patch: dict[str, Any] | None,
        reason: str,
        correlation_id: str,
        request_id: str | None = None,
        expected_row_version: int | None = None,
    ) -> EditedVersion:
        """Apply an edit to one draft version, producing a new version row.

        ``body`` is the full replacement text for the entrypoint file, or None to
        leave it untouched. ``manifest_patch`` is a JSON Merge Patch over the
        manifest, and must carry a new ``version`` whenever ``body`` is given.

        ``expected_row_version`` is the value the caller claims to have read. It is
        forwarded to the command service so its optimistic-lock check compares the
        caller's expectation against the stored row, rather than comparing the record
        to itself — which always passes and silently disables the check.
        """

        if version.status is not LifecycleState.DRAFT:
            raise ConflictError(
                code=ErrorCode.INVALID_STATE_TRANSITION,
                message=f"only draft versions can be edited; this one is {version.status.value}",
            )

        # Refuse a stale expectation before touching the checkout, so a conflicted
        # save cannot have written a file first.
        if expected_row_version is not None and expected_row_version != version.row_version:
            raise PreconditionFailedError(
                code=ErrorCode.ROW_VERSION_MISMATCH,
                message="the version changed since it was read",
            )

        manifest = dict(version.manifest)
        try:
            kind = ResourceKind(str(manifest.get("kind")))
        except ValueError as error:
            raise ValidationError(message=f"unknown kind {manifest.get('kind')!r}") from error
        slug = str(manifest.get("slug") or "")
        if not slug:
            raise ValidationError(message="version manifest has no slug")

        entrypoint = str(manifest.get("entrypoint") or "")
        if not entrypoint:
            raise ValidationError(message="version manifest has no entrypoint")

        if manifest_patch is not None and "status" in manifest_patch:
            # The command service rejects this too; refusing here gives a clearer
            # message and avoids writing a file before the refusal.
            raise ConflictError(
                code=ErrorCode.INVALID_STATE_TRANSITION,
                message="lifecycle status must be changed through the transition endpoint",
            )

        current_version = str(manifest.get("version") or "")
        patched_version = None
        if manifest_patch is not None and "version" in manifest_patch:
            patched_version = str(manifest_patch["version"])
        if body is not None:
            # A body edit always produces new content, so it must carry a new version
            # number: the previous content stays on record as immutable history. A
            # manifest-only correction is different and may keep the version, in which
            # case the command service updates the draft in place.
            if patched_version is None:
                raise ValidationError(
                    message=(
                        "editing a body requires a new manifest version: the previous "
                        f"version ({current_version}) is retained as immutable history, "
                        "so the new content needs its own version number"
                    )
                )
            if patched_version == current_version:
                raise ValidationError(
                    message=(
                        f"the new version must differ from {current_version}; each body "
                        "edit creates a new immutable version record"
                    )
                )

        written_body = False
        previous_body: str | None = None
        if body is not None:
            previous_body = self._read_body(kind, slug, entrypoint)
            self._git.write_resource_file(kind, slug, entrypoint, body)
            written_body = True

        try:
            artifacts: list[ArtifactReference] | None = None
            if body is not None:
                # Re-read what is now on disk rather than trusting the request: the
                # command service compares its own read against these digests, and
                # anything else makes the two disagree for the wrong reason.
                files = self._git.read_resource_files(kind, slug)
                current = files.get(entrypoint)
                if current is None:
                    raise ValidationError(message=f"entrypoint {entrypoint!r} vanished after writing")
                artifacts = [artifact_reference_for(entrypoint, current)]

            updated = self._commands.update_draft(
                actor=actor,
                version_public_id=version.public_id,
                # The caller's expectation, not the record's own value: passing the
                # record back to itself makes the optimistic lock a no-op.
                row_version=(
                    expected_row_version if expected_row_version is not None else version.row_version
                ),
                reason=reason,
                correlation_id=correlation_id,
                request_id=request_id,
                manifest_patch=manifest_patch,
                artifacts=artifacts,
            )
        except Exception:
            # Restore the previous body so the checkout keeps matching the recorded
            # digest of the version that still exists.
            if written_body and previous_body is not None:
                try:
                    self._git.write_resource_file(kind, slug, entrypoint, previous_body)
                except Exception:  # noqa: BLE001 - the original error matters more
                    pass
            raise

        return EditedVersion(
            version_id=str(updated.public_id),
            version=str(updated.identifier),
            artifact_hash=str(updated.artifact_hash),
            body_path=entrypoint,
            body_written=written_body,
            previous_version_id=str(version.public_id),
        )

    def _read_body(self, kind: ResourceKind, slug: str, entrypoint: str) -> str | None:
        try:
            files = self._git.read_resource_files(kind, slug)
        except Exception:  # noqa: BLE001 - a missing directory is handled by the caller
            return None
        return files.get(entrypoint)

    def read_body(self, *, version) -> tuple[str, str | None]:
        """Return the entrypoint path and its current text.

        The version payload carries no file contents (``artifacts`` is empty), so the
        editor cannot show the current body without this. Read-only: works whatever
        the checkout's write setting is, which is why it is not gated on the write
        capability.
        """

        manifest = dict(version.manifest)
        try:
            kind = ResourceKind(str(manifest.get("kind")))
        except ValueError as error:
            raise ValidationError(message=f"unknown kind {manifest.get('kind')!r}") from error
        slug = str(manifest.get("slug") or "")
        entrypoint = str(manifest.get("entrypoint") or "")
        if not slug or not entrypoint:
            raise ValidationError(message="version manifest lacks a slug or entrypoint")
        return entrypoint, self._read_body(kind, slug, entrypoint)
