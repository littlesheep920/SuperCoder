"""RFC 7396-style JSON Merge Patch for resource manifest updates."""

from __future__ import annotations

from collections.abc import Mapping

from .hashing import JsonValue, normalize_json_value

__all__ = ["apply_json_merge_patch"]


def apply_json_merge_patch(document: object, patch: object) -> JsonValue:
    """Apply a JSON Merge Patch without mutating ``document`` or ``patch``.

    Object members are merged recursively; a ``null`` member deletes the
    corresponding object key. A non-object patch replaces the target value.
    Inputs and the result are restricted to the project's canonical JSON
    compatible value domain.
    """

    normalized_patch = normalize_json_value(patch)
    normalized_document = normalize_json_value(document)
    return _merge(normalized_document, normalized_patch)


def _merge(document: JsonValue, patch: JsonValue) -> JsonValue:
    if not isinstance(patch, dict):
        return patch

    target: dict[str, JsonValue]
    if isinstance(document, Mapping):
        target = {str(key): value for key, value in document.items()}
    else:
        target = {}

    for key, value in patch.items():
        if value is None:
            target.pop(key, None)
        else:
            target[key] = _merge(target.get(key), value)
    return target
