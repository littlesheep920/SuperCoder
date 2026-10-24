"""Search domain: index documents, typed chunks, and their value contracts.

The search subsystem is a **rebuildable projection**, never a source of truth.
Every :class:`IndexDocument` and every {@link IndexChunk} carries the exact
``artifact_hash`` and ``manifest_snapshot_hash`` of the resource version it was
built from, so a hit can always be traced back to immutable Git content plus
relational governance facts.

Nothing in this package may decide authorization, lifecycle, or approval.
Visibility filtering happens in the repository layer *before* any candidate
reaches the scorer, which is why the scorers here take an already-authorized
candidate set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "Chunk",
    "IndexDocument",
    "IndexField",
    "RankingProfile",
    "SearchCandidate",
    "SearchSort",
]


class IndexField(StrEnum):
    """Searchable field names, ordered by the frozen weight policy.

    Weights are defined in :mod:`apps.api.search.chunking` and applied by the
    BM25 scorer. The names match the ``matched_fields`` strings required by the
    frozen ``SearchHit`` schema.
    """

    NAME = "name"
    ALIASES = "aliases"
    TAGS = "tags"
    CATEGORIES = "categories"
    RUNTIMES = "runtimes"
    DESCRIPTION = "description"
    DEPENDENCIES = "dependencies"
    BODY = "body"


class RankingProfile(StrEnum):
    """Values accepted by the frozen ``ranking_profile`` field."""

    HYBRID = "hybrid"
    KEYWORD = "keyword"
    SEMANTIC = "semantic"


class SearchSort(StrEnum):
    """Values accepted by the frozen ``sort`` field."""

    RELEVANCE = "relevance"
    NEWEST = "newest"
    NAME = "name"


@dataclass(frozen=True, slots=True)
class Chunk:
    """One indexable unit of a resource version.

    ``section`` is a stable label (``overview``, ``tool:search_repos``, ...) so
    chunks can be diffed between index revisions. ``field`` records which
    searchable field the text belongs to, which drives both weighting and the
    ``matched_fields`` output.
    """

    chunk_id: str
    section: str
    field: IndexField
    text: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class IndexDocument:
    """A complete, immutable index entry for one resource version.

    Identity fields come from MySQL (authoritative governance facts); content
    fields come from the Git manifest and entrypoint files. The two hashes bind
    the document to exactly one state of each.
    """

    resource_public_id: str
    version_public_id: str
    kind: str
    slug: str
    name: str
    version: str
    status: str
    owner_team: str
    description: str
    aliases: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    runtimes: tuple[str, ...] = ()
    dependency_labels: tuple[str, ...] = ()
    permission_names: tuple[str, ...] = ()
    artifact_hash: str = ""
    manifest_snapshot_hash: str = ""
    chunks: tuple[Chunk, ...] = ()
    updated_at_epoch: float = 0.0

    @property
    def index_key(self) -> str:
        """Stable key for one version; changing bytes or status changes it."""

        return f"{self.kind}/{self.slug}@{self.version}#{self.artifact_hash}"

    def field_text(self, field: IndexField) -> str:
        """Return the concatenated text of one field across this document."""

        if field is IndexField.NAME:
            return self.name
        if field is IndexField.ALIASES:
            return " ".join(self.aliases)
        if field is IndexField.TAGS:
            return " ".join(self.tags)
        if field is IndexField.CATEGORIES:
            return " ".join(self.categories)
        if field is IndexField.RUNTIMES:
            return " ".join(self.runtimes)
        if field is IndexField.DESCRIPTION:
            return self.description
        if field is IndexField.DEPENDENCIES:
            return " ".join(self.dependency_labels)
        return " ".join(chunk.text for chunk in self.chunks)


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    """An authorized candidate handed to the scorers.

    The ``document`` is whatever the index currently holds. It is only a
    projection: before a hit is returned, the caller must re-read relational
    facts, which is why the document carries no authority of its own.
    """

    document: IndexDocument
    score_components: dict[str, float] = field(default_factory=dict)
