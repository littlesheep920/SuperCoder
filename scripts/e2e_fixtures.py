"""Write end-to-end fixtures into the checkout, and take them away again.

The workflow and walkthrough checks register real resources, so they need real files under
`resources/`: the API validates and hashes what is on disk. The way they used to write those
files caused two problems.

* Nothing removed them. compose bind-mounts `./resources` into the API container, so a file
  written "inside the container" lands in the operator's working tree. The next
  `python -m unittest` then counted the fixture as one of the repository's workflows and
  `test_workflow_compiler` failed. Writing locally instead of through Docker changes nothing.
* The only writer was `docker compose exec` with `capture_output=True` and no check, so a
  stopped container surfaced much later as an unrelated 422 from the API.

This module is the one place that knows how fixtures are written. It refuses to overwrite a
directory it did not create (the old scripts began with `rm -rf`), removes exactly what it
created when a check ends (pass, fail or exception) unless asked to keep it, reports the
container's stderr when Docker fails, and writes bytes, so the digest it reports is the digest
of what is on disk; text mode on Windows would have written CRLF.

Fixture slugs end in a number (`example-e2e-flow-12345`). `FIXTURE_SLUG` matches only that
shape, so the real resources `example-e2e-scenario` and `example-acceptance-criteria` are never
mistaken for leftovers, and cleanup can never be pointed at one.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import shutil
import subprocess
from typing import Callable

FIXTURE_SLUG = re.compile(r"^example-(?:e2e|acceptance)(?:-[a-z]+)*-\d+$")
DIRECTORY = {
    "skill": "skills",
    "mcp": "mcp",
    "prompt": "prompts",
    "agent": "agents",
    "workflow": "workflows",
    "tool": "tools",
}
CONTAINER_ROOT = "/app"
MODES = ("compose", "local")


class FixtureError(RuntimeError):
    """A fixture could not be written or removed. The message says why."""


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--fixtures",
        choices=MODES,
        default="compose",
        help="compose: write through the API container (default). "
        "local: write into this checkout, for an API and worker running on this machine.",
    )
    parser.add_argument(
        "--keep-fixtures",
        action="store_true",
        help="leave the fixture directories in resources/ for inspection; delete them by hand afterwards",
    )


def artifact(path: str, content: bytes, media_type: str = "text/markdown") -> dict:
    """The artifact entry the API expects, computed from the exact bytes written."""

    return {
        "path": path,
        "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "media_type": media_type,
    }


def leftover_fixtures(root: pathlib.Path) -> list[str]:
    """Fixture directories present under `resources/`, as repository-relative paths."""

    base = root / "resources"
    if not base.is_dir():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in base.glob("*/*")
        if path.is_dir() and FIXTURE_SLUG.match(path.name)
    )


Runner = Callable[..., subprocess.CompletedProcess]


class Fixtures:
    """Fixture directories created by one check run."""

    def __init__(self, root: pathlib.Path, mode: str = "compose", *, keep: bool = False,
                 runner: Runner = subprocess.run) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown fixture mode {mode!r}; expected one of {MODES}")
        self.root = root
        self.mode = mode
        self.keep = keep
        self._run = runner
        self.created: list[str] = []

    # -- writing -------------------------------------------------------------------------

    def write(self, kind: str, slug: str, files: dict[str, bytes]) -> str:
        """Create `resources/<kind dir>/<slug>/` holding exactly `files`. Never overwrites."""

        if not FIXTURE_SLUG.match(slug):
            raise FixtureError(
                f"{slug!r} is not a fixture slug (example-e2e-...-<number> or example-acceptance-<number>); "
                "refusing to write it, because cleanup would then be able to delete a real resource"
            )
        relative = f"resources/{DIRECTORY[kind]}/{slug}"
        if self.mode == "local":
            self._write_local(relative, files)
        else:
            self._write_compose(relative, files)
        return relative

    def _write_local(self, relative: str, files: dict[str, bytes]) -> None:
        folder = self.root / relative
        if folder.exists():
            raise FixtureError(f"{relative} already exists; refusing to overwrite a directory this run did not create")
        folder.mkdir(parents=True)
        self.created.append(relative)
        for name, content in files.items():
            (folder / name).write_bytes(content)

    def _write_compose(self, relative: str, files: dict[str, bytes]) -> None:
        folder = f"{CONTAINER_ROOT}/{relative}"
        exists = self._exec(["test", "-e", folder], check=False)
        if exists.returncode == 0:
            raise FixtureError(f"{relative} already exists in the container's checkout; refusing to overwrite it")
        self._exec(["mkdir", "-p", "--", folder])
        self.created.append(relative)
        for name, content in files.items():
            self._exec(["sh", "-c", 'cat > "$1"', "sh", f"{folder}/{name}"], stdin=content)

    def _exec(self, command: list[str], *, stdin: bytes | None = None, check: bool = True):
        try:
            completed = self._run(
                ["docker", "compose", "exec", "-T", "api", *command],
                cwd=str(self.root), input=stdin if stdin is not None else b"", capture_output=True,
            )
        except OSError as error:
            raise FixtureError(f"cannot run docker compose: {error}") from error
        if check and completed.returncode != 0:
            stderr = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
            raise FixtureError(
                f"docker compose exec {' '.join(command[:3])} failed (exit {completed.returncode}): "
                f"{stderr or 'no output from the container'}"
            )
        return completed

    # -- cleanup -------------------------------------------------------------------------

    def cleanup(self) -> list[str]:
        """Remove every directory this run created, newest first. Returns what was removed."""

        removed: list[str] = []
        problems: list[str] = []
        for relative in reversed(self.created):
            try:
                if self.mode == "local":
                    folder = self.root / relative
                    if folder.exists():
                        shutil.rmtree(folder)
                else:
                    self._exec(["rm", "-rf", "--", f"{CONTAINER_ROOT}/{relative}"])
                removed.append(relative)
            except (OSError, FixtureError) as error:
                problems.append(f"{relative}: {error}")
        self.created = [item for item in self.created if item not in removed]
        if problems:
            raise FixtureError("could not remove every fixture:\n  " + "\n  ".join(problems))
        return removed

    def close(self) -> None:
        """End-of-run handling for a `finally:` block: clean up, or say what was kept."""

        if not self.created:
            return
        if self.keep:
            print("\nfixtures kept (--keep-fixtures); delete them when done:")
            for relative in self.created:
                print(f"  {relative}")
            return
        try:
            removed = self.cleanup()
        except FixtureError as error:
            print(f"\nWARNING: {error}")
            return
        print(f"\nfixtures removed: {', '.join(removed)}")

    def __enter__(self) -> "Fixtures":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
