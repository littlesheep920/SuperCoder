"""Deterministic syntax validation and normalization helpers.

These helpers enforce the frozen Stage 0 lexical rules for relative paths,
slugs, tags, categories, and SemVer.  They are pure functions: no filesystem,
no clock, no process-global state.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath

from .models import VersionIdentifier

__all__ = [
    "SLUG_RE",
    "normalize_category",
    "normalize_tag",
    "parse_semver",
    "validate_category",
    "validate_relative_path",
    "validate_slug",
]

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CATEGORY_RE = re.compile(r"^[a-z0-9]+(?:[-/][a-z0-9]+)*$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def validate_relative_path(value: str | PurePosixPath) -> PurePosixPath:
    """Validate a safe resource-relative path and return its canonical form.

    Absolute, drive-qualified, UNC, empty, and parent-traversing paths are
    rejected.  Backslashes normalize to ``/``; ``.`` and redundant separators
    are collapsed.  Symlink/reparse-point escape detection is the provider's
    responsibility and happens after this lexical check.
    """

    if isinstance(value, PurePosixPath):
        text = value.as_posix()
    elif isinstance(value, str):
        text = value
    else:
        raise TypeError("path must be a string or PurePosixPath")

    if not text or text != text.strip():
        raise ValueError("path must be non-empty and unpadded")
    if "\x00" in text:
        raise ValueError("path must not contain NUL")
    if _DRIVE_RE.match(text):
        raise ValueError("Windows drive-qualified paths are forbidden")

    normalized = text.replace("\\", "/")
    if normalized.startswith("/"):
        raise ValueError("absolute and UNC paths are forbidden")

    segments = normalized.split("/")
    if any(segment == ".." for segment in segments):
        raise ValueError("parent-traversing path segments are forbidden")

    clean = [segment for segment in segments if segment not in ("", ".")]
    if not clean:
        raise ValueError("path must contain at least one real segment")

    canonical = PurePosixPath("/".join(clean))
    if canonical.is_absolute():
        raise ValueError("absolute paths are forbidden")
    return canonical


def validate_slug(value: str) -> str:
    """Validate a frozen manifest slug (2-160 lowercase hyphenated chars)."""

    if not isinstance(value, str):
        raise TypeError("slug must be a string")
    if not 2 <= len(value) <= 160:
        raise ValueError("slug length must be between 2 and 160")
    if not SLUG_RE.match(value):
        raise ValueError(
            "slug must be lowercase alphanumeric words separated by single hyphens"
        )
    return value


def validate_category(value: str) -> str:
    """Validate the frozen category-path format and return its normalized form."""

    if not isinstance(value, str):
        raise TypeError("category must be a string")
    normalized = normalize_category(value)
    if not 2 <= len(normalized) <= 160:
        raise ValueError("category length must be between 2 and 160")
    if not _CATEGORY_RE.match(normalized):
        raise ValueError("category must use lowercase alphanumeric segments with '-' or '/'")
    return normalized


def normalize_category(value: str) -> str:
    """Deterministically normalize a category path (Unicode NFC + lowercase)."""

    if not isinstance(value, str):
        raise TypeError("category must be a string")
    return unicodedata.normalize("NFC", value.strip()).lower()


def normalize_tag(value: str) -> str:
    """Deterministically normalize a free-form tag (Unicode NFC + lowercase)."""

    if not isinstance(value, str):
        raise TypeError("tag must be a string")
    return unicodedata.normalize("NFC", value.strip()).lower()


def parse_semver(value: str) -> VersionIdentifier:
    """Parse a strict SemVer string into a :class:`VersionIdentifier`."""

    return VersionIdentifier.parse(value)
