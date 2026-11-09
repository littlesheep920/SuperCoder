"""The duplicate detector's rules, each pinned by a case built for it.

`apps/api/search/duplicates.py` decides with three rules (reuse is not duplication; same description
and body is an exact duplicate; otherwise a weighted similarity with a text-evidence floor) after
removing template segments. Each rule has a test here that fails if the rule is removed, plus the
properties a reviewer relies on: verdicts are symmetric, deterministic and explained. The last
class guards the evaluation protocol: the constants must be the ones the dev-only tuning chooses,
and the tuning must not depend on test labels.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from apps.api.search import duplicates  # noqa: E402
from apps.api.search.duplicates import DuplicateDetector, ResourceText  # noqa: E402

BOILERPLATE = "Report only what the input supports; state what cannot be determined."


def resource(slug: str, description: str, entry: str, *, kind: str = "prompt", name: str | None = None,
             tags: tuple[str, ...] = (), dependencies: frozenset = frozenset(), nodes: frozenset = frozenset()) -> ResourceText:
    return ResourceText(kind=kind, slug=slug, name=name or slug.replace("-", " "), description=description,
                        entry=entry, tags=tags, dependencies=dependencies, workflow_nodes=nodes)


def filler(count: int) -> list[ResourceText]:
    """Unrelated resources that share the boilerplate sentence, as an example corpus does."""

    topics = ["dns records", "image thumbnails", "payroll export", "queue depth", "font licensing", "tide tables",
              "parking permits", "seed banks"]
    return [resource(f"filler-{index}", f"Handles {topics[index % len(topics)]}. {BOILERPLATE}",
                     f"# Filler {index}\n\nWork on {topics[index % len(topics)]}.\n\n{BOILERPLATE}\n")
            for index in range(count)]


COMMIT = resource("commit-message", "Rewrites a diff summary into a conventional commit message.",
                  "# Commit Message\n\nRewrite the diff summary into a conventional commit message with type, scope and subject.\n",
                  tags=("commit", "writing"))
COMMIT_REWRITE = resource("commit-writer", "Drafts a conventional commit message, type scope and subject, from a diff summary.",
                          "# Commit Writer\n\nDraft the conventional commit message (type, scope, subject) from the diff summary.\n",
                          tags=("commit", "git"))


class RuleTests(unittest.TestCase):
    def detector(self, *extra: ResourceText) -> DuplicateDetector:
        return DuplicateDetector([COMMIT, COMMIT_REWRITE, *extra, *filler(8)])

    def test_a_paraphrase_with_the_same_job_is_a_near_duplicate(self) -> None:
        verdict = self.detector().judge(COMMIT, COMMIT_REWRITE)
        self.assertEqual(verdict.label, "near_duplicate", verdict)
        self.assertIn("weighted-similarity", verdict.rules)

    def test_the_same_description_and_body_is_an_exact_duplicate_whatever_the_title(self) -> None:
        copy = resource("commit-message-copy", COMMIT.description, COMMIT.entry.replace("# Commit Message", "# Team B Commits"),
                        name="Team B Commits", tags=("commit",))
        verdict = self.detector(copy).judge(COMMIT, copy)
        self.assertEqual(verdict.label, "exact_duplicate")
        self.assertIn("same-description-and-body", verdict.rules)

    def test_a_dependency_makes_a_pair_related_however_similar_the_text(self) -> None:
        user = resource("commit-flow", COMMIT.description, COMMIT.entry, kind="workflow",
                        dependencies=frozenset({("prompt", "commit-message")}))
        verdict = self.detector(user).judge(COMMIT, user)
        self.assertEqual(verdict.label, "related")
        self.assertIn("reuse-is-not-duplication", verdict.rules)

    def test_a_workflow_node_is_reuse_too(self) -> None:
        flow = resource("commit-flow", COMMIT_REWRITE.description, COMMIT_REWRITE.entry, kind="workflow",
                        nodes=frozenset({("prompt", "commit-writer")}))
        verdict = self.detector(flow).judge(flow, COMMIT_REWRITE)
        self.assertEqual(verdict.label, "related")

    def test_shared_tags_alone_never_make_a_duplicate(self) -> None:
        """The text-evidence floor: identical tags, unrelated text."""

        left = resource("tide-a", "Prints tomorrow's tide table for a harbour.", "# A\n\nPrint the tide table.\n", tags=("ops", "tides"))
        right = resource("font-b", "Checks a font file for an embedding licence.", "# B\n\nCheck the font licence.\n", tags=("ops", "tides"))
        verdict = self.detector(left, right).judge(left, right)
        self.assertNotIn(verdict.label, {"near_duplicate", "exact_duplicate"})
        self.assertEqual(verdict.components["tags"], 1.0)
        self.assertIn("below-text-floor", verdict.rules)

    def test_a_sentence_repeated_across_the_registry_is_template_and_carries_no_weight(self) -> None:
        detector = self.detector()
        self.assertIn(BOILERPLATE.split(";")[0].casefold() + ";", detector.templates)
        left, right = filler(8)[0], filler(8)[3]
        verdict = detector.judge(left, right)
        self.assertNotIn(verdict.label, {"near_duplicate", "exact_duplicate"})
        self.assertNotIn("supports", verdict.shared_terms)


class PropertyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = [COMMIT, COMMIT_REWRITE, *filler(8)]

    def test_verdicts_are_symmetric(self) -> None:
        detector = DuplicateDetector(self.registry)
        forward, backward = detector.judge(COMMIT, COMMIT_REWRITE), detector.judge(COMMIT_REWRITE, COMMIT)
        self.assertEqual((forward.label, forward.score), (backward.label, backward.score))

    def test_verdicts_are_deterministic(self) -> None:
        first = DuplicateDetector(self.registry).judge(COMMIT, COMMIT_REWRITE)
        second = DuplicateDetector(list(reversed(self.registry))).judge(COMMIT, COMMIT_REWRITE)
        self.assertEqual(first, second)

    def test_a_verdict_explains_itself(self) -> None:
        verdict = DuplicateDetector(self.registry).judge(COMMIT, COMMIT_REWRITE)
        self.assertEqual(set(verdict.components), {"purpose", "body", "tags"})
        self.assertIn("conventional", verdict.shared_terms)
        self.assertTrue(verdict.rules)


class ProtocolTests(unittest.TestCase):
    """The constants come from dev-only tuning, and tuning does not read test labels."""

    @classmethod
    def setUpClass(cls) -> None:
        import run_duplicate_eval

        cls.eval = run_duplicate_eval
        cls.registry = run_duplicate_eval.load_registry(ROOT)
        cls.items = run_duplicate_eval.load_items(ROOT)

    def test_the_module_constants_are_what_dev_tuning_chooses(self) -> None:
        chosen = self.eval.tune(self.items, self.registry)["chosen"]
        self.assertEqual(chosen["weights"], duplicates.WEIGHTS)
        self.assertEqual(chosen["duplicate_threshold"], duplicates.DUPLICATE_THRESHOLD)
        self.assertEqual(chosen["text_floor"], duplicates.TEXT_FLOOR)

    def test_tuning_ignores_the_test_labels(self) -> None:
        flipped = [dict(item, gold="near_duplicate" if item["gold"] == "not_duplicate" else "not_duplicate")
                   if item["split"] == "test" else item for item in self.items]
        self.assertEqual(self.eval.tune(flipped, self.registry)["chosen"], self.eval.tune(self.items, self.registry)["chosen"])


if __name__ == "__main__":
    unittest.main()
