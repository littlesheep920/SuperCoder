"""Rollback-as-new-version: copy an authorized snapshot into a higher draft."""

from __future__ import annotations

from ..domain.errors import ConflictError, ErrorCode, NotFoundError
from ..domain.models import ActorIdentity, LifecycleState, ResourceKind, VersionIdentifier, new_ulid
from ..repositories.contracts import NewVersion, UnitOfWorkFactory, VersionRecord, VisibilityScope
from .helpers import AuditWriter, Clock, now_utc
from .snapshots import SnapshotService

__all__ = ["RollbackService"]


class RollbackService:
    """Copies a non-retired historical snapshot into a new higher-version draft."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        snapshots: SnapshotService,
        clock: Clock = now_utc,
    ) -> None:
        self._uow_factory = uow_factory
        self._snapshots = snapshots
        self._clock = clock

    def rollback(
        self,
        *,
        actor: ActorIdentity,
        source_version_public_id: str,
        reason: str,
        scope: VisibilityScope,
        correlation_id: str,
        request_id: str | None = None,
    ) -> VersionRecord:
        with self._uow_factory() as uow:
            source = uow.versions.get(source_version_public_id, for_update=True)
            if source is None or not self._visible(source, scope):
                uow.rollback()
                raise NotFoundError("source version not found or not visible")
            if source.status is LifecycleState.RETIRED:
                uow.rollback()
                raise ConflictError(
                    code=ErrorCode.INVALID_STATE_TRANSITION,
                    message="a retired version cannot be a rollback source",
                )
            resource = uow.resources.get(source.resource_public_id)
            if resource is None:
                uow.rollback()
                raise NotFoundError("resource not found")

            next_version = self._next_version(source.identifier)
            kind = ResourceKind(str(source.manifest.get("kind")))
            slug = str(source.manifest.get("slug"))

            loaded = self._snapshots.load(kind, slug)
            new_manifest = dict(source.manifest)
            new_manifest["version"] = next_version.raw
            new_manifest["status"] = LifecycleState.DRAFT.value

            from ..domain.hashing import calculate_resource_hashes

            hashes = calculate_resource_hashes(new_manifest, loaded.files)
            now = self._clock()
            version_public_id = new_ulid()

            version = uow.versions.add(
                NewVersion(
                    public_id=version_public_id,
                    resource_public_id=source.resource_public_id,
                    identifier=next_version,
                    schema_version="1",
                    name=str(new_manifest.get("name")),
                    description=str(new_manifest.get("description")),
                    owner_team=str(new_manifest.get("owner_team")),
                    entrypoint=str(new_manifest.get("entrypoint")),
                    aliases=tuple(new_manifest.get("tags", [])),
                    inputs=tuple(new_manifest.get("inputs", [])),
                    outputs=tuple(new_manifest.get("outputs", [])),
                    manifest=new_manifest,
                    type_spec=dict(new_manifest.get(kind.value, {}) or {}),
                    artifact_hash=hashes.artifact_hash,
                    manifest_snapshot_hash=hashes.manifest_snapshot_hash,
                    status_changed_at=now,
                    status_changed_by=actor.subject_id,
                    status_reason=f"rollback of {source.identifier.raw}: {reason}",
                    created_by=actor.subject_id,
                )
            )
            uow.metadata.replace_snapshot(
                version_public_id,
                self._snapshots.build_metadata(loaded),
                expected_version_row=1,
            )
            uow.permissions.replace_resource_declarations(
                version_public_id,
                self._snapshots.build_permissions(loaded),
                expected_version_row=1,
            )
            AuditWriter(uow).append(
                actor=actor,
                action="version.rollback",
                outcome="succeeded",
                entity_type="resource_version",
                entity_public_id=version_public_id,
                correlation_id=correlation_id,
                reason_code="ROLLBACK",
                reason_summary=reason,
                request_id=request_id,
                version_public_id=version_public_id,
                artifact_hash=hashes.artifact_hash,
            )
            uow.commit()
            return version

    @staticmethod
    def _visible(record, scope: VisibilityScope) -> bool:
        if scope.organization_member:
            return True
        return record.owner_team in scope.team_ids

    @staticmethod
    def _next_version(identifier: VersionIdentifier) -> VersionIdentifier:
        return VersionIdentifier(
            major=identifier.major,
            minor=identifier.minor,
            patch=identifier.patch + 1,
        )
