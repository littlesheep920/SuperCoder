"""Permission-filtered resource listing and detail queries."""

from __future__ import annotations

from ..domain.models import ActorIdentity
from ..repositories.contracts import (
    CursorPage,
    ResourceQuery,
    ResourceWithVersion,
    UnitOfWorkFactory,
)

__all__ = ["ResourceQueryService"]


class ResourceQueryService:
    """Lists resources after authorization filtering in the repository layer."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def list(self, query: ResourceQuery) -> CursorPage[ResourceWithVersion]:
        with self._uow_factory() as uow:
            page = uow.resources.list_visible(query)
            uow.commit()
            return page
