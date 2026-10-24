"""BM25 keyword scoring over the typed field structure of index documents.

The implementation follows Okapi BM25 with field weights applied multiplicatively.
Two deliberate choices:

* **Field-level aggregation.** A document's score is the weighted sum of the
  per-field scores, not one score over concatenated text. This is what makes a
  ``name`` hit outrank a ``body`` hit and what lets the API report
  ``matched_fields`` truthfully.
* **Deterministic ties.** Ties are broken by resource id, so results are stable
  across processes and runs (acceptance item B7).

The index is rebuilt from a full document list. At the current scale (hundreds
of documents) that is far cheaper than maintaining incremental structures, and
it guarantees the index can never drift from its inputs.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from . import IndexDocument, IndexField
from .chunking import FIELD_WEIGHTS
from .tokenizer import tokenize

__all__ = ["BM25Index", "BM25Params", "ScoredDocument"]

# Standard Okapi defaults. ``b`` controls length normalisation; with short
# resource descriptions a moderate value avoids over-penalising longer entries.
BM25_K1 = 1.5
BM25_B = 0.75


@dataclass(frozen=True, slots=True)
class BM25Params:
    k1: float = BM25_K1
    b: float = BM25_B


@dataclass(frozen=True, slots=True)
class ScoredDocument:
    """One BM25 hit with the evidence needed for ``score_components``."""

    document: IndexDocument
    score: float
    matched_fields: tuple[str, ...]
    field_scores: dict[str, float]


class BM25Index:
    """An immutable BM25 index over a fixed set of documents.

    Build once per index revision, then query many times. ``search`` is pure:
    it never mutates the index, so concurrent reads are safe.
    """

    def __init__(self, documents: list[IndexDocument], params: BM25Params | None = None) -> None:
        self._params = params or BM25Params()
        self._documents = list(documents)
        # Per-field postings: field -> term -> {doc_index: term_frequency}
        self._postings: dict[IndexField, dict[str, dict[int, int]]] = {
            field: {} for field in IndexField
        }
        # Per-field document length and statistics.
        self._field_lengths: dict[IndexField, dict[int, int]] = {
            field: {} for field in IndexField
        }
        self._field_total_length: dict[IndexField, int] = {field: 0 for field in IndexField}
        self._field_doc_count: dict[IndexField, int] = {field: 0 for field in IndexField}
        self._index()

    def _index(self) -> None:
        for doc_index, document in enumerate(self._documents):
            for field in IndexField:
                text = document.field_text(field)
                if not text:
                    continue
                tokens = tokenize(text)
                if not tokens:
                    continue
                counts = Counter(tokens)
                self._field_lengths[field][doc_index] = len(tokens)
                self._field_total_length[field] += len(tokens)
                self._field_doc_count[field] += 1
                for term, frequency in counts.items():
                    self._postings[field].setdefault(term, {})[doc_index] = frequency

    @property
    def document_count(self) -> int:
        return len(self._documents)

    @property
    def documents(self) -> tuple[IndexDocument, ...]:
        return tuple(self._documents)

    def _idf(self, field: IndexField, term: str) -> float:
        """Inverse document frequency with the standard +1 smoothing.

        Using the field's own document count keeps a term that appears in every
        name from dominating merely because names are short.
        """

        doc_count = self._field_doc_count[field]
        if doc_count == 0:
            return 0.0
        postings = self._postings[field].get(term)
        df = len(postings) if postings else 0
        return math.log(1.0 + (doc_count - df + 0.5) / (df + 0.5))

    def _avg_length(self, field: IndexField) -> float:
        count = self._field_doc_count[field]
        if count == 0:
            return 0.0
        return self._field_total_length[field] / count

    def search(self, query: str, *, limit: int | None = None) -> list[ScoredDocument]:
        """Score every document against *query*.

        Documents with a score of zero are omitted. Results are ordered by score
        descending, then by resource id ascending for a deterministic tie-break.
        """

        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        # Unique query terms: a repeated query term must not multiply its own
        # contribution, which would let one repeated word dominate the ranking.
        unique_terms = list(dict.fromkeys(query_tokens))
        params = self._params

        scores: dict[int, float] = {}
        matched: dict[int, set[str]] = {}
        per_field: dict[int, dict[str, float]] = {}

        for field in IndexField:
            weight = FIELD_WEIGHTS[field]
            avg_length = self._avg_length(field)
            if avg_length == 0:
                continue
            for term in unique_terms:
                postings = self._postings[field].get(term)
                if not postings:
                    continue
                idf = self._idf(field, term)
                for doc_index, frequency in postings.items():
                    length = self._field_lengths[field].get(doc_index, 0)
                    norm = 1.0 - params.b + params.b * (length / avg_length)
                    contribution = idf * (
                        frequency * (params.k1 + 1.0)
                    ) / (frequency + params.k1 * norm)
                    weighted = contribution * weight
                    scores[doc_index] = scores.get(doc_index, 0.0) + weighted
                    matched.setdefault(doc_index, set()).add(field.value)
                    field_map = per_field.setdefault(doc_index, {})
                    field_map[field.value] = field_map.get(field.value, 0.0) + weighted

        ordered = sorted(
            (index for index, score in scores.items() if score > 0.0),
            key=lambda index: (-scores[index], self._documents[index].resource_public_id),
        )
        if limit is not None:
            ordered = ordered[:limit]

        return [
            ScoredDocument(
                document=self._documents[index],
                score=round(scores[index], 6),
                matched_fields=tuple(sorted(matched[index])),
                field_scores={key: round(value, 6) for key, value in per_field[index].items()},
            )
            for index in ordered
        ]
