"""Full platform walkthrough: create, edit, validate, review, publish, audit.

Every earlier stage was verified on its own path. This runs the whole sequence the way
a team would, switching identity where the server requires it, and asserts the state
at each step. It is the acceptance test for the finished front-to-back loop.

Requires the compose stack with MySQL enabled and a writable checkout.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = "http://localhost:8000/v1"
TOKENS = {
    "owner": "dev-owner-token",
    "validator": "dev-validator-token",
    "reviewer": "dev-reviewer-token",
    "publisher": "dev-publisher-token",
    "member": "dev-member-token",
}


def call(path, *, acting_as="owner", method="GET", body=None, if_match=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": f"Bearer {TOKENS[acting_as]}",
               "Content-Type": "application/json"}
    if if_match:
        headers["If-Match"] = if_match
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            return error.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return error.code, {"raw": raw[:200]}


def wait_health(timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/health", timeout=5) as response:
                return json.load(response)
        except Exception:
            time.sleep(2)
    raise SystemExit("API not healthy")


def materialise(slug: str, manifest: dict, body: str) -> list[dict]:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "api", "sh", "-c",
         f"rm -rf /app/resources/skills/{slug} && mkdir -p /app/resources/skills/{slug}"],
        cwd=str(ROOT), capture_output=True,
    )
    import base64

    for name, content in (("resource.yaml", json.dumps(manifest, indent=2) + "\n"), ("SKILL.md", body)):
        encoded = base64.b64encode(content.encode()).decode()
        subprocess.run(
            ["docker", "compose", "exec", "-T", "api", "sh", "-c",
             f"echo {encoded} | base64 -d > /app/resources/skills/{slug}/{name}"],
            cwd=str(ROOT), capture_output=True,
        )
    return [{
        "path": "SKILL.md",
        "digest": "sha256:" + hashlib.sha256(body.encode()).hexdigest(),
        "size_bytes": len(body.encode()),
        "media_type": "text/markdown",
    }]


def wait_job(job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    final = {}
    while time.time() < deadline:
        _, final = call(f"/jobs/{job_id}")
        if final.get("status") in ("succeeded", "failed", "dead_letter", "cancelled"):
            return final
        time.sleep(2)
    return final


def main() -> int:
    health = wait_health()
    print(f"mysql={health['components']['mysql']['enabled']} git_read_only={health['components']['git']['read_only']}")

    slug = f"example-acceptance-{int(time.time()) % 100000}"
    manifest = {
        "schema_version": "1",
        "kind": "skill",
        "slug": slug,
        "name": "Acceptance Walkthrough",
        "version": "1.0.0",
        "description": "Example resource for the SuperCoder protocol. Acceptance walkthrough fixture.",
        "aliases": [],
        "tags": ["example", "probe"],
        "categories": ["software-development/testing"],
        "runtimes": ["claude-code"],
        "inputs": [{"name": "text", "type": "text", "required": True, "description": "Input."}],
        "outputs": [{"name": "out", "type": "text", "required": True, "description": "Output."}],
        "permissions": [],
        "dependencies": [],
        "entrypoint": "SKILL.md",
        "status": "draft",
        "owner_team": "platform-ai",
    }
    body_v1 = "# Acceptance Walkthrough\n\nExample resource for the SuperCoder protocol.\n"

    checks: dict[str, bool] = {}

    # 1. create
    artifacts = materialise(slug, manifest, body_v1)
    status, created = call("/resources", method="POST",
                           body={"manifest": manifest, "artifacts": artifacts, "reason": "acceptance"})
    checks["1. owner creates a resource"] = status == 201
    if status != 201:
        print(json.dumps(created, ensure_ascii=False)[:400])
        return 1
    resource_id = created["resource_id"]
    version = created["version"]
    print(f"created {slug} ({resource_id}) version {version['version']}")

    # 2. organization members may read it; a non-member may not. Visibility is
    # derived server-side: `organization_member` grants read access to every
    # resource, and team membership widens what else is visible. Asserting "another
    # team cannot read it" tests the wrong rule — a member of the organization can,
    # by design (PRD §3.3).
    status_member, member_view = call(f"/resource-versions/{version['version_id']}", acting_as="member")
    checks["2. an organization member can read the version"] = status_member == 200

    # 3. edit the body (creates version 1.0.1)
    status, current = call(f"/resource-versions/{version['version_id']}/body")
    body_v2 = (current.get("body") or body_v1) + "\n## Revision\n\nEdited during acceptance.\n"
    status, edited = call(
        f"/resource-versions/{version['version_id']}/body",
        method="PUT",
        body={"body": body_v2, "manifest": {"version": "1.0.1"},
              "row_version": version["row_version"], "reason": "acceptance edit"},
        if_match=f'"{version["version_id"]}:{version["row_version"]}"',
    )
    checks["3. owner edits the body, creating a new version"] = (
        status == 200 and edited.get("version", {}).get("version") == "1.0.1"
    )
    if status != 200:
        print(json.dumps(edited, ensure_ascii=False)[:300])
        return 1
    version = edited["version"]

    # 4. submit for validation
    status, submitted = call(
        f"/resource-versions/{version['version_id']}/transitions",
        method="POST",
        body={"target_status": "validating", "reason": "acceptance submit",
              "row_version": version["row_version"]},
        if_match=f'"{version["version_id"]}:{version["row_version"]}"',
    )
    checks["4. owner submits for validation"] = status == 202
    job_id = (submitted.get("job") or {}).get("job_id")
    checks["4b. a validation job was created"] = job_id is not None
    version = submitted["version"]
    print(f"submitted, job {job_id}")

    # 5. the worker validates it
    if job_id:
        final = wait_job(job_id)
        print(f"job final: {final.get('status')}")
        checks["5. the worker completes validation"] = final.get("status") == "succeeded"

    # 6. validator moves it to pending_review
    status, reviewed = call(
        f"/resource-versions/{version['version_id']}/transitions",
        acting_as="validator", method="POST",
        body={"target_status": "pending_review", "reason": "validation passed",
              "row_version": version["row_version"]},
        if_match=f'"{version["version_id"]}:{version["row_version"]}"',
    )
    checks["6. validator moves it to pending review"] = status == 200
    if status != 200:
        print(json.dumps(reviewed, ensure_ascii=False)[:300])
        return 1
    version = reviewed["version"]

    # 7. an independent reviewer approves
    status, review = call(
        f"/resource-versions/{version['version_id']}/reviews",
        acting_as="reviewer", method="POST",
        body={
            "review_type": "governance",
            "decision": "approve",
            "reviewer_role": "reviewer",
            "artifact_hash": version["artifact_hash"],
            "manifest_snapshot_hash": version["manifest_snapshot_hash"],
            "runtime_scope": ["claude-code"],
            "validation_evidence_ref": f"job:{job_id}",
            "rationale": "acceptance approval",
        },
    )
    checks["7. an independent reviewer approves"] = status in (200, 201)
    if status not in (200, 201):
        print(json.dumps(review, ensure_ascii=False)[:300])

    # 8. approve then publish
    status, approved = call(
        f"/resource-versions/{version['version_id']}/transitions",
        acting_as="reviewer", method="POST",
        body={"target_status": "approved", "reason": "approved",
              "row_version": version["row_version"]},
        if_match=f'"{version["version_id"]}:{version["row_version"]}"',
    )
    checks["8. reviewer moves it to approved"] = status == 200
    if status == 200:
        version = approved["version"]

    status, published = call(
        f"/resource-versions/{version['version_id']}/transitions",
        acting_as="publisher", method="POST",
        body={"target_status": "published", "reason": "publish for acceptance",
              "row_version": version["row_version"]},
        if_match=f'"{version["version_id"]}:{version["row_version"]}"',
    )
    checks["9. publisher publishes it"] = status == 200
    if status == 200:
        version = published["version"]
        checks["9b. published_at is recorded"] = bool(version.get("published_at"))
    else:
        print(json.dumps(published, ensure_ascii=False)[:300])
    print(f"final version status: {version.get('status')}")

    # 10. the audit trail holds every step. Transitions are recorded against the
    # version; a review is recorded against the review entity itself, so both queries
    # are needed.
    status, audit = call(f"/audit?version_public_id={version['version_id']}&limit=50")
    actions = [item["action"] for item in audit.get("items", [])]
    status_review, reviews = call("/audit?action=review.create&limit=50")
    review_actions = [item["action"] for item in reviews.get("items", [])]
    print(f"audit actions (version): {sorted(set(actions))}")
    print(f"review.create entries: {len(review_actions)}")
    checks["10. transitions are audited"] = "version.transition" in actions
    checks["10b. the review is audited"] = "review.create" in review_actions

    # 11. the published resource appears in search
    status, search = call(
        "/search/resources",
        method="POST",
        body={"query": "acceptance walkthrough", "filters": {}, "limit": 5},
    )
    hits = [item["resource"]["slug"] for item in search.get("items", [])] if status == 200 else []
    checks["11. the published resource is searchable"] = slug in hits
    if status != 200:
        print(f"search status {status}: {json.dumps(search, ensure_ascii=False)[:200]}")

    # 12. the body on disk is the edited text
    host_file = ROOT / "resources" / "skills" / slug / "SKILL.md"
    checks["12. the edit reached the checkout"] = (
        host_file.is_file() and host_file.read_text(encoding="utf-8") == body_v2
    )

    print()
    for label, passed in checks.items():
        print(f"  [{'ok' if passed else 'FAIL'}] {label}")
    print(f"\nfixture: {slug} ({resource_id})")
    return 0 if checks and all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
