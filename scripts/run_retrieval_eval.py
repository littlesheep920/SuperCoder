"""Run the Stage 2 retrieval evaluation and archive raw per-query results.

Design rules this script obeys (PRD §7.1, §7.2; acceptance B7, B8):

* **Report, do not gate.** It prints metrics and exits 0 whether or not a
  quality target is met. A metric is evidence, not a pass/fail switch.
* **Archive the raw run.** Every query stores its ranked hits, the graded
  judgments, per-stage latency, and the index revision. Summary numbers alone
  are not reproducible.
* **Record provenance.** Dataset hash, code revision, provider/model name,
  configuration, and a seed are written next to the results, so a number can
  always be traced to the exact state that produced it.
* **Label the baseline honestly.** Offline the provider is a hashing baseline,
  not a semantic model; the report says so and never presents its numbers as
  semantic quality.

Usage:
    python scripts/run_retrieval_eval.py
    python scripts/run_retrieval_eval.py --profile keyword --out evals/results
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.config import get_settings  # noqa: E402
from apps.api.search import IndexDocument, RankingProfile  # noqa: E402
from apps.api.search.chunking import build_index_document, extract_type_spec  # noqa: E402
from apps.api.search.factory import (  # noqa: E402
    build_embedding_provider,
    build_rerank_provider,
)
from apps.api.search.service import SearchFilters, SearchService  # noqa: E402

QUERIES = ROOT / "evals" / "retrieval" / "stage2_queries.jsonl"
QRELS = ROOT / "evals" / "retrieval" / "stage2_qrels.jsonl"
RESOURCE_ROOT = ROOT / "resources"

# Fixed seed for reproducibility bookkeeping. Retrieval itself is deterministic;
# the value is recorded so a future stochastic stage cannot silently differ.
SEED = 0


@dataclass
class QueryResult:
    query_id: str
    query: str
    profile: str
    ranked_ids: list[str]
    relevant_ids: list[str]
    graded: dict[str, int]
    reached_at_k: int
    reciprocal_rank: float
    ndcg_at_10: float
    took_ms: int
    degraded: tuple[str, ...]


@dataclass
class Report:
    results: list[QueryResult] = field(default_factory=list)

    def metrics(self, k: int = 5) -> dict[str, float]:
        """Aggregate metrics over all queries.

        A query that returned nothing, or returned documents none of which are
        relevant, is a **miss**. Only a relevant document inside the top ``k``
        counts as a hit; a query with an empty result list can never be a hit.
        Getting this wrong is how a broken run reports Hit@5 = 1.0.
        """

        if not self.results:
            return {}
        count = len(self.results)
        hits = sum(
            1
            for r in self.results
            if r.ranked_ids and r.reached_at_k <= k and r.reached_at_k <= len(r.ranked_ids)
        )
        return {
            f"hit_at_{k}": round(hits / count, 4),
            "mrr_at_10": round(sum(r.reciprocal_rank for r in self.results) / count, 4),
            "ndcg_at_10": round(sum(r.ndcg_at_10 for r in self.results) / count, 4),
            "zero_result_queries": sum(1 for r in self.results if not r.ranked_ids),
            "no_relevant_retrieved": sum(
                1
                for r in self.results
                if not r.ranked_ids or r.reached_at_k > len(r.ranked_ids)
            ),
        }


def _load_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _load_documents() -> list[IndexDocument]:
    """Build index documents for every demo resource, without a database.

    The evaluation must be runnable in a bare clone, so it reads manifests from
    the managed Git root directly rather than requiring MySQL.
    """

    from apps.api.providers.git_repository import GitProvider
    from apps.api.providers.manifest_loader import ManifestLoader

    loader = ManifestLoader(GitProvider(str(RESOURCE_ROOT.parent), read_only=True))
    git = GitProvider(str(RESOURCE_ROOT.parent), read_only=True)
    documents: list[IndexDocument] = []
    for directory in git.list_resource_directories():
        loaded = loader.load(directory.kind, directory.slug)
        # The evaluation ID must match the judgment format in stage2_qrels.jsonl:
        # "<kind>/<slug>@<version>". Using the bare resource id here would make
        # every comparison miss while rankings looked perfectly reasonable.
        resource_id = f"{directory.kind.value}/{directory.slug}@{loaded.version.raw}"
        documents.append(
            build_index_document(
                resource_public_id=resource_id,
                version_public_id=resource_id,
                kind=directory.kind.value,
                slug=directory.slug,
                name=str(loaded.manifest.get("name", loaded.slug)),
                version=loaded.version.raw,
                status=str(loaded.manifest.get("status", "draft")),
                owner_team=str(loaded.manifest.get("owner_team", "")),
                description=str(loaded.manifest.get("description", "")),
                manifest=loaded.manifest,
                type_spec=extract_type_spec(directory.kind.value, loaded.manifest),
                files=dict(loaded.files),
                artifact_hash=str(loaded.artifact_hash),
                manifest_snapshot_hash=str(loaded.manifest_snapshot_hash),
            )
        )
    return documents


def _dcg(grades: list[int], k: int) -> float:
    total = 0.0
    for position, grade in enumerate(grades[:k], start=1):
        total += (2**grade - 1) / math.log2(position + 1)
    return total


def _ndcg(ranked_grades: list[int], ideal_grades: list[int], k: int = 10) -> float:
    ideal = _dcg(sorted(ideal_grades, reverse=True), k)
    if ideal == 0:
        return 0.0
    return _dcg(ranked_grades, k) / ideal


def run(profile: RankingProfile, out_dir: Path) -> int:
    settings = get_settings()
    queries = _load_jsonl(QUERIES)
    qrels = _load_jsonl(QRELS)

    graded: dict[str, dict[str, int]] = {}
    for record in qrels:
        query_id = record["query_id"]
        resource_id = record["resource"]["resource_id"]
        graded.setdefault(query_id, {})[resource_id] = int(record["judgment"]["grade"])

    missing = [q["query_id"] for q in queries if q["query_id"] not in graded]
    if missing:
        print(f"ERROR: queries without judgments: {missing}", file=sys.stderr)
        return 2

    documents = _load_documents()
    embedding = build_embedding_provider(settings.search)
    rerank = build_rerank_provider(settings.search)
    service = SearchService(
        documents, embedding=embedding, rerank=rerank, index_revision="stage2-eval"
    )

    report = Report()
    for query in queries:
        filters = query.get("filters") or {}
        outcome = service.search(
            query["query"],
            filters=SearchFilters(
                kinds=frozenset(filters.get("kinds") or ()),
                statuses=frozenset(filters.get("statuses") or ()),
                runtimes=frozenset(filters.get("runtimes") or ()),
                tags=frozenset(filters.get("tags") or ()),
                categories=frozenset(filters.get("categories") or ()),
            ),
            profile=profile,
            limit=10,
        )
        ranked_ids = [r.document.resource_public_id for r in outcome.results]
        judgments = graded[query["query_id"]]
        relevant = sorted(
            (rid for rid, grade in judgments.items() if grade >= 2),
        )
        reached = next(
            (position for position, rid in enumerate(ranked_ids, start=1) if rid in relevant),
            len(ranked_ids) + 1,
        )
        ranked_grades = [judgments.get(rid, 0) for rid in ranked_ids]
        ideal_grades = list(judgments.values())

        # ``reached`` is the 1-based rank of the first relevant hit. When no
        # relevant document was returned it is set past the end of the list so
        # callers cannot mistake "no hit" for "hit at position len+1".
        in_range = reached <= len(ranked_ids)

        report.results.append(
            QueryResult(
                query_id=query["query_id"],
                query=query["query"],
                profile=profile.value,
                ranked_ids=ranked_ids,
                relevant_ids=relevant,
                graded=judgments,
                reached_at_k=reached if in_range else 10**6,
                reciprocal_rank=round(1.0 / reached, 6) if in_range and reached <= 10 else 0.0,
                ndcg_at_10=round(_ndcg(ranked_grades, ideal_grades), 6),
                took_ms=outcome.took_ms,
                degraded=outcome.degraded,
            )
        )

    metrics = report.metrics(k=5)
    latencies = sorted(r.took_ms for r in report.results)
    p95 = latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)] if latencies else 0

    payload = {
        "dataset": {
            "queries_path": str(QUERIES.relative_to(ROOT)),
            "queries_sha256_16": _file_digest(QUERIES),
            "qrels_path": str(QRELS.relative_to(ROOT)),
            "qrels_sha256_16": _file_digest(QRELS),
            "query_count": len(queries),
            "judgment_count": len(qrels),
        },
        "config": {
            "profile": profile.value,
            "embedding_provider": getattr(embedding, "name", "unknown"),
            "rerank_provider": getattr(rerank, "name", "unknown"),
            "remote_enabled": settings.search.remote_enabled,
            "seed": SEED,
            "corpus_size": len(documents),
        },
        "environment": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "python": sys.version.split()[0],
        },
        "metrics": {**metrics, "p95_latency_ms": p95},
        "honesty": {
            "semantic_quality_claimed": bool(getattr(embedding, "name", "").startswith(("Qwen", "BAAI"))),
            "note": (
                "Offline hashing embedding is a pipeline baseline and cannot measure "
                "semantic quality; these numbers must not be cited as retrieval quality."
                if not getattr(embedding, "name", "").startswith(("Qwen", "BAAI"))
                else "Embedding used a real semantic model."
            ),
        },
        "per_query": [
            {
                "query_id": item.query_id,
                "query": item.query,
                "profile": item.profile,
                "ranked_ids": item.ranked_ids,
                "relevant_ids": item.relevant_ids,
                "graded": item.graded,
                "first_relevant_rank": item.reached_at_k,
                "reciprocal_rank": item.reciprocal_rank,
                "ndcg_at_10": item.ndcg_at_10,
                "took_ms": item.took_ms,
                "degraded": list(item.degraded),
            }
            for item in report.results
        ],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = out_dir / f"stage2-retrieval-{profile.value}-{stamp}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("== Stage 2 retrieval evaluation ==")
    print(f"queries: {len(queries)}  corpus: {len(documents)}  profile: {profile.value}")
    print(f"embedding: {getattr(embedding, 'name', '?')}  rerank: {getattr(rerank, 'name', '?')}")
    print(f"Hit@5: {metrics.get('hit_at_5')}  MRR@10: {metrics.get('mrr_at_10')}  "
          f"NDCG@10: {metrics.get('ndcg_at_10')}  P95: {p95}ms")
    if metrics.get("zero_result_queries"):
        print(f"WARNING: {metrics['zero_result_queries']} queries returned no results")
    if not payload["honesty"]["semantic_quality_claimed"]:
        print("NOTE: offline baseline — these numbers measure the pipeline, not semantic quality.")
    # ``--out`` may point outside the repository (tests use a temp directory),
    # so never assume the result is under ROOT.
    try:
        shown = target.relative_to(ROOT)
    except ValueError:
        shown = target
    print(f"raw results: {shown}")
    # Always exit 0: metrics are reported, never used as a gate.
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        default="hybrid",
        choices=[profile.value for profile in RankingProfile],
        help="ranking profile to evaluate",
    )
    parser.add_argument(
        "--out",
        default=str(ROOT / "evals" / "results"),
        help="directory for archived raw results",
    )
    args = parser.parse_args(argv)
    return run(RankingProfile(args.profile), Path(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
