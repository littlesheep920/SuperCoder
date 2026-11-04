"""W5: recording what a run actually used.

The rule under test is that a usage row means something happened. A row invented from a
definition would make a review report capabilities that were never exercised, which is worse
than reporting nothing.
"""

from __future__ import annotations

import pathlib
import sys
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.application.usage import (  # noqa: E402
    UsageEntry,
    UsageRecordingError,
    UsageService,
    summarise,
)
from apps.api.application.workflow_runs import (  # noqa: E402
    RunCreationRequest,
    WorkflowRunService,
)
from apps.api.auth import AuthContext
from apps.api.domain.models import ActorIdentity
from apps.api.repositories.contracts import UsageRecord  # noqa: E402
from tests.support.memory import MemoryStore, make_uow_factory  # noqa: E402
from tests.support.workflow_fixtures import (  # noqa: E402
    publish_capability,
    register_workflow,
)


def actor(name: str = "user-owner") -> AuthContext:
    return AuthContext(
        actor=ActorIdentity(subject_id=name, actor_type="user", display_name=name),
        grants=(),
        teams=frozenset(),
        ui_roles_by_team={},
        organization_member=True,
    )


class UsageRecordingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryStore()
        self.uow_factory = make_uow_factory(self.store)
        self.service = UsageService(self.uow_factory)
        self.now = datetime.now(timezone.utc)

    def entry(self, **overrides) -> UsageEntry:
        base = dict(
            version_public_id="v1",
            actor_id="user-owner",
            action="execute",
            status="succeeded",
            correlation_id="c1",
            started_at=self.now,
            finished_at=self.now,
        )
        base.update(overrides)
        return UsageEntry(**base)

    def test_a_record_is_stored(self) -> None:
        self.service.record(self.entry())
        self.assertEqual(len(self.store.usage), 1)

    def test_an_unknown_action_is_refused(self) -> None:
        """A typo in the action would make the row unqueryable by every consumer."""

        with self.assertRaises(UsageRecordingError):
            self.service.record(self.entry(action="executed"))

    def test_an_unknown_status_is_refused(self) -> None:
        with self.assertRaises(UsageRecordingError):
            self.service.record(self.entry(status="ok"))

    def test_a_record_without_a_resource_is_refused(self) -> None:
        """A usage row that cannot be attributed to a resource answers nothing."""

        with self.assertRaises(UsageRecordingError):
            self.service.record(self.entry(version_public_id=""))

    def test_the_run_link_is_stored(self) -> None:
        self.service.record(self.entry(workflow_run_public_id="run-1"))
        self.assertEqual(self.store.usage[0].workflow_run_public_id, "run-1")

    def test_the_hashes_are_stored(self) -> None:
        self.service.record(self.entry(input_hash="sha256:in", output_hash="sha256:out"))
        record = self.store.usage[0]
        self.assertEqual(record.input_hash, "sha256:in")
        self.assertEqual(record.output_hash, "sha256:out")

    def test_metrics_are_stored(self) -> None:
        self.service.record(self.entry(metrics={"tokens": 42}))
        self.assertEqual(self.store.usage[0].metrics, {"tokens": 42})

    def test_duration_is_derived_from_the_timestamps(self) -> None:
        started = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        finished = datetime(2026, 1, 1, 12, 0, 2, 500000, tzinfo=timezone.utc)
        record = self.service.record(self.entry(started_at=started, finished_at=finished))
        self.assertEqual(record.duration_ms, 2500)

    def test_a_record_still_running_has_no_duration(self) -> None:
        record = self.service.record(self.entry(status="started", finished_at=None))
        self.assertIsNone(record.duration_ms)


class RunResolutionUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryStore()
        self.uow_factory = make_uow_factory(self.store)
        self.runs = WorkflowRunService(self.uow_factory)
        self.definition = register_workflow(
            self.uow_factory,
            stages=[
                {
                    "stage_id": "analysis",
                    "title": "分析",
                    "capabilities": [
                        {"kind": "skill", "slug": "review", "version": "1.0.0"},
                        {"kind": "tool", "slug": "lint", "version": "1.0.0"},
                    ],
                }
            ],
        )
        publish_capability(self.uow_factory, "skill", "review", "1.0.0")
        publish_capability(self.uow_factory, "tool", "lint", "1.0.0")

    def create(self):
        return self.runs.create_run(
            actor(),
            RunCreationRequest(
                workflow_definition_id=self.definition.public_id,
                runtime="agent-skills",
                input={},
                timeout_seconds=60,
                idempotency_key="usage-key",
                correlation_id="corr-usage",
            ),
        )

    def test_creating_a_run_records_one_resolve_row_per_capability(self) -> None:
        """The review asks which resources a run used, so each gets its own row."""

        self.create()
        resolves = [record for record in self.store.usage if record.action == "resolve"]
        self.assertEqual(len(resolves), 2)

    def test_the_rows_are_linked_to_the_run(self) -> None:
        run = self.create()
        for record in self.store.usage:
            self.assertEqual(record.workflow_run_public_id, run.public_id)

    def test_the_rows_name_the_pinned_version(self) -> None:
        self.create()
        versions = {record.metrics.get("version") for record in self.store.usage}
        self.assertEqual(versions, {"1.0.0"})

    def test_an_unresolvable_workflow_records_nothing(self) -> None:
        """Fail closed extends to usage: nothing happened, so nothing is recorded."""

        self.definition = register_workflow(
            self.uow_factory,
            stages=[
                {
                    "stage_id": "s",
                    "title": "S",
                    "capabilities": [{"kind": "skill", "slug": "absent", "version": "1.0.0"}],
                }
            ],
        )
        with self.assertRaises(Exception):
            self.create()
        self.assertEqual(len(self.store.usage), 0)


class SummariseTests(unittest.TestCase):
    def record(self, version: str, status: str) -> UsageRecord:
        moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return UsageRecord(
            public_id=f"{version}-{status}-{id(self)}-{moment.timestamp()}",
            version_public_id=version,
            actor_id="a",
            action="execute",
            status=status,
            correlation_id="c",
            started_at=moment,
            finished_at=moment,
        )

    def test_counts_are_per_resource_version(self) -> None:
        records = [
            self.record("v1", "succeeded"),
            self.record("v1", "failed"),
            self.record("v2", "succeeded"),
        ]
        summaries = {summary.version_public_id: summary for summary in summarise(records)}
        self.assertEqual(summaries["v1"].total, 2)
        self.assertEqual(summaries["v1"].succeeded, 1)
        self.assertEqual(summaries["v1"].failed, 1)
        self.assertEqual(summaries["v2"].total, 1)

    def test_the_failure_rate_is_computed(self) -> None:
        records = [self.record("v1", "succeeded")] * 3 + [self.record("v1", "failed")]
        summary = summarise(records)[0]
        self.assertEqual(summary.failure_rate, 0.25)

    def test_a_version_with_no_uses_has_no_failure_rate(self) -> None:
        self.assertEqual(summarise([]), ())

    def test_other_statuses_are_counted_separately(self) -> None:
        records = [self.record("v1", "succeeded"), self.record("v1", "cancelled")]
        summary = summarise(records)[0]
        self.assertEqual(summary.other, 1)
        self.assertEqual(summary.failed, 0)


if __name__ == "__main__":
    unittest.main()
