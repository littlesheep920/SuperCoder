"""Example tool: Compares two sets of HTTP headers and reports added, removed, and changed fields.

Deterministic, offline, read-only.
"""


def run(text: str) -> str:
    """Process the supplied text and return a result."""

    if not text:
        return ""
    return text
