"""Example markdown TOC builder: deterministic, offline."""

import re

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def build(text: str) -> str:
    """Return a bullet list of headings, indented by heading level."""

    lines = []
    for raw in text.splitlines():
        match = _HEADING.match(raw)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2)
        anchor = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", title.lower()).strip("-")
        lines.append(f"{'  ' * (level - 1)}- [{title}](#{anchor})")
    return "\n".join(lines)
