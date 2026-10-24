#!/usr/bin/env python3
"""Deterministic, local-only text statistics demo tool."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from typing import NoReturn

MAX_TEXT_CHARACTERS = 1_000_000
MAX_JSON_CHARACTERS = MAX_TEXT_CHARACTERS + 1_024


class InputError(ValueError):
    """Raised when an invocation violates the public input contract."""


def calculate_text_stats(request: Mapping[str, object]) -> dict[str, int]:
    """Validate *request* and return deterministic statistics for its text."""
    unexpected = sorted(set(request) - {"text"})
    if unexpected:
        raise InputError(f"unknown properties: {', '.join(unexpected)}")
    if "text" not in request:
        raise InputError("missing required property: text")

    text = request["text"]
    if not isinstance(text, str):
        raise InputError("property 'text' must be a string")
    if len(text) > MAX_TEXT_CHARACTERS:
        raise InputError(
            f"property 'text' must contain at most {MAX_TEXT_CHARACTERS} characters"
        )

    try:
        utf8_bytes = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InputError(
            "property 'text' must contain only Unicode scalar values"
        ) from exc

    return {
        "character_count": len(text),
        "non_whitespace_character_count": sum(
            1 for character in text if not character.isspace()
        ),
        "word_count": len(text.split()),
        "line_count": len(text.splitlines()),
        "utf8_byte_count": len(utf8_bytes),
    }


def _parse_request(raw: str) -> Mapping[str, object]:
    if len(raw) > MAX_JSON_CHARACTERS:
        raise InputError("JSON input exceeds the supported size")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InputError(
            f"invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(value, dict):
        raise InputError("input must be a JSON object")
    return value


def _emit_error(message: str) -> NoReturn:
    error = {
        "error": {
            "code": "INVALID_ARGUMENT",
            "message": message,
            "retryable": False,
        }
    }
    # ASCII-only serialization keeps structured errors writable even when an
    # invalid property name itself contains an unpaired surrogate.
    print(json.dumps(error, ensure_ascii=True, sort_keys=True), file=sys.stderr)
    raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute deterministic statistics for text in a JSON object."
    )
    parser.add_argument(
        "--input-json",
        help="JSON request object; if omitted, one object is read from standard input.",
    )
    args = parser.parse_args(argv)

    raw = (
        args.input_json
        if args.input_json is not None
        else sys.stdin.read(MAX_JSON_CHARACTERS + 1)
    )
    try:
        request = _parse_request(raw)
        result = calculate_text_stats(request)
    except InputError as exc:
        _emit_error(str(exc))

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
