from __future__ import annotations

import unittest
from datetime import timedelta

from apps.api.application.helpers import now_utc
from apps.api.auth import AuthContext
from apps.api.domain.hashing import sha256_digest
from apps.api.domain.models import ActorIdentity, ActorType, ArtifactHash, JobState, ResourceKind
from apps.api.repositories.contracts import JobClaim
from apps.api.schemas.resources import CreateResourceRequest, LifecycleTransitionRequest
from apps.worker.main import Worker
from tests.support.memory import MemoryStore
from tests.test_stage1_api import _KINDS, _request_body, build_container


class Stage1WorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryStore()
        self.container = build_container(self.store)
        self.actor = ActorIdentity("owner-1", ActorType.USER)
        self.worker = Worker(self.container, worker_id="worker-test")

    def _create_validation_job(self):
        kind, slug = _KINDS[0]
        request = CreateResourceRequest.model_validate(_request_body(kind, slug))
        _, version = self.container.resource_commands.create_resource(
            actor=self.actor,
            request=request,
            correlation_id="corr-worker-test",
        )
        owner_auth = AuthContext(
            actor=self.actor,
            grants=(),
            teams=frozenset(),
            ui_roles_by_team={},
            organization_member=True,
            governance_roles=frozenset({"owner"}),
        )
        result = self.container.governance.transition(
            auth=owner_auth,
            version_public_id=version.public_id,
            request=LifecycleTransitionRequest(
                target_status="validating",
                reason="start deterministic validation",
                row_version=version.row_version,
                evidence_refs=[],
            ),
            correlation_id="corr-worker-test",
        )
        self.assertIsNotNone(result.job)
        return result.job, version

    def test_worker_claims_validates_and_succeeds(self):
        job, version = self._create_validation_job()

        self.assertTrue(self.worker.poll_once())
        updated = self.container.jobs.get(job.public_id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, JobState.SUCCEEDED)
        self.assertIsNotNone(updated.result_hash)
        self.assertEqual(
            self.container.versions.get(
                self.actor,
                version.public_id,
                self.container.auth.resolve("dev-owner-token").visibility_scope(),
            ).status.value,
            "pending_review",
        )
        events = self.container.jobs.list_events(job.public_id)
        self.assertEqual([event.event_type for event in events], ["queued", "leased", "running", "succeeded"])
        self.assertEqual([event.sequence_no for event in events], [1, 2, 3, 4])

    def test_unsupported_job_reaches_failed_or_dead_letter(self):
        job = self.container.jobs.create_job(
            actor=self.actor,
            job_kind="unsupported",
            queue_name="validation",
            idempotency_key="unsupported-1",
            correlation_id="corr-worker-test",
            payload={},
            max_attempts=1,
        )

        self.assertTrue(self.worker.poll_once())
        updated = self.container.jobs.get(job.public_id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, JobState.DEAD_LETTER)
        self.assertEqual(updated.error_code, "UNSUPPORTED_JOB_KIND")

    def test_an_exception_in_the_handler_still_reaches_a_terminal_state(self):
        """A crashed handler must fail the job, not leave it leased until expiry.

        The failure path used to pass the record read *before* the RUNNING
        transition. Its row_version was then one behind, the failure transition was
        rejected with ROW_VERSION_MISMATCH, and the job sat leased until the lease
        expired — every crashed validation ended as LEASE_EXPIRED after
        max_attempts, which is a dead end no operator can fix.
        """

        job, _ = self._create_validation_job()

        def explode(_job):
            raise RuntimeError("handler blew up")

        original = self.worker._validate
        self.worker._validate = explode
        try:
            self.assertTrue(self.worker.poll_once())
        finally:
            self.worker._validate = original

        updated = self.container.jobs.get(job.public_id)
        self.assertIsNotNone(updated)
        self.assertNotEqual(
            updated.status,
            JobState.RUNNING,
            "a crashed handler must not leave the job RUNNING",
        )
        self.assertIn(
            updated.status,
            (JobState.QUEUED, JobState.RETRY_SCHEDULED, JobState.FAILED, JobState.DEAD_LETTER),
            f"unexpected status after a crash: {updated.status}",
        )
        self.assertEqual(updated.error_code, "WORKER_ERROR")
        self.assertIn("RuntimeError", updated.error_summary or "")

    def test_expired_lease_is_rescheduled_then_reclaimed(self):
        job, _ = self._create_validation_job()
        now = now_utc()
        claimed = self.container.jobs.claim_next(
            JobClaim(
                queue_name="validation",
                worker_id="old-worker",
                lease_token_hash=ArtifactHash(sha256_digest(b"old-lease")),
                now=now,
                leased_until=now - timedelta(seconds=1),
            )
        )
        self.assertEqual(claimed.status, JobState.LEASED)

        self.assertFalse(self.worker.poll_once())
        recovered = self.container.jobs.get(job.public_id)
        self.assertEqual(recovered.status, JobState.RETRY_SCHEDULED)
        self.assertEqual(recovered.error_code, "LEASE_EXPIRED")
        self.assertTrue(self.worker.poll_once())
        self.assertEqual(self.container.jobs.get(job.public_id).status, JobState.SUCCEEDED)

    def test_expired_lease_at_attempt_limit_dead_letters(self):
        job = self.container.jobs.create_job(
            actor=self.actor,
            job_kind="unsupported",
            queue_name="validation",
            idempotency_key="expired-dead-letter",
            correlation_id="corr-worker-test",
            payload={},
            max_attempts=1,
        )
        now = now_utc()
        self.container.jobs.claim_next(
            JobClaim(
                queue_name="validation",
                worker_id="old-worker",
                lease_token_hash=ArtifactHash(sha256_digest(b"old-lease-2")),
                now=now,
                leased_until=now - timedelta(seconds=1),
            )
        )

        self.assertFalse(self.worker.poll_once())
        recovered = self.container.jobs.get(job.public_id)
        self.assertEqual(recovered.status, JobState.DEAD_LETTER)
        self.assertEqual(recovered.error_code, "LEASE_EXPIRED")

    def test_cancelled_queued_job_is_not_claimed(self):
        job = self.container.jobs.create_job(
            actor=self.actor,
            job_kind="resource_validation",
            queue_name="validation",
            idempotency_key="cancel-1",
            correlation_id="corr-worker-test",
            payload={"kind": "skill", "slug": "demo-code-review"},
        )
        cancelled = self.container.jobs.cancel(
            actor=self.actor,
            job_public_id=job.public_id,
            reason="operator cancelled",
            row_version=job.row_version,
        )

        self.assertEqual(cancelled.status, JobState.CANCELLED)
        self.assertFalse(self.worker.poll_once())

    def test_job_creation_is_idempotent(self):
        kwargs = dict(
            actor=self.actor,
            job_kind="resource_validation",
            queue_name="validation",
            idempotency_key="same-request",
            correlation_id="corr-worker-test",
            payload={"kind": "skill", "slug": "demo-code-review"},
        )
        first = self.container.jobs.create_job(**kwargs)
        second = self.container.jobs.create_job(**kwargs)

        self.assertEqual(first.public_id, second.public_id)
        self.assertEqual(len(self.store.jobs), 1)
        self.assertEqual(len(self.store.job_events[first.public_id]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
