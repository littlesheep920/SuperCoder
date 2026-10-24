"""Reciprocal Rank Fusion of multiple ranked lists.

RRF combines rankings without comparing their raw scores, which is essential
here: a BM25 score is an unbounded positive number while a cosine similarity is
bounded in ``[-1, 1]``. Adding them directly would let BM25 dominate purely
because its scale is larger.

    RRF(d) = Σ_i  weight_i / (k + rank_i(d))

where ``rank_i`` is 1-based and a document missing from list ``i`` contributes
nothing. ``k`` dampens the influence of top ranks; 60 is the value from the
original RRF paper and the default here.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RRF_K", "RankedList", "fuse"]

RRF_K = 60


@dataclass(frozen=True, slots=True)
class RankedList:
    """One input ranking: ordered keys (best first) plus a source weight."""

    name: str
    keys: tuple[str, ...]
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class FusedResult:
    key: str
    score: float
    contributions: dict[str, float]


def fuse(lists: list[RankedList], *, k: int = RRF_K) -> list[FusedResult]:
    """Fuse ranked lists into one ordering.

    Keys are document identifiers chosen by the caller (``IndexDocument.index_key``
    is the natural choice). Output is ordered by fused score descending, then by
    key ascending so equal scores resolve deterministically (acceptance B7).
    """

    if k < 0:
        raise ValueError("RRF k must be non-negative")

    scores: dict[str, float] = {}
    contributions: dict[str, dict[str, float]] = {}

    for ranked in lists:
        if ranked.weight <= 0:
            continue
        # A key repeated inside one list keeps only its best (first) rank;
        # otherwise a duplicated entry would contribute twice.
        seen: set[str] = set()
        for position, key in enumerate(ranked.keys, start=1):
            if key in seen:
                continue
            seen.add(key)
            value = ranked.weight / (k + position)
            scores[key] = scores.get(key, 0.0) + value
            contributions.setdefault(key, {})[ranked.name] = round(value, 9)

    ordered = sorted(scores, key=lambda key: (-scores[key], key))
    return [
        FusedResult(
            key=key,
            score=round(scores[key], 9),
            contributions=contributions.get(key, {}),
        )
        for key in ordered
    ]
