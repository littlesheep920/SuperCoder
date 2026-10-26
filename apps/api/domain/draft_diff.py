"""Diff a manifest draft against the resource it would replace.

Why this is separate from :class:`DiffService`: that service compares two
*persisted* versions by id. A draft does not exist yet — it is a candidate the
model produced and a human has not accepted. Comparing it to a stored version
requires reading the stored manifest, not version ids.

Two properties matter here:

* **Deterministic.** The same draft and the same base always produce byte-identical
  output, so a reviewer diffing twice sees the same thing.
* **Read-only.** Producing a diff never writes, and never implies the draft was
  accepted. It exists so a person can decide.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from ..domain.hashing import canonical_manifest_bytes

__all__ = ["DraftDiff", "diff_manifest"]


@dataclass(frozen=True, slots=True)
class DraftDiff:
    """Difference between a draft manifest and an optional base manifest.

    ``base_present`` is false when no existing resource matches, in which case
    ``unified_diff`` shows the draft as a pure addition. That distinction matters:
    "this replaces something" and "this is new" require different review care.
    """

    base_present: bool
    unified_diff: str
    changed_paths: tuple[str, ...]
    added_paths: tuple[str, ...]
    removed_paths: tuple[str, ...]
    identical: bool

    def to_json(self) -> dict:
        return {
            "base_present": self.base_present,
            "unified_diff": self.unified_diff,
            "changed_paths": list(self.changed_paths),
            "added_paths": list(self.added_paths),
            "removed_paths": list(self.removed_paths),
            "identical": self.identical,
        }


def _flatten(value: Any, prefix: str = "") -> dict[str, str]:
    """Flatten a manifest into JSON-pointer-ish paths with rendered leaf values."""

    flat: dict[str, str] = {}
    if isinstance(value, Mapping):
        if not value:
            flat[prefix or "/"] = "{}"
            return flat
        for key in sorted(value):
            child = f"{prefix}/{key}"
            flat.update(_flatten(value[key], child))
        return flat
    if isinstance(value, list):
        if not value:
            flat[prefix or "/"] = "[]"
            return flat
        for index, item in enumerate(value):
            flat.update(_flatten(item, f"{prefix}/{index}"))
        return flat
    flat[prefix or "/"] = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return flat


def diff_manifest(
    draft: Mapping[str, Any],
    base: Mapping[str, Any] | None,
    *,
    base_label: str = "existing",
    draft_label: str = "draft",
) -> DraftDiff:
    """Compare a draft against ``base`` and describe the difference by path.

    Paths are reported in addition to the unified text because a reviewer scanning
    a long manifest needs the list of what actually moved, not only a blob.
    """

    draft_flat = _flatten(draft)
    base_flat = _flatten(base) if base is not None else {}

    base_lines = json.dumps(base_flat, indent=2, sort_keys=True, ensure_ascii=False).splitlines()
    draft_lines = json.dumps(draft_flat, indent=2, sort_keys=True, ensure_ascii=False).splitlines()

    unified = "\n".join(
        difflib.unified_diff(
            base_lines,
            draft_lines,
            fromfile=base_label,
            tofile=draft_label,
            lineterm="",
        )
    )

    base_keys = set(base_flat)
    draft_keys = set(draft_flat)
    changed = tuple(
        sorted(key for key in base_keys & draft_keys if base_flat[key] != draft_flat[key])
    )
    added = tuple(sorted(draft_keys - base_keys))
    removed = tuple(sorted(base_keys - draft_keys))

    return DraftDiff(
        base_present=base is not None,
        unified_diff=unified,
        changed_paths=changed,
        added_paths=added,
        removed_paths=removed,
        identical=not changed and not added and not removed,
    )


def canonical_fingerprint(manifest: Mapping[str, Any]) -> str:
    """Hash of the canonical form, matching what the artifact hash is built from."""

    import hashlib

    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()
