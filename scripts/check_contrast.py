"""CI gate: the colour tokens must meet WCAG AA contrast.

`theme.css` claims every text/surface pairing clears 4.5:1. A claim in a comment is not a
guarantee — the next palette change would silently break it, and nothing renders differently
in a test to reveal that. This computes the ratios from the token values themselves.

Ratio formula and thresholds are WCAG 2.1: 4.5:1 for body text, 3:1 for large text and for
UI boundaries that must remain perceivable. Contrast is also the reason the palette does not
use colour alone for state — that is checked by the badges carrying text labels, not here.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
THEME = ROOT / "apps" / "web" / "src" / "styles" / "theme.css"

TOKEN = re.compile(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})")


def load_tokens() -> dict[str, str]:
    text = THEME.read_text(encoding="utf-8")
    return {name: value.lower() for name, value in TOKEN.findall(text)}


def relative_luminance(hex_colour: str) -> float:
    channels = []
    for offset in (1, 3, 5):
        value = int(hex_colour[offset : offset + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(foreground: str, background: str) -> float:
    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


#: (foreground token, background token, minimum ratio, what it is used for)
PAIRS = [
    ("text-1", "surface-0", 4.5, "body text on the page"),
    ("text-1", "surface-1", 4.5, "body text in a card"),
    ("text-1", "surface-2", 4.5, "text in inputs and table headers"),
    ("text-2", "surface-1", 4.5, "labels and secondary text"),
    ("text-2", "surface-2", 4.5, "labels inside inputs"),
    ("text-3", "surface-1", 3.0, "ambient metadata (large or non-essential)"),
    ("text-3", "surface-2", 3.0, "ambient metadata in table headers"),
    ("accent-text", "surface-1", 4.5, "accent text on a card"),
    ("accent-text", "accent-soft", 4.5, "active navigation label"),
    ("ok", "ok-soft", 4.5, "published badge"),
    ("warn", "warn-soft", 4.5, "pending_review and deprecated badges"),
    ("danger", "danger-soft", 4.5, "error text and danger button"),
    ("info", "info-soft", 4.5, "validating badge and links"),
    ("text-2", "surface-3", 4.5, "draft badge on its own tint"),
    ("#04110f", "accent", 4.5, "primary button label"),
    ("focus", "surface-1", 3.0, "focus ring against a card"),
]


def main() -> int:
    tokens = load_tokens()
    missing = sorted(
        {
            name
            for foreground, background, _, _ in PAIRS
            for name in (foreground, background)
            if not name.startswith("#") and name not in tokens
        }
    )
    if missing:
        print(f"[FAIL] tokens referenced but not defined: {missing}")
        return 1

    failures = 0
    print(f"{'pair':52} {'ratio':>6}  {'min':>4}  result")
    for foreground, background, minimum, purpose in PAIRS:
        fg = foreground if foreground.startswith("#") else tokens[foreground]
        bg = background if background.startswith("#") else tokens[background]
        ratio = contrast(fg, bg)
        ok = ratio >= minimum
        if not ok:
            failures += 1
        print(f"{purpose:52} {ratio:6.2f}  {minimum:4.1f}  {'ok' if ok else 'FAIL'}")

    print()
    if failures:
        print(f"[FAIL] {failures} pairing(s) below the required contrast")
        print("RESULT: FAIL")
        return 1

    print(f"[PASS] all {len(PAIRS)} pairings meet WCAG AA")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
