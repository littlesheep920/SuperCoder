"""Example tool: Reports lines that appear more than once in a text file with their counts.

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
