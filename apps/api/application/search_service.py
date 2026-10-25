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
import threading
from collections import OrderedDict
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
from ..search.vector import DocumentVectorCache

__all__ = ["DocumentCache", "SearchResult", "SearchServiceFactory"]

# Documents deeper than this are not fetched for one search request. The corpus
# is expected to stay well below it; the cap prevents an accidental full-table
# scan from becoming an unbounded in-process index.
MAX_INDEX_DOCUMENTS = 5000

# How many projected documents to keep between requests.
DEFAULT_DOCUMENT_CACHE_SIZE = 2000


class DocumentCache:
    """Reuse projected :class:`IndexDocument` objects across requests.

    Building a document reads the managed Git manifest and slices the body into
    chunks. That work depends only on **immutable inputs**: the version row and
    its content hash. It does not depend on who is asking.

    What is deliberately *not* cached is the authorized set. Visibility is
    re-derived from the repository on every request, so a permission change
    takes effect immediately; only the pure projection is reused.

    The key is the version id plus both content hashes. A version whose content
    changes gets a new hash and therefore a different key, so a stale document
    can never be served for edited content.
    """

    def __init__(self, capacity: int = DEFAULT_DOCUMENT_CACHE_SIZE) -> None:
        self._capacity = capacity
        self._entries: OrderedDict[str, IndexDocument] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key_for(item: ResourceWithVersion) -> str:
        return "|".join(
            (
                str(item.version.public_id),
                str(item.version.artifact_hash),
                str(item.version.manifest_snapshot_hash),
                item.version.updated_at.isoformat() if item.version.updated_at else "",
            )
        )

    def get(self, item: ResourceWithVersion) -> IndexDocument | None:
        key = self.key_for(item)
        with self._lock:
            if key not in self._entries:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return self._entries[key]

    def put(self, item: ResourceWithVersion, document: IndexDocument) -> None:
        if self._capacity == 0:
            return
        with self._lock:
            self._entries[self.key_for(item)] = document
            self._entries.move_to_end(self.key_for(item))
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)

    def stats(self) -> dict[str, float | int]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "capacity": self._capacity,
                "size": len(self._entries),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 4) if total else 0.0,
            }

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.hits = 0
            self.misses = 0


@dataclass(frozen=True, slots=True)
class SearchResult:
    outcome: SearchOutcome
    documents_by_key: dict[str, IndexDocument] = field(default_factory=dict)
    visible_resources: dict[str, ResourceWithVersion] = field(default_factory=dict)


class SearchServiceFactory:
    """Creates a per-request search service bound to one visibility scope.

    A fresh :class:`SearchService` per request is deliberate: the authorized
    candidate set must always be current, and no in-memory structure may outlive
    a permission change.

    Projection caching sits *behind* that guarantee rather than replacing it.
    The repository still decides visibility on every request; what is reused is
    the pure function ``(version row, content hash) -> IndexDocument``, which
    reads Git and chunks text and is identical for every caller. Measured cost
    of that function was ~933 ms per request on the Stage 2 corpus, almost all
    of it a remote embedding call for the document vectors.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        manifest_loader: ManifestLoader,
        *,
        embedding: EmbeddingProvider | None = None,
        rerank: RerankProvider | None = None,
        document_cache: DocumentCache | None = None,
        document_vectors: DocumentVectorCache | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._manifest_loader = manifest_loader
        self._embedding = embedding
        self._rerank = rerank
        # ``None`` keeps the pre-cache behaviour (rebuild every request) for
        # callers that want it, including most unit tests.
        self._document_cache = document_cache
        self._document_vectors = document_vectors

    @property
    def document_cache_stats(self) -> dict[str, float | int]:
        return self._document_cache.stats() if self._document_cache is not None else {}

    def warm_connections(self) -> dict[str, bool]:
        """Open provider connections before the first query arrives.

        Without this, the first search of a process absorbs both TLS handshakes
        and measured 1664 ms while every subsequent search took ~230 ms. This is
        a warm-up, never a gate: failures are reported back but do not raise, so
        a provider outage degrades search instead of preventing the API from
        serving.
        """

        results: dict[str, bool] = {}
        for name, provider in (("embedding", self._embedding), ("rerank", self._rerank)):
            warmer = getattr(provider, "warm_connection", None)
            if warmer is None:
                results[name] = False
                continue
            try:
                results[name] = bool(warmer())
            except Exception:  # noqa: BLE001 - warm-up must never break startup
                results[name] = False
        return results

    def _documents_for(self, visible: list[ResourceWithVersion]) -> list[IndexDocument]:
        """Project each visible row, reusing cached projections when unchanged."""

        if self._document_cache is None:
            return [self._build_document(item) for item in visible]
        documents: list[IndexDocument] = []
        for item in visible:
            cached = self._document_cache.get(item)
            if cached is not None:
                documents.append(cached)
                continue
            document = self._build_document(item)
            self._document_cache.put(item, document)
            documents.append(document)
        return documents

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
        documents = self._documents_for(visible)
        by_key = {document.index_key: document for document in documents}

        service = SearchService(
            documents,
            embedding=self._embedding,
            rerank=self._rerank,
            index_revision=_index_revision(documents),
            document_vectors=self._document_vectors,
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
