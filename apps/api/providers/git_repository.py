"""Contained managed-Git adapter for versioned resource content.

This adapter talks to one explicitly configured local Git root through the
``git`` CLI.  It only reads ``resources/<mapped-kind-directory>/<slug>/``
paths, rejects every escape (absolute, drive, UNC, parent, home, symlink,
reparse point), and performs bounded UTF-8 reads.  It never mutates remotes,
hooks, branches, or repository configuration.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..domain.models import ResourceKind
from ..domain.validation import validate_relative_path

__all__ = [
    "GitProvider",
    "KIND_DIRECTORIES",
    "ResourceDirectory",
    "resolve_resource_root",
]

KIND_DIRECTORIES: dict[ResourceKind, str] = {
    ResourceKind.SKILL: "skills",
    ResourceKind.MCP: "mcp",
    ResourceKind.PROMPT: "prompts",
    ResourceKind.AGENT: "agents",
    ResourceKind.WORKFLOW: "workflows",
    ResourceKind.TOOL: "tools",
}

_MAX_READ_BYTES = 1_048_576  # 1 MiB per text artifact


@dataclass(frozen=True, slots=True)
class ResourceDirectory:
    kind: ResourceKind
    slug: str
    root: Path


def resolve_resource_root(
    repository_root: str | Path, kind: ResourceKind, slug: str
) -> Path:
    """Resolve the bounded resource directory for ``(kind, slug)``.

    The canonical layout is ``resources/<kind-directory>/<slug>/``.  Any path
    escape is rejected fail-closed.
    """

    base = Path(repository_root).expanduser()
    try:
        relative = PurePosixPath("resources", KIND_DIRECTORIES[kind], slug)
    except KeyError as error:
        raise ValueError(f"unsupported resource kind: {kind}") from error
    candidate = (base / Path(*relative.parts)).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError as error:
        raise ValueError("resource path escapes the managed repository root") from error
    return candidate


class GitProvider:
    """Minimal, read-only-first managed Git access via the ``git`` CLI."""

    def __init__(self, repository_root: str | Path, *, read_only: bool = True) -> None:
        self._root = Path(repository_root).expanduser().resolve()
        self._read_only = read_only

    @property
    def repository_root(self) -> Path:
        return self._root

    def _git(self, *args: str) -> str:
        command = ["git", "-C", str(self._root), *args]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"git command failed ({' '.join(command)}): {completed.stderr.strip()}"
            )
        return completed.stdout

    def current_commit_sha(self) -> str | None:
        """Return the exact HEAD commit SHA, or ``None`` when unavailable."""

        try:
            return self._git("rev-parse", "HEAD").strip() or None
        except RuntimeError:
            return None

    def is_dirty(self) -> bool:
        try:
            return bool(self._git("status", "--porcelain").strip())
        except RuntimeError:
            return True

    def _safe_read(self, path: Path) -> str:
        if not path.is_file():
            raise FileNotFoundError(f"resource artifact does not exist: {path}")
        if path.stat().st_size > _MAX_READ_BYTES:
            raise ValueError(f"resource artifact exceeds the size limit: {path}")
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"resource artifact is not valid UTF-8: {path}") from error

    def read_resource_files(self, kind: ResourceKind, slug: str) -> dict[str, str]:
        """Read every UTF-8 text file inside one resource directory.

        Returns a mapping of repository-relative POSIX paths to content.  The
        ``resource.yaml`` manifest is included.  Symlinks and reparse points
        that escape the resource root are rejected.
        """

        root = resolve_resource_root(self._root, kind, slug)
        if not root.is_dir():
            raise FileNotFoundError(f"resource directory does not exist: {root}")
        files: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.is_symlink() or _is_reparse_point(path):
                raise ValueError(f"symlink/reparse points are forbidden: {path}")
            relative = path.relative_to(root)
            rel_posix = relative.as_posix()
            if rel_posix.startswith("."):
                continue
            files[rel_posix] = self._safe_read(path)
        return files

    def read_entrypoint(self, kind: ResourceKind, slug: str, entrypoint: str) -> str:
        """Read a validated entrypoint relative to the resource directory."""

        root = resolve_resource_root(self._root, kind, slug)
        relative = validate_relative_path(entrypoint)
        target = (root / Path(*relative.parts)).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as error:
            raise ValueError("entrypoint escapes the resource directory") from error
        return self._safe_read(target)

    def write_resource_file(
        self, kind: ResourceKind, slug: str, relative_path: str, content: str
    ) -> None:
        """Write one resource file. Requires a non-read-only provider."""

        if self._read_only:
            raise RuntimeError("managed Git provider is read-only")
        root = resolve_resource_root(self._root, kind, slug)
        relative = validate_relative_path(relative_path)
        target = (root / Path(*relative.parts)).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as error:
            raise ValueError("target escapes the resource directory") from error
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def list_resource_directories(self) -> tuple[ResourceDirectory, ...]:
        """Discover ``(kind, slug)`` resource directories under the managed root."""

        resources_root = self._root / "resources"
        if not resources_root.is_dir():
            return ()
        discovered: list[ResourceDirectory] = []
        for kind, directory in KIND_DIRECTORIES.items():
            kind_root = resources_root / directory
            if not kind_root.is_dir():
                continue
            for child in sorted(kind_root.iterdir()):
                if not child.is_dir() or child.name.startswith("."):
                    continue
                if (child / "resource.yaml").is_file():
                    discovered.append(ResourceDirectory(kind=kind, slug=child.name, root=child))
        return tuple(discovered)


def _is_reparse_point(path: Path) -> bool:
    """Detect Windows reparse points (junctions/symlinks) without extra deps."""

    try:
        return bool(path.stat().st_file_attributes & 0x400)
    except (AttributeError, OSError):
        return False
