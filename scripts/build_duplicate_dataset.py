"""Build the Stage 5 duplicate-detection dataset from its reviewable spec.

`evals/duplicates/pairs.jsonl` holds four Stage 0 pairs and none of them is a duplicate, so no
F1 can be computed from it. Duplicates also cannot be added to `resources/`: that would change the
290-resource retrieval corpus and its archived baselines. This script therefore builds a separate
dataset from one hand-written, reviewable file:

    evals/duplicates/stage5_spec.json      families and their split, 33 variants, every pair's label
        |
        v  (deterministic; the only writer of the files below)
    evals/duplicates/stage5_registry/      the variants, laid out like resources/ (an extension of the
                                            registry, not part of the retrieval corpus)
    evals/duplicates/stage5_pairs.jsonl    duplicate_pair records   (evals/FORMAT.md §6.1)
    evals/duplicates/stage5_labels.jsonl   duplicate_label records  (evals/FORMAT.md §6.2)

A variant is "another team registered it again": a rewrite, a copy, the same capability as another
kind, a Chinese re-registration, or a resource that sounds the same but does another job (a
lookalike). The corpus side of every pair is read from the git-tracked resources only.

The labels were drafted by an AI assistant, the same one that wrote the detector, and nobody has
reviewed them. Every label record says so (`x-label-author`, `x-human-review-status`), and
`adjudication_status` stays `draft` until a pair id is listed in the spec's `reviewed_pairs`.

    python scripts/build_duplicate_dataset.py           # write the generated files
    python scripts/build_duplicate_dataset.py --check   # exit 1 if any generated file is stale
"""

from __future__ import annotations

import argparse
import copy
import json
import pathlib
import subprocess
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = "evals/duplicates/stage5_spec.json"
REGISTRY = "evals/duplicates/stage5_registry"
PAIRS = "evals/duplicates/stage5_pairs.jsonl"
LABELS = "evals/duplicates/stage5_labels.jsonl"
DIRECTORY = {"skill": "skills", "mcp": "mcp", "prompt": "prompts", "agent": "agents", "workflow": "workflows", "tool": "tools"}
POSITIVE = frozenset({"exact_duplicate", "near_duplicate"})
LABELS_ALLOWED = frozenset({"not_duplicate", "related", "near_duplicate", "exact_duplicate", "uncertain"})
CATEGORIES = frozenset({"rewrite", "copy", "kind-change", "cross-language", "lookalike", "reuse"})
CLEAR = {"status": "clear", "flags": {"pii_removed": False, "secrets_removed": False,
                                       "proprietary_content_removed": False, "identifiers_pseudonymized": False}}


class DatasetError(ValueError):
    """The spec is inconsistent. The message names the record."""


def tracked_manifests(root: pathlib.Path) -> dict[str, pathlib.Path]:
    """`kind/slug` -> directory, for the git-tracked corpus only (an untracked draft is not corpus)."""

    listed = subprocess.run(["git", "ls-files", "-z", "--", "resources"], cwd=str(root), capture_output=True)
    names = [item for item in (listed.stdout or b"").decode("utf-8", errors="replace").split("\0") if item]
    if listed.returncode != 0 or not names:
        paths = sorted((root / "resources").glob("*/*/resource.yaml"))
    else:
        paths = [root / name for name in names if name.endswith("/resource.yaml")]
    index: dict[str, pathlib.Path] = {}
    for path in paths:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        index[f"{manifest['kind']}/{manifest['slug']}"] = path.parent
    return index


def _manifest(directory: pathlib.Path) -> dict:
    return json.loads((directory / "resource.yaml").read_text(encoding="utf-8"))


def _entry(directory: pathlib.Path, manifest: dict) -> str:
    return (directory / manifest["entrypoint"]).read_text(encoding="utf-8")


def variant_files(variant: dict, corpus: dict[str, pathlib.Path]) -> tuple[dict, str]:
    """The manifest and entry text of one variant, derived from its base (and template) resource."""

    base_dir = corpus.get(variant["base"])
    if base_dir is None:
        raise DatasetError(f"variant {variant['slug']}: base {variant['base']} is not a tracked resource")
    base = _manifest(base_dir)
    template_ref = variant.get("kind_from")
    if template_ref:
        if template_ref not in corpus:
            raise DatasetError(f"variant {variant['slug']}: kind_from {template_ref} is not a tracked resource")
        manifest = copy.deepcopy(_manifest(corpus[template_ref]))
        manifest["categories"] = copy.deepcopy(base["categories"])
    else:
        manifest = copy.deepcopy(base)
    if manifest["kind"] != variant["kind"]:
        raise DatasetError(f"variant {variant['slug']}: kind {variant['kind']} does not match its template")
    manifest.update({
        "slug": variant["slug"],
        "name": variant["name"],
        "version": "1.0.0",
        "description": variant.get("description") or base["description"],
        "aliases": [],
        "tags": list(variant.get("tags") or base["tags"]),
        "status": "draft",
        "owner_team": variant["owner_team"],
    })
    if variant.get("kind_section"):
        manifest[variant["kind"]] = {**manifest[variant["kind"]], **variant["kind_section"]}
    uses = variant.get("uses")
    if uses:
        if uses not in corpus:
            raise DatasetError(f"variant {variant['slug']}: uses {uses} is not a tracked resource")
        kind, slug = uses.split("/", 1)
        nodes = [node for node in manifest["workflow"]["nodes"] if node.get("type") == "resource"]
        if len(nodes) != 1:
            raise DatasetError(f"variant {variant['slug']}: the workflow template must have exactly one resource node")
        nodes[0]["resource"] = {"kind": kind, "slug": slug, "version": "^1.0.0"}
    entry = variant.get("entry")
    if entry is None:
        entry = _entry(base_dir, base)
        if template_ref:
            raise DatasetError(f"variant {variant['slug']}: a kind change needs its own entry text")
    return manifest, entry


def build(root: pathlib.Path = ROOT) -> dict[str, bytes]:
    """Every generated file, keyed by repository-relative path. Raises DatasetError on any inconsistency."""

    spec = json.loads((root / SPEC).read_text(encoding="utf-8"))
    corpus = tracked_manifests(root)
    files: dict[str, bytes] = {}
    registry: dict[str, dict] = {}

    for variant in spec["variants"]:
        if variant["category"] not in CATEGORIES:
            raise DatasetError(f"variant {variant['slug']}: unknown category {variant['category']!r}")
        ref = f"{variant['kind']}/{variant['slug']}"
        if ref in corpus or ref in registry:
            raise DatasetError(f"variant {ref} collides with an existing resource")
        manifest, entry = variant_files(variant, corpus)
        folder = f"{REGISTRY}/{DIRECTORY[variant['kind']]}/{variant['slug']}"
        files[f"{folder}/resource.yaml"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        files[f"{folder}/{manifest['entrypoint']}"] = entry.encode("utf-8")
        registry[ref] = manifest

    def reference(ref: str) -> dict:
        if ref in registry:
            version = registry[ref]["version"]
        elif ref in corpus:
            version = _manifest(corpus[ref])["version"]
        else:
            raise DatasetError(f"pair reference {ref} is neither a tracked resource nor a variant")
        kind, slug = ref.split("/", 1)
        return {"resource_id": f"{kind}/{slug}@{version}", "kind": kind, "slug": slug, "version": version}

    version = spec["dataset_version"]
    created = spec["created_at"]
    reviewed = set(spec.get("reviewed_pairs", []))
    seen: set[tuple[str, str]] = set()
    split_of: dict[str, str] = {}
    counters: dict[str, int] = defaultdict(int)
    pair_lines: list[str] = []
    label_lines: list[str] = []
    for pair in spec["pairs"]:
        family = pair["family"]
        if family not in spec["families"]:
            raise DatasetError(f"pair {pair['left']} ~ {pair['right']}: unknown family {family!r}")
        if pair["label"] not in LABELS_ALLOWED:
            raise DatasetError(f"pair {pair['left']} ~ {pair['right']}: unknown label {pair['label']!r}")
        left, right = sorted((reference(pair["left"]), reference(pair["right"])), key=lambda item: item["resource_id"])
        key = (left["resource_id"], right["resource_id"])
        if key[0] == key[1]:
            raise DatasetError(f"pair {key[0]} pairs a resource with itself")
        if key in seen:
            raise DatasetError(f"pair {key[0]} ~ {key[1]} appears twice")
        seen.add(key)
        split = spec["families"][family]["split"]
        for ref in (pair["left"], pair["right"]):
            if split_of.setdefault(ref, split) != split:
                raise DatasetError(f"{ref} appears in both the dev and the test split")
        counters[family] += 1
        pair_id = f"dp5-{family}-{counters[family]:02d}"
        slices = list(pair.get("slices", []))
        machine_labels = ["stage5", "duplicates", f"family:{family}", f"split:{split}", *[f"slice:{name}" for name in slices]]
        sides = {"left": "extension" if pair["left"] in registry else "corpus",
                 "right": "extension" if pair["right"] in registry else "corpus"}
        pair_record = {
            "schema_version": "1", "record_type": "duplicate_pair", "dataset_version": version, "pair_id": pair_id,
            "left_resource": left, "right_resource": right,
            "comparison_basis": ["manifest", "content"],
            "provenance": {"source_type": "synthetic", "source_id": "stage5-duplicate-spec", "created_by": "ai-assistant-draft",
                           "created_at": created,
                           "derivation": "Generated by scripts/build_duplicate_dataset.py from evals/duplicates/stage5_spec.json. "
                                         "Variant resources live in evals/duplicates/stage5_registry; corpus resources are the "
                                         "git-tracked manifests under resources/."},
            "redaction": CLEAR, "labels": machine_labels,
            "extensions": {"x-family": family, "x-split": split, "x-sides": sides},
        }
        is_reviewed = pair_id in reviewed
        label_record = {
            "schema_version": "1", "record_type": "duplicate_label", "dataset_version": version,
            "label_id": pair_id.replace("dp5-", "dl5-", 1), "pair_id": pair_id,
            "judgment": {"label": pair["label"], "confidence": pair["confidence"], "dimensions": pair["dimensions"],
                         "rationale": pair["rationale"], "assessor_type": "deterministic-rule",
                         "adjudication_status": "single-reviewed" if is_reviewed else "draft"},
            "provenance": {"source_type": "synthetic", "source_id": "stage5-duplicate-spec", "created_by": "ai-assistant-draft",
                           "created_at": created,
                           "derivation": "Label drafted by an AI assistant, the same assistant that wrote the duplicate detector, "
                                         "and copied verbatim from the spec by a deterministic generator. It is not a human "
                                         "judgment unless x-human-review-status says reviewed."},
            "redaction": CLEAR, "labels": machine_labels,
            "extensions": {"x-label-author": "ai-assistant", "x-human-review-status": "reviewed" if is_reviewed else "pending",
                           "x-slices": slices},
        }
        pair_lines.append(json.dumps(pair_record, ensure_ascii=False, separators=(",", ":")))
        label_lines.append(json.dumps(label_record, ensure_ascii=False, separators=(",", ":")))
    files[PAIRS] = ("\n".join(pair_lines) + "\n").encode("utf-8")
    files[LABELS] = ("\n".join(label_lines) + "\n").encode("utf-8")
    return files


def stale(root: pathlib.Path, files: dict[str, bytes]) -> list[str]:
    """Generated paths whose bytes differ on disk, plus registry files the spec no longer produces."""

    problems = [path for path, content in files.items()
                if not (root / path).is_file() or (root / path).read_bytes() != content]
    registry = root / REGISTRY
    if registry.is_dir():
        problems += sorted(path.relative_to(root).as_posix() for path in registry.rglob("*")
                           if path.is_file() and path.relative_to(root).as_posix() not in files)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the generated files differ from the spec")
    args = parser.parse_args(argv)
    try:
        files = build(ROOT)
    except DatasetError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    if args.check:
        problems = stale(ROOT, files)
        for path in problems:
            print(f"stale: {path}")
        print("RESULT:", "FAIL" if problems else "PASS")
        return 1 if problems else 0
    for path, content in files.items():
        target = ROOT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    for path in stale(ROOT, files):
        (ROOT / path).unlink()
    print(f"wrote {len(files)} files: {sum(1 for p in files if p.endswith('resource.yaml'))} variants, "
          f"{files[PAIRS].count(b'\n')} pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
