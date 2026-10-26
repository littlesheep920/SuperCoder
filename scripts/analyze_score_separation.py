"""Measure whether retrieval scores can support a "no evidence" refusal decision.

This is a diagnostic, not a metric. It answers three separate questions that are
easy to confuse:

1. Within a query that has an answer, does the relevant hit score above the rest?
2. Across queries, can any single threshold tell "has evidence" from "has none"?
3. Does the reranker help or hurt overall?

The answers decide how the discovery agent must implement refusal. A pooled
threshold that looks accurate in hindsight is not a usable operating point if the
two situations overlap, so the paired and cross-query views are reported
separately rather than collapsed into one number.

Usage:
    python scripts/analyze_score_separation.py --queries 60

Requires the embedding and rerank providers; the LLM is not used.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.config import get_settings  # noqa: E402
from apps.api.search import RankingProfile  # noqa: E402
from apps.api.search.factory import build_embedding_provider, build_rerank_provider  # noqa: E402
from apps.api.search.service import SearchService  # noqa: E402

QUERIES = ROOT / "evals" / "retrieval" / "stage2_queries.jsonl"
QRELS = ROOT / "evals" / "retrieval" / "stage2_qrels.jsonl"
RESULTS = ROOT / "evals" / "results"


def load_jsonl(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_documents():
    """Build index documents the same way the evaluation harness does."""

    sys.path.insert(0, str(ROOT / "scripts"))
    import run_retrieval_eval  # noqa: PLC0415

    return run_retrieval_eval._load_documents()


def summarise(values: list[float], label: str) -> None:
    if not values:
        print(f"{label}: no data")
        return
    ordered = sorted(values)
    print(
        f"{label}: n={len(ordered)} min={ordered[0]:.4f} "
        f"p25={ordered[len(ordered) // 4]:.4f} median={statistics.median(ordered):.4f} "
        f"p75={ordered[3 * len(ordered) // 4]:.4f} max={ordered[-1]:.4f}"
    )


def dcg(grades: list[int], k: int) -> float:
    return sum((2**grade - 1) / math.log2(position + 2) for position, grade in enumerate(grades[:k]))


def build_service(documents, *, with_rerank: bool) -> SearchService:
    settings = get_settings()
    embedding = build_embedding_provider(settings.search)
    rerank = build_rerank_provider(settings.search) if with_rerank else None
    for provider in (embedding, rerank):
        warmer = getattr(provider, "warm_connection", None)
        if warmer is not None:
            warmer()
    return SearchService(documents, embedding=embedding, rerank=rerank, index_revision="score-analysis")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=int, default=60, help="how many queries to score")
    parser.add_argument("--profiles", type=int, default=80, help="sample size for the rerank comparison")
    args = parser.parse_args()

    queries = load_jsonl(QUERIES)
    qrels = load_jsonl(QRELS)

    graded: dict[str, dict[str, int]] = {}
    for record in qrels:
        graded.setdefault(record["query_id"], {})[record["resource"]["resource_id"]] = int(
            record["judgment"]["grade"]
        )

    documents = load_documents()
    service = build_service(documents, with_rerank=True)

    rows: list[dict] = []
    for index, query in enumerate(queries[: args.queries], start=1):
        outcome = service.search(query["query"], profile=RankingProfile.HYBRID, limit=10)
        judgments = graded.get(query["query_id"], {})
        hits = [
            (result.document.resource_public_id, float(result.score),
             judgments.get(result.document.resource_public_id, 0))
            for result in outcome.results
        ]
        if not hits:
            continue
        rows.append(
            {
                "query_id": query["query_id"],
                "top1_score": hits[0][1],
                "top1_grade": hits[0][2],
                "best_relevant_score": next((s for _, s, g in hits if g >= 2), None),
                "best_irrelevant_score": max((s for _, s, g in hits if g < 2), default=None),
            }
        )
        if index % 20 == 0:
            print(f"  scored {index}/{min(args.queries, len(queries))}", file=sys.stderr)

    relevant = [r["best_relevant_score"] for r in rows if r["best_relevant_score"] is not None]
    irrelevant = [r["best_irrelevant_score"] for r in rows if r["best_irrelevant_score"] is not None]
    paired = [r for r in rows if r["best_relevant_score"] is not None and r["best_irrelevant_score"] is not None]
    wins = sum(1 for r in paired if r["best_relevant_score"] > r["best_irrelevant_score"])

    print()
    print(f"=== Q1: score distributions (n={len(rows)}) ===")
    summarise(relevant, "best RELEVANT hit  ")
    summarise(irrelevant, "best IRRELEVANT hit")
    print(f"relevant outranks irrelevant within a query: {wins}/{len(paired)} = "
          f"{wins / max(1, len(paired)):.1%}")

    answerable = [r["top1_score"] for r in rows if r["best_relevant_score"] is not None]
    unanswerable = [r["top1_score"] for r in rows if r["best_relevant_score"] is None]
    print()
    print("=== Q2: can one threshold separate answerable from unanswerable? ===")
    separable = False
    if answerable and unanswerable:
        high_unanswerable = max(unanswerable)
        low_answerable = min(answerable)
        print(f"highest top-1 among unanswerable: {high_unanswerable:.4f}")
        print(f"lowest  top-1 among answerable  : {low_answerable:.4f}")
        separable = high_unanswerable < low_answerable
        if separable:
            print(f"SEPARABLE: any threshold in [{high_unanswerable:.4f}, {low_answerable:.4f}] works")
        else:
            shadowed = sum(1 for score in answerable if score < high_unanswerable)
            print(f"OVERLAP: a threshold above {high_unanswerable:.4f} would refuse "
                  f"{shadowed}/{len(answerable)} answerable queries "
                  f"({shadowed / len(answerable):.1%})")
            print("=> a pooled threshold is not a valid refusal rule")
    else:
        print("insufficient data (need both answerable and unanswerable queries in the sample)")

    # Q3: rerank effect, measured on its own sample.
    print()
    print(f"=== Q3: rerank effect (first {args.profiles} queries) ===")
    sample = queries[: args.profiles]
    effect: dict[str, dict[str, float]] = {}
    for label, with_rerank in (("with_rerank", True), ("no_rerank", False)):
        svc = service if with_rerank else build_service(documents, with_rerank=False)
        hit = rr = ndcg = 0.0
        for query in sample:
            outcome = svc.search(query["query"], profile=RankingProfile.HYBRID, limit=10)
            judgments = graded.get(query["query_id"], {})
            ids = [r.document.resource_public_id for r in outcome.results]
            rank = next((i for i, rid in enumerate(ids, start=1) if judgments.get(rid, 0) >= 2), None)
            if rank is not None and rank <= 5:
                hit += 1
            rr += 1.0 / rank if rank else 0.0
            ranked = [judgments.get(rid, 0) for rid in ids]
            ideal = sorted(judgments.values(), reverse=True)
            ndcg += dcg(ranked, 10) / dcg(ideal, 10) if dcg(ideal, 10) > 0 else 0.0
        n = len(sample)
        effect[label] = {"hit_at_5": hit / n, "mrr": rr / n, "ndcg": ndcg / n}
        print(f"  {label:12} Hit@5={effect[label]['hit_at_5']:.4f} "
              f"MRR={effect[label]['mrr']:.4f} NDCG={effect[label]['ndcg']:.4f}")
    delta = {key: effect["with_rerank"][key] - effect["no_rerank"][key] for key in effect["with_rerank"]}
    print(f"  delta        Hit@5={delta['hit_at_5']:+.4f} MRR={delta['mrr']:+.4f} NDCG={delta['ndcg']:+.4f}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / "score-separation-analysis.json"
    out.write_text(
        json.dumps(
            {
                "profile": "hybrid",
                "sample_size": len(rows),
                "per_query": rows,
                "paired_wins": wins,
                "paired_total": len(paired),
                "threshold_separable": separable,
                "rerank_effect": {"results": effect, "delta": delta, "sample_size": len(sample)},
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"\nwrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
