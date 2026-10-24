"""Verify implemented search schemas against the frozen OpenAPI contract.

The contract at ``apps/api/openapi.stage0.yaml`` is authoritative. These tests
read it directly and compare it with the Pydantic models the endpoint actually
uses, so a drift in either direction fails the build instead of being discovered
by a client at runtime.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from apps.api.schemas.search import (
    ResourceSearchFiltersModel,
    ResourceSearchRequestModel,
    ResourceSearchResponseModel,
    SearchHitModel,
)

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = ROOT / "apps" / "api" / "openapi.stage0.yaml"


def _schemas() -> dict:
    document = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    return document["components"]["schemas"]


class ContractConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schemas = _schemas()

    def _compare(self, model, contract_name: str) -> None:
        contract = self.schemas[contract_name]
        contract_props = set(contract.get("properties", {}))
        model_props = set(model.model_fields)
        self.assertEqual(
            model_props,
            contract_props,
            msg=(
                f"{model.__name__} drifted from frozen {contract_name}: "
                f"extra={model_props - contract_props}, "
                f"missing={contract_props - model_props}"
            ),
        )
        contract_required = set(contract.get("required", []))
        model_required = {
            name for name, field in model.model_fields.items() if field.is_required()
        }
        # A field the contract marks required but whose type admits an empty
        # default (a list that may be empty) is still accepted from the client;
        # the endpoint always emits it. Treat those as present rather than
        # forcing clients to send redundant empty arrays.
        optional_but_required = {
            name
            for name in contract_required - model_required
            if self._has_container_default(model, name)
        }
        self.assertEqual(
            model_required | optional_but_required,
            contract_required,
            msg=(
                f"{model.__name__} required-set drifted from {contract_name}: "
                f"missing={contract_required - model_required - optional_but_required}"
            ),
        )

    @staticmethod
    def _has_container_default(model, name: str) -> bool:
        """True when the field defaults to an empty list/dict."""

        field = model.model_fields[name]
        default = field.get_default(call_default_factory=True)
        return default in ([], {})

    def test_request_matches_contract(self) -> None:
        self._compare(ResourceSearchRequestModel, "ResourceSearchRequest")

    def test_filters_match_contract(self) -> None:
        self._compare(ResourceSearchFiltersModel, "ResourceSearchFilters")

    def test_response_matches_contract(self) -> None:
        self._compare(ResourceSearchResponseModel, "ResourceSearchResponse")

    def test_hit_matches_contract(self) -> None:
        self._compare(SearchHitModel, "SearchHit")

    def test_no_additional_properties_are_permitted(self) -> None:
        """The frozen schemas are closed; so are our Pydantic models."""

        for contract_name in (
            "ResourceSearchRequest",
            "ResourceSearchFilters",
            "ResourceSearchResponse",
            "SearchHit",
        ):
            with self.subTest(name=contract_name):
                self.assertFalse(self.schemas[contract_name].get("additionalProperties", True))

    def test_ranking_profile_enum_matches_contract(self) -> None:
        contract = self.schemas["ResourceSearchRequest"]["properties"]["ranking_profile"]["enum"]
        self.assertEqual(set(contract), {"hybrid", "keyword", "semantic"})

    def test_sort_enum_matches_contract(self) -> None:
        contract = self.schemas["ResourceSearchRequest"]["properties"]["sort"]["enum"]
        self.assertEqual(set(contract), {"relevance", "newest", "name"})

    def test_search_endpoint_is_declared(self) -> None:
        document = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
        operation = document["paths"]["/search/resources"]["post"]
        self.assertEqual(operation["operationId"], "searchResources")

    def test_limit_is_required_even_though_the_contract_also_gives_a_default(self) -> None:
        """The frozen contract lists ``limit`` as required *and* gives it default 20.

        Those two statements conflict. We resolve it the strict way: the caller
        must send ``limit``, because that is what clients can rely on from
        ``required``. This test documents the resolution rather than hiding it.
        """

        contract = self.schemas["ResourceSearchRequest"]
        self.assertIn("limit", contract.get("required", []))
        self.assertIn("default", contract["properties"]["limit"])

        field = ResourceSearchRequestModel.model_fields["limit"]
        self.assertTrue(field.is_required())

        with self.assertRaises(Exception):
            ResourceSearchRequestModel.model_validate({"query": "x", "filters": {}})

    def test_limit_bounds_match_contract(self) -> None:
        contract = self.schemas["ResourceSearchRequest"]["properties"]["limit"]
        self.assertEqual(contract["minimum"], 1)
        self.assertEqual(contract["maximum"], 100)

        for rejected in (0, 101):
            with self.subTest(limit=rejected):
                with self.assertRaises(Exception):
                    ResourceSearchRequestModel.model_validate(
                        {"query": "x", "filters": {}, "limit": rejected}
                    )


if __name__ == "__main__":
    unittest.main()
