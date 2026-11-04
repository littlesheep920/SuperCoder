"""W6: the run review.

The review's value depends on every figure being traceable. These tests check that it reports
what was recorded — not what was merely declared — and that a run with no executions says so
rather than reporting success.
"""

from __future__ import annotations

import pathlib
import sys
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.application.review import ReviewService, build_review  # noqa: E402
from apps.api.application.usage import UsageEntry, UsageService  # noqa: E402
from apps.api.application.workflow_runs import (  # noqa: E402
    RunCreationRequest,
    WorkflowRunService,
)
from apps.api.auth import AuthContext
from apps.api.domain.models import ActorIdentity
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


class ReviewFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryStore()
        self.uow_factory = make_uow_factory(self.store)
        self.runs = WorkflowRunService(self.uow_factory)
        self.usage = UsageService(self.uow_factory)
        self.reviews = ReviewService(self.uow_factory)
        self.definition = register_workflow(
            self.uow_factory,
            stages=[
                {
                    "stage_id": "analysis",
                    "title": "分析",
                    "capabilities": [{"kind": "skill", "slug": "review", "version": "1.0.0"}],
                },
                {
                    "stage_id": "check",
                    "title": "检查",
                    "capabilities": [{"kind": "tool", "slug": "lint", "version": "1.0.0"}],
                    "gate": "human-approval",
                },
            ],
        )
        publish_capability(self.uow_factory, "skill", "review", "1.0.0")
        publish_capability(self.uow_factory, "tool", "lint", "1.0.0")

    def create_run(self):
        return self.runs.create_run(
            actor(),
            RunCreationRequest(
                workflow_definition_id=self.definition.public_id,
                runtime="agent-skills",
                input={"ticket": "PROJ-9"},
                timeout_seconds=600,
                idempotency_key="review-key",
                correlation_id="corr-review",
            ),
        )

    def record_execution(self, run, node_index: int, *, status: str, output_hash=None, error=None):
        """Record an execution against the version the run pinned."""

        version_id = run.resolution_snapshot["nodes"][node_index]["resource_version_id"]
        now = datetime.now(timezone.utc)
        self.usage.record(
            UsageEntry(
                version_public_id=version_id,
                actor_id="user-owner",
                action="execute",
                status=status,
                correlation_id="corr-review",
                started_at=now,
                finished_at=now,
                workflow_run_public_id=run.public_id,
                output_hash=output_hash,
                error_code=error,
            )
        )


class ReviewContentTests(ReviewFixture):
    def test_the_review_names_the_run(self) -> None:
        run = self.create_run()
        review = self.reviews.review_run(run.public_id)
        self.assertEqual(review.run_id, run.public_id)

    def test_the_stages_come_from_the_snapshot(self) -> None:
        """The snapshot is what the run actually planned, not the definition read now."""

        run = self.create_run()
        review = self.reviews.review_run(run.public_id)
        self.assertEqual([stage.stage_id for stage in review.stages], ["analysis", "check"])

    def test_the_gate_is_visible_per_stage(self) -> None:
        run = self.create_run()
        review = self.reviews.review_run(run.public_id)
        gates = {stage.stage_id: stage.gate for stage in review.stages}
        self.assertEqual(gates["check"], "human-approval")
        self.assertEqual(gates["analysis"], "none")

    def test_every_pinned_capability_appears(self) -> None:
        run = self.create_run()
        review = self.reviews.review_run(run.public_id)
        self.assertEqual({item.slug for item in review.capabilities}, {"review", "lint"})

    def test_each_capability_records_its_exact_version_and_hash(self) -> None:
        run = self.create_run()
        review = self.reviews.review_run(run.public_id)
        for item in review.capabilities:
            self.assertEqual(item.version, "1.0.0")
            self.assertTrue(item.artifact_hash)

    def test_a_fresh_run_reports_no_executions(self) -> None:
        """The resolve row is bookkeeping and must not be counted as a use."""

        run = self.create_run()
        review = self.reviews.review_run(run.public_id)
        self.assertTrue(all(item.uses == 0 for item in review.capabilities))

    def test_a_fresh_run_says_it_had_no_executions(self) -> None:
        """Silence would be read as success, so the absence of executions is stated."""

        run = self.create_run()
        review = self.reviews.review_run(run.public_id)
        combined = " ".join(review.notes)
        self.assertIn("没有使用记录", combined)
        self.assertIn("没有任何执行", combined)

    def test_executions_are_counted_per_capability(self) -> None:
        run = self.create_run()
        self.record_execution(run, 0, status="succeeded")
        self.record_execution(run, 0, status="succeeded")
        self.record_execution(run, 1, status="failed", error="lint_error")

        review = self.reviews.review_run(run.public_id)
        by_slug = {item.slug: item for item in review.capabilities}
        self.assertEqual(by_slug["review"].uses, 2)
        self.assertEqual(by_slug["review"].succeeded, 2)
        self.assertEqual(by_slug["lint"].uses, 1)
        self.assertEqual(by_slug["lint"].failed, 1)

    def test_a_failure_is_surfaced_with_its_code(self) -> None:
        run = self.create_run()
        self.record_execution(run, 1, status="failed", error="lint_error")
        review = self.reviews.review_run(run.public_id)
        lint = next(item for item in review.capabilities if item.slug == "lint")
        self.assertEqual(lint.last_error, "lint_error")

    def test_produced_outputs_are_listed(self) -> None:
        run = self.create_run()
        self.record_execution(run, 0, status="succeeded", output_hash="sha256:aaa")
        self.record_execution(run, 0, status="succeeded", output_hash="sha256:bbb")
        review = self.reviews.review_run(run.public_id)
        item = next(entry for entry in review.capabilities if entry.slug == "review")
        self.assertEqual(item.output_hashes, ("sha256:aaa", "sha256:bbb"))

    def test_a_repeated_output_hash_is_listed_once(self) -> None:
        """Distinct outputs are what matter, not how many times each was produced."""

        run = self.create_run()
        self.record_execution(run, 0, status="succeeded", output_hash="sha256:same")
        self.record_execution(run, 0, status="succeeded", output_hash="sha256:same")
        review = self.reviews.review_run(run.public_id)
        item = next(entry for entry in review.capabilities if entry.slug == "review")
        self.assertEqual(item.output_hashes, ("sha256:same",))

    def test_the_approval_fields_are_reported(self) -> None:
        run = self.create_run()
        review = self.reviews.review_run(run.public_id)
        self.assertIsNone(review.approved_by)


class ReviewDeterminismTests(ReviewFixture):
    def test_the_same_run_reviews_identically(self) -> None:
        run = self.create_run()
        first = self.reviews.review_run(run.public_id)
        second = self.reviews.review_run(run.public_id)
        self.assertEqual(first.digest(), second.digest())

    def test_a_recorded_execution_changes_the_digest(self) -> None:
        """The digest has to move when the review's content moves, or it identifies nothing."""

        run = self.create_run()
        before = self.reviews.review_run(run.public_id).digest()
        self.record_execution(run, 0, status="succeeded")
        after = self.reviews.review_run(run.public_id).digest()
        self.assertNotEqual(before, after)

    def test_the_digest_is_a_sha256_hash(self) -> None:
        """The digest is compared and stored, so its form is part of the contract."""

        run = self.create_run()
        digest = self.reviews.review_run(run.public_id).digest()
        self.assertIsInstance(digest, str)
        self.assertTrue(digest.startswith("sha256:"))
        self.assertEqual(len(digest), len("sha256:") + 64)
        int(digest[len("sha256:"):], 16)  # must be hexadecimal

    def test_the_payload_is_json_safe(self) -> None:
        import json

        run = self.create_run()
        payload = self.reviews.review_run(run.public_id).to_payload()
        json.dumps(payload)  # must not raise


class ReviewIsolationTests(ReviewFixture):
    def test_usage_from_another_run_is_not_included(self) -> None:
        """A review reports one run; borrowing another's rows would misattribute the work."""

        first = self.create_run()
        second = self.runs.create_run(
            actor("user-other"),
            RunCreationRequest(
                workflow_definition_id=self.definition.public_id,
                runtime="agent-skills",
                input={},
                timeout_seconds=60,
                idempotency_key="other-key",
                correlation_id="corr-other",
            ),
        )
        self.record_execution(first, 0, status="succeeded")
        review = self.reviews.review_run(second.public_id)
        self.assertTrue(all(item.uses == 0 for item in review.capabilities))

    def test_an_unknown_run_is_refused(self) -> None:
        with self.assertRaises(LookupError):
            self.reviews.review_run("does-not-exist")


if __name__ == "__main__":
    unittest.main()
