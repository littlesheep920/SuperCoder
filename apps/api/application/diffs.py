"""Deterministic, authorized unified diff between two resource versions."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass

from ..domain.errors import ConflictError, ErrorCode, NotFoundError
from ..domain.hashing import canonical_manifest_bytes
from ..repositories.contracts import UnitOfWorkFactory, VisibilityScope

__all__ = ["DiffResult", "DiffService"]


@dataclass(frozen=True, slots=True)
class DiffResult:
    base_version_id: str
    target_version_id: str
    base_version: str
    target_version: str
    unified_diff: str
    changed_paths: tuple[str, ...]


class DiffService:
    """Generates manifest-level diffs bound to two exact, visible versions.

    File-level diffs require per-version Git history; when only the canonical
    manifests are available the diff is computed over those snapshots, which is
    deterministic and always valid.  Both versions must belong to the same
    resource identity.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def diff(
        self,
        *,
        base_version_id: str,
        target_version_id: str,
        scope: VisibilityScope,
    ) -> DiffResult:
        with self._uow_factory() as uow:
            base = uow.versions.get(base_version_id)
            target = uow.versions.get(target_version_id)
            uow.commit()

        if base is None or target is None:
            raise NotFoundError("version not found or not visible")
        if not self._visible(base, scope) or not self._visible(target, scope):
            raise NotFoundError("version not found or not visible")
        if base.resource_public_id != target.resource_public_id:
            raise ConflictError(
                code=ErrorCode.GATE_CONFLICT,
                message="versions belong to different resources",
            )

        base_text = canonical_manifest_bytes(base.manifest).decode("utf-8")
        target_text = canonical_manifest_bytes(target.manifest).decode("utf-8")
        diff_lines = list(
            difflib.unified_diff(
                base_text.splitlines(),
                target_text.splitlines(),
                fromfile=f"a/{base.resource_public_id}/{base.identifier.raw}.yaml",
                tofile=f"b/{target.resource_public_id}/{target.identifier.raw}.yaml",
                lineterm="",
            )
        )
        changed_paths = self._changed_paths(base.manifest, target.manifest)
        return DiffResult(
            base_version_id=base_version_id,
            target_version_id=target_version_id,
            base_version=base.identifier.raw,
            target_version=target.identifier.raw,
            unified_diff="\n".join(diff_lines),
            changed_paths=tuple(changed_paths),
        )

    @staticmethod
    def _visible(record, scope: VisibilityScope) -> bool:
        if scope.organization_member:
            return True
        return record.owner_team in scope.team_ids

    @staticmethod
    def _changed_paths(base: dict[str, object], target: dict[str, object]) -> list[str]:
        keys = set(base) | set(target)
        return sorted(
            key for key in keys
            if json.dumps(base.get(key), sort_keys=True) != json.dumps(target.get(key), sort_keys=True)
        )
