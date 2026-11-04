"""W2: resolving capability references to exact versions.

Each test states one rule from RESOURCE_GOVERNANCE.md §7.1 and checks both that the rule
holds and that a violation is refused rather than repaired. The refusal cases matter more than
the acceptance cases: a resolver that silently picks something is worse than one that stops,
because the wrong version is only discovered when someone tries to reproduce a run.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.domain.workflow_resolution import (  # noqa: E402
    CandidateVersion,
    DeclaredDependency,
    ResolutionError,
    resolve_definition,
)
from apps.api.domain.workflows import parse_definition  # noqa: E402


class FakeCatalog:
    """A catalog the tests fully control, with no database behind it."""

    def __init__(self) -> None:
        self.versions: dict[tuple[str, str], list[CandidateVersion]] = {}
        self.dependencies: dict[str, list[DeclaredDependency]] = {}

    def add(self, kind: str, slug: str, version: str, status: str = "published", **kwargs) -> None:
        version_id = kwargs.pop("version_id", f"v-{slug}-{version}")
        resource_id = kwargs.pop("resource_id", f"r-{slug}")
        artifact_hash = kwargs.pop("artifact_hash", f"sha256:{slug}{version}".replace(".", ""))
        self.versions.setdefault((kind, slug), []).append(
            CandidateVersion(
                version_id=version_id,
                resource_id=resource_id,
                version=version,
                status=status,
                artifact_hash=artifact_hash,
            )
        )

    def depends_on(self, version_id: str, kind: str, slug: str, constraint: str = "*") -> None:
        self.dependencies.setdefault(version_id, []).append(
            DeclaredDependency(kind=kind, slug=slug, version_constraint=constraint)
        )

    def candidate_versions(self, kind: str, slug: str):
        return list(self.versions.get((kind, slug), ()))

    def declared_dependencies(self, version_public_id: str):
        return list(self.dependencies.get(version_public_id, ()))


def definition_for(*stages):
    return parse_definition(
        {
            "schema_version": "workflow/v1",
            "title": "test",
            "stages": list(stages),
        }
    )


def one_stage(kind="skill", slug="review", version="1.0.0", **extra):
    return {
        "stage_id": "s1",
        "title": "Stage",
        "capabilities": [{"kind": kind, "slug": slug, "version": version, **extra}],
    }


class SelectionTests(unittest.TestCase):
    def test_an_exact_constraint_selects_that_version(self) -> None:
        catalog = FakeCatalog()
        catalog.add("skill", "review", "1.0.0")
        catalog.add("skill", "review", "1.1.0")

        resolution = resolve_definition(definition_for(one_stage(version="1.0.0")), catalog)

        self.assertEqual([node.version for node in resolution.nodes], ["1.0.0"])

    def test_the_highest_version_satisfying_a_range_wins(self) -> None:
        catalog = FakeCatalog()
        for version in ("1.0.0", "1.4.0", "1.9.0", "2.0.0"):
            catalog.add("skill", "review", version)

        resolution = resolve_definition(definition_for(one_stage(version=">=1.0.0 <2.0.0")), catalog)

        self.assertEqual(resolution.nodes[0].version, "1.9.0")

    def test_a_prerelease_is_never_selected(self) -> None:
        """Selecting 2.0.0-rc by accident is how a run picks up unreviewed behaviour."""

        catalog = FakeCatalog()
        catalog.add("skill", "review", "1.0.0")
        catalog.add("skill", "review", "2.0.0-rc.1")

        resolution = resolve_definition(definition_for(one_stage(version=">=1.0.0")), catalog)

        self.assertEqual(resolution.nodes[0].version, "1.0.0")

    def test_a_published_version_is_preferred_over_a_newer_deprecated_one(self) -> None:
        """Executable does not mean preferred: a withdrawal notice should be respected."""

        catalog = FakeCatalog()
        catalog.add("skill", "review", "1.0.0", status="published")
        catalog.add("skill", "review", "1.1.0", status="deprecated")

        resolution = resolve_definition(definition_for(one_stage(version=">=1.0.0")), catalog)

        self.assertEqual(resolution.nodes[0].version, "1.0.0")

    def test_a_deprecated_version_is_usable_when_it_is_the_only_one(self) -> None:
        """An existing workflow must not stop dead the moment a version is deprecated."""

        catalog = FakeCatalog()
        catalog.add("skill", "review", "1.0.0", status="deprecated")

        resolution = resolve_definition(definition_for(one_stage(version="1.0.0")), catalog)

        self.assertEqual(resolution.nodes[0].status, "deprecated")

    def test_the_artifact_hash_is_carried_into_the_resolution(self) -> None:
        """The hash is what makes a run reproducible; dropping it defeats the purpose."""

        catalog = FakeCatalog()
        catalog.add("skill", "review", "1.0.0", artifact_hash="sha256:abc123")

        resolution = resolve_definition(definition_for(one_stage()), catalog)

        self.assertEqual(resolution.nodes[0].artifact_hash, "sha256:abc123")


class RefusalTests(unittest.TestCase):
    def assert_refused(self, catalog, definition, code: str) -> None:
        with self.assertRaises(ResolutionError) as caught:
            resolve_definition(definition, catalog)
        codes = {problem.code for problem in caught.exception.problems}
        self.assertIn(code, codes, f"expected {code}, got {codes}")

    def test_an_unknown_resource_is_refused(self) -> None:
        self.assert_refused(FakeCatalog(), definition_for(one_stage()), "not_found")

    def test_a_draft_version_is_not_executable(self) -> None:
        catalog = FakeCatalog()
        catalog.add("skill", "review", "1.0.0", status="draft")
        self.assert_refused(catalog, definition_for(one_stage()), "not_executable")

    def test_a_retired_version_is_not_executable(self) -> None:
        catalog = FakeCatalog()
        catalog.add("skill", "review", "1.0.0", status="retired")
        self.assert_refused(catalog, definition_for(one_stage()), "not_executable")

    def test_a_star_constraint_is_refused_even_though_the_schema_allows_it(self) -> None:
        """`*` is legal while drafting; a formal run must not float."""

        catalog = FakeCatalog()
        catalog.add("skill", "review", "1.0.0")
        self.assert_refused(catalog, definition_for(one_stage(version="*")), "no_matching_version")

    def test_an_unsatisfiable_range_is_refused(self) -> None:
        catalog = FakeCatalog()
        catalog.add("skill", "review", "1.0.0")
        self.assert_refused(
            catalog, definition_for(one_stage(version=">=9.0.0")), "no_matching_version"
        )

    def test_only_a_prerelease_matching_is_refused(self) -> None:
        catalog = FakeCatalog()
        catalog.add("skill", "review", "2.0.0-rc.1")
        self.assert_refused(
            catalog, definition_for(one_stage(version=">=2.0.0-rc.1")), "no_stable_version"
        )

    def test_an_unparseable_constraint_is_refused(self) -> None:
        catalog = FakeCatalog()
        catalog.add("skill", "review", "1.0.0")
        self.assert_refused(
            catalog, definition_for(one_stage(version="latest")), "no_matching_version"
        )

    def test_one_unresolvable_capability_refuses_the_whole_workflow(self) -> None:
        """A partial resolution would let a run start with an unrecorded dependency."""

        catalog = FakeCatalog()
        catalog.add("skill", "review", "1.0.0")

        definition = definition_for(
            {
                "stage_id": "s1",
                "title": "Good",
                "capabilities": [{"kind": "skill", "slug": "review", "version": "1.0.0"}],
            },
            {
                "stage_id": "s2",
                "title": "Bad",
                "capabilities": [{"kind": "skill", "slug": "missing", "version": "1.0.0"}],
            },
        )

        with self.assertRaises(ResolutionError):
            resolve_definition(definition, catalog)

    def test_every_problem_is_reported_together(self) -> None:
        catalog = FakeCatalog()
        catalog.add("skill", "review", "1.0.0", status="draft")
        catalog.add("tool", "lint", "1.0.0", status="draft")

        definition = definition_for(
            {
                "stage_id": "s1",
                "title": "Two bad",
                "capabilities": [
                    {"kind": "skill", "slug": "review", "version": "1.0.0"},
                    {"kind": "tool", "slug": "lint", "version": "1.0.0"},
                ],
            }
        )

        with self.assertRaises(ResolutionError) as caught:
            resolve_definition(definition, catalog)
        self.assertEqual(len(caught.exception.problems), 2)

    def test_the_refusal_message_names_the_visible_states(self) -> None:
        """'not executable' is useless without saying what is visible instead."""

        catalog = FakeCatalog()
        catalog.add("skill", "review", "1.0.0", status="draft")

        with self.assertRaises(ResolutionError) as caught:
            resolve_definition(definition_for(one_stage()), catalog)
        self.assertIn("draft", str(caught.exception))


class DependencyGraphTests(unittest.TestCase):
    def test_a_dependency_on_a_later_stage_is_refused(self) -> None:
        """A stage cannot use what a later stage provides."""

        catalog = FakeCatalog()
        catalog.add("skill", "early", "1.0.0")
        catalog.add("skill", "late", "1.0.0")
        catalog.depends_on("v-early-1.0.0", "skill", "late")

        definition = definition_for(
            {
                "stage_id": "first",
                "title": "First",
                "capabilities": [{"kind": "skill", "slug": "early", "version": "1.0.0"}],
            },
            {
                "stage_id": "second",
                "title": "Second",
                "capabilities": [{"kind": "skill", "slug": "late", "version": "1.0.0"}],
            },
        )

        with self.assertRaises(ResolutionError) as caught:
            resolve_definition(definition, catalog)
        self.assertIn("forward_dependency", {p.code for p in caught.exception.problems})

    def test_a_cycle_between_resolved_capabilities_is_refused(self) -> None:
        catalog = FakeCatalog()
        catalog.add("skill", "a", "1.0.0")
        catalog.add("skill", "b", "1.0.0")
        catalog.depends_on("v-a-1.0.0", "skill", "b")
        catalog.depends_on("v-b-1.0.0", "skill", "a")

        definition = definition_for(
            {
                "stage_id": "s1",
                "title": "Both",
                "capabilities": [
                    {"kind": "skill", "slug": "a", "version": "1.0.0"},
                    {"kind": "skill", "slug": "b", "version": "1.0.0"},
                ],
            }
        )

        with self.assertRaises(ResolutionError) as caught:
            resolve_definition(definition, catalog)
        self.assertIn("cycle", {p.code for p in caught.exception.problems})

    def test_a_dependency_on_something_outside_the_workflow_is_ignored(self) -> None:
        """A capability may depend on anything; only what the workflow resolves is a cycle."""

        catalog = FakeCatalog()
        catalog.add("skill", "a", "1.0.0")
        catalog.depends_on("v-a-1.0.0", "skill", "not-in-this-workflow")

        resolution = resolve_definition(definition_for(one_stage(slug="a")), catalog)

        self.assertEqual(len(resolution.nodes), 1)


class DigestTests(unittest.TestCase):
    def test_the_digest_is_stable_across_stage_order(self) -> None:
        """The hash identifies what was resolved, not the order it was written in.

        The stages really are ordered differently here. Comparing two definitions with the
        same order would pass even if the implementation hashed the list as given, which is
        the thing this test exists to prevent.
        """

        catalog = FakeCatalog()
        catalog.add("skill", "a", "1.0.0")
        catalog.add("skill", "b", "1.0.0")

        first = resolve_definition(
            definition_for(
                {"stage_id": "s1", "title": "A", "capabilities": [{"kind": "skill", "slug": "a", "version": "1.0.0"}]},
                {"stage_id": "s2", "title": "B", "capabilities": [{"kind": "skill", "slug": "b", "version": "1.0.0"}]},
            ),
            catalog,
        )
        reordered = resolve_definition(
            definition_for(
                {"stage_id": "s2", "title": "B", "capabilities": [{"kind": "skill", "slug": "b", "version": "1.0.0"}]},
                {"stage_id": "s1", "title": "A", "capabilities": [{"kind": "skill", "slug": "a", "version": "1.0.0"}]},
            ),
            catalog,
        )

        # The node lists really are in different orders; only the digest is order-free.
        self.assertNotEqual(
            [node.slug for node in first.nodes],
            [node.slug for node in reordered.nodes],
            "the test is only meaningful if the two resolutions are ordered differently",
        )
        self.assertEqual(first.digest_material(), reordered.digest_material())

    def test_a_different_version_changes_the_digest(self) -> None:
        catalog = FakeCatalog()
        catalog.add("skill", "a", "1.0.0")
        catalog.add("skill", "a", "1.1.0")

        one = resolve_definition(definition_for(one_stage(slug="a", version="1.0.0")), catalog)
        two = resolve_definition(definition_for(one_stage(slug="a", version="1.1.0")), catalog)

        self.assertNotEqual(one.digest_material(), two.digest_material())


if __name__ == "__main__":
    unittest.main()
