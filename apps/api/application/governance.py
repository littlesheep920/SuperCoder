"""Lifecycle transitions, reviews, publication, deprecation, and withdrawal."""

from __future__ import annotations

from dataclasses import dataclass

from ..auth import AuthContext
from ..domain import lifecycle as lifecycle_rules
from ..domain.capabilities import is_owner, is_publisher, is_validator
from ..domain.dependencies import parse_version_range, select_highest_stable, version_satisfies
from ..domain.errors import (
    ConflictError,
    ErrorCode,
    NotFoundError,
    PermissionDeniedError,
    PreconditionFailedError,
    ValidationError,
)
from ..domain.hashing import canonical_manifest_bytes, sha256_digest
from ..domain.models import (
    ArtifactHash,
    JobState,
    LifecycleState,
    ResourceKind,
    ReviewDecision,
    VersionIdentifier,
    new_ulid,
)
from ..repositories.contracts import (
    DependencyRecord,
    JobEventRecord,
    LifecycleUpdate,
    MetadataSnapshot,
    NewJob,
    NewReview,
    ReviewRecord,
    UnitOfWorkFactory,
    VersionRecord,
    VisibilityScope,
)
from ..schemas.resources import CreateResourceReviewRequest, LifecycleTransitionRequest
from .helpers import AuditWriter, Clock, now_utc

__all__ = ["GovernanceService", "TransitionResult"]


@dataclass(frozen=True, slots=True)
class TransitionResult:
    version: VersionRecord
    job: object | None
    audit_id: str | None


class GovernanceService:
    """Enforces the frozen lifecycle matrix and review/publication gates."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock = now_utc) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def transition(
        self,
        *,
        auth: AuthContext,
        version_public_id: str,
        request: LifecycleTransitionRequest,
        correlation_id: str,
        request_id: str | None = None,
    ) -> TransitionResult:
        actor = auth.actor
        target = LifecycleState(request.target_status)
        now = self._clock()

        with self._uow_factory() as uow:
            version = uow.versions.get(version_public_id, for_update=True)
            if version is None:
                uow.rollback()
                raise NotFoundError("version not found or not visible")
            if request.row_version != version.row_version:
                uow.rollback()
                raise PreconditionFailedError(
                    code=ErrorCode.ROW_VERSION_MISMATCH,
                    message="The version changed since it was last read.",
                )
            self._assert_owner_or_member(auth, version)

            transition = lifecycle_rules.require_transition(version.status, target)

            if transition.is_replay:
                uow.commit()
                return TransitionResult(version=version, job=None, audit_id=self._last_audit(uow))

            self._check_gate(auth, version, transition, uow, request)

            new_manifest = dict(version.manifest)
            new_manifest["status"] = target.value
            manifest_snapshot_hash_after = sha256_digest(
                canonical_manifest_bytes(new_manifest, include_lifecycle=True)
            )

            job = None
            if target is LifecycleState.VALIDATING:
                job = self._create_validation_job(
                    uow, actor, version, correlation_id
                )

            updated = uow.versions.transition_lifecycle(
                LifecycleUpdate(
                    version_public_id=version_public_id,
                    expected_row_version=version.row_version,
                    expected_status=version.status,
                    expected_artifact_hash=version.artifact_hash,
                    expected_manifest_snapshot_hash=version.manifest_snapshot_hash,
                    target_status=target,
                    changed_at=now,
                    changed_by=actor.subject_id,
                    reason=request.reason,
                    manifest_snapshot_hash_after=ArtifactHash(manifest_snapshot_hash_after),
                    manifest_json_after=new_manifest,
                    replacement_version_public_id=request.replacement_version_id,
                    retire_after=request.retire_after,
                )
            )
            audit = AuditWriter(uow).append(
                actor=actor,
                action="version.transition",
                outcome="succeeded",
                entity_type="resource_version",
                entity_public_id=version_public_id,
                correlation_id=correlation_id,
                reason_code=target.value.upper(),
                reason_summary=request.reason,
                request_id=request_id,
                version_public_id=version_public_id,
                from_status=version.status.value,
                to_status=target.value,
                artifact_hash=version.artifact_hash,
                manifest_snapshot_hash_before=version.manifest_snapshot_hash,
                manifest_snapshot_hash_after=manifest_snapshot_hash_after,
                evidence_refs=tuple(request.evidence_refs),
            )
            uow.commit()
            return TransitionResult(version=updated, job=job, audit_id=audit.public_id)

    def create_review(
        self,
        *,
        auth: AuthContext,
        version_public_id: str,
        request: CreateResourceReviewRequest,
        correlation_id: str,
        request_id: str | None = None,
    ) -> ReviewRecord:
        actor = auth.actor
        now = self._clock()
        with self._uow_factory() as uow:
            version = uow.versions.get(version_public_id, for_update=True)
            if version is None:
                uow.rollback()
                raise NotFoundError("version not found or not visible")
            if request.artifact_hash != version.artifact_hash:
                uow.rollback()
                raise ValidationError(
                    code=ErrorCode.CONTENT_HASH_MISMATCH,
                    message="review artifact hash does not match the current candidate",
                )
            if version.created_by == actor.subject_id:
                uow.rollback()
                raise PermissionDeniedError(
                    message="a resource owner cannot be the sole reviewer"
                )
            existing = uow.reviews.find_idempotent(
                version_public_id, actor.subject_id, f"{correlation_id}:{version.artifact_hash}"
            )
            if existing is not None:
                uow.commit()
                return existing

            review = uow.reviews.append(
                NewReview(
                    public_id=new_ulid(),
                    version_public_id=version_public_id,
                    review_type=request.review_type,
                    reviewer_id=actor.subject_id,
                    reviewer_role=request.reviewer_role,
                    decision=ReviewDecision(request.decision),
                    artifact_hash=ArtifactHash(request.artifact_hash),
                    manifest_snapshot_hash=ArtifactHash(request.manifest_snapshot_hash),
                    runtime_scope=tuple(request.runtime_scope),
                    validation_evidence_ref=request.validation_evidence_ref,
                    permission_summary=request.permission_summary,
                    dependency_summary=request.dependency_summary,
                    evaluation_refs=tuple(request.evaluation_refs),
                    rationale=request.rationale,
                    idempotency_key=f"{correlation_id}:{version.artifact_hash}",
                    decided_at=now,
                )
            )
            AuditWriter(uow).append(
                actor=actor,
                action="review.create",
                outcome="succeeded",
                entity_type="resource_review",
                entity_public_id=review.public_id,
                correlation_id=correlation_id,
                reason_code=request.decision.upper(),
                reason_summary=request.rationale[:200],
                request_id=request_id,
                version_public_id=version_public_id,
                artifact_hash=ArtifactHash(request.artifact_hash),
            )
            uow.commit()
            return review

    def list_reviews(
        self, version_public_id: str, *, include_invalidated: bool = False
    ) -> tuple[ReviewRecord, ...]:
        with self._uow_factory() as uow:
            reviews = uow.reviews.list_for_version(version_public_id, include_invalidated=include_invalidated)
            uow.commit()
            return reviews

    def withdraw_resource(
        self,
        *,
        auth: AuthContext,
        resource_public_id: str,
        reason: str,
        correlation_id: str,
        request_id: str | None = None,
    ) -> None:
        """Retire every unpublished version of a resource (audited withdrawal)."""

        actor = auth.actor
        now = self._clock()
        with self._uow_factory() as uow:
            resource = uow.resources.get(resource_public_id, for_update=True)
            if resource is None:
                uow.rollback()
                raise NotFoundError("resource not found or not visible")
            self._assert_owner(auth, resource)
            versions = uow.versions.list_for_resource(
                resource_public_id, scope=auth.visibility_scope(), limit=1000
            ).items
            if any(v.status in (LifecycleState.PUBLISHED, LifecycleState.DEPRECATED) for v in versions):
                uow.rollback()
                raise ConflictError(
                    code=ErrorCode.VERSION_IMMUTABLE,
                    message="published resources cannot be deleted; use deprecation and retirement",
                )
            for version in versions:
                if version.status is LifecycleState.RETIRED:
                    continue
                transition = lifecycle_rules.require_transition(version.status, LifecycleState.RETIRED)
                if transition.is_replay:
                    continue
                new_manifest = dict(version.manifest)
                new_manifest["status"] = LifecycleState.RETIRED.value
                snapshot_after = sha256_digest(
                    canonical_manifest_bytes(new_manifest, include_lifecycle=True)
                )
                uow.versions.transition_lifecycle(
                    LifecycleUpdate(
                        version_public_id=version.public_id,
                        expected_row_version=version.row_version,
                        expected_status=version.status,
                        expected_artifact_hash=version.artifact_hash,
                        expected_manifest_snapshot_hash=version.manifest_snapshot_hash,
                        target_status=LifecycleState.RETIRED,
                        changed_at=now,
                        changed_by=actor.subject_id,
                        reason=reason,
                        manifest_snapshot_hash_after=ArtifactHash(snapshot_after),
                        manifest_json_after=new_manifest,
                    )
                )
                AuditWriter(uow).append(
                    actor=actor,
                    action="resource.withdraw",
                    outcome="succeeded",
                    entity_type="resource_version",
                    entity_public_id=version.public_id,
                    correlation_id=correlation_id,
                    reason_code="WITHDRAWN",
                    reason_summary=reason,
                    request_id=request_id,
                    version_public_id=version.public_id,
                    from_status=version.status.value,
                    to_status=LifecycleState.RETIRED.value,
                )
            uow.commit()

    def _check_gate(self, auth, version, transition, uow, request) -> None:
        gates = transition.gates
        target = transition.target

        if lifecycle_rules.TransitionGate.APPROVAL_VALID in gates:
            self._require_approval(uow, version)
        if lifecycle_rules.TransitionGate.PUBLICATION_READY in gates:
            self._require_publication_ready(auth, uow, version)
        if lifecycle_rules.TransitionGate.DEPRECATION_PLAN in gates:
            if not request.reason:
                raise ValidationError(code=ErrorCode.REVIEW_EVIDENCE_MISSING, message="deprecation requires a reason")
        if lifecycle_rules.TransitionGate.RETIREMENT_READY in gates:
            if not request.reason:
                raise ValidationError(code=ErrorCode.REVIEW_EVIDENCE_MISSING, message="retirement requires a reason")
        if lifecycle_rules.TransitionGate.DEPRECATION_REVERSAL_APPROVED in gates:
            # Uses the shared predicate so /v1/auth/me cannot disagree with this.
            if not (is_owner(auth) and is_publisher(auth)):
                raise PermissionDeniedError(message="deprecation reversal requires owner and publisher approval")
        if lifecycle_rules.TransitionGate.VALIDATION_PASSED in gates:
            if not is_validator(auth):
                raise PermissionDeniedError(message="validation completion requires a validator")
        if lifecycle_rules.TransitionGate.CANDIDATE_FREEZE in gates:
            if not is_owner(auth):
                raise PermissionDeniedError(message="submitting validation requires owner role")

    def _require_approval(self, uow, version) -> None:
        reviews = uow.reviews.list_for_version(version.public_id, include_invalidated=False)
        approved = any(
            review.decision is ReviewDecision.APPROVE
            and review.artifact_hash == version.artifact_hash
            and review.reviewer_id != version.created_by
            for review in reviews
        )
        if not approved:
            raise ValidationError(
                code=ErrorCode.REVIEW_REQUIRED,
                message="an independent valid approval review is required",
            )

    def _require_publication_ready(self, auth, uow, version) -> None:
        if not is_publisher(auth):
            raise PermissionDeniedError(message="publication requires publisher role")
        metadata = uow.metadata.get_snapshot(version.public_id)
        resolved = self._resolve_dependencies(uow, version, metadata)
        for dependency in resolved.dependencies:
            if not dependency.is_optional and dependency.resolution_status != "resolved":
                raise ValidationError(
                    code=ErrorCode.DEPENDENCY_UNRESOLVED,
                    message=f"required dependency {dependency.dependency_kind}/{dependency.dependency_slug} is unresolved",
                )
        if not resolved.runtimes:
            raise ValidationError(
                code=ErrorCode.RUNTIME_NOT_VALIDATED,
                message="publication requires at least one declared runtime",
            )

    def _resolve_dependencies(self, uow, version, metadata) -> MetadataSnapshot:
        resolved_dependencies: list[DependencyRecord] = []
        for dependency in metadata.dependencies:
            if dependency.source == "external":
                resolved_dependencies.append(dependency)
                continue
            resource = uow.resources.get_by_identity(
                ResourceKind(dependency.dependency_kind), dependency.dependency_slug
            )
            if resource is None:
                resolved_dependencies.append(
                    _with_status(dependency, "failed", "DEPENDENCY_MISSING")
                )
                continue
            versions = uow.versions.list_for_resource(
                resource.public_id, scope=_org_scope(), limit=100
            ).items
            constraint = parse_version_range(dependency.version_constraint)
            best = select_highest_stable([item.identifier for item in versions], constraint)
            match = next(
                (
                    item
                    for item in versions
                    if item.identifier == best
                    and item.status in (LifecycleState.PUBLISHED, LifecycleState.DEPRECATED)
                ),
                None,
            )
            if match is not None:
                resolved_dependencies.append(_with_resolution(dependency, match.public_id))
            else:
                resolved_dependencies.append(
                    _with_status(dependency, "failed", "DEPENDENCY_UNSATISFIED")
                )
        snapshot = MetadataSnapshot(
            tags=metadata.tags,
            categories=metadata.categories,
            runtimes=metadata.runtimes,
            dependencies=tuple(resolved_dependencies),
        )
        uow.metadata.replace_snapshot(
            version.public_id, snapshot, expected_version_row=version.row_version
        )
        return snapshot

    def _create_validation_job(self, uow, actor, version, correlation_id):
        now = self._clock()
        public_id = new_ulid()
        payload = {
            "resource_version_id": version.public_id,
            "artifact_hash": version.artifact_hash,
            "kind": version.manifest.get("kind"),
            "slug": version.manifest.get("slug"),
        }
        from ..domain.hashing import canonical_json_bytes, sha256_digest as _sha

        job = uow.jobs.add(
            NewJob(
                public_id=public_id,
                job_kind="resource_validation",
                queue_name="validation",
                idempotency_key=public_id,
                correlation_id=correlation_id,
                payload=payload,
                payload_hash=ArtifactHash(_sha(canonical_json_bytes(payload))),
                available_at=now,
                max_attempts=3,
                version_public_id=version.public_id,
            ),
            JobEventRecord(
                job_public_id=public_id,
                sequence_no=1,
                event_type="queued",
                actor_type=actor.actor_type,
                actor_id=actor.subject_id,
                payload={"job_kind": "resource_validation"},
                occurred_at=now,
                to_status=JobState.QUEUED,
            ),
        )
        return job

    def _last_audit(self, uow) -> str | None:
        from ..repositories.contracts import AuditQuery

        page = uow.audit.list(AuditQuery(limit=1))
        return page.items[0].public_id if page.items else None

    def _assert_owner(self, auth, resource) -> None:
        if not is_owner(auth):
            if resource.owner_team not in auth.teams:
                raise PermissionDeniedError(message="caller is not the resource owner")

    def _assert_owner_or_member(self, auth, version) -> None:
        if auth.organization_member:
            return
        if version.owner_team in auth.teams:
            return
        raise PermissionDeniedError(message="caller is not a member of the owning team")


def _org_scope():
    return VisibilityScope(actor_id="system", team_ids=frozenset(), organization_member=True)


def _with_resolution(dependency, resolved_public_id) -> DependencyRecord:
    return DependencyRecord(
        dependency_kind=dependency.dependency_kind,
        dependency_slug=dependency.dependency_slug,
        version_constraint=dependency.version_constraint,
        source=dependency.source,
        ordinal=dependency.ordinal,
        is_optional=dependency.is_optional,
        resolution_status="resolved",
        row_version=dependency.row_version,
        purpose=dependency.purpose,
        resolved_version_public_id=resolved_public_id,
    )


def _with_status(dependency, status: str, error_code: str) -> DependencyRecord:
    return DependencyRecord(
        dependency_kind=dependency.dependency_kind,
        dependency_slug=dependency.dependency_slug,
        version_constraint=dependency.version_constraint,
        source=dependency.source,
        ordinal=dependency.ordinal,
        is_optional=dependency.is_optional,
        resolution_status=status,
        row_version=dependency.row_version,
        purpose=dependency.purpose,
        resolution_error_code=error_code,
    )
