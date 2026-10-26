"""Registration drafts: natural language in, a validated manifest candidate out.

The hard rule, from PRD §2.2: **the model never registers anything.** It produces a
candidate manifest. A human reads the draft, the platform validates it against the
same schema that gates commits, and only an explicit ``POST /v1/resources`` — a
separate, authorized, audited call — creates anything.

Why the model is constrained this way rather than trusted to submit:

* The manifest schema is strict across six resource kinds. While building the
  evaluation corpus by hand, six schema errors occurred across 248 resources. A
  model writing a manifest unassisted will make the same mistakes, and a draft is
  the cheap place to catch them.
* Status is forced to ``draft``. A generated manifest cannot arrive published,
  approved, or validated.
* The description must state that the resource is an example unless the author
  says otherwise. Registering fake capability as if it were real is the failure
  this project has guarded against throughout.

The model sees an explicit list of allowed values (kinds, runtimes, lifecycle
states, required fields) extracted from the schema itself, not a prose summary that
could drift from it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..providers.llm import LlmClient, LlmError, LlmUnavailable
from .manifest_skeleton import build_manifest_skeleton
from .manifest_validation import ManifestValidation, validate_manifest

__all__ = [
    "DraftOutcome",
    "ManifestDrafter",
    "LlmManifestDrafter",
    "build_drafter",
    "extract_allowed_values",
]


@dataclass(frozen=True, slots=True)
class DraftOutcome:
    """A candidate manifest plus everything needed to review it.

    ``needs_input`` lists what the model could not infer. It is not an error: a
    draft that guesses at a slug or an owner team is worse than one that asks.
    """

    manifest: dict[str, Any] | None
    validation: ManifestValidation
    warnings: tuple[str, ...] = ()
    needs_input: tuple[str, ...] = ()
    degraded: tuple[str, ...] = ()
    model: str | None = None

    def to_json(self) -> dict:
        return {
            "manifest": self.manifest,
            "validation": self.validation.to_json(),
            "warnings": list(self.warnings),
            "needs_input": list(self.needs_input),
            "degraded": list(self.degraded),
            "model": self.model,
        }


def extract_allowed_values(schema: Mapping[str, Any]) -> dict[str, list[str]]:
    """Pull every closed value set the model must respect out of the schema.

    Reading them from the schema rather than restating them in the prompt means a
    schema change cannot leave the prompt describing the old contract.

    Nested enumerations matter as much as top-level ones. An early version listed
    only kind/runtime/status, and a real run then emitted ``execution_mode:
    "sync"`` — a value the model invented because nothing told it the allowed set.
    """

    values: dict[str, list[str]] = {}

    def add(name: str, enum: Any) -> None:
        if isinstance(enum, list) and enum:
            rendered = [str(item) for item in enum]
            if all(isinstance(item, str) for item in enum):
                values[name] = rendered

    defs = schema.get("$defs", {})

    kind_enum = schema.get("properties", {}).get("kind", {}).get("enum")
    add("kind", kind_enum)

    runtime_enum = defs.get("runtime", {}).get("enum")
    add("runtime", runtime_enum)

    status_enum = defs.get("lifecycleStatus", {}).get("enum")
    add("status", status_enum)

    required = schema.get("required")
    if isinstance(required, list):
        values["required_fields"] = [str(item) for item in required]

    # Nested enumerations, keyed by <spec>.<field> so the prompt is unambiguous.
    for spec_name in ("toolSpec", "mcpSpec", "promptSpec", "agentSpec", "workflowSpec"):
        properties = defs.get(spec_name, {}).get("properties", {})
        for field_name, definition in properties.items():
            enum = definition.get("enum")
            if enum:
                label = f"{spec_name.replace('Spec', '')}.{field_name}"
                add(label, enum)

    return values


_SYSTEM_TEMPLATE = """You draft a SuperCoder resource manifest from an engineer's description.

You are producing a DRAFT for a human to review. You never publish, approve, or
register anything. Another system validates your output and a person decides.

Allowed values you must respect exactly:
{allowed}

The manifest has a fixed shape. Copy this skeleton and fill it in; do not add,
remove, or rename top-level keys, and do not change the type of any value:

{skeleton}

Rules:
1. Output one JSON object: {{"manifest": <the filled skeleton>, "needs_input": [...]}}.
2. "schema_version" is the string "1" — not "1.0", not a number.
3. "inputs" and "outputs" are ARRAYS of objects with keys name, type, required,
   description. They are not objects or maps.
4. "status" is always "draft". Never omit it, never change it.
5. "slug" is kebab-case, lowercase, describing the capability (max 64 chars).
6. "owner_team" is kebab-case and must be non-empty; if the engineer did not name
   a team, use "unassigned" and list "owner_team" in needs_input.
7. "entrypoint" is the relative path of the file that carries the resource body,
   chosen by kind: skill=SKILL.md, prompt=PROMPT.md, agent=AGENT.md,
   workflow=WORKFLOW.md, tool=tool.py, mcp=MCP.md.
8. Include the type-specific object matching "kind" and follow its required keys
   shown in the skeleton. Include no other type-specific objects. The required
   keys for the OTHER kinds are listed here — do not copy them into your manifest:
{kind_keys}
9. "description" states what the resource does. If the engineer did not say this is
   a real reviewed capability, begin with "Example resource for the SuperCoder
   protocol." — never present unverified content as a reviewed asset.
10. Do not invent dependencies or permissions the engineer did not mention. Use []
    rather than guessing.
11. If a required field cannot be inferred, still emit the skeleton with a
    reasonable placeholder and list that field name in "needs_input".
"""


@dataclass
class LlmManifestDrafter:
    """Draft a manifest with an LLM, then validate it locally."""

    client: LlmClient | None
    root: str | None = None  # filesystem root holding schemas/, str to keep this importable
    allowed: dict[str, list[str]] = field(default_factory=dict)
    # The parsed schema, so the prompt skeleton and the validator read one contract.
    _schema: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return getattr(self.client, "name", "disabled")

    def _schema_root(self):
        import pathlib

        if self.root:
            return pathlib.Path(self.root)
        return pathlib.Path(__file__).resolve().parents[3]

    def _system_prompt(self, kind_hint: str) -> str:
        rendered = "\n".join(
            f"- {key}: {', '.join(values)}" for key, values in sorted(self.allowed.items())
        )
        # Show the skeleton for the hinted kind, plus the required keys of every
        # other kind. Showing a single skeleton made the model copy the skill
        # object into a tool manifest; showing all six full skeletons is too long.
        # This middle ground states what each kind requires without inviting a mix.
        kind = kind_hint or "skill"
        skeleton = build_manifest_skeleton(self._schema, kind) if self._schema else {}
        others = self._kind_key_summary(exclude=kind)
        return _SYSTEM_TEMPLATE.format(
            allowed=rendered,
            skeleton=json.dumps(skeleton, indent=2, ensure_ascii=False),
            kind_keys=others,
        )

    def _kind_key_summary(self, *, exclude: str) -> str:
        """List the required type-specific keys for each kind."""

        defs = (self._schema or {}).get("$defs", {})
        lines: list[str] = []
        for kind in ("skill", "tool", "mcp", "prompt", "agent", "workflow"):
            if kind == exclude:
                continue
            required = defs.get(f"{kind}Spec", {}).get("required", [])
            keys = ", ".join(str(item) for item in required) or "(none)"
            lines.append(f"- {kind}: the \"{kind}\" object MUST contain {keys}")
        return "\n".join(lines)

    def draft(self, *, description: str, kind_hint: str = "") -> DraftOutcome:
        if self.client is None or not getattr(self.client, "available", False):
            return DraftOutcome(
                manifest=None,
                validation=ManifestValidation(False, ("llm_unavailable",)),
                degraded=("llm_unavailable",),
                model=self.name,
            )

        user = f"Engineer description:\n{description}"
        if kind_hint:
            user += f"\n\nSuggested kind: {kind_hint}"

        try:
            result = self.client.complete(
                system=self._system_prompt(kind_hint), user=user, json_mode=True
            )
        except LlmUnavailable:
            return DraftOutcome(
                manifest=None,
                validation=ManifestValidation(False, ("llm_unavailable",)),
                degraded=("llm_unavailable",),
                model=self.name,
            )
        except LlmError as error:
            marker = f"llm_error:{error.reason}"
            return DraftOutcome(
                manifest=None,
                validation=ManifestValidation(False, (marker,)),
                degraded=(marker,),
                model=self.name,
            )

        try:
            payload = json.loads(result.text)
        except (ValueError, TypeError):
            return DraftOutcome(
                manifest=None,
                validation=ManifestValidation(False, ("model_output_unparseable",)),
                degraded=("model_output_unparseable",),
                model=self.name,
            )

        manifest = payload.get("manifest") if isinstance(payload, dict) else None
        needs_input = payload.get("needs_input") if isinstance(payload, dict) else None
        if not isinstance(manifest, dict):
            return DraftOutcome(
                manifest=None,
                validation=ManifestValidation(False, ("model_output_missing_manifest",)),
                degraded=("model_output_missing_manifest",),
                model=self.name,
            )

        asked = tuple(
            str(item).strip()
            for item in (needs_input or [])
            if isinstance(item, str) and item.strip()
        )

        # Force status before validating: a draft must never be born published, and
        # correcting it here means the validation result describes what would
        # actually be written.
        forced: list[str] = []
        if manifest.get("status") != "draft":
            forced.append(
                f"status was {manifest.get('status')!r}; forced to 'draft' — "
                "registration never publishes"
            )
            manifest["status"] = "draft"

        validation = validate_manifest(self._schema_root(), manifest)
        return DraftOutcome(
            manifest=manifest,
            validation=validation,
            warnings=tuple(forced) + validation.warnings,
            needs_input=asked,
            degraded=(),
            model=self.name,
        )


class ManifestDrafter:
    """Interface so the endpoint can be tested without a model."""

    @property
    def name(self) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def draft(self, *, description: str, kind_hint: str = "") -> DraftOutcome:
        raise NotImplementedError


def build_drafter(client: LlmClient | None, root: str | None = None) -> ManifestDrafter | None:
    """Return a drafter when a model is configured, else ``None``.

    ``None`` is deliberate: the registration endpoint reports that drafting is
    unavailable rather than emitting a half-formed manifest from a template. A
    template-based draft would look like a working feature while producing
    manifests nobody had reasoned about.
    """

    if client is None:
        return None

    import json
    import pathlib

    base = pathlib.Path(root) if root else pathlib.Path(__file__).resolve().parents[3]
    try:
        schema = json.loads((base / "schemas" / "resource.schema.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    return LlmManifestDrafter(
        client=client,
        root=str(base),
        allowed=extract_allowed_values(schema),
        _schema=schema,
    )
