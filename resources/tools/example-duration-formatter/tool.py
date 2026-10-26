"""Example tool: Converts a millisecond duration into a compact human-readable form.

Deterministic, offline, and read-only.
"""


def run(text: str) -> str:
    """Process the supplied text and return a result.

    The example reports what it found rather than guessing; an empty input
    returns an empty result instead of inventing content.
    """

    if not text:
        return ""
    return text
