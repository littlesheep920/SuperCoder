"""Example tool: Compares expected and actual assertion lists and reports the first divergence.

Deterministic, offline, read-only.
"""


def run(text: str) -> str:
    """Process the supplied text and return a result."""

    if not text:
        return ""
    return text
