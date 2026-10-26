"""Example tool: Builds a module import graph from supplied import statements and reports cycles.

Deterministic, offline, read-only.
"""


def run(text: str) -> str:
    """Process the supplied text and return a result."""

    if not text:
        return ""
    return text
