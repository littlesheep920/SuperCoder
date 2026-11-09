"""Duplicate-resource detection: deterministic, dependency-free, and explained.

PRD §1.2 lists "重复建设" (the same capability built again by another team) among the problems the
platform exists to solve. This module judges one pair of resources and says why. It is lexical on
purpose: it runs without a model or a network, so the same registry always yields the same verdicts
and an evaluation can be reproduced exactly. Its limits follow from that (docs/STAGE5_ACCEPTANCE_REPORT.md).

A pair goes through three rules, in this order:

1. **Reuse is not duplication.** When one resource lists the other in `dependencies`, or a workflow
   runs the other as a node, the pair is at most `related`, however similar the text: composing a
   capability is the opposite of rebuilding it.
2. **Same description and same entry body** (after whitespace and case normalization, ignoring the
   entry's title line) is an `exact_duplicate`.
3. Otherwise a weighted score of three similarities decides between `near_duplicate`, `related` and
   `not_duplicate`: the TF-IDF cosine of the purpose text (name and description), the TF-IDF cosine
   of the entry body, and the IDF-weighted overlap of the tags. Before any of that, template
   segments are removed (below). A text-evidence floor applies: unless one of the two text
   similarities reaches `TEXT_FLOOR`, the pair cannot be a duplicate, so shared tags alone never
   make one.

**Template segments.** Example corpora and real registries alike repeat boilerplate ("Report only
what the input supports", a stock docstring line). A sentence of prose, or a line of code, that
appears verbatim in at least `TEMPLATE_MIN_RESOURCES` resources of the registry carries no
information about what a resource does, so it is dropped before weighting. The threshold is a
count over the registry being judged, not a hand-written list, so it adapts to the corpus.

Every verdict carries its component scores, the terms that contributed most to the overlap, and the
rules that fired, so a reviewer can check it rather than trust it. The thresholds below were chosen
on the development split only (`scripts/run_duplicate_eval.py --tune`).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from apps.api.search.tokenizer import tokenize

__all__ = [
    "DUPLICATE_THRESHOLD",
    "RELATED_THRESHOLD",
    "TEMPLATE_MIN_RESOURCES",
    "TEXT_FLOOR",
    "WEIGHTS",
    "DuplicateDetector",
    "DuplicateVerdict",
    "ResourceText",
    "classify",
]

TEMPLATE_MIN_RESOURCES = 5
# Chosen by `python scripts/run_duplicate_eval.py --tune` on the dev split of dataset 0.2.0 (1950
# configurations; selection rule in the script). The test split played no part. RELATED_THRESHOLD
# only separates `related` from `not_duplicate` and does not affect duplicate F1; it is not tuned.
WEIGHTS = {"purpose": 0.3, "body": 0.5, "tags": 0.2}
DUPLICATE_THRESHOLD = 0.36
RELATED_THRESHOLD = 0.12
TEXT_FLOOR = 0.30

DUPLICATE_LABELS = frozenset({"exact_duplicate", "near_duplicate"})
_SENTENCE_END = re.compile(r"(?<=[.!?。！？；;])\s+|(?<=[。！？；])")
_LINE_MARKER = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s+)?")
_TITLE = re.compile(r"^#\s+\S")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class ResourceText:
    """What the detector reads from one resource version."""

    kind: str
    slug: str
    name: str
    description: str
    entry: str
    tags: tuple[str, ...] = ()
    dependencies: frozenset[tuple[str, str]] = frozenset()
    workflow_nodes: frozenset[tuple[str, str]] = frozenset()

    @property
    def ref(self) -> tuple[str, str]:
        return (self.kind, self.slug)

    @classmethod
    def from_manifest(cls, manifest: dict, entry: str) -> "ResourceText":
        dependencies = frozenset(
            (item.get("kind", ""), item.get("slug", "")) for item in manifest.get("dependencies") or []
            if isinstance(item, dict)
        )
        nodes = frozenset(
            (node["resource"].get("kind", ""), node["resource"].get("slug", ""))
            for node in (manifest.get("workflow") or {}).get("nodes") or []
            if isinstance(node, dict) and isinstance(node.get("resource"), dict)
        )
        return cls(
            kind=manifest["kind"], slug=manifest["slug"], name=manifest.get("name", ""),
            description=manifest.get("description", ""), entry=entry, tags=tuple(manifest.get("tags") or ()),
            dependencies=dependencies, workflow_nodes=nodes,
        )


@dataclass(frozen=True)
class DuplicateVerdict:
    label: str
    score: float
    components: dict[str, float]
    shared_terms: tuple[str, ...]
    rules: tuple[str, ...]

    @property
    def is_duplicate(self) -> bool:
        return self.label in DUPLICATE_LABELS


def _normalize(text: str) -> str:
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", text)).strip().casefold()


def segments(text: str) -> list[str]:
    """Sentences of prose and lines of code, normalized, in order."""

    found: list[str] = []
    for line in text.splitlines():
        stripped = _LINE_MARKER.sub("", line).strip()
        if not stripped:
            continue
        for piece in _SENTENCE_END.split(stripped):
            normalized = _normalize(piece)
            if normalized:
                found.append(normalized)
    return found


def _body_without_title(entry: str) -> str:
    return "\n".join(line for line in entry.splitlines() if not _TITLE.match(line))


@dataclass
class _Prepared:
    purpose: dict[str, float] = field(default_factory=dict)
    body: dict[str, float] = field(default_factory=dict)
    tags: frozenset[str] = frozenset()
    exact_key: tuple[str, str] = ("", "")


class DuplicateDetector:
    """Judges pairs of resources against the statistics of one registry."""

    def __init__(self, registry: list[ResourceText], *, weights: dict[str, float] | None = None,
                 duplicate_threshold: float = DUPLICATE_THRESHOLD, related_threshold: float = RELATED_THRESHOLD,
                 text_floor: float = TEXT_FLOOR, template_min_resources: int = TEMPLATE_MIN_RESOURCES) -> None:
        self.weights = dict(weights or WEIGHTS)
        self.duplicate_threshold = duplicate_threshold
        self.related_threshold = related_threshold
        self.text_floor = text_floor
        seen_in: Counter[str] = Counter()
        for resource in registry:
            seen_in.update(set(segments(resource.description)) | set(segments(_body_without_title(resource.entry))))
        self.templates = frozenset(segment for segment, count in seen_in.items() if count >= template_min_resources)
        self._purpose_idf = self._idf([self._purpose_tokens(resource) for resource in registry])
        self._body_idf = self._idf([self._body_tokens(resource) for resource in registry])
        self._tag_idf = self._idf([list(set(resource.tags)) for resource in registry])
        self._prepared: dict[tuple[str, str], _Prepared] = {}

    # -- text ----------------------------------------------------------------------------

    def _strip(self, text: str) -> str:
        return " ".join(segment for segment in segments(text) if segment not in self.templates)

    def _purpose_tokens(self, resource: ResourceText) -> list[str]:
        return tokenize(resource.name + " " + self._strip(resource.description))

    def _body_tokens(self, resource: ResourceText) -> list[str]:
        return tokenize(self._strip(_body_without_title(resource.entry)))

    @staticmethod
    def _idf(documents: list[list[str]]) -> dict[str, float]:
        frequency: Counter[str] = Counter()
        for tokens in documents:
            frequency.update(set(tokens))
        total = len(documents)
        return {term: math.log((total + 1) / (count + 1)) + 1.0 for term, count in frequency.items()}

    @staticmethod
    def _vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
        counts = Counter(tokens)
        default = max(idf.values(), default=1.0)
        weights = {term: (1.0 + math.log(count)) * idf.get(term, default) for term, count in counts.items()}
        norm = math.sqrt(sum(value * value for value in weights.values()))
        return {term: value / norm for term, value in weights.items()} if norm else {}

    def prepare(self, resource: ResourceText) -> _Prepared:
        cached = self._prepared.get(resource.ref)
        if cached is None:
            cached = _Prepared(
                purpose=self._vector(self._purpose_tokens(resource), self._purpose_idf),
                body=self._vector(self._body_tokens(resource), self._body_idf),
                tags=frozenset(resource.tags),
                exact_key=(_normalize(resource.description), _normalize(_body_without_title(resource.entry))),
            )
            self._prepared[resource.ref] = cached
        return cached

    # -- judging -------------------------------------------------------------------------

    @staticmethod
    def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
        if len(left) > len(right):
            left, right = right, left
        return sum(value * right.get(term, 0.0) for term, value in left.items())

    def _tag_overlap(self, left: frozenset[str], right: frozenset[str]) -> float:
        union = left | right
        if not union:
            return 0.0
        default = max(self._tag_idf.values(), default=1.0)
        # Sorted, so the float sum does not depend on set iteration order (PYTHONHASHSEED).
        weight = lambda tags: sum(self._tag_idf.get(tag, default) for tag in sorted(tags))  # noqa: E731
        return weight(left & right) / weight(union)

    def judge(self, left: ResourceText, right: ResourceText) -> DuplicateVerdict:
        a, b = self.prepare(left), self.prepare(right)
        purpose = self._cosine(a.purpose, b.purpose)
        body = self._cosine(a.body, b.body)
        tags = self._tag_overlap(a.tags, b.tags)
        shared = sorted(
            (term for term in a.purpose.keys() & b.purpose.keys() | a.body.keys() & b.body.keys()),
            # Ties broken by the term itself: set order changes with PYTHONHASHSEED, verdicts must not.
            key=lambda term: (-(min(a.purpose.get(term, 0), b.purpose.get(term, 0)) + min(a.body.get(term, 0), b.body.get(term, 0))), term),
        )[:8]

        reuse = right.ref in left.dependencies | left.workflow_nodes or left.ref in right.dependencies | right.workflow_nodes
        exact = a.exact_key == b.exact_key and all(a.exact_key)
        label, score, rules = classify(
            purpose, body, tags, exact=exact, reuse=reuse, weights=self.weights,
            duplicate_threshold=self.duplicate_threshold, related_threshold=self.related_threshold,
            text_floor=self.text_floor,
        )
        components = {"purpose": round(purpose, 4), "body": round(body, 4), "tags": round(tags, 4)}
        return DuplicateVerdict(label=label, score=round(score, 4), components=components,
                                shared_terms=tuple(shared), rules=tuple(rules))


def classify(purpose: float, body: float, tags: float, *, exact: bool, reuse: bool, weights: dict[str, float],
             duplicate_threshold: float, related_threshold: float, text_floor: float) -> tuple[str, float, tuple[str, ...]]:
    """The three rules applied to already-computed similarities. Shared by `judge` and the tuner."""

    score = weights["purpose"] * purpose + weights["body"] * body + weights["tags"] * tags
    rules: list[str] = []
    if exact:
        label = "exact_duplicate"
        rules.append("same-description-and-body")
    elif max(purpose, body) < text_floor:
        label = "related" if score >= related_threshold else "not_duplicate"
        rules.append("below-text-floor")
    elif score >= duplicate_threshold:
        label = "near_duplicate"
        rules.append("weighted-similarity")
    else:
        label = "related" if score >= related_threshold else "not_duplicate"
    if reuse and label in DUPLICATE_LABELS:
        label = "related"
        rules.append("reuse-is-not-duplication")
    elif reuse:
        rules.append("reuse")
    return label, round(score, 4), tuple(rules)
