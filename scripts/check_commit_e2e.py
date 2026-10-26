"""Verify the registration commit path end to end against real MySQL and Git.

The commit endpoint writes to the managed Git checkout, so this is the one script
that mutates repository state. It is built to be safe:

* refuses to run unless HEAD is on a branch whose name starts with ``verify-``;
* refuses to run on a dirty working tree, so the diff afterwards is only its own;
* uses a unique slug per run, because probe rows cannot be deleted while an audit
  record references them (ON DELETE RESTRICT, by design);
* reads the resource back *before* removing the fixture directory;
* prints the exact rollback command and the cleanup statements it did not run.

Usage:
    git checkout -b verify-commit-path
    docker compose up -d mysql
    python scripts/check_commit_e2e.py
    git checkout main && git branch -D verify-commit-path
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOKEN = "dev-owner-token"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(ROOT), capture_output=True, text=True, check=False
    ).stdout.strip()


def wait_for(url: str, timeout: float = 120.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status < 500:
                    return True
        except Exception:
            time.sleep(1.5)
    return False


def call(base: str, path: str, body: dict | None = None, method: str = "POST") -> tuple[int, dict]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base + path,
        data=data,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, {"raw": raw[:400]}


def manifest_for(slug: str) -> dict:
    return {
        "schema_version": "1",
        "kind": "tool",
        "slug": slug,
        "name": f"Commit Path Probe {slug[-6:]}",
        "version": "1.0.0",
        "description": (
            "Example resource for the SuperCoder protocol. Created by the commit-path "
            "verification script; not a real capability."
        ),
        "aliases": [],
        "tags": ["example", "tool", "probe"],
        "categories": ["software-development/testing"],
        "runtimes": ["claude-code"],
        "inputs": [{"name": "text", "type": "text", "required": True, "description": "Input."}],
        "outputs": [{"name": "result", "type": "text", "required": True, "description": "Output."}],
        "permissions": [],
        "dependencies": [],
        "entrypoint": "tool.py",
        "status": "draft",
        "owner_team": "qa-automation",
        "tool": {
            "execution_mode": "python",
            "parameters_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            "returns_schema": {
                "type": "object",
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
            },
            "deterministic": True,
            "timeout_seconds": 5,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8005)
    args = parser.parse_args()

    branch = git("branch", "--show-current")
    if not branch.startswith("verify-"):
        print(
            f"refusing to run on branch {branch!r}.\n"
            "This script writes into the checkout; create a scratch branch first:\n"
            "  git checkout -b verify-commit-path",
            file=sys.stderr,
        )
        return 2
    if git("status", "--porcelain"):
        print("working tree is dirty; commit or stash first", file=sys.stderr)
        return 2

    slug = f"example-commit-probe-{int(time.time()) % 100000}"
    manifest = manifest_for(slug)
    target_dir = ROOT / "resources" / "tools" / slug
    before_sha = git("rev-parse", "HEAD")
    base = f"http://127.0.0.1:{args.port}/v1"
    print(f"branch={branch} slug={slug}")

    env = dict(os.environ)
    env.update(
        {
            "SUPERCODER_MYSQL_ENABLED": "true",
            "SUPERCODER_MYSQL_HOST": os.environ.get("SUPERCODER_MYSQL_HOST", "127.0.0.1"),
            "SUPERCODER_MYSQL_DATABASE": os.environ.get("SUPERCODER_MYSQL_DATABASE", "supercoder"),
            "SUPERCODER_MYSQL_USER": os.environ.get("SUPERCODER_MYSQL_USER", "supercoder_local"),
            "SUPERCODER_MYSQL_PASSWORD": os.environ.get("SUPERCODER_MYSQL_PASSWORD", "supercoder"),
            "SUPERCODER_GIT_REPOSITORY_ROOT": str(ROOT),
            # The point of the exercise: allow the write.
            "SUPERCODER_GIT_READ_ONLY": "false",
            "PYTHONPATH": str(ROOT),
        }
    )

    log_path = ROOT / "commit-e2e.log"
    log = open(log_path, "w", encoding="utf-8")
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "apps.api.main:app", "--port", str(args.port)],
        cwd=str(ROOT),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )

    checks: dict[str, bool] = {}
    resource_id: str | None = None
    try:
        if not wait_for(f"http://127.0.0.1:{args.port}/v1/health"):
            print(f"API did not start; see {log_path.name}", file=sys.stderr)
            return 1

        _, health = call(base, "/health", method="GET")
        read_only = ((health.get("components") or {}).get("git") or {}).get("read_only")
        print(f"git read_only: {read_only}")

        status, preview = call(base, "/registration/preview", {"manifest": manifest})
        print(f"preview: status={status} valid={preview.get('validation_valid')}")
        checks["preview reports a new resource"] = preview.get("base_present") is False

        status, result = call(
            base,
            "/registration/commit",
            {"manifest": manifest, "reason": "commit-path verification", "confirmed": True},
        )
        print(f"commit: status={status}")
        resource_id = result.get("resource_id")
        checks["commit returned 201"] = status == 201
        checks["artifact hash returned"] = bool(result.get("artifact_hash"))
        checks["created status is draft"] = result.get("created_status") == "draft"

        checks["manifest and body written to Git"] = (
            (target_dir / "resource.yaml").is_file() and (target_dir / "tool.py").is_file()
        )
        stored_status = None
        if (target_dir / "resource.yaml").is_file():
            stored_status = json.loads(
                (target_dir / "resource.yaml").read_text(encoding="utf-8")
            ).get("status")
        checks["written status is draft"] = stored_status == "draft"

        # Read back before removing the fixture, so the projection can be rebuilt.
        detail_status = None
        detail: dict = {}
        if resource_id:
            detail_status, detail = call(base, f"/resources/{resource_id}", method="GET")
        version = (detail.get("selected_version") or {}) if isinstance(detail, dict) else {}
        checks["resource readable via API"] = detail_status == 200
        checks["version is a draft"] = version.get("status") == "draft"

        if status != 201:
            log.flush()
            print("\n--- server log tail ---")
            print(log_path.read_text(encoding="utf-8", errors="replace")[-1200:])
    finally:
        api.terminate()
        try:
            api.wait(timeout=15)
        except subprocess.TimeoutExpired:
            api.kill()
        log.close()
        try:
            log_path.unlink()
        except OSError:
            pass
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
            print(f"removed {target_dir.relative_to(ROOT)}")

    print()
    for label, passed in checks.items():
        print(f"  [{'ok' if passed else 'FAIL'}] {label}")

    if resource_id:
        print(
            "\nThe database row is intentionally left in place: audit_log references the\n"
            "version with ON DELETE RESTRICT, so removing it would orphan governance\n"
            "history. To remove the fixture, delete its audit rows first:\n"
            f"  DELETE FROM audit_log WHERE resource_version_id IN (\n"
            f"    SELECT id FROM resource_version WHERE resource_id = (\n"
            f"      SELECT id FROM resource WHERE public_id = '{resource_id}'));"
        )
    print(f"\nrollback: git reset --hard {before_sha[:12]} && git clean -fd resources/")
    return 0 if checks and all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
