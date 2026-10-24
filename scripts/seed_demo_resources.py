"""Seed the six demo resources into MySQL via the control-plane services.

Requires ``SUPERCODER_MYSQL_ENABLED=true`` and a reachable, migrated MySQL 8
database.  The managed Git root already contains ``resources/``; this script
only registers governance metadata (identity + first draft version) for the
existing content.  It never publishes or approves anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

# This file is executed as a script (``python scripts/seed_demo_resources.py``)
# rather than imported as a package module, so the repository root is not
# automatically on ``sys.path``. Add it before importing ``apps``.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from apps.api.application.resource_commands import ResourceCommandService  # noqa: E402
from apps.api.application.snapshots import SnapshotService  # noqa: E402
from apps.api.auth import AuthContext  # noqa: E402
from apps.api.config import get_settings  # noqa: E402
from apps.api.dependencies import build_container  # noqa: E402
from apps.api.domain.errors import ConflictError  # noqa: E402
from apps.api.domain.hashing import normalize_utf8_text, sha256_digest  # noqa: E402
from apps.api.domain.models import ActorIdentity, ActorType, ResourceKind  # noqa: E402
from apps.api.providers.git_repository import GitProvider  # noqa: E402
from apps.api.schemas.resources import CreateResourceRequest  # noqa: E402

_OWNER = AuthContext(
    actor=ActorIdentity("seed-owner", ActorType.SYSTEM, "seed"),
    grants=(),
    teams=frozenset({"platform-ai"}),
    ui_roles_by_team={"platform-ai": "maintainer"},
    organization_member=True,
    governance_roles=frozenset({"owner"}),
)


def main() -> int:
    settings = get_settings()
    if not settings.mysql.enabled:
        print("SUPERCODER_MYSQL_ENABLED must be true to seed MySQL.", file=sys.stderr)
        return 2

    container = build_container(settings)
    git = GitProvider(settings.git.repository_root, read_only=True)
    snapshots = SnapshotService(container.manifest_loader)

    seeded = 0
    already_present = 0
    failures: list[str] = []
    for directory in git.list_resource_directories():
        label = f"{directory.kind.value}/{directory.slug}"
        loaded = snapshots.load(directory.kind, directory.slug)
        artifacts = [
            {
                "path": path,
                "digest": sha256_digest(normalize_utf8_text(content)),
                "size_bytes": len(normalize_utf8_text(content)),
            }
            for path, content in sorted(loaded.files.items())
        ]
        request = CreateResourceRequest(
            manifest=loaded.manifest,
            artifacts=artifacts,
            reason="stage1 seed",
        )
        try:
            container.resource_commands.create_resource(
                actor=_OWNER.actor,
                request=request,
                correlation_id="seed-demo-resources",
            )
            seeded += 1
            print(f"seeded {label} {loaded.version}")
        except ConflictError:
            # Re-running the seed is expected to hit the natural unique keys.
            # That is idempotent success, not a failure.
            already_present += 1
            print(f"already present {label} {loaded.version}")
        except Exception as error:  # noqa: BLE001 - report and continue, fail at end
            failures.append(label)
            print(f"FAILED {label}: {error}", file=sys.stderr)

    print(
        f"seeded {seeded} resources, {already_present} already present, "
        f"{len(failures)} failed"
    )
    if failures:
        # Non-zero exit so a compose `service_completed_successfully` gate
        # blocks the worker instead of starting it against a partial database.
        print(f"seed failed for: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
