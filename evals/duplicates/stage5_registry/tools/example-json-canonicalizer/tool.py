"""Canonical JSON: sorted keys, fixed indent. Offline and side-effect free."""

import json


def canonical(document: str, indent: int = 2) -> str:
    """Parse the document and print it back with sorted keys and a fixed indent."""

    value = json.loads(document)
    return json.dumps(value, sort_keys=True, indent=indent, ensure_ascii=False) + "\n"
