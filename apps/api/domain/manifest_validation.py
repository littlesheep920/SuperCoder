"""Validate one resource manifest, for the registration channel.

``scripts/validate_stage0.py`` validates a whole repository: it walks every
manifest, cross-checks directories, and enforces repository-level invariants.
The registration channel needs a narrower thing — "is this single generated
manifest acceptable?" — but it must not use a *weaker* standard than the one that
gates commits, or a draft could pass review and then fail CI.

So this module reuses the same :class:`JsonSchemaValidator` and the same schema
file. It adds the cross-field rules that apply to a single manifest (kind/directory
agreement is a repository concern and is checked at write time instead).
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from typing import Any

__all__ = ["ManifestValidation", "load_schema", "validate_manifest"]

SCHEMA_PATH = pathlib.Path("schemas/resource.schema.json")
RESOURCE_KINDS = frozenset({"skill", "mcp", "prompt", "agent", "workflow", "tool"})


@dataclass(frozen=True, slots=True)
class ManifestValidation:
    """The result of validating one candidate manifest."""

    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_json(self) -> dict:
        return {"valid": self.valid, "errors": list(self.errors), "warnings": list(self.warnings)}


def load_schema(root: pathlib.Path) -> dict[str, Any]:
    return json.loads((root / SCHEMA_PATH).read_text(encoding="utf-8"))


def _load_validator(root: pathlib.Path):
    """Import the Stage 0 validator so both paths share one implementation."""

    import importlib.util
    import sys

    script = root / "scripts" / "validate_stage0.py"
    module_name = "_supercoder_stage0_validator"
    if module_name in sys.modules:
        return sys.modules[module_name].JsonSchemaValidator

    spec = importlib.util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("cannot load the Stage 0 validator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.JsonSchemaValidator


def validate_manifest(root: pathlib.Path, manifest: Any) -> ManifestValidation:
    """Check one manifest against the repository schema plus single-manifest rules."""

    if not isinstance(manifest, dict):
        return ManifestValidation(False, ("manifest must be an object",))

    errors: list[str] = []
    warnings: list[str] = []

    try:
        validator_cls = _load_validator(root)
        schema = load_schema(root)
        validator = validator_cls(schema)
        errors.extend(validator.errors(manifest))
    except Exception as error:  # noqa: BLE001 - never let validation itself crash the endpoint
        return ManifestValidation(False, (f"validator unavailable: {type(error).__name__}",))

    kind = manifest.get("kind")
    slug = manifest.get("slug")

    # The type-specific object must exist and be the only one present. The schema
    # expresses this, but stating it here gives a clearer message to a draft author.
    if kind in RESOURCE_KINDS:
        if not isinstance(manifest.get(kind), dict):
            errors.append(f"missing required type-specific object {kind!r}")
        for other in RESOURCE_KINDS - {kind}:
            if other in manifest:
                errors.append(f"contains unrelated type-specific object {other!r}")

    if isinstance(slug, str) and isinstance(kind, str):
        expected = f"resources/{'mcp' if kind == 'mcp' else kind + 's'}/{slug}"
        warnings.append(f"will be stored at {expected}")

    # Status is deliberately forced to draft: a generated manifest must not be
    # able to arrive already published.
    status = manifest.get("status")
    if status != "draft":
        warnings.append(
            f"status {status!r} will be replaced with 'draft'; registration never publishes"
        )

    return ManifestValidation(not errors, tuple(errors), tuple(warnings))
