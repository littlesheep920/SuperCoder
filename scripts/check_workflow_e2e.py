"""Workflow end to end: publish, compile, run, gate, approve, finish, review, audit.

Every workflow rule has unit tests against in-memory repositories. This checks what those cannot:
that the same path works against MySQL, through the real routes, with the real worker. Several
defects in this area passed every unit test and failed on the first real insert, so a workflow
change is not finished until this passes on the compose stack.

What it does, as the people who would do it:

1. the owner creates two skills and a workflow that chains them through an approval node, and
   each goes through validation, review and publication;
2. publishing the workflow must compile it into a definition (`GET /workflows`), audited;
3. the owner starts a run without a correlation id in the body (the console never sends one);
4. the worker executes the first stage and stops at the gate;
5. a member without the reviewer role cannot decide; the reviewer approves;
6. the worker finishes the run; the review counts one execution per capability; every step is
   in the audit log;
7. a reviewer who started a run cannot approve it, and can cancel it.

Requires MySQL, the API and the worker, all reading the same checkout. With the compose stack,
fixtures are written through the API container (the default):

    docker compose up -d --build
    python scripts/check_workflow_e2e.py

Without Docker, run MySQL, the API and the worker on this machine against this checkout, then:

    python scripts/check_workflow_e2e.py --fixtures local

The fixtures are removed when the check ends, whatever its outcome (see scripts/e2e_fixtures.py);
`--keep-fixtures` leaves them for inspection. Rows written to MySQL stay: this is a development
database, and the audit trail of a check is part of what it checks.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from e2e_fixtures import Fixtures, add_arguments, artifact  # noqa: E402

BASE = "http://localhost:8000/v1"
TOKENS = {
    "owner": "dev-owner-token",
    "validator": "dev-validator-token",
    "reviewer": "dev-reviewer-token",
    "publisher": "dev-publisher-token",
    "member": "dev-member-token",
}
HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
ENTRY = {"skill": "SKILL.md", "workflow": "WORKFLOW.md"}


def call(path, *, acting_as="owner", method="GET", body=None, headers=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    sent = {"Authorization": f"Bearer {TOKENS[acting_as]}", "Content-Type": "application/json"}
    sent.update(headers or {})
    request = urllib.request.Request(BASE + path, data=data, headers=sent, method=method)
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


def materialise(fixtures: Fixtures, kind: str, slug: str, manifest: dict, body: str) -> list[dict]:
    """Write the resource into the checkout the API and the worker read."""

    entry = body.encode("utf-8")
    fixtures.write(kind, slug, {
        "resource.yaml": (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
        ENTRY[kind]: entry,
    })
    return [artifact(ENTRY[kind], entry)]


def manifest_for(kind: str, slug: str, name: str, extra: dict | None = None) -> dict:
    manifest = {
        "schema_version": "1",
        "kind": kind,
        "slug": slug,
        "name": name,
        "version": "1.0.0",
        "description": "Example resource for the SuperCoder protocol. Workflow end-to-end fixture.",
        "aliases": [],
        "tags": ["example", "probe"],
        "categories": ["software-development/testing"],
        "runtimes": ["claude-code"],
        "inputs": [{"name": "request", "type": "text", "required": True, "description": "Input."}],
        "outputs": [{"name": "result", "type": "text", "required": True, "description": "Output."}],
        "permissions": [],
        "dependencies": [],
        "entrypoint": ENTRY[kind],
        "status": "draft",
        "owner_team": "platform-ai",
    }
    manifest.update(extra or {})
    return manifest


def wait_job(job_id: str, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    final: dict = {}
    while time.time() < deadline:
        _, final = call(f"/jobs/{job_id}")
        if final.get("status") in ("succeeded", "failed", "dead_letter", "cancelled"):
            return final
        time.sleep(2)
    return final


def transition(version: dict, target: str, acting_as: str, reason: str) -> tuple[int, dict]:
    return call(
        f"/resource-versions/{version['version_id']}/transitions",
        acting_as=acting_as,
        method="POST",
        body={"target_status": target, "reason": reason, "row_version": version["row_version"]},
        headers={"If-Match": f'"{version["version_id"]}:{version["row_version"]}"'},
    )


def publish(fixtures: Fixtures, kind: str, slug: str, manifest: dict, body: str) -> dict:
    """Create, validate, review, approve and publish one resource. Raises on any refusal."""

    artifacts = materialise(fixtures, kind, slug, manifest, body)
    status, created = call("/resources", method="POST",
                           body={"manifest": manifest, "artifacts": artifacts, "reason": "workflow e2e"})
    if status != 201:
        raise SystemExit(f"create {slug}: {status} {json.dumps(created, ensure_ascii=False)[:300]}")
    version = created["version"]

    status, submitted = transition(version, "validating", "owner", "e2e submit")
    if status != 202:
        raise SystemExit(f"submit {slug}: {status} {json.dumps(submitted, ensure_ascii=False)[:300]}")
    version = submitted["version"]
    job_id = (submitted.get("job") or {}).get("job_id")
    final = wait_job(job_id) if job_id else {}
    if final.get("status") != "succeeded":
        raise SystemExit(f"validation of {slug} ended {final.get('status')}: {final.get('error_summary')}")

    status, moved = transition(version, "pending_review", "validator", "validation passed")
    if status != 200:
        raise SystemExit(f"pending_review {slug}: {status} {json.dumps(moved, ensure_ascii=False)[:300]}")
    version = moved["version"]

    status, review = call(
        f"/resource-versions/{version['version_id']}/reviews",
        acting_as="reviewer",
        method="POST",
        body={
            "review_type": "governance",
            "decision": "approve",
            "reviewer_role": "reviewer",
            "artifact_hash": version["artifact_hash"],
            "manifest_snapshot_hash": version["manifest_snapshot_hash"],
            "runtime_scope": ["claude-code"],
            "validation_evidence_ref": f"job:{job_id}",
            "rationale": "workflow e2e approval",
        },
    )
    if status not in (200, 201):
        raise SystemExit(f"review {slug}: {status} {json.dumps(review, ensure_ascii=False)[:300]}")

    for target, acting_as in (("approved", "reviewer"), ("published", "publisher")):
        status, moved = transition(version, target, acting_as, f"e2e {target}")
        if status != 200:
            raise SystemExit(f"{target} {slug}: {status} {json.dumps(moved, ensure_ascii=False)[:300]}")
        version = moved["version"]
    print(f"published {kind}/{slug} {version['version']} ({version['version_id']})")
    return version


def wait_run(run_id: str, statuses: set[str], timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    run: dict = {}
    while time.time() < deadline:
        _, run = call(f"/workflow-runs/{run_id}")
        if run.get("status") in statuses:
            return run
        time.sleep(2)
    return run


def start_run(workflow_id: str, acting_as: str, key: str, runtime: str = "claude-code") -> tuple[int, dict]:
    # No correlation id in the body, exactly as the console sends it.
    return call(
        f"/workflows/{workflow_id}/runs",
        acting_as=acting_as,
        method="POST",
        body={"runtime": runtime, "input": {"request": "e2e"}, "timeout_seconds": 600},
        headers={"Idempotency-Key": key},
    )


def decide(run: dict, decision: str, acting_as: str) -> tuple[int, dict]:
    return call(
        f"/workflow-runs/{run['run_id']}/approvals",
        acting_as=acting_as,
        method="POST",
        body={"gate": run["approval_gate"], "decision": decision, "reason": "e2e check",
              "row_version": run["row_version"]},
        headers={"If-Match": f'"{run["run_id"]}:{run["row_version"]}"'},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Workflow end to end against MySQL, the real routes and the worker.")
    add_arguments(parser)
    args = parser.parse_args(argv)

    health = wait_health()
    if not health["components"]["mysql"]["enabled"]:
        raise SystemExit("MySQL is not enabled; this check needs an API running with MySQL")

    fixtures = Fixtures(ROOT, args.fixtures, keep=args.keep_fixtures)
    try:
        return run(fixtures)
    finally:
        fixtures.close()


def run(fixtures: Fixtures) -> int:
    suffix = int(time.time()) % 100000
    first, second, flow = f"example-e2e-plan-{suffix}", f"example-e2e-notes-{suffix}", f"example-e2e-flow-{suffix}"
    checks: dict[str, bool] = {}

    publish(fixtures, "skill", first, manifest_for("skill", first, "E2E Plan"), "# E2E Plan\n\nExample resource.\n")
    publish(fixtures, "skill", second, manifest_for("skill", second, "E2E Notes"), "# E2E Notes\n\nExample resource.\n")
    graph = {
        "nodes": [
            {"id": "start", "type": "input"},
            {"id": "plan", "type": "resource", "resource": {"kind": "skill", "slug": first, "version": "^1.0.0"}},
            {"id": "approve", "type": "approval"},
            {"id": "notes", "type": "resource", "resource": {"kind": "skill", "slug": second, "version": "^1.0.0"}},
            {"id": "done", "type": "output"},
        ],
        "edges": [
            {"from": "start", "to": "plan"},
            {"from": "plan", "to": "approve"},
            {"from": "approve", "to": "notes"},
            {"from": "notes", "to": "done"},
        ],
        "retry_policy": {"max_attempts": 2, "backoff": "exponential"},
        "approval_gates": ["approve"],
    }
    workflow_version = publish(
        fixtures, "workflow", flow, manifest_for("workflow", flow, "E2E Flow", {"workflow": graph}),
        "# E2E Flow\n\nExample workflow: plan, approve, write notes.\n",
    )

    # 2. publishing compiled it
    status, listing = call("/workflows")
    definition = next(
        (item for item in listing.get("items", []) if item.get("resource_version_id") == workflow_version["version_id"]),
        None,
    )
    checks["W1. publishing the workflow compiled it into a listed definition"] = definition is not None
    if definition is None:
        print(json.dumps(listing, ensure_ascii=False)[:400])
        return report(checks)
    workflow_id = definition["workflow_id"]
    checks["W2. the graph hash is a sha256 digest"] = bool(HASH.match(definition.get("graph_hash", "")))
    _, compiled = call(f"/audit?action=workflow.compile&version_public_id={workflow_version['version_id']}")
    checks["W3. the compilation is audited"] = any(
        item.get("outcome") == "succeeded" for item in compiled.get("items", [])
    )

    # 3. a run, as the console starts it
    status, run = start_run(workflow_id, "owner", f"e2e-{suffix}-a")
    checks["W4. a run starts without a correlation id in the body (202)"] = status == 202
    if status != 202:
        print(json.dumps(run, ensure_ascii=False)[:400])
        return report(checks)
    run_id = run["run_id"]
    status_round_trip, _ = call(f"/workflows/{run['workflow_id']}")
    checks["W5. the run's workflow_id is the definition's public id"] = (
        run["workflow_id"] == workflow_id and status_round_trip == 200
    )
    status_bad, _ = start_run(workflow_id, "owner", f"e2e-{suffix}-bad", runtime="agent-skills")
    checks["W6. an unsupported runtime is refused with 422"] = status_bad == 422

    # 4. the worker executes the first stage and stops at the gate
    waiting = wait_run(run_id, {"awaiting_approval", "failed", "timed_out", "cancelled"})
    checks["W7. the worker runs the first stage and stops at the gate"] = (
        waiting.get("status") == "awaiting_approval" and waiting.get("approval_gate") == "approve-approval"
    )
    if waiting.get("status") != "awaiting_approval":
        print(json.dumps(waiting, ensure_ascii=False)[:400])
        return report(checks)

    # 5. who may decide
    status_member, _ = decide(waiting, "approve", "member")
    checks["W8. a member without the reviewer role cannot decide (403)"] = status_member == 403
    status_owner, _ = decide(waiting, "approve", "owner")
    checks["W9. the owner, not a reviewer, cannot decide (403)"] = status_owner == 403
    status_ok, approved = decide(waiting, "approve", "reviewer")
    checks["W10. the reviewer approves and the run continues"] = status_ok == 200 and approved.get("status") == "running"

    # 6. the run finishes, and says what it did
    finished = wait_run(run_id, {"succeeded", "failed", "timed_out", "cancelled"})
    checks["W11. the worker finishes the run"] = finished.get("status") == "succeeded"
    checks["W12. the run has an output hash"] = bool(HASH.match(finished.get("output_hash") or ""))
    _, review = call(f"/workflow-runs/{run_id}/review")
    capabilities = review.get("capabilities", [])
    checks["W13. the review counts one successful execution per capability"] = (
        len(capabilities) == 2 and all(item["uses"] == 1 and item["succeeded"] == 1 for item in capabilities)
    )
    _, audit = call(f"/audit?entity_public_id={run_id}&limit=100")
    actions = {item.get("action") for item in audit.get("items", [])}
    expected = {"workflow.run.create", "workflow.run.start", "workflow.gate.open", "workflow.gate.decide",
                "workflow.run.succeed"}
    checks["W14. every step of the run is audited"] = expected <= actions
    if not expected <= actions:
        print(f"audit actions for {run_id}: {sorted(action for action in actions if action)}")

    # 7. no self-approval, and the requester can cancel
    status, own = start_run(workflow_id, "reviewer", f"e2e-{suffix}-b")
    own_waiting = wait_run(own.get("run_id", ""), {"awaiting_approval", "failed", "timed_out", "cancelled"}) if status == 202 else {}
    status_self, _ = decide(own_waiting, "approve", "reviewer") if own_waiting.get("approval_gate") else (None, {})
    checks["W15. a reviewer cannot approve a run they started (403)"] = status_self == 403
    if own_waiting.get("run_id"):
        status_cancel, _ = call(
            f"/workflow-runs/{own_waiting['run_id']}/cancel", acting_as="reviewer", method="POST",
            headers={"Idempotency-Key": f"e2e-{suffix}-cancel"},
        )
        cancelled = wait_run(own_waiting["run_id"], {"cancelled", "succeeded", "failed", "timed_out"})
        checks["W16. the requester's cancellation ends a run waiting at a gate"] = (
            status_cancel == 202 and cancelled.get("status") == "cancelled"
        )

    print(f"\nfixtures: {first}, {second}, {flow}; run {run_id}")
    return report(checks)


def report(checks: dict[str, bool]) -> int:
    print()
    for label, passed in checks.items():
        print(f"  [{'ok' if passed else 'FAIL'}] {label}")
    passed_all = bool(checks) and all(checks.values())
    print("\nRESULT:", "PASS" if passed_all else "FAIL")
    return 0 if passed_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
