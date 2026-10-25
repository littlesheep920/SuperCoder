"""Example log redactor: removes credential-shaped values."""

import re

_PATTERNS = [
    re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(password\s*[=:]\s*)\S+"),
]


def redact(text: str) -> str:
    """Replace credential-shaped values with a placeholder.

    This is a best-effort filter. It reduces accidental exposure; it does not
    make arbitrary logs safe to publish, and it must not be the only control.
    """

    out = text
    for pattern in _PATTERNS:
        out = pattern.sub(lambda m: m.group(1) + "<redacted>", out)
    return out
