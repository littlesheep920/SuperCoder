"""W5: record what a run actually used.

Why a separate service
----------------------
Usage is the only evidence that a capability was used, so it is written from one place rather
than sprinkled through the execution path. The rule that matters: a usage row is written only
for something that really happened. `discover` and `resolve` are recorded because the platform
really did them; `execute` is recorded only when an execution was attempted, never because a
definition mentioned a capability.

The schema comment in the data model says the table must not record metadata-only success, so
every row here comes with a status and, for a failure, an error code.

Aggregation
-----------
A project review (W6) needs counts and outcomes by resource, by stage, and by run. Those are
computed from these rows, which is why each row carries the run id, the actor, the timing, and
the hashes: a review that could not say what was produced would be a summary, not a review.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from ..domain.models import new_ulid
from ..repositories.contracts import UsageRecord, UnitOfWorkFactory

#: The usage actions the data model allows. Anything else is a typo in the calling code.
ACTIONS = frozenset({"discover", "resolve", "validate", "execute", "publish", "deprecation_warning"})

#: The outcomes the data model allows.
STATUSES = frozenset({"started", "succeeded", "failed", "denied", "cancelled"})


class UsageRecordingError(ValueError):
    """A usage row that the schema would not accept."""


@dataclass(frozen=True)
class UsageEntry:
    """One thing the platform did with one resource version."""

    version_public_id: str
    actor_id: str
    action: str
    status: str
    correlation_id: str
    started_at: datetime
    workflow_run_public_id: str | None = None
    job_public_id: str | None = None
    runtime: str | None = None
    adapter_version: str | None = None
    request_id: str | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    input_hash: str | None = None
    output_hash: str | None = None
    metrics: dict | None = None


class UsageService:
    """Appends usage rows; never updates them."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def record(self, entry: UsageEntry) -> UsageRecord:
        if entry.action not in ACTIONS:
            raise UsageRecordingError(
                f"unknown usage action {entry.action!r}; allowed: {sorted(ACTIONS)}"
            )
        if entry.status not in STATUSES:
            raise UsageRecordingError(
                f"unknown usage status {entry.status!r}; allowed: {sorted(STATUSES)}"
            )
        if not entry.version_public_id:
            # A usage row with no resource cannot be attributed, which is the whole purpose.
            raise UsageRecordingError("a usage record must name a resource version")

        record = UsageRecord(
            public_id=new_ulid(),
            version_public_id=entry.version_public_id,
            actor_id=entry.actor_id,
            action=entry.action,
            status=entry.status,
            correlation_id=entry.correlation_id,
            started_at=entry.started_at,
            runtime=entry.runtime,
            request_id=entry.request_id,
            job_public_id=entry.job_public_id,
            finished_at=entry.finished_at,
            error_code=entry.error_code,
            workflow_run_public_id=entry.workflow_run_public_id,
            adapter_version=entry.adapter_version,
            input_hash=entry.input_hash,
            output_hash=entry.output_hash,
            metrics=dict(entry.metrics or {}),
        )
        with self._uow_factory() as uow:
            uow.metadata.append_usage(record)
            uow.commit()
        return record

    def record_run_resolution(
        self,
        *,
        run_public_id: str,
        resolved: list[dict],
        actor_id: str,
        correlation_id: str,
        at: datetime | None = None,
    ) -> tuple[UsageRecord, ...]:
        """Record the `resolve` action for every capability a run pinned.

        One row per resolved node, because a review asks "which resources did this run use" and
        a single aggregate row could not answer that.
        """

        moment = at or datetime.now(timezone.utc)
        records: list[UsageRecord] = []
        with self._uow_factory() as uow:
            for node in resolved:
                record = UsageRecord(
                    public_id=new_ulid(),
                    version_public_id=node["resource_version_id"],
                    actor_id=actor_id,
                    action="resolve",
                    status="succeeded",
                    correlation_id=correlation_id,
                    started_at=moment,
                    finished_at=moment,
                    workflow_run_public_id=run_public_id,
                    input_hash=None,
                    metrics={
                        "node_id": node["node_id"],
                        "stage_id": node["stage_id"],
                        "version": node["version"],
                        "constraint": node["constraint"],
                    },
                )
                uow.metadata.append_usage(record)
                records.append(record)
            uow.commit()
        return tuple(records)


@dataclass(frozen=True)
class UsageSummary:
    """Counters for one resource version within whatever set was asked about."""

    version_public_id: str
    total: int
    succeeded: int
    failed: int
    other: int
    last_used_at: datetime | None

    @property
    def failure_rate(self) -> float:
        return 0.0 if self.total == 0 else round(self.failed / self.total, 4)


def summarise(records: list[UsageRecord]) -> tuple[UsageSummary, ...]:
    """Aggregate usage rows by resource version.

    Kept a free function so a review can summarise a set it already loaded, without a second
    query and without a service instance.
    """

    buckets: dict[str, list[UsageRecord]] = defaultdict(list)
    for record in records:
        buckets[record.version_public_id].append(record)

    summaries: list[UsageSummary] = []
    for version_id, entries in sorted(buckets.items()):
        succeeded = sum(1 for entry in entries if entry.status == "succeeded")
        failed = sum(1 for entry in entries if entry.status == "failed")
        moments = [entry.started_at for entry in entries if entry.started_at is not None]
        summaries.append(
            UsageSummary(
                version_public_id=version_id,
                total=len(entries),
                succeeded=succeeded,
                failed=failed,
                other=len(entries) - succeeded - failed,
                last_used_at=max(moments) if moments else None,
            )
        )
    return tuple(summaries)
