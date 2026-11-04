"""The error classes must accept a message in the first position.

Found by an end-to-end check: `ConflictError("...")` stored its text in `code`, and the response
mapper then failed reading `code.value`, so a 409 became a 500. Existing call sites use both the
positional and the keyword form, so the classes are tolerant rather than the call sites rewritten.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.domain.errors import (  # noqa: E402
    ConflictError,
    ErrorCode,
    NotFoundError,
    PreconditionFailedError,
    ValidationError,
)


class PositionalMessageTests(unittest.TestCase):
    CASES = (
        (ConflictError, ErrorCode.INVALID_STATE_TRANSITION),
        (ValidationError, ErrorCode.SCHEMA_VALIDATION_FAILED),
        (PreconditionFailedError, ErrorCode.ROW_VERSION_MISMATCH),
        (NotFoundError, ErrorCode.NOT_FOUND),
    )

    def test_a_positional_string_becomes_the_message(self) -> None:
        for error_class, expected_code in self.CASES:
            with self.subTest(error_class=error_class.__name__):
                error = error_class("something specific")
                self.assertEqual(error.code, expected_code)
                self.assertEqual(error.message, "something specific")

    def test_the_code_is_always_an_error_code(self) -> None:
        """The response mapper reads `code.value`; a str there raises and turns into a 500."""

        for error_class, _ in self.CASES:
            with self.subTest(error_class=error_class.__name__):
                error = error_class("x")
                self.assertIsInstance(error.code, ErrorCode)
                self.assertIsInstance(error.code.value, str)

    def test_the_keyword_form_is_unchanged(self) -> None:
        error = ValidationError(code=ErrorCode.PROVENANCE_INVALID, message="kind mismatch")
        self.assertEqual(error.code, ErrorCode.PROVENANCE_INVALID)
        self.assertEqual(error.message, "kind mismatch")

    def test_the_status_code_is_preserved(self) -> None:
        self.assertEqual(ConflictError("x").status_code, 409)
        self.assertEqual(ValidationError("x").status_code, 422)
        self.assertEqual(PreconditionFailedError("x").status_code, 412)
        self.assertEqual(NotFoundError("x").status_code, 404)

    def test_extra_detail_is_still_carried(self) -> None:
        error = ValidationError("workflow cannot be resolved", detail="skill/review: not found")
        self.assertEqual(error.detail, "skill/review: not found")


if __name__ == "__main__":
    unittest.main()
