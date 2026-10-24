"""Exact version reads and permission-filtered listing."""

from __future__ import annotations

from ..domain.errors import NotFoundError
from ..domain.models import ActorIdentity, ArtifactHash
from ..repositories.contracts import (
    CursorPage,
    UnitOfWorkFactory,
    VersionRecord,
    VisibilityScope,
)

__all__ = ["VersionQueryService"]


class VersionQueryService:
    """Queries exact versions and version lists within the caller's scope."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def get(self, actor: ActorIdentity, version_public_id: str, scope: VisibilityScope) -> VersionRecord:
        with self._uow_factory() as uow:
            record = uow.versions.get(version_public_id)
            uow.commit()
            if record is None or not self._visible(record, scope):
                raise NotFoundError("version not found or not visible")
            return record

    def list_for_resource(
        self,
        resource_public_id: str,
        *,
        scope: VisibilityScope,
        cursor: str | None = None,
        limit: int = 50,
    ) -> CursorPage[VersionRecord]:
        with self._uow_factory() as uow:
            page = uow.versions.list_for_resource(
                resource_public_id, scope=scope, cursor=cursor, limit=limit
            )
            uow.commit()
            return page

    def get_by_artifact_hash(
        self, artifact_hash: ArtifactHash, *, scope: VisibilityScope
    ) -> tuple[VersionRecord, ...]:
        with self._uow_factory() as uow:
            records = uow.versions.get_by_artifact_hash(artifact_hash, scope=scope)
            uow.commit()
            return records

    @staticmethod
    def _visible(record: VersionRecord, scope: VisibilityScope) -> bool:
        if scope.organization_member:
            return True
        if record.owner_team in scope.team_ids:
            return True
        return False
