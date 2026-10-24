from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone

from apps.api.auth import AuthContext
from apps.api.domain.errors import NotFoundError
from apps.api.domain.models import ActorIdentity, ActorType, JobState
from apps.api.repositories.contracts import JobEventRecord, JobQuery
from apps.api.routers.system import stream_job_events
from tests.support.memory import MemoryStore, make_uow_factory
from tests.test_stage1_api import build_container


class _ControlledRequest:
    def __init__(self, last_event_id: str | None = None) -> None:
        self.headers = {} if last_event_id is None else {"Last-Event-ID": last_event_id}
        self._disconnect_checks = 0

    async def is_disconnected(self) -> bool:
        self._disconnect_checks += 1
        return self._disconnect_checks > 0


class Stage1SseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryStore()
        self.container = build_container(self.store)
        self.actor = ActorIdentity("worker-1", ActorType.WORKER)
        self.auth = AuthContext(
            actor=self.actor,
            grants=(),
            teams=frozenset(),
            ui_roles_by_team={},
            organization_member=True,
        )
        self.job = self.container.jobs.create_job(
            actor=self.actor,
            job_kind="resource.validate",
            queue_name="validation",
            idempotency_key="sse-test-job",
            correlation_id="corr-sse-test",
            payload={"resource": "demo"},
        )
        now = datetime.now(timezone.utc)
        with make_uow_factory(self.store)() as uow:
            uow.jobs.append_event(
                JobEventRecord(
                    job_public_id=self.job.public_id,
                    sequence_no=2,
                    event_type="leased",
                    actor_type=ActorType.WORKER,
                    actor_id="worker-1",
                    payload={},
                    occurred_at=now,
                    from_status=JobState.QUEUED,
                    to_status=JobState.LEASED,
                )
            )
            uow.jobs.append_event(
                JobEventRecord(
                    job_public_id=self.job.public_id,
                    sequence_no=3,
                    event_type="succeeded",
                    actor_type=ActorType.WORKER,
                    actor_id="worker-1",
                    payload={"result": "ok"},
                    occurred_at=now,
                    from_status=JobState.LEASED,
                    to_status=JobState.SUCCEEDED,
                )
            )
            uow.commit()

    @staticmethod
    def _body(response) -> str:
        async def consume() -> str:
            chunks: list[str] = []
            async for chunk in response.body_iterator:
                chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
            return "".join(chunks)

        return asyncio.run(consume())

    def test_stream_emits_ordered_events_and_terminal_event(self):
        request = _ControlledRequest()
        response = stream_job_events(self.job.public_id, request, self.container, self.auth)
        body = self._body(response)

        self.assertEqual(response.media_type, "text/event-stream")
        self.assertIn("id: 1\nevent: queued", body)
        self.assertIn("id: 2\nevent: leased", body)
        self.assertIn("id: 3\nevent: succeeded", body)
        self.assertLess(body.index("id: 1"), body.index("id: 2"))
        self.assertLess(body.index("id: 2"), body.index("id: 3"))

    def test_last_event_id_resumes_after_consumed_event(self):
        request = _ControlledRequest("1")
        response = stream_job_events(self.job.public_id, request, self.container, self.auth)
        body = self._body(response)

        self.assertNotIn("id: 1\nevent: queued", body)
        self.assertIn("id: 2\nevent: leased", body)
        self.assertIn("id: 3\nevent: succeeded", body)

    def test_non_organization_caller_cannot_read_unscoped_job(self):
        restricted = AuthContext(
            actor=ActorIdentity("member-1", ActorType.USER),
            grants=(),
            teams=frozenset({"other-team"}),
            ui_roles_by_team={},
            organization_member=False,
        )
        with self.assertRaises(NotFoundError):
            stream_job_events(self.job.public_id, _ControlledRequest(), self.container, restricted)

    def test_job_list_filters_unscoped_jobs_before_returning(self):
        restricted = JobQuery(
            scope=AuthContext(
                actor=ActorIdentity("member-1", ActorType.USER),
                grants=(),
                teams=frozenset({"other-team"}),
                ui_roles_by_team={},
                organization_member=False,
            ).visibility_scope()
        )
        self.assertEqual(self.container.jobs.list_jobs(restricted), [])
        self.assertEqual(len(self.container.jobs.list_jobs(JobQuery(scope=self.auth.visibility_scope()))), 1)

    def test_unknown_job_fails_before_streaming(self):
        with self.assertRaises(NotFoundError):
            stream_job_events("missing-job", _ControlledRequest(), self.container, self.auth)


if __name__ == "__main__":
    unittest.main(verbosity=2)
