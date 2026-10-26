"""End-to-end check of the discovery channel against a real MySQL container.

Requires ``docker compose up -d mysql``. Runs the API on a spare port, then asserts
that retrieval produces candidates and that the judge answers a fitting question
while refusing an unrelated one.

This exists because the discovery channel was developed while MySQL was
unavailable, so only its decision layer could be exercised. Running it is the
difference between "the model judges well" and "the endpoint works".

Usage:
    docker compose up -d mysql
    python scripts/check_discovery_e2e.py
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOKEN = "dev-owner-token"


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
            return error.code, {"raw": raw[:300]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--skip-refusal", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("SUPERCODER_LLM_API_KEY"):
        print("SUPERCODER_LLM_API_KEY is not set; discovery needs it", file=sys.stderr)
        return 2

    base = f"http://127.0.0.1:{args.port}/v1"
    env = dict(os.environ)
    env.update(
        {
            "SUPERCODER_MYSQL_ENABLED": "true",
            "SUPERCODER_MYSQL_HOST": os.environ.get("SUPERCODER_MYSQL_HOST", "127.0.0.1"),
            "SUPERCODER_MYSQL_PORT": os.environ.get("SUPERCODER_MYSQL_PORT", "3306"),
            "SUPERCODER_MYSQL_DATABASE": os.environ.get("SUPERCODER_MYSQL_DATABASE", "supercoder"),
            "SUPERCODER_MYSQL_USER": os.environ.get("SUPERCODER_MYSQL_USER", "supercoder_local"),
            "SUPERCODER_MYSQL_PASSWORD": os.environ.get("SUPERCODER_MYSQL_PASSWORD", "supercoder"),
            "SUPERCODER_GIT_REPOSITORY_ROOT": str(ROOT),
            "SUPERCODER_SEARCH_REMOTE_ENABLED": "true",
            "SUPERCODER_EMBEDDING_ENABLED": "true",
            "SUPERCODER_RERANK_ENABLED": "true",
            "SUPERCODER_EXTERNAL_LLM_ENABLED": "true",
            "PYTHONPATH": str(ROOT),
        }
    )

    log_path = ROOT / f"discovery-e2e-{args.port}.log"
    log = open(log_path, "w", encoding="utf-8")
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "apps.api.main:app", "--port", str(args.port)],
        cwd=str(ROOT),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )

    try:
        if not wait_for(f"http://127.0.0.1:{args.port}/v1/health"):
            print(f"API did not start; see {log_path.name}", file=sys.stderr)
            return 1

        status, search = call(base, "/search/resources", {"query": "code review", "filters": {}, "limit": 3})
        hits = len(search.get("items") or []) if isinstance(search, dict) else 0
        print(f"search: status={status} hits={hits}")
        if status != 200 or hits == 0:
            print("  body:", json.dumps(search, ensure_ascii=False)[:300], file=sys.stderr)
            return 1

        status, body = call(base, "/discovery/recommend", {"question": "帮我找一个能审查代码改动的能力，要给出每个问题的证据"})
        answered = body.get("answered")
        recs = body.get("recommendations") or []
        shapes_ok = all("/" not in str(item.get("resource_id")) for item in recs)
        print(f"answerable: status={status} answered={answered} recs={len(recs)}")
        for item in recs[:3]:
            print(f"  -> {item['resource_id']} v{item['version']}")
        print(f"  ids are public ids (no slug): {shapes_ok}")

        refused = True
        if not args.skip_refusal:
            _, refusal = call(base, "/discovery/recommend", {"question": "帮我订一张明天去上海的机票"})
            refused = refusal.get("answered") is False
            print(f"unrelated: answered={refusal.get('answered')} reason={(refusal.get('refusal_reason') or '')[:90]}")

        checks = {
            "retrieval produced candidates": hits > 0,
            "answerable question answered": answered is True,
            "ids are resource public ids": shapes_ok,
            "unrelated question refused": refused,
        }
        print()
        for label, passed in checks.items():
            print(f"  [{'ok' if passed else 'FAIL'}] {label}")
        return 0 if all(checks.values()) else 1
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


if __name__ == "__main__":
    raise SystemExit(main())
