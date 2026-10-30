"""Resource identity and draft-version command orchestration."""

from __future__ import annotations

from ..domain.errors import (
    ConflictError,
    ErrorCode,
    NotFoundError,
    ValidationError,
)
from ..domain.hashing import normalize_utf8_text, sha256_digest
from ..domain.models import (
    ActorIdentity,
    ArtifactHash,
    LifecycleState,
    ManifestSnapshotHash,
    ResourceKind,
    VersionIdentifier,
    new_ulid,
)
from ..domain.validation import validate_slug
from ..repositories.contracts import (
    MetadataSnapshot,
    NewResource,
    NewVersion,
    UnitOfWorkFactory,
    VersionRecord,
)
from ..schemas.resources import ArtifactReference, CreateResourceRequest, CreateResourceVersionRequest
from .helpers import AuditWriter, Clock, now_utc
from .snapshots import SnapshotService

__all__ = ["ResourceCommandService"]


class ResourceCommandService:
    """Creates resource identities, draft versions, and updates drafts."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        snapshots: SnapshotService,
        clock: Clock = now_utc,
    ) -> None:
        self._uow_factory = uow_factory
        self._snapshots = snapshots
        self._clock = clock

    def create_resource(
        self,
        *,
        actor: ActorIdentity,
        request: CreateResourceRequest,
        correlation_id: str,
        request_id: str | None = None,
    ) -> tuple[object, VersionRecord]:
        manifest = request.manifest
        kind = ResourceKind(str(manifest.get("kind")))
        slug = validate_slug(str(manifest.get("slug")))

        loaded = self._snapshots.load(kind, slug)
        self._verify_identity(manifest, loaded)
        self._verify_artifacts(request.artifacts, loaded.files)

        metadata = self._snapshots.build_metadata(loaded)
        permissions = self._snapshots.build_permissions(loaded)
        now = self._clock()
        resource_public_id = new_ulid()
        version_public_id = new_ulid()

        with self._uow_factory() as uow:
            existing = uow.resources.get_by_identity(kind, slug)
            if existing is not None:
                uow.rollback()
                raise ConflictError(
                    code=ErrorCode.RESOURCE_IDENTITY_EXISTS,
                    message=f"resource {kind.value}/{slug} already exists",
                )
            uow.resources.add(
                NewResource(
                    public_id=resource_public_id,
                    kind=kind,
                    slug=slug,
                    owner_team=str(manifest.get("owner_team")),
                    created_by=actor.subject_id,
                )
            )
            version = uow.versions.add(self._new_version(version_public_id, resource_public_id, loaded, actor, now, str(request.reason)))
            uow.metadata.replace_snapshot(version_public_id, metadata, expected_version_row=1)
            uow.permissions.replace_resource_declarations(version_public_id, permissions, expected_version_row=1)
            AuditWriter(uow).append(
                actor=actor,
                action="resource.create",
                outcome="succeeded",
                entity_type="resource",
                entity_public_id=resource_public_id,
                correlation_id=correlation_id,
                reason_code="CREATED",
                reason_summary=str(request.reason),
                request_id=request_id,
                version_public_id=version_public_id,
                to_status=LifecycleState.DRAFT.value,
                artifact_hash=loaded.artifact_hash,
            )
            uow.commit()
            return (uow.resources.get(resource_public_id), version)

    def create_version(
        self,
        *,
        actor: ActorIdentity,
        resource_public_id: str,
        request: CreateResourceVersionRequest,
        correlation_id: str,
        request_id: str | None = None,
    ) -> VersionRecord:
        manifest = request.manifest
        kind = ResourceKind(str(manifest.get("kind")))
        slug = validate_slug(str(manifest.get("slug")))
        loaded = self._snapshots.load(kind, slug)
        self._verify_identity(manifest, loaded)
        self._verify_artifacts(request.artifacts, loaded.files)

        metadata = self._snapshots.build_metadata(loaded)
        permissions = self._snapshots.build_permissions(loaded)
        now = self._clock()
        version_public_id = new_ulid()

        with self._uow_factory() as uow:
            resource = uow.resources.get(resource_public_id, for_update=True)
            if resource is None:
                uow.rollback()
                raise NotFoundError("resource not found")
            if resource.kind is not kind or resource.slug != slug:
                uow.rollback()
                raise ConflictError(
                    code=ErrorCode.GATE_CONFLICT,
                    message="manifest identity does not match the target resource",
                )
            existing = uow.versions.get_exact(resource_public_id, loaded.version)
            if existing is not None:
                uow.rollback()
                raise ConflictError(code=ErrorCode.VERSION_EXISTS, message="version already exists")
            version = uow.versions.add(
                self._new_version(version_public_id, resource_public_id, loaded, actor, now, str(request.reason))
            )
            uow.metadata.replace_snapshot(version_public_id, metadata, expected_version_row=1)
            uow.permissions.replace_resource_declarations(version_public_id, permissions, expected_version_row=1)
            AuditWriter(uow).append(
                actor=actor,
                action="version.create",
                outcome="succeeded",
                entity_type="resource_version",
                entity_public_id=version_public_id,
                correlation_id=correlation_id,
                reason_code="CREATED",
                reason_summary=str(request.reason),
                request_id=request_id,
                version_public_id=version_public_id,
                to_status=LifecycleState.DRAFT.value,
                artifact_hash=loaded.artifact_hash,
            )
            uow.commit()
            return version

    def update_draft(
        self,
        *,
        actor: ActorIdentity,
        version_public_id: str,
        row_version: int,
        reason: str,
        correlation_id: str,
        request_id: str | None = None,
        manifest_patch: dict[str, object] | None = None,
        artifacts: list[ArtifactReference] | None = None,
    ) -> VersionRecord:
        from ..domain.patching import apply_json_merge_patch

        with self._uow_factory() as uow:
            current = uow.versions.get(version_public_id, for_update=True)
            if current is None:
                uow.rollback()
                raise NotFoundError("version not found")
            if current.status is not LifecycleState.DRAFT:
                uow.rollback()
                raise ConflictError(
                    code=ErrorCode.INVALID_STATE_TRANSITION,
                    message="only draft versions can be modified",
                )
            if current.row_version != row_version:
                uow.rollback()
                raise ValidationError(
                    code=ErrorCode.ROW_VERSION_MISMATCH, message="row_version is stale"
                )
            kind = current.manifest.get("kind")
            slug = current.manifest.get("slug")
            loaded = self._snapshots.load(ResourceKind(str(kind)), str(slug))
            self._verify_identity(dict(current.manifest), loaded)

            if manifest_patch is not None and "status" in manifest_patch:
                uow.rollback()
                raise ConflictError(
                    code=ErrorCode.INVALID_STATE_TRANSITION,
                    message="lifecycle status must be changed through the transition endpoint",
                )
            patched_manifest = apply_json_merge_patch(loaded.manifest, manifest_patch or {})
            if not isinstance(patched_manifest, dict):
                uow.rollback()
                raise ValidationError(
                    code=ErrorCode.INVALID_STATE_TRANSITION,
                    message="manifest patch must produce an object",
                )
            patched_manifest["status"] = current.status.value
            loaded = self._snapshots.load_snapshot(
                ResourceKind(str(kind)), str(slug), patched_manifest, loaded.files
            )
            if artifacts is not None:
                self._verify_artifacts(artifacts, loaded.files)

            metadata = self._snapshots.build_metadata(loaded)
            permissions = self._snapshots.build_permissions(loaded)
            now = self._clock()

            # Two legitimate meanings for "edit a draft":
            #
            # * the version number changed -> a new version, keeping the old one as
            #   immutable history;
            # * the version number is the same -> correcting this draft in place.
            #
            # The first implementation always appended a row, so a manifest-only edit
            # (same version number) collided with uq_rv_resource_version and returned a
            # 500 from MySQL. Which one the caller meant is decided here, by whether
            # the identifier actually changed.
            identifier_changed = loaded.version != current.identifier

            if identifier_changed:
                version = uow.versions.add(
                    self._new_version(
                        new_ulid(),
                        current.resource_public_id,
                        loaded,
                        actor,
                        now,
                        reason,
                    )
                )
                uow.metadata.replace_snapshot(version.public_id, metadata, expected_version_row=1)
                uow.permissions.replace_resource_declarations(
                    version.public_id, permissions, expected_version_row=1
                )
            else:
                version = uow.versions.update_draft_content(
                    public_id=version_public_id,
                    expected_row_version=current.row_version,
                    expected_artifact_hash=current.artifact_hash,
                    expected_manifest_snapshot_hash=current.manifest_snapshot_hash,
                    manifest=patched_manifest,
                    artifact_hash=loaded.artifact_hash,
                    manifest_snapshot_hash=loaded.manifest_snapshot_hash,
                    reason=reason,
                    changed_by=actor.subject_id,
                    changed_at=now,
                    # The columns that mirror manifest fields, kept in step with the
                    # JSON. Updating only the JSON would leave reads reporting stale
                    # values, because these are separate columns.
                    derived=self._derived_columns(loaded),
                )
                # `update_draft_content` already bumped row_version, so the metadata
                # writes must expect the NEW value. Passing the pre-update value made
                # `replace_snapshot` reject the change with "version row changed" —
                # which surfaced as a 412 on a perfectly valid edit.
                expected_row = version.row_version
                uow.metadata.replace_snapshot(
                    version.public_id, metadata, expected_version_row=expected_row
                )
                uow.permissions.replace_resource_declarations(
                    version.public_id, permissions, expected_version_row=expected_row
                )

            # Invalidate prior approvals; content that was approved is no longer the
            # content on record, whether it lives under a new version or the same one.
            uow.reviews.invalidate_for_version(
                version_public_id,
                invalidated_at=now,
                reason="draft content changed",
                expected_artifact_hash=current.artifact_hash,
            )
            AuditWriter(uow).append(
                actor=actor,
                action="version.update_draft",
                outcome="succeeded",
                entity_type="resource_version",
                entity_public_id=version.public_id,
                correlation_id=correlation_id,
                reason_code="UPDATED",
                reason_summary=reason,
                request_id=request_id,
                version_public_id=version.public_id,
                artifact_hash=loaded.artifact_hash,
            )
            uow.commit()
            return version

    def _derived_columns(self, loaded) -> dict[str, object]:
        """Columns that mirror manifest fields, for an in-place draft update.

        `name`, `description`, `owner_team`, `aliases_json`, `inputs_json`,
        `outputs_json` and `type_spec_json` are stored separately from `manifest_json`.
        Any path that changes the manifest must rewrite them too, or reads through
        those columns keep returning the pre-edit values.

        The JSON columns are SQLAlchemy `JSON`, so they take Python values directly;
        passing pre-serialised strings would store JSON-inside-JSON and break the
        `JSON_TYPE(...) = 'ARRAY'` CHECK constraints.
        """

        return {
            "name": str(loaded.manifest.get("name")),
            "description": str(loaded.manifest.get("description")),
            "owner_team": str(loaded.manifest.get("owner_team")),
            "entrypoint": loaded.entrypoint,
            "aliases_json": list(loaded.tags),
            "inputs_json": list(loaded.manifest.get("inputs", [])),
            "outputs_json": list(loaded.manifest.get("outputs", [])),
            "type_spec_json": dict(loaded.manifest.get(loaded.kind.value, {}) or {}),
        }

    def _new_version(
        self,
        public_id: str,
        resource_public_id: str,
        loaded,
        actor: ActorIdentity,
        now,
        reason: str,
    ) -> NewVersion:
        return NewVersion(
            public_id=public_id,
            resource_public_id=resource_public_id,
            identifier=loaded.version,
            schema_version="1",
            name=str(loaded.manifest.get("name")),
            description=str(loaded.manifest.get("description")),
            owner_team=str(loaded.manifest.get("owner_team")),
            entrypoint=loaded.entrypoint,
            aliases=loaded.tags,
            inputs=tuple(loaded.manifest.get("inputs", [])),
            outputs=tuple(loaded.manifest.get("outputs", [])),
            manifest=loaded.manifest,
            type_spec=dict(loaded.manifest.get(loaded.kind.value, {}) or {}),
            artifact_hash=loaded.artifact_hash,
            manifest_snapshot_hash=loaded.manifest_snapshot_hash,
            status_changed_at=now,
            status_changed_by=actor.subject_id,
            status_reason=reason,
            created_by=actor.subject_id,
        )

    def _verify_identity(self, request_manifest: dict[str, object], loaded) -> None:
        if str(request_manifest.get("kind")) != loaded.kind.value:
            raise ValidationError(code=ErrorCode.PROVENANCE_INVALID, message="manifest kind mismatch")
        if str(request_manifest.get("slug")) != loaded.slug:
            raise ValidationError(code=ErrorCode.PROVENANCE_INVALID, message="manifest slug mismatch")
        if str(request_manifest.get("version")) != loaded.version.raw:
            raise ValidationError(code=ErrorCode.PROVENANCE_INVALID, message="manifest version mismatch")

    def _verify_artifacts(self, artifacts: list[ArtifactReference], files: dict[str, str]) -> None:
        for artifact in artifacts:
            content = files.get(artifact.path)
            if content is None:
                raise ValidationError(
                    code=ErrorCode.PROVENANCE_INVALID,
                    message=f"artifact {artifact.path!r} is not present in the resource directory",
                )
            normalized = normalize_utf8_text(content)
            digest = sha256_digest(normalized)
            if digest != artifact.digest or len(normalized) != artifact.size_bytes:
                raise ValidationError(
                    code=ErrorCode.CONTENT_HASH_MISMATCH,
                    message=f"artifact digest/size mismatch for {artifact.path!r}",
                )
