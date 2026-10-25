"""A10/A12: the capability list and the server's authorization must agree.

The console renders buttons based on ``/v1/auth/me``. If that list can disagree
with what the server actually allows, the product breaks in both directions:

* a capability is advertised and the server rejects the action — a dead button,
  which reads as a bug and wastes the operator's time;
* a capability is withheld and the server would have allowed it — a hidden
  feature, which is invisible and therefore never reported.

The approach here is to **enforce** rather than restate. A small simulator mirrors
the governance service's gate checks by reading the same shared predicates, and
the test asserts that "capability present" and "simulated server allows" agree for
every role combination and every gated operation. If enforcement and the
capability table drift apart, one of them changes and this fails.

The simulator is deliberately written against the *predicates the server calls*,
not against the capability table, so the comparison is meaningful: two
independent derivations must land on the same answer.
"""

from __future__ import annotations

import itertools
import unittest

from apps.api.domain.capabilities import (
    Capability,
    GatedOperation,
    capabilities_for,
    capability_required_by,
    is_owner,
    is_publisher,
    is_validator,
)

ROLES = ("owner", "publisher", "validator", "reviewer")


class _Caller:
    """Minimal stand-in for AuthContext; the predicates read only these fields."""

    def __init__(self, roles: frozenset[str], teams: frozenset[str] = frozenset()) -> None:
        self.governance_roles = roles
        self.teams = teams
        self.actor = object()


def _server_allows(caller: _Caller, operation: GatedOperation) -> bool:
    """Mirror the governance service's role gates using the shared predicates.

    This intentionally restates the *checks* the service performs (owner for
    submission, publisher for publication, validator-or-reviewer for validation,
    owner-and-publisher for deprecation reversal) but expresses them through the
    same predicates the service now calls. Comparing this against the capability
    table is what makes the test meaningful.
    """

    if operation is GatedOperation.CREATE_RESOURCE:
        return is_owner(caller)
    if operation is GatedOperation.CREATE_VERSION:
        return is_owner(caller)
    if operation is GatedOperation.UPDATE_DRAFT:
        return is_owner(caller)
    if operation is GatedOperation.ROLLBACK:
        return is_owner(caller)
    if operation is GatedOperation.DELETE_RESOURCE:
        return is_owner(caller)
    if operation is GatedOperation.SUBMIT_VALIDATION:
        return is_owner(caller)
    if operation is GatedOperation.COMPLETE_VALIDATION:
        return is_validator(caller)
    if operation is GatedOperation.PUBLISH:
        return is_publisher(caller)
    if operation is GatedOperation.REVERSE_DEPRECATION:
        return is_owner(caller) and is_publisher(caller)
    if operation is GatedOperation.CREATE_REVIEW:
        return "reviewer" in caller.governance_roles
    if operation is GatedOperation.CANCEL_JOB:
        return is_owner(caller)
    if operation is GatedOperation.RETRY_JOB:
        return is_owner(caller) or is_publisher(caller)
    raise AssertionError(f"unmapped operation {operation}")


class CapabilityShapeTests(unittest.TestCase):
    def test_anonymous_roles_still_get_read_capabilities(self) -> None:
        """Read surfaces are not role-gated; visibility is filtered separately."""

        caps = capabilities_for(_Caller(frozenset()))

        self.assertIn(Capability.RESOURCE_READ, caps)
        self.assertIn(Capability.SEARCH, caps)
        self.assertIn(Capability.AUDIT_READ, caps)

    def test_anonymous_roles_get_no_mutation(self) -> None:
        caps = capabilities_for(_Caller(frozenset()))
        mutations = {cap for cap in caps if cap not in {
            Capability.RESOURCE_READ,
            Capability.SEARCH,
            Capability.AUDIT_READ,
        }}

        self.assertEqual(mutations, set())

    def test_owner_gets_authoring_and_submit(self) -> None:
        caps = capabilities_for(_Caller(frozenset({"owner"})))

        self.assertIn(Capability.RESOURCE_CREATE, caps)
        self.assertIn(Capability.RESOURCE_EDIT, caps)
        self.assertIn(Capability.LIFECYCLE_SUBMIT, caps)

    def test_owner_alone_cannot_publish(self) -> None:
        """Authoring and publishing are separate duties."""

        caps = capabilities_for(_Caller(frozenset({"owner"})))

        self.assertNotIn(Capability.LIFECYCLE_PUBLISH, caps)

    def test_publisher_can_publish(self) -> None:
        self.assertIn(
            Capability.LIFECYCLE_PUBLISH,
            capabilities_for(_Caller(frozenset({"publisher"}))),
        )

    def test_reviewer_can_complete_validation(self) -> None:
        """The server accepts validator *or* reviewer for validation."""

        for role in ("validator", "reviewer"):
            with self.subTest(role=role):
                self.assertIn(
                    Capability.LIFECYCLE_VALIDATE,
                    capabilities_for(_Caller(frozenset({role}))),
                )

    def test_deprecation_reversal_requires_both_owner_and_publisher(self) -> None:
        cases = {
            frozenset({"owner"}): False,
            frozenset({"publisher"}): False,
            frozenset({"owner", "publisher"}): True,
        }
        for roles, expected in cases.items():
            with self.subTest(roles=sorted(roles)):
                caps = capabilities_for(_Caller(roles))
                self.assertEqual(
                    Capability.LIFECYCLE_REVERSE_DEPRECATION in caps, expected
                )

    def test_every_role_combination_is_covered(self) -> None:
        for size in range(len(ROLES) + 1):
            for combination in itertools.combinations(ROLES, size):
                with self.subTest(roles=combination):
                    caps = capabilities_for(_Caller(frozenset(combination)))
                    self.assertIsInstance(caps, frozenset)
                    self.assertTrue(caps)


class OperationMappingTests(unittest.TestCase):
    def test_every_gated_operation_has_a_capability(self) -> None:
        assigned = [capability_required_by(op) for op in GatedOperation]
        self.assertEqual(len(assigned), len(list(GatedOperation)))

    def test_capability_and_server_agree_in_both_directions(self) -> None:
        """A12: for all 16 role combinations x all gated operations, they match.

        Both directions are covered by a single equality: if the capability is
        present but the server would refuse, or vice versa, this fails.
        """

        checked = 0
        for size in range(len(ROLES) + 1):
            for combination in itertools.combinations(ROLES, size):
                caller = _Caller(frozenset(combination))
                caps = capabilities_for(caller)
                for operation in GatedOperation:
                    required = capability_required_by(operation)
                    with self.subTest(roles=combination, operation=operation.value):
                        self.assertEqual(
                            required in caps,
                            _server_allows(caller, operation),
                            "capability presence must equal server permission",
                        )
                    checked += 1
        self.assertEqual(checked, 16 * len(list(GatedOperation)))

    def test_no_capability_is_orphaned(self) -> None:
        """Every mutation capability must gate at least one operation.

        An orphaned capability would let the console offer an action whose
        mapping nobody maintains.
        """

        mapped = {capability_required_by(op) for op in GatedOperation}
        mutations = {
            cap
            for cap in Capability
            if cap
            not in {
                Capability.RESOURCE_READ,
                Capability.SEARCH,
                Capability.AUDIT_READ,
            }
        }

        self.assertEqual(mutations - mapped, set(), "capabilities with no operation")

    def test_granting_more_roles_never_removes_a_capability(self) -> None:
        """Monotonicity: adding a role must not take an ability away."""

        for size in range(len(ROLES)):
            for combination in itertools.combinations(ROLES, size):
                base = capabilities_for(_Caller(frozenset(combination)))
                for extra in ROLES:
                    if extra in combination:
                        continue
                    grown = capabilities_for(_Caller(frozenset(combination) | {extra}))
                    with self.subTest(roles=combination, added=extra):
                        self.assertTrue(base <= grown, f"{extra} removed a capability")


if __name__ == "__main__":
    unittest.main()
