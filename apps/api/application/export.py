"""Export a published version to a runtime environment.

Two rules shape this service, both decided before any code was written:

1. **Only published versions export.** An unpublished resource is not a deliverable; it
   has not passed review and its content may still change. Refusing here keeps the
   export path consistent with the governance chain rather than creating a second way for
   unreviewed content to leave the platform.

2. **Warnings travel with the artefact.** An environment that cannot carry a capability
   still receives the files, but the loss is attached to the response. Dropping it would
   make the export look complete when it is not.

The service reads the body from the checkout for the same reason the editor does: the
record holds hashes, not text. That is a known limitation of the storage model, and the
export reports it rather than pretending the text is the version's snapshot.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from io import BytesIO

from ..auth import AuthContext
from ..domain.adapters import (
    AdaptationTarget,
    ExportBundle,
    ResourceForExport,
    find_target,
)
from ..domain.errors import ConflictError, ErrorCode, NotFoundError
from ..domain.models import LifecycleState, ResourceKind
from ..repositories.contracts import UnitOfWorkFactory

__all__ = ["ExportResult", "ExportService"]


@dataclass(frozen=True, slots=True)
class _VersionSnapshot(ResourceForExport):
    """A resource version, flattened into what an adapter reads."""

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


@dataclass(frozen=True, slots=True)
class ExportResult:
    bundle: ExportBundle
    archive: bytes
    filename: str


class ExportService:
    def __init__(self, uow_factory: UnitOfWorkFactory, git, versions=None) -> None:
        self._uow_factory = uow_factory
        self._git = git
        # `VersionQueryService.get` is the one place that applies visibility rules; reusing
        # it here means an export can never be a way around them.
        self._versions = versions

    def export(
        self,
        *,
        auth: AuthContext,
        version_public_id: str,
        target_identifier: str,
    ) -> ExportResult:
        target = find_target(target_identifier)
        if target is None:
            raise NotFoundError(f"unknown export target {target_identifier!r}")

        # Raises NotFound when the version is not visible to this caller, which is the same
        # response an absent version produces: an export must not confirm that a hidden
        # version exists.
        version = self._versions.get(auth.actor, version_public_id, auth.visibility_scope())

        if version.status is not LifecycleState.PUBLISHED:
            raise ConflictError(
                code=ErrorCode.INVALID_STATE_TRANSITION,
                message=(
                    "only published versions can be exported; this version is "
                    f"{version.status.value}"
                ),
            )

        with self._uow_factory() as uow:
            resource = uow.resources.get(version.resource_public_id)
            if resource is None:
                uow.rollback()
                raise NotFoundError("resource not found")
            uow.commit()

        manifest = dict(version.manifest)
        snapshot = self._snapshot(resource, version, manifest)
        bundle = target.export(snapshot)

        # The adapter's own rules are applied before the files leave: an export that would
        # not load is worse than a refused one, because the failure surfaces on the user's
        # machine with no context.
        validation = target.validate(bundle)
        if not validation.ok:
            details = "; ".join(issue.message for issue in validation.fatal)
            raise ConflictError(
                code=ErrorCode.VALIDATION_FAILED,
                message=f"export failed static validation for {target.identifier}: {details}",
            )

        archive = _zip(bundle)
        filename = f"{resource.slug}-{version.identifier}-{target.identifier}.zip"
        return ExportResult(bundle=bundle, archive=archive, filename=filename)

    def _snapshot(self, resource, version, manifest: dict) -> _VersionSnapshot:
        kind = str(manifest.get("kind") or "")
        slug = str(manifest.get("slug") or "")
        entrypoint = str(manifest.get("entrypoint") or "")

        try:
            body = self._git.read_resource_files(ResourceKind(kind), slug).get(entrypoint, "")
        except FileNotFoundError as error:
            # The record exists but the checkout does not have the files. Saying so is
            # better than exporting a skill with an empty body that looks intentional.
            raise NotFoundError(
                f"resource directory for {kind}/{slug} is missing from the checkout"
            ) from error

        return _VersionSnapshot(
            resource_id=resource.public_id,
            version_id=version.public_id,
            version=str(version.identifier),
            kind=kind,
            slug=slug,
            name=str(version.name),
            description=str(version.description),
            entrypoint=entrypoint,
            body=body,
            manifest=manifest,
            tags=tuple(str(tag) for tag in (version.aliases or ())),
        )


def _zip(bundle: ExportBundle) -> bytes:
    """Build the archive with fixed metadata so the same bundle yields the same bytes.

    `ZipInfo` is constructed explicitly rather than letting `writestr(name, ...)` stamp the
    current time: a timestamped archive would differ on every export, making the artifact
    impossible to verify by hash.
    """

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in bundle.ordered_files():
            info = zipfile.ZipInfo(filename=entry.path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, entry.encoded())
    return buffer.getvalue()
