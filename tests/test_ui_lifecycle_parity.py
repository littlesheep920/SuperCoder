"""Assert the frontend lifecycle table matches the Python state machine.

`apps/web/src/domain/lifecycle.ts` mirrors `allowed_targets()`. Two copies of a rule
drift, and the drift is silent: the console offers an edge the server rejects, or
hides one it would accept. Neither shows up as a crash.

This test reads both sources and compares them edge by edge. It is deliberately a
source-level comparison rather than a behavioural one — there is no TypeScript
runtime in the Python test process, and parsing the table is stable enough to be
worth it.

If this test fails, the fix is to update the TypeScript table, not to relax the test.
"""

from __future__ import annotations

import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.domain.lifecycle import allowed_targets  # noqa: E402
from apps.api.domain.models import LifecycleState  # noqa: E402

TYPESCRIPT = ROOT / "apps" / "web" / "src" / "domain" / "lifecycle.ts"


def parse_allowed_targets() -> dict[str, list[str]]:
    """Extract ALLOWED_TARGETS from the TypeScript module."""

    text = TYPESCRIPT.read_text(encoding="utf-8")
    block = re.search(
        r"export const ALLOWED_TARGETS[^=]*=\s*\{(.*?)\n\};",
        text,
        re.S,
    )
    if block is None:
        raise AssertionError("ALLOWED_TARGETS not found in lifecycle.ts")

    table: dict[str, list[str]] = {}
    for line in block.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        match = re.fullmatch(r"(\w+):\s*\[([^\]]*)\],?", line)
        if match is None:
            continue
        source = match.group(1)
        targets = re.findall(r'"(\w+)"', match.group(2))
        table[source] = targets
    return table


class LifecycleParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.front = parse_allowed_targets()

    def test_every_state_is_present_in_both(self) -> None:
        server_states = {state.value for state in LifecycleState}
        self.assertEqual(
            set(self.front),
            server_states,
            "lifecycle.ts and LifecycleState must declare the same states",
        )

    def test_edges_match_the_python_matrix(self) -> None:
        for state in LifecycleState:
            with self.subTest(state=state.value):
                server = sorted(target.value for target in allowed_targets(state))
                front = sorted(self.front.get(state.value, []))
                self.assertEqual(
                    front,
                    server,
                    f"{state.value}: frontend offers {front}, server allows {server}",
                )

    def test_no_unknown_state_names(self) -> None:
        known = {state.value for state in LifecycleState}
        for source, targets in self.front.items():
            with self.subTest(source=source):
                for target in targets:
                    self.assertIn(target, known, f"{source} points at unknown state {target}")

    def test_specs_only_use_permitted_edges(self) -> None:
        """Every offered button must correspond to an edge the server permits."""

        text = TYPESCRIPT.read_text(encoding="utf-8")
        specs = re.findall(r"target:\s*\"(\w+)\"", text)
        self.assertTrue(specs, "no transition specs found")
        permitted = {
            target for targets in self.front.values() for target in targets
        }
        for target in specs:
            with self.subTest(target=target):
                self.assertIn(target, permitted, f"spec targets {target}, which no edge reaches")

    def test_every_non_replay_edge_is_offerable(self) -> None:
        """A legal move with no button would be invisible to users.

        Same-state edges are exempt: they exist for replay semantics and a
        "re-apply current state" button would be noise.
        """

        text = TYPESCRIPT.read_text(encoding="utf-8")
        specs = set(re.findall(r"target:\s*\"(\w+)\"", text))
        for source, targets in self.front.items():
            with self.subTest(source=source):
                for target in targets:
                    if target == source:
                        continue
                    # Reachable from at least one state through a spec.
                    if target in specs:
                        continue
                    self.fail(f"edge {source} -> {target} has no transition spec")

    def test_back_to_draft_is_suppressed_on_draft(self) -> None:
        """The panel must not offer "back to draft" while already draft.

        The server accepts it as a replay and changes nothing, so the button would
        look broken. The rule lives in lifecycle.ts; this asserts it is written down.
        """

        text = TYPESCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'spec.target === "draft" && state === "draft"',
            text,
            "lifecycle.ts must suppress the no-op draft->draft button",
        )


if __name__ == "__main__":
    unittest.main()
