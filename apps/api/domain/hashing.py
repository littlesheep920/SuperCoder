"""Deterministic hashing for frozen Stage 0 resource artifacts.

Stage 0 manifests are JSON-compatible YAML: they are JSON documents, not the
larger YAML type system.  This module therefore canonicalizes parsed JSON
values without importing a permissive YAML loader.  The authoritative hashes
are:

* ``manifest_snapshot_hash``: the complete canonical manifest, including its
  current top-level lifecycle ``status``.
* ``artifact_hash``: the same manifest with only that mutable ``status`` field
  removed, followed by every managed UTF-8 text file in canonical path order.

The aggregate stream is length framed, so different path/content boundaries
cannot produce the same byte stream by concatenation ambiguity.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Final, TypeAlias

from .models import ArtifactHash, ContentHash, ManifestSnapshotHash
from .validation import validate_relative_path

__all__ = [
    "ArtifactContent",
    "JsonValue",
    "LIFECYCLE_MANIFEST_FIELDS",
    "ResourceHashes",
    "calculate_resource_hashes",
    "canonical_json_bytes",
    "canonical_manifest_bytes",
    "compute_artifact_hash",
    "compute_content_hash",
    "compute_manifest_snapshot_hash",
    "normalize_json_value",
    "normalize_line_endings",
    "normalize_utf8_text",
    "parse_json_compatible_document",
    "sha256_digest",
]

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
TextContent: TypeAlias = str | bytes | bytearray | memoryview
ArtifactInput: TypeAlias = Mapping[str | PurePosixPath, TextContent] | Iterable["ArtifactContent"]

LIFECYCLE_MANIFEST_FIELDS: Final[frozenset[str]] = frozenset({"status"})
_MANIFEST_RECORD_PATH: Final = "resource.yaml"
_ARTIFACT_STREAM_DOMAIN: Final = b"supercoder.resource-artifact.v1\x00"
_MAX_FRAME_LENGTH: Final = (1 << 64) - 1


@dataclass(frozen=True, slots=True)
class ArtifactContent:
    """One managed repository-relative UTF-8 text artifact."""

    path: str | PurePosixPath
    content: TextContent


@dataclass(frozen=True, slots=True)
class ResourceHashes:
    """Hashes bound to one exact normalized resource snapshot."""

    artifact_hash: ArtifactHash
    manifest_snapshot_hash: ManifestSnapshotHash
    content_hashes: Mapping[str, ContentHash]

    def __post_init__(self) -> None:
        object.__setattr__(self, "content_hashes", MappingProxyType(dict(self.content_hashes)))


def normalize_line_endings(text: str) -> str:
    """Return text with CRLF and lone CR represented as LF."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalize_utf8_text(content: TextContent) -> bytes:
    """Strictly decode UTF-8, normalize line endings, and return UTF-8 bytes.

    A UTF-8 BOM is rejected rather than silently becoming part of a resource.
    This keeps hashes identical across readers and follows Stage 0's canonical
    BOM-free write format. Invalid Unicode and NUL are rejected fail-closed.
    """

    if isinstance(content, str):
        text = content
    elif isinstance(content, (bytes, bytearray, memoryview)):
        raw = bytes(content)
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError("UTF-8 BOM is forbidden in canonical resource text")
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("resource content must be valid UTF-8 text") from error
    else:
        raise TypeError("resource content must be text or a bytes-like value")

    if text.startswith("\ufeff"):
        raise ValueError("UTF-8 BOM is forbidden in canonical resource text")
    if "\x00" in text:
        raise ValueError("NUL is forbidden in canonical resource text")
    try:
        return normalize_line_endings(text).encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError("resource content contains invalid Unicode") from error


def _normalize_json_string(value: str) -> str:
    return normalize_utf8_text(value).decode("utf-8")


def normalize_json_value(value: object) -> JsonValue:
    """Copy a value into the strict JSON-compatible canonical value domain.

    Object keys are sorted during serialization, while array order is
    intentionally preserved. Strings use normalized line endings. Mapping keys
    must be strings, numbers must be finite, and non-JSON Python/YAML types are
    rejected instead of being coerced.
    """

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _normalize_json_string(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numbers are forbidden in JSON-compatible manifests")
        return value
    if isinstance(value, list):
        return [normalize_json_value(item) for item in value]
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("manifest object keys must be strings")
            normalized_key = _normalize_json_string(key)
            if normalized_key in normalized:
                raise ValueError("manifest object keys collide after normalization")
            normalized[normalized_key] = normalize_json_value(item)
        return normalized
    raise TypeError(f"value of type {type(value).__name__!r} is not JSON-compatible")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def parse_json_compatible_document(content: TextContent) -> JsonValue:
    """Parse a Stage 0 JSON-compatible YAML/JSON document fail-closed."""

    normalized = normalize_utf8_text(content)
    try:
        parsed = json.loads(
            normalized,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number is forbidden: {value}")
            ),
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("document is not valid JSON-compatible YAML/JSON") from error
    return normalize_json_value(parsed)


def canonical_json_bytes(value: object) -> bytes:
    """Serialize a JSON-compatible value with stable keys and trailing LF."""

    normalized = normalize_json_value(value)
    serialized = json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return serialized.encode("utf-8") + b"\n"


def canonical_manifest_bytes(
    manifest: Mapping[str, object],
    *,
    include_lifecycle: bool = True,
) -> bytes:
    """Return canonical manifest bytes, optionally excluding mutable status.

    Only the top-level frozen lifecycle projection ``status`` is excluded.
    Nested fields named ``status`` remain part of business content.
    """

    if not isinstance(manifest, Mapping):
        raise TypeError("manifest must be a mapping")
    normalized = normalize_json_value(manifest)
    if not isinstance(normalized, dict):  # Defensive narrowing for type checkers.
        raise TypeError("manifest must normalize to a JSON object")
    if not include_lifecycle:
        normalized = {
            key: value
            for key, value in normalized.items()
            if key not in LIFECYCLE_MANIFEST_FIELDS
        }
    return canonical_json_bytes(normalized)


def sha256_digest(content: bytes | bytearray | memoryview) -> str:
    """Return the frozen lowercase, algorithm-prefixed SHA-256 wire value."""

    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise TypeError("SHA-256 input must be bytes-like")
    return f"sha256:{hashlib.sha256(bytes(content)).hexdigest()}"


def compute_content_hash(content: TextContent) -> ContentHash:
    """Hash one UTF-8 text file after canonical line-ending normalization."""

    return ContentHash(sha256_digest(normalize_utf8_text(content)))


def compute_manifest_snapshot_hash(manifest: Mapping[str, object]) -> ManifestSnapshotHash:
    """Hash the complete canonical manifest, including lifecycle status."""

    return ManifestSnapshotHash(
        sha256_digest(canonical_manifest_bytes(manifest, include_lifecycle=True))
    )


def _canonical_artifacts(artifacts: ArtifactInput) -> tuple[tuple[str, bytes], ...]:
    source: Iterable[tuple[str | PurePosixPath, TextContent]]
    if isinstance(artifacts, Mapping):
        source = artifacts.items()
    else:
        source = ((artifact.path, artifact.content) for artifact in artifacts)

    canonical: dict[str, bytes] = {}
    for raw_path, content in source:
        path = validate_relative_path(raw_path).as_posix()
        if path == _MANIFEST_RECORD_PATH:
            raise ValueError("resource.yaml is hashed from the manifest value, not as an artifact")
        if path in canonical:
            raise ValueError(f"duplicate managed artifact path after normalization: {path!r}")
        canonical[path] = normalize_utf8_text(content)
    return tuple(sorted(canonical.items(), key=lambda item: item[0].encode("utf-8")))


def _frame(payload: bytes) -> bytes:
    if len(payload) > _MAX_FRAME_LENGTH:
        raise ValueError("hash record exceeds the supported length")
    return len(payload).to_bytes(8, byteorder="big", signed=False) + payload


def _artifact_stream(
    manifest: Mapping[str, object],
    artifacts: ArtifactInput,
) -> tuple[bytes, tuple[tuple[str, bytes], ...]]:
    canonical_artifacts = _canonical_artifacts(artifacts)
    entrypoint = manifest.get("entrypoint")
    if not isinstance(entrypoint, str):
        raise ValueError("manifest entrypoint must be a string")
    canonical_entrypoint = validate_relative_path(entrypoint).as_posix()
    if canonical_entrypoint not in {path for path, _ in canonical_artifacts}:
        raise ValueError("managed artifacts must include the manifest entrypoint")

    stream = bytearray(_ARTIFACT_STREAM_DOMAIN)
    manifest_content = canonical_manifest_bytes(manifest, include_lifecycle=False)
    stream.extend(_frame(_MANIFEST_RECORD_PATH.encode("utf-8")))
    stream.extend(_frame(manifest_content))
    for path, content in canonical_artifacts:
        stream.extend(_frame(path.encode("utf-8")))
        stream.extend(_frame(content))
    return bytes(stream), canonical_artifacts


def compute_artifact_hash(
    manifest: Mapping[str, object],
    artifacts: ArtifactInput,
) -> ArtifactHash:
    """Hash immutable manifest semantics and all managed files deterministically."""

    stream, _ = _artifact_stream(manifest, artifacts)
    return ArtifactHash(sha256_digest(stream))


def calculate_resource_hashes(
    manifest: Mapping[str, object],
    artifacts: ArtifactInput,
) -> ResourceHashes:
    """Calculate authoritative aggregate hashes and diagnostic file hashes once."""

    stream, canonical_artifacts = _artifact_stream(manifest, artifacts)
    content_hashes = {
        path: ContentHash(sha256_digest(content)) for path, content in canonical_artifacts
    }
    return ResourceHashes(
        artifact_hash=ArtifactHash(sha256_digest(stream)),
        manifest_snapshot_hash=compute_manifest_snapshot_hash(manifest),
        content_hashes=content_hashes,
    )
