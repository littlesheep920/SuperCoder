"""W4: the approval gate, end to end through the service.

The domain rules are tested in `test_workflow_runs.py`. This tests what the service and the
route add on top: that the decision is applied to the right run, that a refusal writes nothing,
and that the gate is cleared once answered so a second decision cannot match it.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.application.workflow_runs import (
    GateDecisionRequest,
    RunCreationRequest,
    WorkflowRunService,
)
from apps.api.auth import AuthContext
from apps.api.domain.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    PreconditionFailedError,
)
from apps.api.domain.models import ActorIdentity
from tests.support.memory import MemoryStore, make_uow_factory
from tests.support.workflow_fixtures import publish_capability, register_workflow


def actor(name: str = "user-reviewer", roles: frozenset[str] = frozenset({"reviewer"})) -> AuthContext:
    """A caller. Deciding a gate needs the reviewer role, so that is the default here."""

    return AuthContext(
        actor=ActorIdentity(subject_id=name, actor_type="user", display_name=name),
        grants=(),
        teams=frozenset(),
        ui_roles_by_team={},
        organization_member=True,
        governance_roles=roles,
    )


class GateFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryStore()
        self.uow_factory = make_uow_factory(self.store)
        self.service = WorkflowRunService(self.uow_factory)
        self.definition = register_workflow(
            self.uow_factory,
            stages=[
                {
                    "stage_id": "review",
                    "title": "评审",
                    "capabilities": [{"kind": "skill", "slug": "review", "version": "1.0.0"}],
                    "gate": "human-approval",
                }
            ],
        )
        publish_capability(self.uow_factory, "skill", "review", "1.0.0")

    def create_run(self, requester: str = "user-owner"):
        return self.service.create_run(
            actor(requester),
            RunCreationRequest(
                workflow_definition_id=self.definition.public_id,
                runtime="claude-code",
                input={"ticket": "PROJ-2"},
                timeout_seconds=600,
                idempotency_key=f"gate-key-{requester}",
                correlation_id="corr-gate",
            ),
        )

    def run_at_gate(self, gate: str = "review-approval", requester: str = "user-owner"):
        """A run that has reached a gate, as the worker would leave it."""

        run = self.create_run(requester)
        # Move it through the real state machine rather than writing a status directly, so the
        # test cannot set up a state the machine would refuse.
        with self.uow_factory() as uow:
            from apps.api.repositories.contracts import WorkflowRunUpdate

            uow.workflows.update_run(
                run.public_id,
                WorkflowRunUpdate(status="running", row_version=run.row_version),
                expected_row_version=run.row_version,
            )
            uow.commit()
        return self.service.open_gate(actor(), run.public_id, gate)


class OpenGateTests(GateFixture):
    def test_a_running_run_can_enter_a_gate(self) -> None:
        run = self.run_at_gate()
        self.assertEqual(run.status, "awaiting_approval")

    def test_the_gate_name_is_recorded(self) -> None:
        """Without the name, a decision cannot be checked against anything."""

        run = self.run_at_gate()
        self.assertEqual(run.approval_gate, "review-approval")

    def test_a_queued_run_cannot_enter_a_gate(self) -> None:
        """Only a running run has passed enough to be waiting for approval."""

        run = self.create_run()
        with self.assertRaises(Exception):
            self.service.open_gate(actor(), run.public_id, "some-gate")

    def test_opening_a_gate_on_an_unknown_run_is_refused(self) -> None:
        with self.assertRaises(NotFoundError):
            self.service.open_gate(actor(), "nope", "g")


class DecideTests(GateFixture):
    def decision(self, run, **overrides) -> GateDecisionRequest:
        base = dict(
            gate="review-approval",
            decision="approve",
            reason="looks right",
            row_version=run.row_version,
        )
        base.update(overrides)
        return GateDecisionRequest(**base)

    def test_approval_returns_the_run_to_running(self) -> None:
        run = self.run_at_gate()
        decided = self.service.decide(actor(), run.public_id, self.decision(run))
        self.assertEqual(decided.status, "running")

    def test_approval_records_who_and_why(self) -> None:
        """A decision with no actor or reason is not an audit record."""

        run = self.run_at_gate()
        decided = self.service.decide(actor("user-approver"), run.public_id, self.decision(run))
        self.assertEqual(decided.approved_by, "user-approver")
        self.assertEqual(decided.approval_reason, "looks right")

    def test_approval_clears_the_gate(self) -> None:
        """A cleared gate is what stops the same approval matching a later gate."""

        run = self.run_at_gate()
        decided = self.service.decide(actor(), run.public_id, self.decision(run))
        self.assertIsNone(decided.approval_gate)

    def test_a_rejection_records_when_the_run_finished(self) -> None:
        """A finished run says when it finished; MySQL refuses one that does not."""

        run = self.run_at_gate()
        decided = self.service.decide(
            actor(), run.public_id, self.decision(run, decision="reject")
        )
        self.assertIsNotNone(decided.finished_at)

    def test_an_approval_leaves_the_run_unfinished(self) -> None:
        run = self.run_at_gate()
        decided = self.service.decide(actor(), run.public_id, self.decision(run))
        self.assertIsNone(decided.finished_at)

    def test_a_caller_without_the_reviewer_role_cannot_decide(self) -> None:
        """Any authenticated identity could approve before; the read-only member included."""

        run = self.run_at_gate()
        with self.assertRaises(PermissionDeniedError):
            self.service.decide(
                actor("user-member", roles=frozenset()), run.public_id, self.decision(run)
            )
        with self.uow_factory() as uow:
            unchanged = uow.workflows.get_run(run.public_id)
            uow.commit()
        self.assertEqual(unchanged.status, "awaiting_approval")
        self.assertIsNone(unchanged.approved_by)

    def test_an_owner_or_publisher_is_not_a_reviewer(self) -> None:
        run = self.run_at_gate()
        for roles in (frozenset({"owner"}), frozenset({"owner", "publisher"}), frozenset({"validator"})):
            with self.subTest(roles=sorted(roles)):
                with self.assertRaises(PermissionDeniedError):
                    self.service.decide(actor("user-x", roles=roles), run.public_id, self.decision(run))

    def test_the_requester_cannot_approve_their_own_run(self) -> None:
        """SECURITY_AND_CONFIGURATION.md §10, even when the requester is a reviewer."""

        run = self.run_at_gate(requester="user-reviewer")
        with self.assertRaises(PermissionDeniedError):
            self.service.decide(actor("user-reviewer"), run.public_id, self.decision(run))
        with self.uow_factory() as uow:
            unchanged = uow.workflows.get_run(run.public_id)
            uow.commit()
        self.assertEqual(unchanged.status, "awaiting_approval")

    def test_the_requester_may_reject_their_own_run(self) -> None:
        """Rejecting only makes the outcome more conservative; it is not self-approval."""

        run = self.run_at_gate(requester="user-reviewer")
        decided = self.service.decide(
            actor("user-reviewer"), run.public_id, self.decision(run, decision="reject")
        )
        self.assertEqual(decided.status, "failed")

    def test_another_reviewer_can_approve(self) -> None:
        run = self.run_at_gate(requester="user-reviewer")
        decided = self.service.decide(actor("user-reviewer-2"), run.public_id, self.decision(run))
        self.assertEqual(decided.status, "running")
        self.assertEqual(decided.approved_by, "user-reviewer-2")

    def test_rejection_ends_the_run(self) -> None:
        run = self.run_at_gate()
        decided = self.service.decide(
            actor(), run.public_id, self.decision(run, decision="reject")
        )
        self.assertEqual(decided.status, "failed")
        self.assertEqual(decided.error_code, "approval_rejected")

    def test_a_stale_row_version_is_a_precondition_failure(self) -> None:
        """412, not 409: the client's copy is old rather than the request being wrong."""

        run = self.run_at_gate()
        with self.assertRaises(PreconditionFailedError):
            self.service.decide(actor(), run.public_id, self.decision(run, row_version=99))

    def test_a_decision_at_the_wrong_gate_is_a_conflict(self) -> None:
        run = self.run_at_gate()
        with self.assertRaises(ConflictError):
            self.service.decide(actor(), run.public_id, self.decision(run, gate="other-gate"))

    def test_a_second_decision_is_refused(self) -> None:
        run = self.run_at_gate()
        decided = self.service.decide(actor(), run.public_id, self.decision(run))
        with self.assertRaises((ConflictError, PreconditionFailedError)):
            self.service.decide(
                actor("someone-else"),
                run.public_id,
                self.decision(run, row_version=decided.row_version),
            )

    def test_a_refused_decision_writes_nothing(self) -> None:
        """A refused decision must not leave a partial record of having been made."""

        run = self.run_at_gate()
        before = self.service.get_run(actor(), run.public_id)
        with self.assertRaises(ConflictError):
            self.service.decide(actor(), run.public_id, self.decision(run, gate="wrong"))
        after = self.service.get_run(actor(), run.public_id)
        self.assertEqual(before.row_version, after.row_version)
        self.assertEqual(after.status, "awaiting_approval")
        self.assertIsNone(after.approved_by)

    def test_deciding_an_unknown_run_is_refused(self) -> None:
        with self.assertRaises(NotFoundError):
            self.service.decide(
                actor(),
                "nope",
                GateDecisionRequest("g", "approve", "r", 1),
            )

    def test_an_empty_reason_is_refused(self) -> None:
        run = self.run_at_gate()
        with self.assertRaises(ConflictError):
            self.service.decide(actor(), run.public_id, self.decision(run, reason="   "))
