"""Builders for workflow tests: resources, versions, and compiled definitions.

Registered through the real repositories rather than by poking at the store, so a test failure
means the service rejected something the API would also reject.

The helpers are deliberate about required fields. A builder that fills in plausible defaults
is how a fixture starts passing for reasons the production path would never allow, so every
identifier and hash here is generated from the slug and version it belongs to.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timezone

from apps.api.domain.models import LifecycleState, ResourceKind, VersionIdentifier
from apps.api.repositories.contracts import (
    NewResource,
    NewVersion,
    LifecycleUpdate,
    NewWorkflowDefinition,
    ResourceRecord,
    VisibilityScope,
    WorkflowDefinitionRecord,
)

_COUNTER = itertools.count(1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public_id() -> str:
    """A unique 26-character id shaped like the production ULID."""

    return f"01FIX{next(_COUNTER):021d}"


def register_capability(
    uow_factory,
    kind: str,
    slug: str,
    version: str,
    *,
    status: str = "published",
    artifact_hash: str | None = None,
    owner_team: str = "platform",
) -> ResourceRecord:
    """Register a resource with one version, optionally published."""

    resource_id = _public_id()
    with uow_factory() as uow:
        uow.resources.add(
            NewResource(
                public_id=resource_id,
                kind=ResourceKind(kind),
                slug=slug,
                owner_team=owner_team,
                created_by="user-owner",
            )
        )
        uow.commit()

    now = _now()
    version_id = _public_id()
    artifact = artifact_hash or f"sha256:{slug}-{version}".replace(".", "")
    with uow_factory() as uow:
        # The version is created as a draft: `add` does not take a status, because a version
        # always starts unreviewed. Moving it to `published` goes through the same conditional
        # transition the API uses, so the fixture cannot create a state the API could not.
        uow.versions.add(
            NewVersion(
                public_id=version_id,
                resource_public_id=resource_id,
                identifier=VersionIdentifier.parse(version),
                schema_version="supercoder/v1",
                name=slug,
                description=f"{slug} fixture",
                owner_team=owner_team,
                entrypoint="SKILL.md",
                aliases=(),
                inputs=(),
                outputs=(),
                manifest={"apiVersion": "supercoder/v1", "kind": kind, "name": slug},
                type_spec={},
                artifact_hash=artifact,
                manifest_snapshot_hash="sha256:manifest",
                status_changed_at=now,
                status_changed_by="user-owner",
                status_reason="fixture",
                created_by="user-owner",
            )
        )
        uow.commit()

    if status != "draft":
        with uow_factory() as uow:
            uow.versions.transition_lifecycle(
                LifecycleUpdate(
                    version_public_id=version_id,
                    expected_row_version=1,
                    expected_status=LifecycleState.DRAFT,
                    expected_artifact_hash=artifact,
                    expected_manifest_snapshot_hash="sha256:manifest",
                    target_status=LifecycleState(status),
                    changed_at=now,
                    changed_by="user-owner",
                    reason="fixture",
                    manifest_snapshot_hash_after="sha256:manifest",
                )
            )
            uow.commit()

    with uow_factory() as uow:
        record = uow.resources.get(resource_id)
        uow.commit()
    return record


def publish_capability(uow_factory, kind: str, slug: str, version: str, **kwargs) -> ResourceRecord:
    """Register a capability that is immediately `published`."""

    return register_capability(uow_factory, kind, slug, version, status="published", **kwargs)


def register_workflow(
    uow_factory,
    *,
    stages: list[dict],
    title: str = "fixture workflow",
    version: str = "1.0.0",
    graph_hash: str | None = None,
) -> WorkflowDefinitionRecord:
    """Register a workflow resource and its compiled definition.

    The resource exists because the contract ties a definition to a workflow resource version;
    a definition with no resource behind it would be reachable only through this helper.
    """

    definition_body = {
        "schema_version": "workflow/v1",
        "title": title,
        "stages": stages,
    }

    slug = f"wf-{next(_COUNTER)}"
    resource = register_capability(uow_factory, "workflow", slug, version, status="published")

    # Look the version up through a scope that sees everything: the fixture is arranging
    # data, not exercising visibility rules, which the service tests do separately.
    scope = VisibilityScope(actor_id="user-owner", organization_member=True)
    with uow_factory() as uow:
        versions = uow.versions.list_for_resource(resource.public_id, scope=scope)
        uow.commit()

    with uow_factory() as uow:
        record = uow.workflows.add_definition(
            NewWorkflowDefinition(
                version_public_id=versions.items[0].public_id,
                definition_schema_version="workflow/v1",
                definition=definition_body,
                graph_hash=graph_hash or f"sha256:graph-{slug}-{version}",
                is_deterministic=True,
                compiler_version="workflow-compiler/1",
            )
        )
        uow.commit()
    return record
