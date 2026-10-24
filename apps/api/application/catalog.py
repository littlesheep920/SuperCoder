"""Taxonomy, dependency, runtime, and compatibility queries."""

from __future__ import annotations

from ..domain.errors import NotFoundError
from ..domain.models import ActorIdentity
from ..repositories.contracts import (
    DependencyRecord,
    MetadataSnapshot,
    UnitOfWorkFactory,
    VisibilityScope,
)

__all__ = ["CatalogService"]


class CatalogService:
    """Reads metadata projections, dependencies, and runtime compatibility."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def get_metadata(self, version_public_id: str) -> MetadataSnapshot:
        with self._uow_factory() as uow:
            snapshot = uow.metadata.get_snapshot(version_public_id)
            uow.commit()
            return snapshot

    def get_dependencies(self, version_public_id: str) -> tuple[DependencyRecord, ...]:
        return self.get_metadata(version_public_id).dependencies

    def list_reverse_dependencies(self, version_public_id: str) -> tuple[DependencyRecord, ...]:
        with self._uow_factory() as uow:
            records = uow.metadata.list_reverse_dependencies(version_public_id)
            uow.commit()
            return records

    def is_runtime_declared(self, version_public_id: str, runtime: str) -> bool:
        snapshot = self.get_metadata(version_public_id)
        return any(item.runtime == runtime for item in snapshot.runtimes)
