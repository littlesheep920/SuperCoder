"""Example semantic version comparison: deterministic, offline."""

import re

_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


def compare(left: str, right: str) -> str:
    """Return 'older', 'same', or 'newer' for left relative to right."""

    def parse(version: str) -> tuple[int, int, int]:
        match = _PATTERN.match(version)
        if not match:
            raise ValueError(f"not a semantic version: {version}")
        return int(match.group(1)), int(match.group(2)), int(match.group(3))

    a, b = parse(left), parse(right)
    if a < b:
        return "older"
    if a > b:
        return "newer"
    return "same"
