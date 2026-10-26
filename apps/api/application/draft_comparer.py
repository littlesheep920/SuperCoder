"""Compare a draft against an existing resource of the same slug.

The lookup is deliberately narrow: it asks the repository for visible resources
whose text matches the slug, then confirms an exact slug match. A draft for a slug
the caller cannot see is treated as new rather than leaking the existence of an
invisible resource through a diff.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..domain.draft_diff import DraftDiff, diff_manifest
from ..repositories.contracts import ResourceQuery, UnitOfWorkFactory

__all__ = ["DraftComparer"]


@dataclass(frozen=True, slots=True)
class DraftComparison:
    diff: DraftDiff
    existing_resource_id: str | None
    existing_version: str | None
    existing_manifest: Mapping[str, Any] | None


class DraftComparer:
    """Find an existing resource with the draft's slug and diff against it."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def compare(
        self, *, draft: Mapping[str, Any], scope, limit: int = 20
    ) -> DraftComparison:
        slug = draft.get("slug")
        kind = draft.get("kind")

        if not isinstance(slug, str) or not isinstance(kind, str):
            return DraftComparison(diff_manifest(draft, None), None, None, None)

        try:
            with self._uow_factory() as uow:
                page = uow.resources.list_visible(
                    ResourceQuery(scope=scope, search_text=slug, limit=limit)
                )
                candidates = list(page.items)
                uow.commit()
        except Exception:
            # A missing database must not block drafting: without a base the diff
            # is simply reported as a new resource.
            return DraftComparison(diff_manifest(draft, None), None, None, None)

        match = next(
            (
                item
                for item in candidates
                if getattr(item.resource, "slug", None) == slug
                and getattr(item.resource, "kind", None) is not None
                and str(getattr(item.resource.kind, "value", item.resource.kind)) == kind
            ),
            None,
        )
        if match is None:
            return DraftComparison(diff_manifest(draft, None), None, None, None)

        existing_manifest = getattr(match.version, "manifest", None)
        if not isinstance(existing_manifest, Mapping):
            return DraftComparison(diff_manifest(draft, None), None, None, None)

        diff = diff_manifest(
            draft,
            existing_manifest,
            base_label=f"existing:{match.resource.public_id}",
            draft_label="draft",
        )
        return DraftComparison(
            diff=diff,
            existing_resource_id=str(match.resource.public_id),
            existing_version=str(getattr(match.version, "version", "")),
            existing_manifest=existing_manifest,
        )
