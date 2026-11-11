"""Run the resource quality check-up over the git-tracked corpus.

    python scripts/assess_resource_quality.py                      # summary: rules, kinds, worklist
    python scripts/assess_resource_quality.py --resource skill/x   # one resource, with why and how to fix
    python scripts/assess_resource_quality.py --json report.json   # every finding, machine-readable
    python scripts/assess_resource_quality.py --check-baseline     # exit 1 unless the archive reproduces
    python scripts/assess_resource_quality.py --write-baseline     # rewrite the archive (a deliberate change)

The corpus is the one Stage 5 reads (`build_duplicate_dataset.tracked_manifests`): resources git
tracks, never an untracked local draft. Boilerplate is counted over that same corpus. The rules are
in apps/api/search/quality.py; this script only loads, runs and prints.

The check-up is a report (PRD §7.1). Exit status is non-zero only for `--check-baseline` when the
archive does not reproduce, or for a resource that cannot be read at all.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from apps.api.domain.hashing import checkout_manifest, compute_checkout_artifact_hash  # noqa: E402
from apps.api.domain.models import ResourceKind  # noqa: E402
from apps.api.providers.git_repository import GitProvider  # noqa: E402
from apps.api.search.quality import (  # noqa: E402
    CHECKS,
    RULESET_VERSION,
    QualityResource,
    assess_corpus,
    check_by_id,
)
from build_duplicate_dataset import tracked_manifests  # noqa: E402

BASELINE = ROOT / "evals" / "results" / "BASELINE-resource-quality.json"


def load_corpus(root: pathlib.Path = ROOT) -> list[QualityResource]:
    git = GitProvider(root)
    resources = []
    for key in sorted(tracked_manifests(root)):
        kind, slug = key.split("/", 1)
        files = git.read_resource_files(ResourceKind(kind), slug)
        resources.append(
            QualityResource(
                manifest=checkout_manifest(files),
                files={path: body for path, body in files.items() if path != "resource.yaml"},
                artifact_hash=str(compute_checkout_artifact_hash(files)),
            )
        )
    return resources


def build_report(root: pathlib.Path = ROOT) -> dict:
    assessment = assess_corpus(load_corpus(root))
    return {
        "ruleset": RULESET_VERSION,
        "corpus": {
            "resources": assessment.context.resources,
            "boilerplate_min_resources": assessment.context.min_resources,
            "boilerplate_segments": len(assessment.context.boilerplate_segments),
            "boilerplate_field_descriptions": len(assessment.context.boilerplate_field_descriptions),
        },
        "resources": [report.to_json() for report in assessment.reports],
    }


def baseline_view(report: dict) -> dict:
    """What the archive keeps: per resource, the content it judged and the rules it failed."""

    return {
        "ruleset": report["ruleset"],
        "corpus": report["corpus"],
        "resources": {
            f"{item['kind']}/{item['slug']}": {
                "artifact_hash": item["artifact_hash"],
                "failed": sorted(f["check"] for f in item["findings"] if not f["passed"]),
            }
            for item in report["resources"]
        },
    }


def dump_baseline(view: dict) -> str:
    """One resource per line, so a diff of the archive reads as a list of changed resources."""

    head = {key: view[key] for key in ("ruleset", "corpus")}
    lines = [json.dumps(head, ensure_ascii=False, sort_keys=True)[:-1] + ', "resources": {']
    entries = sorted(view["resources"].items())
    for index, (key, value) in enumerate(entries):
        comma = "," if index < len(entries) - 1 else ""
        lines.append(f"  {json.dumps(key)}: {json.dumps(value, sort_keys=True)}{comma}")
    lines.append("}}")
    return "\n".join(lines) + "\n"


def baseline_differences(current: dict, baseline: dict) -> list[str]:
    """Each difference says whether the resource changed or the rules did."""

    problems: list[str] = []
    if current["ruleset"] != baseline["ruleset"]:
        problems.append(f"ruleset {current['ruleset']} != archived {baseline['ruleset']}")
    if current["corpus"] != baseline["corpus"]:
        problems.append(f"corpus {current['corpus']} != archived {baseline['corpus']}")
    now, then = current["resources"], baseline["resources"]
    for key in sorted(set(now) - set(then)):
        problems.append(f"{key}: not in the archive")
    for key in sorted(set(then) - set(now)):
        problems.append(f"{key}: archived but no longer in the corpus")
    for key in sorted(set(now) & set(then)):
        if now[key] == then[key]:
            continue
        cause = "content changed" if now[key]["artifact_hash"] != then[key]["artifact_hash"] else "same content, other verdict"
        problems.append(f"{key}: {cause}; failed {then[key]['failed']} -> {now[key]['failed']}")
    return problems


def ratio(passed: int, applicable: int) -> str:
    return f"{passed}/{applicable}" if applicable else "-"


def print_summary(report: dict, worst: int) -> None:
    items = report["resources"]
    corpus = report["corpus"]
    print(f"规则集 {report['ruleset']}；资源 {corpus['resources']} 个；"
          f"通用套话：句子 {corpus['boilerplate_segments']} 条、字段说明 {corpus['boilerplate_field_descriptions']} 条"
          f"（在至少 {corpus['boilerplate_min_resources']} 个资源中逐字出现）")
    print()
    print("| 规则 | 维度 | 级别 | 适用 | 未通过 |")
    print("|---|---|---|---|---|")
    applicable: Counter[str] = Counter()
    failed: Counter[str] = Counter()
    for item in items:
        for finding in item["findings"]:
            applicable[finding["check"]] += 1
            failed[finding["check"]] += 0 if finding["passed"] else 1
    for check in CHECKS:
        print(f"| `{check.check_id}` {check.title} | {check.dimension} | {check.severity} | "
              f"{applicable[check.check_id]} | {failed[check.check_id]} |")
    print()
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        by_kind[item["kind"]].append(item)
    print("| 类型 | 资源 | 加权通过 | 无警告项的资源 |")
    print("|---|---|---|---|")
    for kind in sorted(by_kind):
        group = by_kind[kind]
        passed = sum(item["passed_weight"] for item in group)
        total = sum(item["applicable_weight"] for item in group)
        clean = sum(
            1 for item in group
            if not any(not f["passed"] and f["severity"] == "warning" for f in item["findings"])
        )
        print(f"| {kind} | {len(group)} | {passed * 100 // total if total else 0}% | {clean} |")
    print()
    ranked = sorted(items, key=lambda item: (item["passed_weight"] / max(item["applicable_weight"], 1),
                                             item["kind"], item["slug"]))
    print(f"最需要改进的 {worst} 个资源：")
    for item in ranked[:worst]:
        missing = ", ".join(f["check"] for f in item["findings"] if not f["passed"])
        print(f"  {item['kind']}/{item['slug']}  {ratio(item['passed_weight'], item['applicable_weight'])}  {missing}")


def print_resource(report: dict, key: str) -> int:
    for item in report["resources"]:
        if f"{item['kind']}/{item['slug']}" != key:
            continue
        print(f"{key} {item['version']}  {item['artifact_hash']}")
        print(f"加权通过 {ratio(item['passed_weight'], item['applicable_weight'])}（{item['ruleset']}）")
        for finding in item["findings"]:
            check = check_by_id(finding["check"])
            mark = "通过" if finding["passed"] else ("警告" if finding["severity"] == "warning" else "建议")
            print(f"\n[{mark}] {check.title}（{check.check_id}，{check.dimension}）")
            if not finding["passed"]:
                for line in finding["evidence"]:
                    print(f"  发现：{line}")
                print(f"  为什么：{check.why}")
                print(f"  怎么改：{check.fix}")
        return 0
    print(f"{key}: not in the tracked corpus", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--resource", help="kind/slug of one resource to explain")
    parser.add_argument("--json", type=pathlib.Path, help="write the full report here")
    parser.add_argument("--worst", type=int, default=10, help="how many resources the worklist shows")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check-baseline", action="store_true")
    group.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args(argv)

    report = build_report()
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.write_baseline:
        BASELINE.write_text(dump_baseline(baseline_view(report)), encoding="utf-8")
        print(f"wrote {BASELINE.relative_to(ROOT)}")
        return 0
    if args.check_baseline:
        problems = baseline_differences(baseline_view(report), json.loads(BASELINE.read_text(encoding="utf-8")))
        for problem in problems:
            print(problem)
        print("baseline reproduced" if not problems else f"{len(problems)} difference(s)")
        return 1 if problems else 0
    if args.resource:
        return print_resource(report, args.resource)
    print_summary(report, args.worst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
