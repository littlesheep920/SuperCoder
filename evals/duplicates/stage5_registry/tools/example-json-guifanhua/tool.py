"""Example JSON normalizer: deterministic, no network, no filesystem writes."""

import json


def normalize(text: str, indent: int = 2) -> str:
    """Return JSON with sorted keys and stable indentation."""

    if indent < 0 or indent > 8:
        raise ValueError("indent must be between 0 and 8")
    return json.dumps(json.loads(text), sort_keys=True, indent=indent, ensure_ascii=False)
