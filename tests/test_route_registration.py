"""Every router decorator must produce a registered route.

Why this exists: the `GET /resource-versions/{id}/diff` decorator was accidentally
deleted while another handler was added to the same file. The handler body stayed, so
the module imported cleanly, the tests passed, and CI stayed green — the endpoint just
silently stopped existing. The client kept calling it and received 404s.

A unit test cannot notice a route that is absent unless something asserts the route
*set*. That is what this does: it compares the decorators found in the router sources
against the routes FastAPI actually registered, in both directions.
"""

from __future__ import annotations

import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.main import create_app  # noqa: E402

ROUTERS = ROOT / "apps" / "api" / "routers"
PREFIX = "/v1"


def decorated_routes() -> set[tuple[str, str]]:
    """(method, path) pairs declared by `@router.<verb>("...")` in every router."""

    found: set[tuple[str, str]] = set()
    for source in sorted(ROUTERS.glob("*.py")):
        text = source.read_text(encoding="utf-8")
        for match in re.finditer(r'@router\.(get|post|patch|put|delete)\("([^"]+)"', text):
            found.add((match.group(1).upper(), PREFIX + match.group(2)))
    return found


def registered_routes() -> set[tuple[str, str]]:
    app = create_app()
    found: set[tuple[str, str]] = set()
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            if method.lower() in {"get", "post", "patch", "put", "delete"}:
                found.add((method.upper(), path))
    return found


class RouteRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.declared = decorated_routes()
        cls.registered = registered_routes()

    def test_every_decorated_route_is_registered(self) -> None:
        missing = sorted(self.declared - self.registered)
        self.assertEqual(
            missing,
            [],
            "these decorators exist in the source but no route was registered: "
            f"{missing}",
        )

    def test_every_registered_route_comes_from_a_decorator(self) -> None:
        """A route nobody declared would mean a second registration path."""

        extra = sorted(self.registered - self.declared)
        self.assertEqual(extra, [], f"registered but undeclared: {extra}")

    def test_the_diff_route_is_registered(self) -> None:
        """Regression guard for the specific decorator that was lost."""

        self.assertIn(
            ("GET", f"{PREFIX}/resource-versions/{{version_id}}/diff"),
            self.registered,
        )

    def test_the_body_route_has_both_methods(self) -> None:
        for method in ("GET", "PUT"):
            with self.subTest(method=method):
                self.assertIn(
                    (method, f"{PREFIX}/resource-versions/{{version_id}}/body"),
                    self.registered,
                )


if __name__ == "__main__":
    unittest.main()
