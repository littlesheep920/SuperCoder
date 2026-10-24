from __future__ import annotations

import unittest

from apps.api.domain.models import ResourceKind
from apps.api.repositories.contracts import NewResource
from tests.support.memory import MemoryStore, make_uow_factory


class MemoryUnitOfWorkTransactionTest(unittest.TestCase):
    def _resource(self) -> NewResource:
        return NewResource(
            public_id="resource-1",
            kind=ResourceKind.SKILL,
            slug="demo",
            owner_team="team-a",
            created_by="user-1",
        )

    def test_commit_preserves_all_mutations(self):
        store = MemoryStore()
        with make_uow_factory(store)() as uow:
            uow.resources.add(self._resource())
            store.permissions["version-1"] = ("read",)
            store.job_events["job-1"] = ["started"]
            store.audit.append("created")
            uow.commit()

        self.assertIn("resource-1", store.resources)
        self.assertEqual(store.permissions["version-1"], ("read",))
        self.assertEqual(store.job_events["job-1"], ["started"])
        self.assertEqual(store.audit, ["created"])

    def test_exception_restores_every_store_collection(self):
        store = MemoryStore()
        with self.assertRaises(RuntimeError):
            with make_uow_factory(store)() as uow:
                uow.resources.add(self._resource())
                store.permissions["version-1"] = ("read",)
                store.job_events["job-1"] = ["started"]
                store.audit.append("created")
                raise RuntimeError("simulated transaction failure")

        self.assertEqual(store.resources, {})
        self.assertEqual(store.resource_by_identity, {})
        self.assertEqual(store.permissions, {})
        self.assertEqual(store.job_events, {})
        self.assertEqual(store.audit, [])

    def test_uncommitted_normal_exit_rolls_back(self):
        store = MemoryStore()
        with make_uow_factory(store)() as uow:
            uow.resources.add(self._resource())

        self.assertEqual(store.resources, {})
        self.assertEqual(store.resource_by_identity, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
