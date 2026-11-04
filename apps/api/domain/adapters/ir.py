"""Intermediate representation for exporting a resource to a runtime environment.

The IR exists to keep N environments from becoming N² conversions. Every adapter receives
the same `ExportBundle` and decides only where the files land; none of them re-derives
content from the resource, so two adapters cannot disagree about what a skill says.

Design rules this module holds to:

* **Deterministic.** The same version must produce byte-identical files. Every mapping is
  ordered, and nothing embeds a timestamp, a random id, or filesystem order.
* **Lossless by default, explicit about loss.** An adapter that cannot carry something
  must say so in `warnings`. Silently dropping a capability is the failure mode the
  platform exists to prevent, so the type has no way to represent "quietly omitted".
* **Plain data.** No repository handles, no database, no clock. A bundle is something you
  can diff, hash, and assert against byte for byte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

__all__ = [
    "BundleFile",
    "ExportBundle",
    "ExportWarning",
    "PermissionReport",
    "ValidationIssue",
    "ValidationResult",
]


@dataclass(frozen=True, slots=True)
class BundleFile:
    """One file in an export, with its exact bytes.

    `text` is stored as `str` because every file this platform exports is text; the
    adapter encodes it once, at the boundary, so a bundle can be compared in Python
    without decoding.
    """

    path: str
    text: str
    media_type: str = "text/markdown"

    def __post_init__(self) -> None:
        if not self.path or self.path.startswith("/") or ".." in self.path.split("/"):
            raise ValueError(f"unsafe bundle path: {self.path!r}")

    def encoded(self) -> bytes:
        return self.text.encode("utf-8")


@dataclass(frozen=True, slots=True)
class ExportWarning:
    """A capability the target environment cannot carry.

    Warnings are first-class: the contract requires the platform to state what will be
    lost. `source` names the feature that triggered it, `effect` says what happens to it,
    and `mitigation` — when one exists — says how a user could compensate.
    """

    code: str
    source: str
    effect: str
    mitigation: str | None = None


@dataclass(frozen=True, slots=True)
class PermissionReport:
    """What the exported artefact needs from the environment.

    `declared` is what the resource says it needs. `requires_approval` is what the target
    will actually prompt for. `unsupported` is what cannot be expressed at all — the gap a
    reviewer needs to see before trusting a deployment.
    """

    declared: tuple[str, ...] = ()
    requires_approval: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One static problem with an exported bundle.

    `fatal` distinguishes "the environment will refuse to load this" from "it loads but
    is worse than it should be". Both are reported; only fatal ones block.
    """

    path: str
    message: str
    fatal: bool = True


@dataclass(frozen=True, slots=True)
class ValidationResult:
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        """True when nothing fatal was found.

        Non-fatal issues do not fail validation: an environment that loads a skill with a
        cosmetic problem still provides the capability, and pretending otherwise would
        make the check useless in practice.
        """

        return not any(issue.fatal for issue in self.issues)

    @property
    def fatal(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.fatal)


@dataclass(frozen=True, slots=True)
class ExportBundle:
    """A complete export: files, the environment they target, and what was lost.

    `files` is a mapping so callers can look up by path; `ordered_files()` returns them in
    a stable order for hashing and archive building.
    """

    target: str
    resource_id: str
    version_id: str
    resource_version: str
    files: Mapping[str, BundleFile] = field(default_factory=dict)
    warnings: tuple[ExportWarning, ...] = ()
    permissions: PermissionReport = field(default_factory=PermissionReport)

    def ordered_files(self) -> tuple[BundleFile, ...]:
        return tuple(self.files[key] for key in sorted(self.files))

    def digest(self) -> str:
        """A hash over the bundle's content, for comparing exports.

        Only the paths and bytes are hashed, never the target name: two environments that
        receive identical content should have identical digests, which is what makes
        "the adapter changed nothing but the directory" checkable.
        """

        import hashlib

        digest = hashlib.sha256()
        for entry in self.ordered_files():
            digest.update(entry.path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(entry.encoded())
            digest.update(b"\0")
        return "sha256:" + digest.hexdigest()
