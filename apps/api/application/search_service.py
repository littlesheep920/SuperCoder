"""Search application service: build the index from authoritative facts.

The index is a **projection**. This service is the only place that knows how to
assemble it, and it does so from two authoritative inputs:

* relational facts (identity, lifecycle, version, artifact hash) from MySQL;
* Git content (manifest body, entrypoint) via the manifest provider.

Because the projection is rebuildable, the service can be reconstructed freely;
no state here needs durability. That is exactly why the pipeline can run with no
external vector database (PRD §3.3).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from ..domain.models import LifecycleState
from ..providers.manifest_loader import ManifestLoader, ManifestLoadError
from ..repositories.contracts import (
    CursorPage,
    ResourceQuery,
    ResourceSort,
    ResourceWithVersion,
    SortDirection,
    UnitOfWorkFactory,
    VisibilityScope,
)
from ..search import IndexDocument, RankingProfile, SearchSort
from ..search.chunking import build_index_document, extract_type_spec
from ..search.providers import EmbeddingProvider, RerankProvider
from ..search.service import SearchFilters, SearchOutcome, SearchService

__all__ = ["SearchResult", "SearchServiceFactory"]

# Documents deeper than this are not fetched for one search request. The corpus
# is expected to stay well below it; the cap prevents an accidental full-table
# scan from becoming an unbounded in-process index.
MAX_INDEX_DOCUMENTS = 5000


@dataclass(frozen=True, slots=True)
class SearchResult:
    outcome: SearchOutcome
    documents_by_key: dict[str, IndexDocument] = field(default_factory=dict)
    visible_resources: dict[str, ResourceWithVersion] = field(default_factory=dict)


class SearchServiceFactory:
    """Creates a per-request search service bound to one visibility scope.

    A fresh :class:`SearchService` per request is deliberate. It means the
    authorized candidate set is always current, and an index can never outlive
    a permission change. When the corpus grows past the point where rebuilding
    per request is too slow, caching can be added *behind* this interface
    without changing any caller.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        manifest_loader: ManifestLoader,
        *,
        embedding: EmbeddingProvider | None = None,
        rerank: RerankProvider | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._manifest_loader = manifest_loader
        self._embedding = embedding
        self._rerank = rerank

    def search(
        self,
        *,
        scope: VisibilityScope,
        query: str,
        filters: SearchFilters,
        profile: RankingProfile,
        sort: SearchSort,
        limit: int,
    ) -> SearchResult:
        """Search within the caller-visible set only.

        Visibility filtering happens in the repository before this method sees
        anything, so no scorer can ever observe a document the caller may not
        read (B4, B10).
        """

        visible = self._load_visible(scope)
        documents = [self._build_document(item) for item in visible]
        by_key = {document.index_key: document for document in documents}

        service = SearchService(
            documents,
            embedding=self._embedding,
            rerank=self._rerank,
            index_revision=_index_revision(documents),
        )
        outcome = service.search(
            query,
            filters=filters,
            profile=profile,
            sort=sort,
            limit=limit,
        )
        return SearchResult(
            outcome=outcome,
            documents_by_key=by_key,
            visible_resources={
                item.version.public_id: item for item in visible
            },
        )

    def _load_visible(self, scope: VisibilityScope) -> list[ResourceWithVersion]:
        query = ResourceQuery(
            scope=scope,
            sort=ResourceSort.UPDATED_AT,
            direction=SortDirection.DESC,
            limit=MAX_INDEX_DOCUMENTS,
        )
        with self._uow_factory() as uow:
            page: CursorPage[ResourceWithVersion] = uow.resources.list_visible(query)
            uow.commit()
        return list(page.items)

    def _build_document(self, item: ResourceWithVersion) -> IndexDocument:
        """Project one relational row into an index document.

        Git content is optional: when the manifest cannot be read (missing file,
        unreadable repository) the document still indexes the governance facts,
        because those alone are already authoritative and searchable. The
        content hashes always come from the relational row, never from the file
        read, so a document can never claim content it did not come from.
        """

        version = item.version
        resource = item.resource
        files: dict[str, str] = {}
        type_spec: dict[str, object] = {}
        try:
            loaded = self._manifest_loader.load(resource.kind, resource.slug)
            files = dict(loaded.files)
            type_spec = extract_type_spec(resource.kind.value, loaded.manifest)
        except (ManifestLoadError, OSError, ValueError):
            # Degrade to manifest-only indexing rather than dropping the row.
            files = {}
            type_spec = extract_type_spec(resource.kind.value, version.manifest or {})

        return build_index_document(
            resource_public_id=resource.public_id,
            version_public_id=version.public_id,
            kind=resource.kind.value,
            slug=resource.slug,
            name=version.name,
            version=version.identifier.raw,
            status=version.status.value,
            owner_team=resource.owner_team,
            description=version.description,
            manifest=dict(version.manifest or {}),
            type_spec=type_spec,
            files=files,
            artifact_hash=str(version.artifact_hash),
            manifest_snapshot_hash=str(version.manifest_snapshot_hash),
            updated_at_epoch=version.updated_at.timestamp() if version.updated_at else 0.0,
        )


def _index_revision(documents: list[IndexDocument]) -> str:
    """Deterministic identifier for the exact index content used by a query.

    Two searches over identical content produce the same revision, so a stored
    evaluation result can be replayed against the same projection (B7).
    """

    digest = hashlib.sha256()
    for document in sorted(documents, key=lambda item: item.index_key):
        digest.update(document.index_key.encode("utf-8"))
        digest.update(document.manifest_snapshot_hash.encode("utf-8"))
    return digest.hexdigest()[:16]
