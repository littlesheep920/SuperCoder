"""Snapshot construction from loaded Git content into governance projections."""

from __future__ import annotations

from collections.abc import Callable

from ..domain.dependencies import resolve_effective_source
from ..domain.models import ResourceKind
from ..domain.permissions import PermissionDeclaration
from ..domain.validation import normalize_category, normalize_tag
from ..providers.manifest_loader import LoadedResource, ManifestLoader
from ..repositories.contracts import (
    CategoryRecord,
    DependencyRecord,
    MetadataSnapshot,
    RuntimeRecord,
    TagRecord,
)

__all__ = ["SnapshotService"]

Clock = Callable[[], object]


class SnapshotService:
    """Builds hash-bound metadata/permission projections from loaded content."""

    def __init__(self, manifest_loader: ManifestLoader) -> None:
        self._loader = manifest_loader

    def load(self, kind: ResourceKind, slug: str) -> LoadedResource:
        return self._loader.load(kind, slug)

    def load_snapshot(
        self,
        kind: ResourceKind,
        slug: str,
        manifest: dict[str, object],
        files: dict[str, str],
    ) -> LoadedResource:
        return self._loader.load_snapshot(kind, slug, manifest, files)

    def build_metadata(self, loaded: LoadedResource) -> MetadataSnapshot:
        tags = tuple(
            TagRecord(value=tag, normalized=normalize_tag(tag), ordinal=index)
            for index, tag in enumerate(loaded.tags, start=1)
        )
        categories = tuple(
            CategoryRecord(
                value=category, normalized=normalize_category(category), ordinal=index
            )
            for index, category in enumerate(loaded.categories, start=1)
        )
        runtimes = tuple(
            RuntimeRecord(
                runtime=runtime, ordinal=index, validation_status="unvalidated", row_version=1
            )
            for index, runtime in enumerate(loaded.runtimes, start=1)
        )
        dependencies = tuple(
            self._dependency_record(item, index)
            for index, item in enumerate(loaded.dependencies, start=1)
        )
        return MetadataSnapshot(
            tags=tags, categories=categories, runtimes=runtimes, dependencies=dependencies
        )

    def build_permissions(self, loaded: LoadedResource) -> tuple[PermissionDeclaration, ...]:
        return loaded.permissions

    @staticmethod
    def _dependency_record(item: dict[str, object], ordinal: int) -> DependencyRecord:
        kind = str(item["kind"])
        slug = str(item["slug"])
        version_constraint = str(item["version"])
        source = resolve_effective_source(kind, item.get("source"))
        return DependencyRecord(
            dependency_kind=kind,
            dependency_slug=slug,
            version_constraint=version_constraint,
            source=source,
            ordinal=ordinal,
            is_optional=bool(item.get("optional", False)),
            resolution_status="unresolved",
            row_version=1,
            purpose=item.get("purpose") if isinstance(item.get("purpose"), str) else None,
        )
