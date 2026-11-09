"""Evaluate the duplicate detector on the Stage 5 dataset.

    python scripts/run_duplicate_eval.py                    # dev and test; writes a JSON report
    python scripts/run_duplicate_eval.py --split dev        # one split
    python scripts/run_duplicate_eval.py --tune             # choose thresholds on the dev split only

The registry the detector sees is the git-tracked corpus under resources/ plus the variants in
evals/duplicates/stage5_registry/; template segments and IDF weights are computed over that whole
registry, labels are never read for them. Gold labels come from stage5_labels.jsonl: exact and near
duplicates are positive, related and not_duplicate are negative, uncertain pairs are excluded and
counted separately. Metrics are precision, recall and F1 of the positive class (PRD §7.4).

`--tune` reads dev labels only and refuses to look at the test split, so the test split can be
scored once, after the constants in apps/api/search/duplicates.py are fixed.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from apps.api.search import duplicates  # noqa: E402
from apps.api.search.duplicates import DuplicateDetector, ResourceText, classify  # noqa: E402
from build_duplicate_dataset import LABELS, PAIRS, REGISTRY, tracked_manifests  # noqa: E402

POSITIVE = {"exact_duplicate", "near_duplicate"}


def load_registry(root: pathlib.Path = ROOT) -> dict[str, ResourceText]:
    registry: dict[str, ResourceText] = {}
    directories = list(tracked_manifests(root).values())
    directories += sorted(path.parent for path in (root / REGISTRY).glob("*/*/resource.yaml"))
    for directory in directories:
        manifest = json.loads((directory / "resource.yaml").read_text(encoding="utf-8"))
        entry_path = directory / manifest["entrypoint"]
        entry = entry_path.read_text(encoding="utf-8") if entry_path.is_file() else ""
        resource = ResourceText.from_manifest(manifest, entry)
        registry[f"{resource.kind}/{resource.slug}"] = resource
    return registry


def load_items(root: pathlib.Path = ROOT) -> list[dict]:
    def read(relative: str) -> list[dict]:
        return [json.loads(line) for line in (root / relative).read_text(encoding="utf-8").splitlines() if line]

    labels = {record["pair_id"]: record["judgment"]["label"] for record in read(LABELS)}
    items = []
    for pair in read(PAIRS):
        items.append({
            "pair_id": pair["pair_id"],
            "split": pair["extensions"]["x-split"],
            "left": f"{pair['left_resource']['kind']}/{pair['left_resource']['slug']}",
            "right": f"{pair['right_resource']['kind']}/{pair['right_resource']['slug']}",
            "gold": labels[pair["pair_id"]],
            "slices": [label.split(":", 1)[1] for label in pair["labels"] if label.startswith("slice:")],
        })
    return sorted(items, key=lambda item: item["pair_id"])


def score(predictions: list[dict]) -> dict:
    counted = [item for item in predictions if item["gold"] != "uncertain"]
    tp = sum(1 for item in counted if item["gold"] in POSITIVE and item["predicted"] in POSITIVE)
    fp = sum(1 for item in counted if item["gold"] not in POSITIVE and item["predicted"] in POSITIVE)
    fn = sum(1 for item in counted if item["gold"] in POSITIVE and item["predicted"] not in POSITIVE)
    tn = len(counted) - tp - fp - fn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"pairs": len(predictions), "excluded_uncertain": len(predictions) - len(counted),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def predict(items: list[dict], registry: dict[str, ResourceText], detector: DuplicateDetector) -> list[dict]:
    predictions = []
    for item in items:
        verdict = detector.judge(registry[item["left"]], registry[item["right"]])
        predictions.append({**item, "predicted": verdict.label, "score": verdict.score,
                            "components": verdict.components, "rules": list(verdict.rules),
                            "shared_terms": list(verdict.shared_terms)})
    return predictions


def errors_by_slice(predictions: list[dict]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for item in predictions:
        if item["gold"] == "uncertain":
            continue
        wrong = (item["gold"] in POSITIVE) != (item["predicted"] in POSITIVE)
        if wrong:
            kind = "missed" if item["gold"] in POSITIVE else "false_alarm"
            for name in item["slices"] or ["none"]:
                grouped.setdefault(f"{kind}:{name}", []).append(item["pair_id"])
    return dict(sorted(grouped.items()))


def tune(items: list[dict], registry: dict[str, ResourceText]) -> dict:
    """Grid search on the dev split. Test labels are never read here."""

    dev = [item for item in items if item["split"] == "dev"]
    assert dev and all(item["split"] == "dev" for item in dev)
    detector = DuplicateDetector(list(registry.values()))
    base = predict(dev, registry, detector)
    results = []
    for wp in (0.3, 0.4, 0.5, 0.6, 0.7):
        for wt in (0.0, 0.1, 0.2):
            wb = round(1.0 - wp - wt, 2)
            if wb < 0.1:
                continue
            weights = {"purpose": wp, "body": wb, "tags": wt}
            for floor in (0.2, 0.25, 0.3, 0.35, 0.4):
                for step in range(26):
                    threshold = round(0.2 + 0.02 * step, 2)
                    predicted = []
                    for item in base:
                        label, _, _ = classify(
                            item["components"]["purpose"], item["components"]["body"], item["components"]["tags"],
                            exact="same-description-and-body" in item["rules"],
                            reuse=any(rule.startswith("reuse") for rule in item["rules"]),
                            weights=weights, duplicate_threshold=threshold,
                            related_threshold=duplicates.RELATED_THRESHOLD, text_floor=floor)
                        predicted.append({**item, "predicted": label})
                    metrics = score(predicted)
                    results.append({"weights": weights, "text_floor": floor, "duplicate_threshold": threshold, **metrics})
    best_f1 = max(result["f1"] for result in results)
    tied = [result for result in results if result["f1"] == best_f1]
    best_precision = max(result["precision"] for result in tied)
    tied = [result for result in tied if result["precision"] == best_precision]
    least_tags = min(result["weights"]["tags"] for result in tied)
    tied = [result for result in tied if result["weights"]["tags"] == least_tags]
    closest = min(abs(result["weights"]["purpose"] - 0.5) for result in tied)
    tied = sorted((result for result in tied if abs(result["weights"]["purpose"] - 0.5) == closest),
                  key=lambda result: (result["duplicate_threshold"], result["text_floor"]))
    chosen = tied[len(tied) // 2]
    return {"configurations": len(results), "tied_at_best": len(tied), "chosen": chosen,
            "rule": "highest dev F1, then higher precision, then less weight on tags, then purpose weight closest "
                    "to 0.5, then the middle of the remaining (threshold, floor) range"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the duplicate detector on the Stage 5 dataset.")
    parser.add_argument("--split", choices=("dev", "test", "all"), default="all")
    parser.add_argument("--tune", action="store_true", help="choose thresholds on the dev split only")
    parser.add_argument("--out", default=str(ROOT / "evals" / "results"), help="directory for the JSON report")
    args = parser.parse_args(argv)

    registry = load_registry(ROOT)
    items = load_items(ROOT)
    if args.tune:
        outcome = tune(items, registry)
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0

    detector = DuplicateDetector(list(registry.values()))
    splits = ("dev", "test") if args.split == "all" else (args.split,)
    report = {
        "evaluation": "stage5-duplicates",
        "dataset_version": json.loads((ROOT / PAIRS).read_text(encoding="utf-8").splitlines()[0])["dataset_version"],
        "registry_size": len(registry),
        "templates_removed": len(detector.templates),
        "detector": {"weights": detector.weights, "duplicate_threshold": detector.duplicate_threshold,
                     "related_threshold": detector.related_threshold, "text_floor": detector.text_floor,
                     "template_min_resources": duplicates.TEMPLATE_MIN_RESOURCES},
        "splits": {},
    }
    for split in splits:
        predictions = predict([item for item in items if item["split"] == split], registry, detector)
        report["splits"][split] = {"metrics": score(predictions), "errors_by_slice": errors_by_slice(predictions),
                                   "predictions": predictions}
        metrics = report["splits"][split]["metrics"]
        print(f"{split:5s} pairs={metrics['pairs']} excluded={metrics['excluded_uncertain']} "
              f"tp={metrics['tp']} fp={metrics['fp']} fn={metrics['fn']} tn={metrics['tn']} "
              f"P={metrics['precision']:.3f} R={metrics['recall']:.3f} F1={metrics['f1']:.4f}")
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = out / f"stage5-duplicates-{args.split}-{stamp}.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"report: {target.relative_to(ROOT) if target.is_relative_to(ROOT) else target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
