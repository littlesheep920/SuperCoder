"""SQLAlchemy repositories for resource identity, versions, and projections.

Concrete repositories translate domain/application records to and from MySQL
rows.  They apply visibility and authorization predicates in SQL before
counting, sorting, and pagination, and they enforce affected-row checks on
conditional writes.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import and_, delete, false, func, or_, select, true, update
from sqlalchemy.orm import Session

from ..common.cursors import decode_cursor, encode_cursor
from ..config import get_settings
from ..db.models_governance import ResourceIndexStatus, ResourceUsage
from ..db.models_resource import (
    Resource,
    ResourceCategory,
    ResourceDependency,
    ResourceRuntime,
    ResourceTag,
    ResourceVersion,
)
from ..domain.models import (
    ArtifactHash,
    JobState,  # noqa: F401  (re-exported for downstream imports)
    LifecycleState,
    ManifestSnapshotHash,
    ResourceKind,
    VersionIdentifier,
    Visibility,
)
from ..domain.errors import ConflictError, ErrorCode, NotFoundError, PreconditionFailedError
from .contracts import (
    CursorPage,
    DependencyRecord,
    CategoryRecord,
    IndexStatusRecord,
    LifecycleUpdate,
    MetadataSnapshot,
    NewResource,
    NewVersion,
    ResourceQuery,
    ResourceRecord,
    ResourceWithVersion,
    RuntimeRecord,
    TagRecord,
    UsageRecord,
    VersionRecord,
    VisibilityScope,
)

__all__ = [
    "SqlMetadataRepository",
    "SqlResourceRepository",
    "SqlVersionRepository",
]


def _resource_record(resource: Resource) -> ResourceRecord:
    return ResourceRecord(
        public_id=resource.public_id,
        kind=ResourceKind(resource.kind),
        slug=resource.slug,
        owner_team=resource.owner_team,
        created_by=resource.created_by,
        created_at=resource.created_at,
        updated_at=resource.updated_at,
        row_version=resource.row_version,
    )


def _version_record(
    version: ResourceVersion,
    resource_public_id: str,
    replacement_public_id: str | None = None,
) -> VersionRecord:
    return VersionRecord(
        public_id=version.public_id,
        resource_public_id=resource_public_id,
        identifier=VersionIdentifier.parse(version.version),
        schema_version=version.schema_version,
        name=version.name,
        description=version.description,
        owner_team=version.owner_team,
        entrypoint=version.entrypoint,
        aliases=tuple(version.aliases_json or ()),
        inputs=tuple(version.inputs_json or ()),
        outputs=tuple(version.outputs_json or ()),
        manifest=dict(version.manifest_json or {}),
        type_spec=dict(version.type_spec_json or {}),
        artifact_hash=ArtifactHash(version.artifact_hash),
        manifest_snapshot_hash=ManifestSnapshotHash(version.manifest_snapshot_hash),
        status=LifecycleState(version.status),
        status_changed_at=version.status_changed_at,
        status_changed_by=version.status_changed_by,
        status_reason=version.status_reason,
        row_version=version.row_version,
        created_by=version.created_by,
        created_at=version.created_at,
        updated_at=version.updated_at,
        validated_at=version.validated_at,
        approved_at=version.approved_at,
        published_at=version.published_at,
        deprecated_at=version.deprecated_at,
        retire_after=version.retire_after,
        retired_at=version.retired_at,
        replacement_version_public_id=replacement_public_id,
    )


def _auth_predicate(scope: VisibilityScope):
    if scope.organization_member:
        return true()
    clauses = [Resource.created_by == scope.actor_id]
    if scope.team_ids:
        clauses.append(Resource.owner_team.in_(scope.team_ids))
    return or_(*clauses)


def _visibility_predicate(query: ResourceQuery):
    if not query.visibilities:
        return true()
    clauses = []
    for visibility in query.visibilities:
        if visibility is Visibility.PUBLIC:
            clauses.append(
                ResourceVersion.status.in_(["published", "deprecated"])
            )
        elif visibility is Visibility.ORGANIZATION:
            clauses.append(true() if query.scope.organization_member else false())
        elif visibility is Visibility.TEAM:
            clauses.append(
                Resource.owner_team.in_(query.scope.team_ids)
                if query.scope.team_ids
                else false()
            )
        elif visibility is Visibility.PRIVATE:
            clauses.append(Resource.created_by == query.scope.actor_id)
    return or_(*clauses)


def _resource_filter_predicates(query: ResourceQuery):
    clauses = []
    if query.kinds:
        clauses.append(Resource.kind.in_([kind.value for kind in query.kinds]))
    if query.lifecycle_states:
        clauses.append(
            ResourceVersion.status.in_([state.value for state in query.lifecycle_states])
        )
    if query.owner_teams:
        clauses.append(Resource.owner_team.in_(query.owner_teams))
    if query.search_text:
        pattern = f"%{query.search_text}%"
        clauses.append(
            or_(
                Resource.slug.like(pattern),
                ResourceVersion.name.like(pattern),
                ResourceVersion.description.like(pattern),
            )
        )
    if query.tags:
        tag_subquery = (
            select(ResourceTag.resource_version_id)
            .where(ResourceTag.normalized_tag.in_(query.tags))
            .distinct()
            .subquery()
        )
        clauses.append(ResourceVersion.id.in_(select(tag_subquery.c.resource_version_id)))
    if query.categories:
        cat_subquery = (
            select(ResourceCategory.resource_version_id)
            .where(ResourceCategory.normalized_category.in_(query.categories))
            .distinct()
            .subquery()
        )
        clauses.append(ResourceVersion.id.in_(select(cat_subquery.c.resource_version_id)))
    if query.runtimes:
        runtime_subquery = (
            select(ResourceRuntime.resource_version_id)
            .where(ResourceRuntime.runtime.in_(query.runtimes))
            .distinct()
            .subquery()
        )
        clauses.append(ResourceVersion.id.in_(select(runtime_subquery.c.resource_version_id)))
    return clauses


def _latest_version_ids() -> "object":
    return (
        select(
            ResourceVersion.resource_id,
            func.max(ResourceVersion.id).label("max_id"),
        )
        .group_by(ResourceVersion.resource_id)
        .subquery()
    )


class SqlResourceRepository:
    """Persistence and authorization-filtered queries for resource identity."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, resource: NewResource) -> ResourceRecord:
        model = Resource(
            public_id=resource.public_id,
            kind=resource.kind.value,
            slug=resource.slug,
            owner_team=resource.owner_team,
            created_by=resource.created_by,
        )
        self._session.add(model)
        self._session.flush()
        self._session.refresh(model)
        return _resource_record(model)

    def get(self, public_id: str, *, for_update: bool = False) -> ResourceRecord | None:
        stmt = select(Resource).where(Resource.public_id == public_id)
        if for_update:
            stmt = stmt.with_for_update()
        model = self._session.execute(stmt).scalar_one_or_none()
        return _resource_record(model) if model else None

    def get_by_identity(
        self, kind: ResourceKind, slug: str, *, for_update: bool = False
    ) -> ResourceRecord | None:
        stmt = select(Resource).where(
            Resource.kind == kind.value, Resource.slug == slug
        )
        if for_update:
            stmt = stmt.with_for_update()
        model = self._session.execute(stmt).scalar_one_or_none()
        return _resource_record(model) if model else None

    def list_visible(self, query: ResourceQuery) -> CursorPage[VersionRecord]:
        session = self._session
        latest = _latest_version_ids()
        base_where = and_(
            _auth_predicate(query.scope),
            _visibility_predicate(query),
            *_resource_filter_predicates(query),
        )

        total = session.execute(
            select(func.count())
            .select_from(Resource)
            .join(ResourceVersion, ResourceVersion.resource_id == Resource.id)
            .join(latest, and_(latest.c.resource_id == Resource.id, latest.c.max_id == ResourceVersion.id))
            .where(base_where)
        ).scalar_one()

        stmt = (
            select(Resource, ResourceVersion)
            .join(ResourceVersion, ResourceVersion.resource_id == Resource.id)
            .join(latest, and_(latest.c.resource_id == Resource.id, latest.c.max_id == ResourceVersion.id))
            .where(base_where)
            .order_by(Resource.created_at.desc(), Resource.id.desc())
            .limit(query.limit + 1)
        )

        if query.cursor:
            created_at, record_id = decode_cursor(get_settings().cursor_key, query.cursor)
            stmt = stmt.where(
                or_(
                    Resource.created_at < created_at,
                    and_(Resource.created_at == created_at, Resource.id < record_id),
                )
            )

        rows = session.execute(stmt).all()
        has_more = len(rows) > query.limit
        rows = rows[: query.limit]
        next_cursor = None
        if has_more and rows:
            last_resource = rows[-1][0]
            next_cursor = encode_cursor(
                get_settings().cursor_key, last_resource.created_at, last_resource.id
            )

        items = tuple(
            ResourceWithVersion(resource=_resource_record(resource), version=_version_record(version, resource.public_id))
            for resource, version in rows
        )
        return CursorPage(items=items, next_cursor=next_cursor, total=total)


class SqlVersionRepository:
    """Persistence and conditional lifecycle updates for exact versions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _resource_id(self, public_id: str) -> int:
        value = self._session.execute(
            select(Resource.id).where(Resource.public_id == public_id)
        ).scalar_one_or_none()
        if value is None:
            raise NotFoundError("resource not found")
        return value

    def _replacement_public_id(self, internal_id: int | None) -> str | None:
        if internal_id is None:
            return None
        return self._session.execute(
            select(ResourceVersion.public_id).where(ResourceVersion.id == internal_id)
        ).scalar_one_or_none()

    def add(self, version: NewVersion) -> VersionRecord:
        resource_id = self._resource_id(version.resource_public_id)
        model = ResourceVersion(
            public_id=version.public_id,
            resource_id=resource_id,
            schema_version=version.schema_version,
            version=version.identifier.raw,
            name=version.name,
            description=version.description,
            owner_team=version.owner_team,
            entrypoint=version.entrypoint,
            aliases_json=list(version.aliases),
            inputs_json=list(version.inputs),
            outputs_json=list(version.outputs),
            permissions_json=list(version.manifest.get("permissions", [])),
            type_spec_json=dict(version.type_spec),
            manifest_json=dict(version.manifest),
            artifact_hash=version.artifact_hash,
            manifest_snapshot_hash=version.manifest_snapshot_hash,
            status=LifecycleState.DRAFT.value,
            status_changed_at=version.status_changed_at,
            status_changed_by=version.status_changed_by,
            status_reason=version.status_reason,
            created_by=version.created_by,
        )
        self._session.add(model)
        self._session.flush()
        self._session.refresh(model)
        return _version_record(model, version.resource_public_id)

    def get(self, public_id: str, *, for_update: bool = False) -> VersionRecord | None:
        stmt = select(ResourceVersion, Resource.public_id).join(
            Resource, Resource.id == ResourceVersion.resource_id
        ).where(ResourceVersion.public_id == public_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = self._session.execute(stmt).one_or_none()
        if row is None:
            return None
        version, resource_public_id = row
        return _version_record(
            version, resource_public_id, self._replacement_public_id(version.replacement_version_id)
        )

    def get_exact(
        self,
        resource_public_id: str,
        identifier: VersionIdentifier,
        *,
        for_update: bool = False,
    ) -> VersionRecord | None:
        stmt = (
            select(ResourceVersion, Resource.public_id)
            .join(Resource, Resource.id == ResourceVersion.resource_id)
            .where(
                Resource.public_id == resource_public_id,
                ResourceVersion.version == identifier.raw,
            )
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = self._session.execute(stmt).one_or_none()
        if row is None:
            return None
        version, resource_public_id = row
        return _version_record(
            version, resource_public_id, self._replacement_public_id(version.replacement_version_id)
        )

    def get_by_artifact_hash(
        self, artifact_hash: ArtifactHash, *, scope: VisibilityScope
    ) -> tuple[VersionRecord, ...]:
        stmt = (
            select(ResourceVersion, Resource.public_id)
            .join(Resource, Resource.id == ResourceVersion.resource_id)
            .where(
                ResourceVersion.artifact_hash == artifact_hash,
                _auth_predicate(scope),
            )
        )
        rows = self._session.execute(stmt).all()
        return tuple(
            _version_record(version, resource_public_id) for version, resource_public_id in rows
        )

    def list_for_resource(
        self,
        resource_public_id: str,
        *,
        scope: VisibilityScope,
        cursor: str | None = None,
        limit: int = 50,
    ) -> CursorPage[VersionRecord]:
        session = self._session
        base_where = and_(
            Resource.public_id == resource_public_id,
            _auth_predicate(scope),
        )
        total = session.execute(
            select(func.count())
            .select_from(ResourceVersion)
            .join(Resource, Resource.id == ResourceVersion.resource_id)
            .where(base_where)
        ).scalar_one()

        stmt = (
            select(ResourceVersion)
            .join(Resource, Resource.id == ResourceVersion.resource_id)
            .where(base_where)
            .order_by(ResourceVersion.status_changed_at.desc(), ResourceVersion.id.desc())
            .limit(limit + 1)
        )
        if cursor:
            changed_at, record_id = decode_cursor(get_settings().cursor_key, cursor)
            stmt = stmt.where(
                or_(
                    ResourceVersion.status_changed_at < changed_at,
                    and_(
                        ResourceVersion.status_changed_at == changed_at,
                        ResourceVersion.id < record_id,
                    ),
                )
            )

        # ``select(ResourceVersion)`` yields rows wrapping the entity, so ``.all()``
        # returns Row objects and the following ``version.replacement_version_id``
        # raises AttributeError. ``.scalars()`` unwraps to ResourceVersion.
        rows = list(session.execute(stmt).scalars().all())
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor(
                get_settings().cursor_key, last.status_changed_at, last.id
            )
        items = tuple(
            _version_record(version, resource_public_id, self._replacement_public_id(version.replacement_version_id))
            for version in rows
        )
        return CursorPage(items=items, next_cursor=next_cursor, total=total)

    def transition_lifecycle(self, lifecycle_update: LifecycleUpdate) -> VersionRecord:
        session = self._session
        values: dict[str, object] = {
            "status": lifecycle_update.target_status.value,
            "status_changed_at": lifecycle_update.changed_at,
            "status_changed_by": lifecycle_update.changed_by,
            "status_reason": lifecycle_update.reason,
            "manifest_snapshot_hash": lifecycle_update.manifest_snapshot_hash_after,
        }
        if lifecycle_update.manifest_json_after is not None:
            values["manifest_json"] = dict(lifecycle_update.manifest_json_after)
        values.update(
            _timestamp_updates(lifecycle_update.expected_status, lifecycle_update.target_status, lifecycle_update.changed_at)
        )
        if lifecycle_update.retire_after is not None:
            values["retire_after"] = lifecycle_update.retire_after
        if lifecycle_update.replacement_version_public_id is not None:
            replacement_id = self._resource_id_for_version(lifecycle_update.replacement_version_public_id)
            values["replacement_version_id"] = replacement_id

        result = session.execute(
            update(ResourceVersion)
            .where(
                ResourceVersion.public_id == lifecycle_update.version_public_id,
                ResourceVersion.row_version == lifecycle_update.expected_row_version,
                ResourceVersion.status == lifecycle_update.expected_status.value,
                ResourceVersion.artifact_hash == lifecycle_update.expected_artifact_hash,
                ResourceVersion.manifest_snapshot_hash == lifecycle_update.expected_manifest_snapshot_hash,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise PreconditionFailedError(
                code=ErrorCode.ROW_VERSION_MISMATCH,
                message="The version changed since it was last read.",
            )
        refreshed = self.get(lifecycle_update.version_public_id)
        if refreshed is None:
            raise ConflictError(code=ErrorCode.INVALID_STATE_TRANSITION, message="version vanished")
        return refreshed

    def update_draft_content(
        self,
        *,
        public_id: str,
        expected_row_version: int,
        expected_artifact_hash: ArtifactHash,
        expected_manifest_snapshot_hash: ArtifactHash,
        manifest: dict,
        artifact_hash: ArtifactHash,
        manifest_snapshot_hash: ArtifactHash,
        reason: str,
        changed_by: str,
        changed_at: datetime,
        derived: dict | None = None,
    ) -> VersionRecord:
        """Rewrite a draft in place, bumping row_version.

        `status` and its timestamps are deliberately untouched: editing content is not
        a lifecycle event, and writing them here would also trip the CHECK constraints
        that tie each state to its timestamp.

        `derived` carries the columns mirrored from manifest fields (name, description,
        owner_team, aliases, inputs, outputs, type_spec). Those are real columns, not a
        view over `manifest_json`, so updating only the JSON left the record reporting
        the old description — which is what the first version of this method did.
        """

        session = self._session
        values: dict[str, object] = {
            "manifest_json": dict(manifest),
            "artifact_hash": artifact_hash,
            "manifest_snapshot_hash": manifest_snapshot_hash,
            "status_reason": reason,
            "status_changed_by": changed_by,
            "updated_at": changed_at,
            "row_version": expected_row_version + 1,
        }
        if derived:
            values.update(derived)

        result = session.execute(
            update(ResourceVersion)
            .where(
                ResourceVersion.public_id == public_id,
                ResourceVersion.row_version == expected_row_version,
                ResourceVersion.status == LifecycleState.DRAFT.value,
                ResourceVersion.artifact_hash == expected_artifact_hash,
                ResourceVersion.manifest_snapshot_hash == expected_manifest_snapshot_hash,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise PreconditionFailedError(
                code=ErrorCode.ROW_VERSION_MISMATCH,
                message="The version changed since it was last read.",
            )
        refreshed = self.get(public_id)
        if refreshed is None:
            raise ConflictError(code=ErrorCode.INVALID_STATE_TRANSITION, message="version vanished")
        return refreshed

    def _resource_id_for_version(self, public_id: str) -> int:
        value = self._session.execute(
            select(ResourceVersion.id).where(ResourceVersion.public_id == public_id)
        ).scalar_one_or_none()
        if value is None:
            raise NotFoundError("replacement version not found")
        return value


def _timestamp_updates(
    expected: LifecycleState, target: LifecycleState, changed_at: object
) -> dict[str, object]:
    """Derive the exact timestamp transitions required by the frozen checks."""

    updates: dict[str, object] = {}
    if target is LifecycleState.APPROVED:
        updates["approved_at"] = changed_at
    elif target is LifecycleState.PUBLISHED:
        updates["published_at"] = changed_at
        if expected is LifecycleState.DEPRECATED:
            updates["deprecated_at"] = None
    elif target is LifecycleState.DEPRECATED:
        updates["deprecated_at"] = changed_at
    elif target is LifecycleState.RETIRED:
        updates["retired_at"] = changed_at
    elif target is LifecycleState.PENDING_REVIEW:
        updates["validated_at"] = changed_at
    elif target is LifecycleState.DRAFT:
        updates["validated_at"] = None
        updates["approved_at"] = None
    return updates


class SqlMetadataRepository:
    """Projection persistence for tags, categories, runtimes, dependencies."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _version_id(self, public_id: str) -> int:
        value = self._session.execute(
            select(ResourceVersion.id).where(ResourceVersion.public_id == public_id)
        ).scalar_one_or_none()
        if value is None:
            raise NotFoundError("version not found")
        return value

    def replace_snapshot(
        self,
        version_public_id: str,
        snapshot: MetadataSnapshot,
        *,
        expected_version_row: int,
    ) -> None:
        session = self._session
        current = session.execute(
            select(ResourceVersion.row_version).where(
                ResourceVersion.public_id == version_public_id
            )
        ).scalar_one_or_none()
        if current != expected_version_row:
            raise PreconditionFailedError(
                code=ErrorCode.ROW_VERSION_MISMATCH, message="version row changed"
            )
        version_id = self._version_id(version_public_id)
        session.execute(delete(ResourceTag).where(ResourceTag.resource_version_id == version_id))
        session.execute(delete(ResourceCategory).where(ResourceCategory.resource_version_id == version_id))
        session.execute(delete(ResourceRuntime).where(ResourceRuntime.resource_version_id == version_id))
        session.execute(delete(ResourceDependency).where(ResourceDependency.resource_version_id == version_id))

        for tag in snapshot.tags:
            session.add(
                ResourceTag(
                    resource_version_id=version_id,
                    tag=tag.value,
                    normalized_tag=tag.normalized,
                    ordinal=tag.ordinal,
                )
            )
        for category in snapshot.categories:
            session.add(
                ResourceCategory(
                    resource_version_id=version_id,
                    category=category.value,
                    normalized_category=category.normalized,
                    ordinal=category.ordinal,
                )
            )
        for runtime in snapshot.runtimes:
            session.add(
                ResourceRuntime(
                    resource_version_id=version_id,
                    runtime=runtime.runtime,
                    ordinal=runtime.ordinal,
                    validation_status=runtime.validation_status,
                )
            )
        for dependency in snapshot.dependencies:
            session.add(
                ResourceDependency(
                    resource_version_id=version_id,
                    ordinal=dependency.ordinal,
                    dependency_kind=dependency.dependency_kind,
                    dependency_slug=dependency.dependency_slug,
                    version_constraint=dependency.version_constraint,
                    source=dependency.source,
                    is_optional=dependency.is_optional,
                    purpose=dependency.purpose,
                    resolved_resource_version_id=self._resolve_version_id(
                        dependency.resolved_version_public_id
                    ),
                    external_locator=dependency.external_locator,
                    external_digest=dependency.external_digest,
                    resolution_status=dependency.resolution_status,
                    resolution_error_code=dependency.resolution_error_code,
                )
            )

    def _resolve_version_id(self, public_id: str | None) -> int | None:
        if public_id is None:
            return None
        return self._version_id(public_id)

    def get_snapshot(self, version_public_id: str) -> MetadataSnapshot:
        version_id = self._version_id(version_public_id)
        tags = tuple(
            TagRecord(value=t.tag, normalized=t.normalized_tag, ordinal=t.ordinal)
            for t in self._session.execute(
                select(ResourceTag).where(ResourceTag.resource_version_id == version_id).order_by(ResourceTag.ordinal)
            ).scalars()
        )
        categories = tuple(
            CategoryRecord(value=c.category, normalized=c.normalized_category, ordinal=c.ordinal)
            for c in self._session.execute(
                select(ResourceCategory).where(ResourceCategory.resource_version_id == version_id).order_by(ResourceCategory.ordinal)
            ).scalars()
        )
        runtimes = tuple(
            RuntimeRecord(
                runtime=r.runtime,
                ordinal=r.ordinal,
                validation_status=r.validation_status,
                row_version=r.row_version,
                adapter_version=r.adapter_version,
                validated_artifact_hash=ArtifactHash(r.validated_artifact_hash) if r.validated_artifact_hash else None,
                validation_evidence_ref=r.validation_evidence_ref,
                validated_at=r.validated_at,
            )
            for r in self._session.execute(
                select(ResourceRuntime).where(ResourceRuntime.resource_version_id == version_id).order_by(ResourceRuntime.ordinal)
            ).scalars()
        )
        dependencies = tuple(
            self._dependency_record(d)
            for d in self._session.execute(
                select(ResourceDependency).where(ResourceDependency.resource_version_id == version_id).order_by(ResourceDependency.ordinal)
            ).scalars()
        )
        return MetadataSnapshot(
            tags=tags, categories=categories, runtimes=runtimes, dependencies=dependencies
        )

    def _dependency_record(self, d: ResourceDependency) -> DependencyRecord:
        resolved_public_id = self._replacement_public_id(d.resolved_resource_version_id)
        return DependencyRecord(
            dependency_kind=d.dependency_kind,
            dependency_slug=d.dependency_slug,
            version_constraint=d.version_constraint,
            source=d.source,
            ordinal=d.ordinal,
            is_optional=d.is_optional,
            resolution_status=d.resolution_status,
            row_version=d.row_version,
            purpose=d.purpose,
            resolved_version_public_id=resolved_public_id,
            external_locator=d.external_locator,
            external_digest=d.external_digest,
            resolution_error_code=d.resolution_error_code,
        )

    def _replacement_public_id(self, internal_id: int | None) -> str | None:
        if internal_id is None:
            return None
        return self._session.execute(
            select(ResourceVersion.public_id).where(ResourceVersion.id == internal_id)
        ).scalar_one_or_none()

    def list_reverse_dependencies(self, version_public_id: str) -> tuple[DependencyRecord, ...]:
        version_id = self._version_id(version_public_id)
        rows = self._session.execute(
            select(ResourceDependency).where(
                ResourceDependency.resolved_resource_version_id == version_id
            )
        ).scalars()
        return tuple(self._dependency_record(d) for d in rows)

    def get_index_status(
        self, version_public_id: str, index_name: str, *, for_update: bool = False
    ) -> IndexStatusRecord | None:
        version_id = self._version_id(version_public_id)
        stmt = select(ResourceIndexStatus).where(
            ResourceIndexStatus.resource_version_id == version_id,
            ResourceIndexStatus.index_name == index_name,
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return IndexStatusRecord(
            version_public_id=version_public_id,
            index_name=row.index_name,
            index_kind=row.index_kind,
            status=row.status,
            source_artifact_hash=ArtifactHash(row.source_artifact_hash),
            attempt_count=row.attempt_count,
            row_version=row.row_version,
            index_revision=row.index_revision,
            last_error_code=row.last_error_code,
            last_error_message=row.last_error_message,
            last_job_public_id=None,
            indexed_at=row.indexed_at,
            next_attempt_at=row.next_attempt_at,
        )

    def save_index_status(
        self, status: IndexStatusRecord, *, expected_row_version: int | None
    ) -> IndexStatusRecord:
        session = self._session
        version_id = self._version_id(status.version_public_id)
        row = session.execute(
            select(ResourceIndexStatus).where(
                ResourceIndexStatus.resource_version_id == version_id,
                ResourceIndexStatus.index_name == status.index_name,
            )
        ).scalar_one_or_none()
        if row is None:
            row = ResourceIndexStatus(
                resource_version_id=version_id,
                index_name=status.index_name,
                index_kind=status.index_kind,
                status=status.status,
                source_artifact_hash=status.source_artifact_hash,
            )
            session.add(row)
        else:
            if expected_row_version is not None and row.row_version != expected_row_version:
                raise PreconditionFailedError(
                    code=ErrorCode.ROW_VERSION_MISMATCH, message="index status changed"
                )
            row.status = status.status
            row.source_artifact_hash = status.source_artifact_hash
            row.attempt_count = status.attempt_count
            row.index_revision = status.index_revision
            row.last_error_code = status.last_error_code
            row.last_error_message = status.last_error_message
            row.indexed_at = status.indexed_at
            row.next_attempt_at = status.next_attempt_at
        session.flush()
        session.refresh(row)
        return IndexStatusRecord(
            version_public_id=status.version_public_id,
            index_name=row.index_name,
            index_kind=row.index_kind,
            status=row.status,
            source_artifact_hash=ArtifactHash(row.source_artifact_hash),
            attempt_count=row.attempt_count,
            row_version=row.row_version,
            index_revision=row.index_revision,
            last_error_code=row.last_error_code,
            last_error_message=row.last_error_message,
            last_job_public_id=None,
            indexed_at=row.indexed_at,
            next_attempt_at=row.next_attempt_at,
        )

    def append_usage(self, usage: UsageRecord) -> UsageRecord:
        session = self._session
        version_id = self._version_id(usage.version_public_id)
        model = ResourceUsage(
            public_id=usage.public_id,
            resource_version_id=version_id,
            actor_id=usage.actor_id,
            action=usage.action,
            runtime=usage.runtime,
            status=usage.status,
            request_id=usage.request_id,
            correlation_id=usage.correlation_id,
            metrics_json={},
            error_code=usage.error_code,
            started_at=usage.started_at,
            finished_at=usage.finished_at,
        )
        session.add(model)
        session.flush()
        session.refresh(model)
        return usage
