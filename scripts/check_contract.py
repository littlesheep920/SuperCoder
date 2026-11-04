"""CI gate: every contract operation must exist in the code, and vice versa.

Twice now the frozen contract and the implementation drifted apart. Once a route's
decorator was deleted while its handler stayed, so the endpoint silently stopped existing
while every test passed. Once eleven implemented operations were never described at all,
so the contract understated the system.

A test can only catch drift it is written to catch. This gate states the invariant
directly: the set of operations in `openapi.stage0.yaml` and the set of routes the routers
register must agree, except for the endpoints the contract explicitly declares out of
scope and the operations it declares not-yet-implemented.
"""

from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "apps" / "api" / "openapi.stage0.yaml"
ROUTERS = ROOT / "apps" / "api" / "routers"

#: Operations the contract names but the code does not implement yet. Listing them here is
#: the point: an unimplemented operation is a known gap, not a silent omission.
NOT_IMPLEMENTED = {
    ("GET", "/agent-registrations"),
    ("POST", "/agent-registrations"),
    ("GET", "/agent-registrations/{}"),
    ("GET", "/workflows"),
    ("GET", "/workflows/{}"),
    ("POST", "/workflows/{}/runs"),
    ("GET", "/workflow-runs"),
    ("GET", "/workflow-runs/{}"),
    ("POST", "/workflow-runs/{}/approvals"),
    ("POST", "/workflow-runs/{}/cancel"),
    ("GET", "/evaluations"),
    ("POST", "/evaluations"),
    ("GET", "/evaluations/{}"),
}


def normalise(path: str) -> str:
    path = re.sub(r"\{[^}]*\}", "{}", path)
    if path.startswith("/v1"):
        path = path[3:]
    return path.rstrip("/") or "/"


def contract_operations() -> set[tuple[str, str]]:
    spec = json.loads(CONTRACT.read_text(encoding="utf-8"))
    return {
        (method.upper(), normalise(path))
        for path, methods in spec["paths"].items()
        for method in methods
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }


def declared_out_of_scope() -> set[tuple[str, str]]:
    spec = json.loads(CONTRACT.read_text(encoding="utf-8"))
    return {
        (item["method"].upper(), normalise(item["path"]))
        for item in spec.get("x-out-of-scope", {}).get("endpoints", [])
    }


def implemented_routes() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for source in sorted(ROUTERS.glob("*.py")):
        text = source.read_text(encoding="utf-8")
        for match in re.finditer(r'@router\.(get|post|patch|put|delete)\(\s*"([^"]+)"', text):
            found.add((match.group(1).upper(), normalise(match.group(2))))
    return found


def main() -> int:
    contract = contract_operations()
    implemented = implemented_routes()
    out_of_scope = declared_out_of_scope()

    errors: list[str] = []

    # Implemented but undeclared: the drift that understates the system.
    undeclared = implemented - contract - out_of_scope
    for method, path in sorted(undeclared):
        errors.append(f"implemented but neither in the contract nor declared out of scope: {method} {path}")

    # Declared but missing from the code: the drift that overstates it.
    expected_missing = contract - implemented
    unexpected_missing = expected_missing - NOT_IMPLEMENTED
    for method, path in sorted(unexpected_missing):
        errors.append(f"in the contract but not implemented and not listed as a known gap: {method} {path}")

    # A listed gap that is now implemented should be removed from the list.
    resolved = NOT_IMPLEMENTED & implemented
    for method, path in sorted(resolved):
        errors.append(f"listed as not implemented but the route exists: {method} {path}")

    # An out-of-scope entry that also appears in paths would be double-counted.
    for method, path in sorted(out_of_scope & contract):
        errors.append(f"declared out of scope but also in paths: {method} {path}")

    print(f"contract operations: {len(contract)}")
    print(f"implemented routes:  {len(implemented)}")
    print(f"declared out of scope: {len(out_of_scope)}")
    print(f"known-not-implemented: {len(NOT_IMPLEMENTED)}")
    print()

    if errors:
        for error in errors:
            print(f"[FAIL] {error}")
        print()
        print("RESULT: FAIL")
        return 1

    print("[PASS] contract and implementation agree")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
