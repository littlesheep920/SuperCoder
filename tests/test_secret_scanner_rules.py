"""The secret scanner must flag literals without flagging expressions.

A CI workflow legitimately does ``token = sys.argv[1]``: the value arrives from
the runner and is never written into the repository. An earlier revision flagged
that as a plaintext secret, which blocks a correct commit and — worse — pressures
people to weaken the check.

These tests pin both directions: real literals must still be caught.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("stage0_validator", ROOT / "scripts" / "validate_stage0.py")
_validator = importlib.util.module_from_spec(_spec)
sys.modules["stage0_validator"] = _validator
_spec.loader.exec_module(_validator)


def _flagged(text: str) -> bool:
    """Replicate the scanner's decision for one line of text."""

    match = _validator.SECRET_ASSIGNMENT_RE.search(text)
    if match is None:
        return False
    value = (match.group("quoted") or match.group("bare") or "").strip()
    if match.group("quote") is None and _validator._looks_like_code_expression(value):
        return False
    if _validator._is_placeholder(value):
        return False
    return True


class ExpressionTests(unittest.TestCase):
    """Values read from elsewhere are not credentials in the file."""

    def test_argv_index_is_not_a_secret(self) -> None:
        self.assertFalse(_flagged("token = sys.argv[1]"))

    def test_environment_lookup_is_not_a_secret(self) -> None:
        self.assertFalse(_flagged('api_key = os.environ["API_KEY"]'))

    def test_function_call_is_not_a_secret(self) -> None:
        self.assertFalse(_flagged("password = read_secret()"))

    def test_shell_variable_is_not_a_secret(self) -> None:
        self.assertFalse(_flagged("API_TOKEN=$TOKEN"))

    def test_attribute_chain_is_not_a_secret(self) -> None:
        self.assertFalse(_flagged("token = settings.token"))


class LiteralTests(unittest.TestCase):
    """A real hard-coded value must still be reported."""

    def test_quoted_key_is_flagged(self) -> None:
        self.assertTrue(_flagged('api_key = "sk-abcdefghijklmnop"'))

    def test_bare_env_password_is_flagged(self) -> None:
        self.assertTrue(_flagged("MYSQL_PASSWORD=supersecret123"))

    def test_quoted_token_is_flagged(self) -> None:
        self.assertTrue(_flagged('token = "realvalue123"'))

    def test_yaml_client_secret_is_flagged(self) -> None:
        self.assertTrue(_flagged("client_secret: hardcodedvalue"))

    def test_high_confidence_pattern_wins_over_expression_rule(self) -> None:
        """An AWS-key-shaped literal is reported regardless of context."""

        text = 'aws_key = "AKIAIOSFODNN7EXAMPLE"'
        matched = any(
            pattern.search(text) for _label, pattern in _validator.HIGH_CONFIDENCE_SECRET_PATTERNS
        )
        self.assertTrue(matched)


if __name__ == "__main__":
    unittest.main()
