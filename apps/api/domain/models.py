"""Canonical enums and immutable value objects for the governance domain.

Every value here is deterministic and framework-free.  String enums use the
exact frozen Stage 0 wire values; the hashes are opaque ``NewType`` wrappers
so type checkers and readers can tell them apart without runtime overhead.

Public identifiers are 26-character uppercase Crockford Base32 ULIDs matching
the frozen regex ``^[0-9A-HJKMNP-TV-Z]{26}$``.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, NewType

__all__ = [
    "ActorIdentity",
    "ActorType",
    "ArtifactHash",
    "ContentHash",
    "JobState",
    "LifecycleState",
    "ManifestSnapshotHash",
    "ResourceKind",
    "ReviewDecision",
    "Role",
    "Runtime",
    "VersionIdentifier",
    "Visibility",
    "new_ulid",
    "validate_ulid",
]

# Opaque, algorithm-prefixed digest strings of the form ``sha256:<64 hex>``.
ArtifactHash = NewType("ArtifactHash", str)
ManifestSnapshotHash = NewType("ManifestSnapshotHash", str)
ContentHash = NewType("ContentHash", str)


class ResourceKind(StrEnum):
    """The six governed resource kinds."""

    SKILL = "skill"
    MCP = "mcp"
    PROMPT = "prompt"
    AGENT = "agent"
    WORKFLOW = "workflow"
    TOOL = "tool"


class LifecycleState(StrEnum):
    """The exact seven-state frozen lifecycle."""

    DRAFT = "draft"
    VALIDATING = "validating"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class ReviewDecision(StrEnum):
    """Append-only review decisions; conditional approval is request_changes."""

    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"


class JobState(StrEnum):
    """Frozen job execution vocabulary."""

    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    RETRY_SCHEDULED = "retry_scheduled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


class Runtime(StrEnum):
    """The five declared compatibility runtimes."""

    KIRO = "kiro"
    CLAUDE_CODE = "claude-code"
    CURSOR = "cursor"
    DIFY = "dify"
    OPENAI_COMPATIBLE = "openai-compatible-agent-runtime"


class Role(StrEnum):
    """Frozen governance roles. UI labels grant nothing by themselves."""

    OWNER = "owner"
    VALIDATOR = "validator"
    REVIEWER = "reviewer"
    PUBLISHER = "publisher"


class ActorType(StrEnum):
    """Authoritative actor categories for events and audit entries."""

    USER = "user"
    SERVICE = "service"
    WORKER = "worker"
    SYSTEM = "system"


class Visibility(StrEnum):
    """Derived resource visibility classes.

    The frozen ``resource`` table has no visibility column.  Effective
    visibility is derived in the query layer from ownership, team membership,
    organization membership, and lifecycle state; these values are therefore a
    query classification, never a persisted client-supplied fact.
    """

    PRIVATE = "private"
    TEAM = "team"
    ORGANIZATION = "organization"
    PUBLIC = "public"


@dataclass(frozen=True, slots=True)
class ActorIdentity:
    """Server-derived identity; client-supplied identity is never authority."""

    subject_id: str
    actor_type: ActorType = ActorType.USER
    display_name: str | None = None

    @property
    def actor_id(self) -> str:
        return self.subject_id


@dataclass(frozen=True, slots=True)
class VersionIdentifier:
    """Strict SemVer 2.0.0 value with deterministic precedence.

    Build metadata does not affect precedence.  Prerelease identifiers compare
    numerically when both are numeric and lexically (ASCII) otherwise, with
    numeric identifiers always lower than alphanumeric ones; a shorter
    prerelease list sorts before a longer one that extends it.
    """

    major: int
    minor: int
    patch: int
    prerelease: tuple[str | int, ...] = ()
    build: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str) -> "VersionIdentifier":
        if not isinstance(text, str):
            raise ValueError("version must be a string")
        stripped = text.strip()
        if not stripped or stripped != text:
            raise ValueError("version must not be padded with whitespace")

        build: tuple[str, ...] = ()
        if "+" in stripped:
            core, _, build_raw = stripped.partition("+")
            if not build_raw or build_raw != build_raw.strip():
                raise ValueError("invalid build metadata")
            build = tuple(build_raw.split("."))
            if not all(_is_valid_build_identifier(part) for part in build):
                raise ValueError("invalid build metadata")
        else:
            core = stripped

        prerelease: tuple[str | int, ...] = ()
        if "-" in core:
            core, _, pre_raw = core.partition("-")
            if not pre_raw or pre_raw != pre_raw.strip():
                raise ValueError("invalid prerelease")
            prerelease = tuple(_parse_prerelease_identifier(part) for part in pre_raw.split("."))

        numeric = core.split(".")
        if len(numeric) != 3:
            raise ValueError("version must be MAJOR.MINOR.PATCH")
        major, minor, patch = (_parse_numeric_component(part, name) for part, name in
                               zip(numeric, ("major", "minor", "patch")))
        return cls(major=major, minor=minor, patch=patch, prerelease=prerelease, build=build)

    @property
    def raw(self) -> str:
        core = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            core += "-" + ".".join(str(part) for part in self.prerelease)
        if self.build:
            core += "+" + ".".join(self.build)
        return core

    def __str__(self) -> str:
        return self.raw

    def __lt__(self, other: object) -> bool:
        return self._compare(other) < 0

    def __le__(self, other: object) -> bool:
        return self._compare(other) <= 0

    def __gt__(self, other: object) -> bool:
        return self._compare(other) > 0

    def __ge__(self, other: object) -> bool:
        return self._compare(other) >= 0

    def __eq__(self, other: object) -> bool:  # noqa: D105
        if not isinstance(other, VersionIdentifier):
            return NotImplemented
        return (
            self.major == other.major
            and self.minor == other.minor
            and self.patch == other.patch
            and self.prerelease == other.prerelease
        )

    def __hash__(self) -> int:
        return hash((self.major, self.minor, self.patch, self.prerelease))

    def _compare(self, other: object) -> int:
        if not isinstance(other, VersionIdentifier):
            raise TypeError("cannot compare VersionIdentifier with non-version value")
        for mine, theirs in (
            (self.major, other.major),
            (self.minor, other.minor),
            (self.patch, other.patch),
        ):
            if mine != theirs:
                return -1 if mine < theirs else 1
        return _compare_prerelease(self.prerelease, other.prerelease)


def _parse_numeric_component(part: str, name: str) -> int:
    if not part:
        raise ValueError(f"{name} component must not be empty")
    if not part.isdigit():
        raise ValueError(f"{name} component must be numeric")
    if len(part) > 1 and part.startswith("0"):
        raise ValueError(f"{name} component must not have leading zeroes")
    return int(part)


def _is_valid_build_identifier(part: str) -> bool:
    return bool(part) and all(ch.isalnum() or ch == "-" for ch in part)


def _parse_prerelease_identifier(part: str) -> str | int:
    if not part:
        raise ValueError("prerelease identifiers must not be empty")
    if part.isdigit():
        if len(part) > 1 and part.startswith("0"):
            raise ValueError("numeric prerelease identifiers must not have leading zeroes")
        return int(part)
    if not all(ch.isalnum() or ch == "-" for ch in part):
        raise ValueError("prerelease identifiers must be alphanumeric with hyphens")
    return part


def _compare_prerelease(a: tuple[str | int, ...], b: tuple[str | int, ...]) -> int:
    if not a and not b:
        return 0
    if not a:
        return 1  # release > prerelease
    if not b:
        return -1
    for left, right in zip(a, b):
        if left == right:
            continue
        if isinstance(left, int) and isinstance(right, int):
            return -1 if left < right else 1
        if isinstance(left, int):
            return -1  # numeric < alphanumeric
        if isinstance(right, int):
            return 1
        return -1 if str(left) < str(right) else 1
    return -1 if len(a) < len(b) else (1 if len(a) > len(b) else 0)


_ULID_ALPHABET: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_LENGTH: Final[int] = 26


def new_ulid(now_ms: int | None = None, entropy: bytes | None = None) -> str:
    """Return a monotonic-ish 26-char Crockford Base32 ULID.

    ``now_ms`` and ``entropy`` are injectable so tests remain deterministic.
    ``entropy`` must be exactly 10 bytes.
    """

    timestamp = int(time.time() * 1000) if now_ms is None else int(now_ms)
    if timestamp < 0:
        raise ValueError("ULID timestamp must be non-negative")
    timestamp &= 0xFFFFFFFFFFFF  # 48 bits
    if entropy is None:
        entropy = secrets.token_bytes(10)
    if not isinstance(entropy, (bytes, bytearray)) or len(entropy) != 10:
        raise ValueError("ULID entropy must be 10 bytes")
    value = int.from_bytes(timestamp.to_bytes(6, "big") + bytes(entropy), "big") << 2
    chars: list[str] = []
    for index in range(_ULID_LENGTH - 1, -1, -1):
        chars.append(_ULID_ALPHABET[(value >> (index * 5)) & 0x1F])
    return "".join(chars)


def validate_ulid(value: str) -> str:
    """Return ``value`` if it is a well-formed frozen ULID, otherwise raise."""

    if not isinstance(value, str) or len(value) != _ULID_LENGTH:
        raise ValueError("public id must be a 26-character ULID")
    if any(ch not in _ULID_ALPHABET for ch in value):
        raise ValueError("public id contains non-Crockford characters")
    return value
