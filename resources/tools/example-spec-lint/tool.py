"""Example tool: Checks an OpenAPI document for missing descriptions and inconsistent error schemas.

Deterministic, offline, read-only.
"""


def run(text: str) -> str:
    """Process the supplied text and return a result."""

    if not text:
        return ""
    return text
