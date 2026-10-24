"""Typed chunking: split a resource version into searchable index chunks.

Each resource kind has its own natural structure, so chunking is type-specific
rather than a generic fixed-width split. This keeps a chunk meaningful on its
own, which matters for two reasons:

* **Semantic recall** — an embedding of "参数 Schema" alone is meaningless;
  an embedding of one tool's parameters is meaningful.
* **Explainability** — ``matched_fields`` and highlights point at a real
  section, not at an arbitrary character window.

The field weights below implement the frozen weighting policy. They are named
constants so tests can assert them and so a future tuning change is a single
edit rather than a search through the scorer.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from . import Chunk, IndexDocument, IndexField

__all__ = [
    "FIELD_WEIGHTS",
    "MAX_CHUNK_CHARS",
    "MIN_CHUNK_CHARS",
    "build_index_document",
    "chunk_resource",
    "extract_type_spec",
]

# Frozen weighting policy: a name match outranks a body match by 5x.
FIELD_WEIGHTS: dict[IndexField, float] = {
    IndexField.NAME: 5.0,
    IndexField.ALIASES: 4.0,
    IndexField.TAGS: 3.5,
    IndexField.CATEGORIES: 3.0,
    IndexField.RUNTIMES: 2.5,
    IndexField.DESCRIPTION: 2.0,
    IndexField.DEPENDENCIES: 1.5,
    IndexField.BODY: 1.0,
}

MAX_CHUNK_CHARS = 1200
MIN_CHUNK_CHARS = 1

_HEADING_RE = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*$")
# Split *after* a sentence terminator with a zero-width assertion. Consuming
# the whitespace would drop it when the terminator is immediately followed by
# another sentence, which is the normal case for Chinese text.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；!?;])|(?<=\.)\s+")


def _slugify_section(text: str) -> str:
    """Turn a heading into a stable, ASCII-safe section label."""

    stripped = text.strip().strip("#").strip()
    normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", stripped).strip("-")
    return normalized or "section"


def _split_long(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split oversized text on sentence boundaries, never mid-sentence.

    A single sentence longer than ``limit`` is hard-split as a last resort; the
    result is still deterministic, which B7 (reproducibility) requires.
    """

    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    buffer = ""
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if not sentence:
            continue
        if len(buffer) + len(sentence) <= limit:
            buffer += sentence
            continue
        if buffer:
            parts.append(buffer)
            buffer = ""
        while len(sentence) > limit:
            parts.append(sentence[:limit])
            sentence = sentence[limit:]
        buffer = sentence
    if buffer:
        parts.append(buffer)
    return parts or [text]


def _markdown_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into ``(section_label, body)`` pairs by heading.

    Text before the first heading is returned under ``overview`` so nothing is
    silently dropped.
    """

    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        title = "overview"
        return [(title, text.strip())] if text.strip() else []

    sections: list[tuple[str, str]] = []
    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.append(("overview", preamble))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        label = _slugify_section(match.group(2))
        if body:
            sections.append((label, body))
    return sections


def _as_text(value: Any) -> str:
    """Render an arbitrary manifest value as compact, deterministic text."""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, Mapping):
        return " ".join(
            f"{key} {_as_text(item)}" for key, item in value.items() if _as_text(item)
        )
    if isinstance(value, (list, tuple)):
        return " ".join(_as_text(item) for item in value if _as_text(item))
    return str(value)


def _dict_items(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, Mapping)]
    if isinstance(value, Mapping):
        return [value]
    return []


def _chunks_from_sections(
    sections: Iterable[tuple[str, str]], *, field: IndexField, start_ordinal: int
) -> list[Chunk]:
    chunks: list[Chunk] = []
    ordinal = start_ordinal
    for label, body in sections:
        for part_index, part in enumerate(_split_long(body)):
            if len(part) < MIN_CHUNK_CHARS:
                continue
            suffix = "" if part_index == 0 else f"-{part_index}"
            chunks.append(
                Chunk(
                    chunk_id=f"{label}{suffix}",
                    section=label,
                    field=field,
                    text=part,
                    ordinal=ordinal,
                )
            )
            ordinal += 1
    return chunks


def _chunk_mcp(spec: Mapping[str, Any]) -> list[Chunk]:
    """MCP splits per tool: one chunk per tool keeps parameter text coherent."""

    sections: list[tuple[str, str]] = []
    overview_bits = [
        _as_text(spec.get("transport")),
        _as_text(spec.get("description")),
    ]
    overview = " ".join(bit for bit in overview_bits if bit)
    if overview:
        sections.append(("overview", overview))
    for tool in _dict_items(spec.get("tools")):
        name = _as_text(tool.get("name")) or "tool"
        body = " ".join(
            bit
            for bit in (
                _as_text(tool.get("description")),
                _as_text(tool.get("input_schema") or tool.get("parameters")),
                _as_text(tool.get("output_schema") or tool.get("returns")),
            )
            if bit
        )
        if body:
            sections.append((f"tool-{_slugify_section(name)}", body))
    return _chunks_from_sections(sections, field=IndexField.BODY, start_ordinal=0)


def _chunk_prompt(spec: Mapping[str, Any], files: Mapping[str, str]) -> list[Chunk]:
    """Prompt splits by template block, falling back to markdown headings."""

    sections: list[tuple[str, str]] = []
    for key in ("system_role", "task", "variables", "output_format"):
        body = _as_text(spec.get(key))
        if body:
            sections.append((key.replace("_", "-"), body))
    if not sections:
        for path, text in sorted(files.items()):
            sections.extend(_markdown_sections(text))
    return _chunks_from_sections(sections, field=IndexField.BODY, start_ordinal=0)


def _chunk_agent(spec: Mapping[str, Any]) -> list[Chunk]:
    """Agent splits by configuration concern (role, tools, permissions, ...)."""

    sections: list[tuple[str, str]] = []
    for key in (
        "role",
        "goal",
        "tools",
        "permissions",
        "context_strategy",
        "memory",
        "decision_rules",
        "safety_boundaries",
    ):
        body = _as_text(spec.get(key))
        if body:
            sections.append((key.replace("_", "-"), body))
    return _chunks_from_sections(sections, field=IndexField.BODY, start_ordinal=0)


def _chunk_workflow(spec: Mapping[str, Any]) -> list[Chunk]:
    """Workflow splits by node, keeping each node's config self-contained."""

    sections: list[tuple[str, str]] = []
    goal = _as_text(spec.get("goal") or spec.get("description"))
    if goal:
        sections.append(("goal", goal))
    for node in _dict_items(spec.get("nodes")):
        name = _as_text(node.get("id") or node.get("name")) or "node"
        body = " ".join(
            bit
            for bit in (
                _as_text(node.get("type")),
                _as_text(node.get("description")),
                _as_text(node.get("config")),
                _as_text(node.get("inputs")),
                _as_text(node.get("outputs")),
            )
            if bit
        )
        if body:
            sections.append((f"node-{_slugify_section(name)}", body))
    return _chunks_from_sections(sections, field=IndexField.BODY, start_ordinal=0)


def _chunk_tool(spec: Mapping[str, Any]) -> list[Chunk]:
    """Tool splits by invocation contract."""

    sections: list[tuple[str, str]] = []
    for key in ("entrypoint", "execution_mode", "parameters", "returns", "permissions"):
        body = _as_text(spec.get(key))
        if body:
            sections.append((key.replace("_", "-"), body))
    return _chunks_from_sections(sections, field=IndexField.BODY, start_ordinal=0)


def _chunk_skill(files: Mapping[str, str]) -> list[Chunk]:
    """Skill splits by heading in its entrypoint (steps, limits, examples)."""

    sections: list[tuple[str, str]] = []
    for _path, text in sorted(files.items()):
        sections.extend(_markdown_sections(text))
    return _chunks_from_sections(sections, field=IndexField.BODY, start_ordinal=0)


def extract_type_spec(kind: str, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return the kind-specific spec section of a manifest.

    The frozen resource protocol stores the typed section under the bare kind
    name (``mcp:``, ``agent:``, ...), not ``mcp_spec:``. Centralising the lookup
    prevents the two call sites (the API projection and the evaluation script)
    from disagreeing, which previously produced documents with zero chunks for
    four of the six kinds.
    """

    section = manifest.get(kind)
    return dict(section) if isinstance(section, Mapping) else {}


def chunk_resource(
    kind: str,
    type_spec: Mapping[str, Any],
    files: Mapping[str, str],
) -> list[Chunk]:
    """Dispatch to the kind-specific chunker.

    ``files`` holds the entrypoint and any managed artifacts, keyed by relative
    path with UTF-8 text content. Unknown kinds fall back to markdown splitting
    over every file, so a future kind still yields a usable index.
    """

    spec = type_spec or {}
    if kind == "mcp":
        return _chunk_mcp(spec)
    if kind == "prompt":
        return _chunk_prompt(spec, files)
    if kind == "agent":
        return _chunk_agent(spec)
    if kind == "workflow":
        return _chunk_workflow(spec)
    if kind == "tool":
        return _chunk_tool(spec)
    return _chunk_skill(files)


def build_index_document(
    *,
    resource_public_id: str,
    version_public_id: str,
    kind: str,
    slug: str,
    name: str,
    version: str,
    status: str,
    owner_team: str,
    description: str,
    manifest: Mapping[str, Any],
    type_spec: Mapping[str, Any],
    files: Mapping[str, str],
    artifact_hash: str,
    manifest_snapshot_hash: str,
    updated_at_epoch: float = 0.0,
) -> IndexDocument:
    """Assemble a complete index document for one resource version.

    ``files`` is optional in practice: when the caller cannot read Git content
    (for example a lifecycle-only update), the manifest still yields usable
    chunks and the document records the content hashes it was told about. A
    document is never invented from nothing — ``artifact_hash`` must be
    supplied by the caller from relational facts.
    """

    if not artifact_hash:
        raise ValueError("artifact_hash is required: an index entry must bind to immutable content")

    def _strings(value: Any) -> tuple[str, ...]:
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value if isinstance(item, (str, int, float)))
        if isinstance(value, str):
            return (value,)
        return ()

    dependencies = []
    for dep in _dict_items(manifest.get("dependencies")):
        label = " ".join(
            str(dep.get(key))
            for key in ("kind", "slug")
            if dep.get(key)
        )
        if label:
            dependencies.append(label)

    permission_names: list[str] = []
    for perm in manifest.get("permissions") or ():
        if isinstance(perm, Mapping) and perm.get("name"):
            permission_names.append(str(perm["name"]))
        elif isinstance(perm, str):
            permission_names.append(perm)

    chunks = chunk_resource(kind, type_spec, files)

    return IndexDocument(
        resource_public_id=resource_public_id,
        version_public_id=version_public_id,
        kind=kind,
        slug=slug,
        name=name,
        version=version,
        status=status,
        owner_team=owner_team,
        description=description,
        aliases=_strings(manifest.get("aliases")),
        tags=_strings(manifest.get("tags")),
        categories=_strings(manifest.get("categories")),
        runtimes=_strings(manifest.get("runtimes")),
        dependency_labels=tuple(dependencies),
        permission_names=tuple(permission_names),
        artifact_hash=artifact_hash,
        manifest_snapshot_hash=manifest_snapshot_hash,
        chunks=tuple(chunks),
        updated_at_epoch=updated_at_epoch,
    )
