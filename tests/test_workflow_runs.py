"""W3: the run state machine and the gate decision rules.

The transitions are simple; the rules that protect the organization are not. Each refusal test
below corresponds to a way an approval could otherwise become a transferable permission:

* a decision at the wrong gate,
* a decision replayed against a run that has moved on,
* a decision on a finished run,
* a decision with no reason, which would be an audit record that records nothing.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.domain.workflow_runs import (  # noqa: E402
    ALLOWED,
    TERMINAL,
    ApprovalRefused,
    GateDecision,
    RunGateState,
    RunStatus,
    RunTransitionRefused,
    approval_gate_for,
    can_transition,
    check_gate_decision,
    is_terminal,
    next_status_after_gate,
    transition,
)


class TransitionTests(unittest.TestCase):
    def test_queued_can_start(self) -> None:
        self.assertEqual(transition("queued", "running"), RunStatus.RUNNING)

    def test_queued_can_be_cancelled(self) -> None:
        self.assertEqual(transition("queued", "cancelled"), RunStatus.CANCELLED)

    def test_running_can_reach_a_gate(self) -> None:
        self.assertEqual(transition("running", "awaiting_approval"), RunStatus.AWAITING_APPROVAL)

    def test_running_can_succeed(self) -> None:
        self.assertEqual(transition("running", "succeeded"), RunStatus.SUCCEEDED)

    def test_a_gate_returns_to_running(self) -> None:
        """Approval continues execution; it does not complete the run by itself."""

        self.assertEqual(transition("awaiting_approval", "running"), RunStatus.RUNNING)

    def test_a_gate_cannot_jump_to_succeeded(self) -> None:
        """Otherwise a run could 'pass' a gate nobody approved."""

        with self.assertRaises(RunTransitionRefused):
            transition("awaiting_approval", "succeeded")

    def test_terminal_states_do_not_move(self) -> None:
        for state in TERMINAL:
            with self.subTest(state=state):
                for target in RunStatus:
                    self.assertFalse(can_transition(state, target))

    def test_a_queued_run_cannot_skip_to_succeeded(self) -> None:
        with self.assertRaises(RunTransitionRefused):
            transition("queued", "succeeded")

    def test_every_state_appears_in_the_table(self) -> None:
        """A state with no row would raise a KeyError at the worst moment."""

        self.assertEqual(set(ALLOWED), set(RunStatus))

    def test_terminal_states_have_no_outgoing_edges(self) -> None:
        for state in TERMINAL:
            self.assertEqual(ALLOWED[state], frozenset())

    def test_is_terminal_accepts_a_string(self) -> None:
        self.assertTrue(is_terminal("succeeded"))
        self.assertFalse(is_terminal("running"))

    def test_the_refusal_names_both_states(self) -> None:
        with self.assertRaises(RunTransitionRefused) as caught:
            transition("succeeded", "running")
        message = str(caught.exception)
        self.assertIn("succeeded", message)
        self.assertIn("running", message)


def gate_state(**overrides):
    base = {
        "status": "awaiting_approval",
        "approval_gate": "review-approval",
        "row_version": 3,
    }
    base.update(overrides)
    return RunGateState(**base)


def decision(**overrides):
    base = {
        "gate": "review-approval",
        "approved": True,
        "actor_id": "user-reviewer",
        "reason": "reviewed the plan",
    }
    base.update(overrides)
    return GateDecision(**base)


class GateDecisionTests(unittest.TestCase):
    def assert_refused(self, state, decision_value, code: str, *, row_version=None) -> None:
        expected = state.row_version if row_version is None else row_version
        with self.assertRaises(ApprovalRefused) as caught:
            check_gate_decision(state, decision_value, expected_row_version=expected)
        self.assertEqual(caught.exception.code, code)

    def test_a_valid_decision_passes(self) -> None:
        check_gate_decision(gate_state(), decision(), expected_row_version=3)

    def test_a_gate_decision_on_a_running_run_is_refused(self) -> None:
        self.assert_refused(gate_state(status="running"), decision(), "run_not_awaiting")

    def test_a_gate_decision_on_a_finished_run_is_refused(self) -> None:
        self.assert_refused(gate_state(status="succeeded"), decision(), "run_finished")

    def test_a_decision_for_a_different_gate_is_refused(self) -> None:
        """This is what stops an approval being carried to another gate."""

        self.assert_refused(gate_state(), decision(gate="publish-approval"), "wrong_gate")

    def test_a_stale_row_version_is_refused(self) -> None:
        self.assert_refused(gate_state(), decision(), "stale", row_version=2)

    def test_a_repeated_decision_says_it_was_already_decided(self) -> None:
        """'already decided' is a different problem from 'not awaiting approval'."""

        self.assert_refused(
            gate_state(status="running", decided_by="user-reviewer"),
            decision(),
            "already_decided",
        )

    def test_a_run_that_never_reached_a_gate_is_not_reported_as_decided(self) -> None:
        self.assert_refused(gate_state(status="running"), decision(), "run_not_awaiting")

    def test_awaiting_approval_without_a_gate_name_is_refused(self) -> None:
        """Refusing beats guessing which gate the approval applies to."""

        self.assert_refused(gate_state(approval_gate=None), decision(), "no_active_gate")

    def test_a_blank_reason_is_refused(self) -> None:
        """A gate decision is an audit record; one with no reason records nothing."""

        self.assert_refused(gate_state(), decision(reason="   "), "reason_required")

    def test_a_missing_actor_is_refused(self) -> None:
        self.assert_refused(gate_state(), decision(actor_id=""), "actor_required")

    def test_a_rejection_is_also_subject_to_the_rules(self) -> None:
        self.assert_refused(gate_state(), decision(approved=False, gate="other"), "wrong_gate")

    def test_approval_continues_the_run(self) -> None:
        self.assertEqual(next_status_after_gate(gate_state(), decision()), RunStatus.RUNNING)

    def test_rejection_stops_the_run(self) -> None:
        self.assertEqual(
            next_status_after_gate(gate_state(), decision(approved=False)), RunStatus.FAILED
        )

    def test_the_refusal_message_explains_the_staleness(self) -> None:
        """'Conflict' alone leaves a user unable to tell an expired page from a decided gate."""

        with self.assertRaises(ApprovalRefused) as caught:
            check_gate_decision(gate_state(), decision(), expected_row_version=1)
        self.assertIn("reload", str(caught.exception))


class GateNamingTests(unittest.TestCase):
    def test_a_stage_requiring_approval_names_its_gate(self) -> None:
        stages = (("needs-analysis", False), ("review", True))
        self.assertEqual(approval_gate_for(stages, 1), "review-approval")

    def test_a_stage_without_approval_has_no_gate(self) -> None:
        stages = (("needs-analysis", False),)
        self.assertIsNone(approval_gate_for(stages, 0))

    def test_an_out_of_range_stage_has_no_gate(self) -> None:
        self.assertIsNone(approval_gate_for((("a", True),), 5))


if __name__ == "__main__":
    unittest.main()
