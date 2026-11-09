"""A text-mode subprocess call must say which encoding it decodes.

`subprocess.run(..., text=True)` without `encoding=` decodes the child's output with the
locale's codec. On a Chinese Windows console that is cp936, while a Python child that inherited
`PYTHONIOENCODING=utf-8` writes UTF-8: `test_stage2_eval` errored six times with
`UnicodeDecodeError: 'gbk' codec can't decode byte 0x94` until both sides were pinned to UTF-8.
The failure only shows on such a machine, so this test looks at the code instead: every call in
the repository that asks for text must also name its encoding.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CALLS = {"run", "Popen", "check_output", "call", "check_call"}
TEXT_FLAGS = {"text", "universal_newlines"}


def offending_calls(source: str, filename: str = "<source>") -> list[str]:
    """`file:line` for each text-mode subprocess call that does not pass `encoding=`."""

    tree = ast.parse(source, filename=filename)
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            aliases |= {alias.asname or alias.name for alias in node.names if alias.name in CALLS}

    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_subprocess = (
            isinstance(func, ast.Attribute)
            and func.attr in CALLS
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ) or (isinstance(func, ast.Name) and func.id in aliases)
        if not is_subprocess:
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
        asks_for_text = any(
            isinstance(keywords.get(flag), ast.Constant) and keywords[flag].value is True for flag in TEXT_FLAGS
        )
        if asks_for_text and "encoding" not in keywords:
            found.append(f"{filename}:{node.lineno}")
    return found


def tracked_python_files() -> list[pathlib.Path]:
    listed = subprocess.run(["git", "ls-files", "-z", "--", "*.py"], cwd=str(ROOT), capture_output=True)
    names = [item for item in (listed.stdout or b"").decode("utf-8", errors="replace").split("\0") if item]
    if listed.returncode != 0 or not names:
        return sorted(path for folder in ("apps", "scripts", "tests") for path in (ROOT / folder).rglob("*.py"))
    return [ROOT / name for name in names if (ROOT / name).is_file()]


class RepositoryTests(unittest.TestCase):
    def test_every_text_mode_subprocess_call_names_its_encoding(self) -> None:
        offenders: list[str] = []
        for path in tracked_python_files():
            offenders += offending_calls(path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix())
        self.assertEqual(offenders, [], "pass encoding='utf-8' (and set PYTHONIOENCODING for Python children)")

    def test_the_scan_covers_the_files_that_had_the_problem(self) -> None:
        scanned = {path.relative_to(ROOT).as_posix() for path in tracked_python_files()}
        for name in ("tests/test_stage2_eval.py", "tests/test_stage1_startup.py", "scripts/check_commit_e2e.py"):
            with self.subTest(file=name):
                self.assertIn(name, scanned)


class DetectorTests(unittest.TestCase):
    """The check must have teeth: it flags the old code and accepts the fixed code."""

    def test_the_old_call_is_flagged(self) -> None:
        old = "import subprocess\nsubprocess.run(['x'], capture_output=True, text=True, timeout=5)\n"
        self.assertEqual(offending_calls(old), ["<source>:2"])

    def test_universal_newlines_and_imported_names_are_flagged_too(self) -> None:
        source = "from subprocess import check_output as co\nco(['x'], universal_newlines=True)\n"
        self.assertEqual(offending_calls(source), ["<source>:2"])

    def test_a_call_with_an_encoding_or_in_binary_mode_passes(self) -> None:
        source = (
            "import subprocess\n"
            "subprocess.run(['x'], text=True, encoding='utf-8')\n"
            "subprocess.run(['x'], capture_output=True)\n"
            "subprocess.Popen(['x'], stdout=subprocess.PIPE)\n"
        )
        self.assertEqual(offending_calls(source), [])


if __name__ == "__main__":
    unittest.main()
