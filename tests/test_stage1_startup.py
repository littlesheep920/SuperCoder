"""Regression tests for the local stack startup contract.

These tests guard the bugs found during review of the Stage 1 delivery:

* the compose stack only mounted the Stage 0 migration, so the six tables that
  ``0002_stage1_extensions.sql`` creates were never created and every request
  that touched identity/permission projections failed on a fresh stack;
* the API image did not ship ``scripts/``, so the one-shot seed gate could not
  run at all;
* ``scripts/seed_demo_resources.py`` could not be executed as a script because
  the repository root was missing from ``sys.path``;
* a failed seed still exited 0, so a compose
  ``service_completed_successfully`` dependency would start the worker against
  an empty governance database.

They are static/structural assertions plus subprocess checks; they do not
require Docker or MySQL, so they run in every environment.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"


class ComposeStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = COMPOSE.read_text(encoding="utf-8")
        cls.document = yaml.safe_load(cls.text)
        cls.services = cls.document["services"]

    def test_both_migrations_are_mounted_in_order(self) -> None:
        """A fresh MySQL must apply 0001 and 0002 before any dependent starts."""

        volumes = [
            str(volume)
            for volume in self.services["mysql"].get("volumes", [])
        ]
        mounted = [volume for volume in volumes if "docker-entrypoint-initdb.d" in volume]
        sources = [Path(volume.split(":")[0]).name for volume in mounted]

        self.assertIn("0001_stage0_contract.sql", sources)
        self.assertIn("0002_stage1_extensions.sql", sources)
        # docker-entrypoint-initdb.d runs files in lexicographic order.
        self.assertEqual(sources, sorted(sources))

    def test_every_tables_needed_by_the_orm_are_created_by_a_mounted_migration(self) -> None:
        """Every mapped table name must appear in a migration the stack applies."""

        mounted = {
            Path(volume.split(":")[0]).name
            for volume in map(str, self.services["mysql"].get("volumes", []))
            if "docker-entrypoint-initdb.d" in volume
        }
        migration_sql = "\n".join(
            (ROOT / "infra" / "migrations" / name).read_text(encoding="utf-8")
            for name in mounted
            if (ROOT / "infra" / "migrations" / name).is_file()
        )
        created = {
            match.group(1).lower()
            for match in re.finditer(
                r"(?i)\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?([a-z][a-z0-9_]*)`?",
                migration_sql,
            )
        }
        models = (ROOT / "apps/api/db").glob("models_*.py")
        mapped: set[str] = set()
        for module in models:
            mapped.update(
                re.findall(
                    r'__tablename__\s*=\s*["\']([a-z][a-z0-9_]*)["\']',
                    module.read_text(encoding="utf-8"),
                )
            )
        self.assertTrue(mapped, "no ORM table names were discovered")

        missing = sorted(mapped - created)
        self.assertEqual(missing, [], f"ORM tables not created by any mounted migration: {missing}")

    def test_seed_gate_runs_before_the_worker(self) -> None:
        worker = self.services["worker"]
        depends = worker["depends_on"]
        self.assertEqual(depends["seed"]["condition"], "service_completed_successfully")

    def test_seed_service_invokes_the_script_by_path(self) -> None:
        """``scripts/`` is not a package, so ``-m scripts.seed_demo_resources`` fails."""

        command = self.services["seed"]["command"]
        self.assertIn("scripts/seed_demo_resources.py", command)
        self.assertNotIn("-m", command)

    def test_api_image_ships_the_seed_script(self) -> None:
        dockerfile = (ROOT / "apps/api/Dockerfile").read_text(encoding="utf-8")
        self.assertRegex(dockerfile, r"(?m)^COPY scripts")
        self.assertTrue((ROOT / "scripts/seed_demo_resources.py").is_file())


class SeedScriptTests(unittest.TestCase):
    def _run(self, **env_overrides: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        for key in (
            "SUPERCODER_MYSQL_ENABLED",
            "SUPERCODER_MYSQL_PASSWORD",
            "SUPERCODER_MYSQL_HOST",
        ):
            env.pop(key, None)
        env.update(env_overrides)
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts/seed_demo_resources.py")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

    def test_script_is_executable_by_path_without_pythonpath(self) -> None:
        """Regression: ``ModuleNotFoundError: No module named 'apps'``."""

        result = self._run()
        self.assertNotIn("ModuleNotFoundError", result.stderr)
        self.assertIn("SUPERCODER_MYSQL_ENABLED must be true", result.stderr)
        self.assertEqual(result.returncode, 2)

    def test_missing_password_reports_the_env_var_name(self) -> None:
        """A fail-closed secret must name what to set, not just 'secret'."""

        result = self._run(SUPERCODER_MYSQL_ENABLED="true")
        self.assertIn("SUPERCODER_MYSQL_PASSWORD", result.stderr)


class RepositoryHygieneTests(unittest.TestCase):
    """Guard the rules in docs/GIT_WORKFLOW.md that protect reproducibility."""

    def test_gitignore_covers_sensitive_and_generated_paths(self) -> None:
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for required in (
            "scripts/dev_tokens.json",
            "node_modules/",
            "apps/web/dist/",
            "__pycache__/",
            ".env",
        ):
            self.assertIn(required, text, f".gitignore must cover {required}")

    def test_dev_token_template_is_committed_and_not_ignored(self) -> None:
        """A clone must be able to create the git-ignored registry from a template."""

        template = ROOT / "scripts/dev_tokens.example.json"
        self.assertTrue(template.is_file(), "dev_tokens.example.json must be committed")

    def test_containers_do_not_run_as_root(self) -> None:
        for relative in ("apps/api/Dockerfile", "apps/worker/Dockerfile", "apps/web/Dockerfile"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertRegex(text, r"(?m)^USER (?!root)", f"{relative} must drop root")

    def test_web_image_ships_nginx_config_with_spa_fallback(self) -> None:
        """createWebHistory() needs try_files.../index.html or deep links 404."""

        conf = ROOT / "apps/web/nginx.conf"
        self.assertTrue(conf.is_file())
        text = conf.read_text(encoding="utf-8")
        self.assertIn("try_files $uri $uri/ /index.html", text)
        self.assertIn("location /v1/", text)

    def test_runtime_and_test_requirement_files_are_pinned(self) -> None:
        for name in ("requirements-stage1.lock.txt", "requirements-test.txt"):
            text = (ROOT / name).read_text(encoding="utf-8")
            unpinned = [
                line
                for line in text.splitlines()
                if line.strip()
                and not line.strip().startswith("#")
                and not line.strip().startswith("-r ")
                and "==" not in line
            ]
            self.assertEqual(unpinned, [], f"{name} must pin every dependency")


if __name__ == "__main__":
    unittest.main()
