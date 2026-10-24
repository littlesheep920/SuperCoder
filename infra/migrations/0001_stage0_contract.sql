-- SuperCoder Stage 0 relational contract
-- Migration: 0001_stage0_contract
-- Target: MySQL 8.0.16+ (CHECK constraints must be enforced)
--
-- This forward-only foundation intentionally contains no DROP statements.
-- MySQL DDL implicitly commits; apply it only through the reviewed migration
-- runner against an empty schema. Production history tables must not be
-- removed by an automated down migration.

SET NAMES utf8mb4 COLLATE utf8mb4_0900_as_cs;

CREATE TABLE resource (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(26) NOT NULL,
    kind VARCHAR(32) NOT NULL,
    slug VARCHAR(160) NOT NULL,
    owner_team VARCHAR(160) NOT NULL,
    created_by VARCHAR(160) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (id),
    CONSTRAINT uq_resource_public_id UNIQUE (public_id),
    CONSTRAINT uq_resource_kind_slug UNIQUE (kind, slug),
    KEY idx_resource_owner (owner_team, kind),
    KEY idx_resource_created (created_at, id),
    CONSTRAINT ck_resource_public_id CHECK (
        public_id REGEXP '^[0-9A-HJKMNP-TV-Z]{26}$'
    ),
    CONSTRAINT ck_resource_kind CHECK (
        kind IN ('skill', 'mcp', 'prompt', 'agent', 'workflow', 'tool')
    ),
    CONSTRAINT ck_resource_slug CHECK (
        slug REGEXP '^[a-z0-9]+([._-][a-z0-9]+)*$'
    ),
    CONSTRAINT ck_resource_owner_nonempty CHECK (CHAR_LENGTH(owner_team) > 0),
    CONSTRAINT ck_resource_creator_nonempty CHECK (CHAR_LENGTH(created_by) > 0),
    CONSTRAINT ck_resource_row_version CHECK (row_version >= 1)
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE resource_version (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(26) NOT NULL,
    resource_id BIGINT UNSIGNED NOT NULL,
    schema_version VARCHAR(32) NOT NULL,
    version VARCHAR(64) NOT NULL,
    name VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    owner_team VARCHAR(160) NOT NULL,
    entrypoint VARCHAR(500) NOT NULL,
    aliases_json JSON NOT NULL,
    inputs_json JSON NOT NULL,
    outputs_json JSON NOT NULL,
    permissions_json JSON NOT NULL,
    type_spec_json JSON NOT NULL,
    manifest_json JSON NOT NULL,
    artifact_hash VARCHAR(80) NOT NULL,
    manifest_snapshot_hash VARCHAR(80) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    status_changed_at DATETIME(6) NOT NULL,
    status_changed_by VARCHAR(160) NOT NULL,
    status_reason VARCHAR(2000) NOT NULL,
    validated_at DATETIME(6) NULL,
    approved_at DATETIME(6) NULL,
    published_at DATETIME(6) NULL,
    deprecated_at DATETIME(6) NULL,
    retire_after DATETIME(6) NULL,
    retired_at DATETIME(6) NULL,
    replacement_version_id BIGINT UNSIGNED NULL,
    created_by VARCHAR(160) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (id),
    CONSTRAINT uq_rv_public_id UNIQUE (public_id),
    CONSTRAINT uq_rv_resource_version UNIQUE (resource_id, version),
    KEY idx_rv_resource_status (resource_id, status, version),
    KEY idx_rv_status_changed (status, status_changed_at, id),
    KEY idx_rv_artifact_hash (artifact_hash),
    KEY idx_rv_replacement (replacement_version_id),
    KEY idx_rv_owner_status (owner_team, status),
    CONSTRAINT fk_rv_resource FOREIGN KEY (resource_id)
        REFERENCES resource (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rv_replacement FOREIGN KEY (replacement_version_id)
        REFERENCES resource_version (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_rv_public_id CHECK (
        public_id REGEXP '^[0-9A-HJKMNP-TV-Z]{26}$'
    ),
    CONSTRAINT ck_rv_schema_version CHECK (schema_version = '1'),
    CONSTRAINT ck_rv_semver CHECK (
        version REGEXP '^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)(-[0-9A-Za-z-]+(\\.[0-9A-Za-z-]+)*)?(\\+[0-9A-Za-z-]+(\\.[0-9A-Za-z-]+)*)?$'
    ),
    CONSTRAINT ck_rv_status CHECK (
        status IN (
            'draft', 'validating', 'pending_review', 'approved',
            'published', 'deprecated', 'retired'
        )
    ),
    CONSTRAINT ck_rv_artifact_hash CHECK (
        artifact_hash REGEXP '^sha256:[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_rv_manifest_hash CHECK (
        manifest_snapshot_hash REGEXP '^sha256:[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_rv_json_shapes CHECK (
        JSON_TYPE(aliases_json) = 'ARRAY'
        AND JSON_TYPE(inputs_json) = 'ARRAY'
        AND JSON_TYPE(outputs_json) = 'ARRAY'
        AND JSON_TYPE(permissions_json) = 'ARRAY'
        AND JSON_TYPE(type_spec_json) = 'OBJECT'
        AND JSON_TYPE(manifest_json) = 'OBJECT'
    ),
    -- MySQL forbids CHECK expressions over AUTO_INCREMENT columns.
    -- The self-reference rule is enforced by migration triggers below.
    CONSTRAINT ck_rv_required_text CHECK (
        CHAR_LENGTH(name) > 0
        AND CHAR_LENGTH(description) > 0
        AND CHAR_LENGTH(owner_team) > 0
        AND CHAR_LENGTH(entrypoint) > 0
        AND LEFT(entrypoint, 1) NOT IN ('/', '\\')
        AND entrypoint NOT LIKE '%..%'
        AND CHAR_LENGTH(status_changed_by) > 0
        AND CHAR_LENGTH(status_reason) > 0
        AND CHAR_LENGTH(created_by) > 0
    ),
    CONSTRAINT ck_rv_validated_state_time CHECK (
        status NOT IN ('approved', 'published', 'deprecated', 'retired')
        OR validated_at IS NOT NULL
    ),
    CONSTRAINT ck_rv_approved_state_time CHECK (
        status NOT IN ('approved', 'published', 'deprecated', 'retired')
        OR approved_at IS NOT NULL
    ),
    CONSTRAINT ck_rv_published_state_time CHECK (
        status NOT IN ('published', 'deprecated', 'retired')
        OR published_at IS NOT NULL
    ),
    CONSTRAINT ck_rv_deprecated_state_time CHECK (
        status NOT IN ('deprecated', 'retired') OR deprecated_at IS NOT NULL
    ),
    CONSTRAINT ck_rv_retired_state_time CHECK (
        status <> 'retired' OR retired_at IS NOT NULL
    ),
    CONSTRAINT ck_rv_time_order CHECK (
        (validated_at IS NULL OR approved_at IS NULL OR validated_at <= approved_at)
        AND (approved_at IS NULL OR published_at IS NULL OR approved_at <= published_at)
        AND (published_at IS NULL OR deprecated_at IS NULL OR published_at <= deprecated_at)
        AND (deprecated_at IS NULL OR retired_at IS NULL OR deprecated_at <= retired_at)
    ),
    CONSTRAINT ck_rv_row_version CHECK (row_version >= 1)
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE resource_tag (
    resource_version_id BIGINT UNSIGNED NOT NULL,
    tag VARCHAR(100) NOT NULL,
    normalized_tag VARCHAR(100) NOT NULL,
    ordinal SMALLINT UNSIGNED NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (resource_version_id, normalized_tag),
    CONSTRAINT uq_resource_tag_ordinal UNIQUE (resource_version_id, ordinal),
    KEY idx_resource_tag_lookup (normalized_tag, resource_version_id),
    CONSTRAINT fk_resource_tag_version FOREIGN KEY (resource_version_id)
        REFERENCES resource_version (id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT ck_resource_tag_nonempty CHECK (
        CHAR_LENGTH(tag) > 0 AND CHAR_LENGTH(normalized_tag) > 0
    )
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE resource_category (
    resource_version_id BIGINT UNSIGNED NOT NULL,
    category VARCHAR(160) NOT NULL,
    normalized_category VARCHAR(160) NOT NULL,
    ordinal SMALLINT UNSIGNED NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (resource_version_id, normalized_category),
    CONSTRAINT uq_resource_category_ordinal UNIQUE (resource_version_id, ordinal),
    KEY idx_resource_category_lookup (normalized_category, resource_version_id),
    CONSTRAINT fk_resource_category_version FOREIGN KEY (resource_version_id)
        REFERENCES resource_version (id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT ck_resource_category_format CHECK (
        category REGEXP '^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$'
        AND normalized_category REGEXP '^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)*$'
    )
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE resource_runtime (
    resource_version_id BIGINT UNSIGNED NOT NULL,
    runtime VARCHAR(64) NOT NULL,
    ordinal SMALLINT UNSIGNED NOT NULL,
    validation_status VARCHAR(24) NOT NULL DEFAULT 'unvalidated',
    adapter_version VARCHAR(64) NULL,
    validated_artifact_hash VARCHAR(80) NULL,
    validation_evidence_ref VARCHAR(500) NULL,
    validated_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (resource_version_id, runtime),
    CONSTRAINT uq_resource_runtime_ordinal UNIQUE (resource_version_id, ordinal),
    KEY idx_runtime_compat (runtime, validation_status, resource_version_id),
    CONSTRAINT fk_resource_runtime_version FOREIGN KEY (resource_version_id)
        REFERENCES resource_version (id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT ck_resource_runtime_name CHECK (
        runtime IN (
            'kiro', 'claude-code', 'cursor', 'dify',
            'openai-compatible-agent-runtime'
        )
    ),
    CONSTRAINT ck_resource_runtime_status CHECK (
        validation_status IN (
            'unvalidated', 'validating', 'validated', 'failed', 'stale'
        )
    ),
    CONSTRAINT ck_resource_runtime_hash CHECK (
        validated_artifact_hash IS NULL
        OR validated_artifact_hash REGEXP '^sha256:[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_resource_runtime_validated CHECK (
        validation_status <> 'validated'
        OR (
            adapter_version IS NOT NULL
            AND validated_artifact_hash IS NOT NULL
            AND validation_evidence_ref IS NOT NULL
            AND validated_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_resource_runtime_row_version CHECK (row_version >= 1)
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE resource_dependency (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    resource_version_id BIGINT UNSIGNED NOT NULL,
    ordinal SMALLINT UNSIGNED NOT NULL,
    dependency_kind VARCHAR(32) NOT NULL,
    dependency_slug VARCHAR(160) NOT NULL,
    version_constraint VARCHAR(255) NOT NULL,
    source VARCHAR(16) NOT NULL,
    is_optional BOOLEAN NOT NULL DEFAULT FALSE,
    purpose VARCHAR(500) NULL,
    resolved_resource_version_id BIGINT UNSIGNED NULL,
    external_locator VARCHAR(1000) NULL,
    external_digest VARCHAR(255) NULL,
    resolution_status VARCHAR(24) NOT NULL DEFAULT 'unresolved',
    resolution_error_code VARCHAR(100) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (id),
    CONSTRAINT uq_dependency_identity UNIQUE (
        resource_version_id, dependency_kind, dependency_slug
    ),
    CONSTRAINT uq_dependency_ordinal UNIQUE (resource_version_id, ordinal),
    KEY idx_dependency_reverse (
        resolved_resource_version_id, resource_version_id
    ),
    KEY idx_dependency_resolution (resolution_status, resource_version_id),
    KEY idx_dependency_declared (dependency_kind, dependency_slug),
    CONSTRAINT fk_dependency_owner_version FOREIGN KEY (resource_version_id)
        REFERENCES resource_version (id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT fk_dependency_resolved_version FOREIGN KEY (resolved_resource_version_id)
        REFERENCES resource_version (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_dependency_kind CHECK (
        dependency_kind IN (
            'skill', 'mcp', 'prompt', 'agent', 'workflow', 'tool', 'external'
        )
    ),
    CONSTRAINT ck_dependency_source CHECK (source IN ('managed', 'external')),
    CONSTRAINT ck_dependency_resolution_status CHECK (
        resolution_status IN ('unresolved', 'resolved', 'failed', 'stale')
    ),
    CONSTRAINT ck_dependency_slug_nonempty CHECK (
        CHAR_LENGTH(dependency_slug) > 0 AND CHAR_LENGTH(version_constraint) > 0
    ),
    CONSTRAINT ck_dependency_not_self CHECK (
        resolved_resource_version_id IS NULL
        OR resolved_resource_version_id <> resource_version_id
    ),
    CONSTRAINT ck_dependency_source_fields CHECK (
        (
            source = 'managed'
            AND dependency_kind <> 'external'
            AND external_locator IS NULL
            AND external_digest IS NULL
        )
        OR (
            source = 'external'
            AND dependency_kind = 'external'
            AND resolved_resource_version_id IS NULL
            AND external_locator IS NOT NULL
            AND external_digest IS NOT NULL
            AND CHAR_LENGTH(external_locator) > 0
            AND CHAR_LENGTH(external_digest) > 0
        )
    ),
    CONSTRAINT ck_dependency_resolved_target CHECK (
        resolution_status <> 'resolved'
        OR (
            (source = 'managed' AND resolved_resource_version_id IS NOT NULL)
            OR (source = 'external' AND external_digest IS NOT NULL)
        )
    ),
    CONSTRAINT ck_dependency_error CHECK (
        resolution_status <> 'failed' OR resolution_error_code IS NOT NULL
    ),
    CONSTRAINT ck_dependency_row_version CHECK (row_version >= 1)
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE resource_review (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(26) NOT NULL,
    resource_version_id BIGINT UNSIGNED NOT NULL,
    review_type VARCHAR(32) NOT NULL,
    reviewer_id VARCHAR(160) NOT NULL,
    reviewer_role VARCHAR(64) NOT NULL,
    decision VARCHAR(32) NOT NULL,
    artifact_hash VARCHAR(80) NOT NULL,
    manifest_snapshot_hash VARCHAR(80) NOT NULL,
    runtime_scope_json JSON NOT NULL,
    validation_evidence_ref VARCHAR(500) NOT NULL,
    permission_summary_json JSON NOT NULL,
    dependency_summary_json JSON NOT NULL,
    evaluation_refs_json JSON NOT NULL,
    rationale TEXT NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    invalidated_at DATETIME(6) NULL,
    invalidation_reason VARCHAR(2000) NULL,
    decided_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    CONSTRAINT uq_resource_review_public_id UNIQUE (public_id),
    CONSTRAINT uq_resource_review_idempotency UNIQUE (
        resource_version_id, reviewer_id, idempotency_key
    ),
    KEY idx_review_version_decision (
        resource_version_id, decision, invalidated_at, decided_at
    ),
    KEY idx_review_reviewer (reviewer_id, decided_at),
    KEY idx_review_artifact (artifact_hash),
    CONSTRAINT fk_resource_review_version FOREIGN KEY (resource_version_id)
        REFERENCES resource_version (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_resource_review_public_id CHECK (
        public_id REGEXP '^[0-9A-HJKMNP-TV-Z]{26}$'
    ),
    CONSTRAINT ck_resource_review_type CHECK (
        review_type IN ('governance', 'security', 'runtime', 'publication')
    ),
    CONSTRAINT ck_resource_review_decision CHECK (
        decision IN ('approve', 'request_changes')
    ),
    CONSTRAINT ck_resource_review_hashes CHECK (
        artifact_hash REGEXP '^sha256:[0-9a-f]{64}$'
        AND manifest_snapshot_hash REGEXP '^sha256:[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_resource_review_json CHECK (
        JSON_TYPE(runtime_scope_json) = 'ARRAY'
        AND JSON_TYPE(permission_summary_json) = 'OBJECT'
        AND JSON_TYPE(dependency_summary_json) = 'OBJECT'
        AND JSON_TYPE(evaluation_refs_json) = 'ARRAY'
    ),
    CONSTRAINT ck_resource_review_required_text CHECK (
        CHAR_LENGTH(reviewer_id) > 0
        AND CHAR_LENGTH(reviewer_role) > 0
        AND CHAR_LENGTH(validation_evidence_ref) > 0
        AND CHAR_LENGTH(rationale) > 0
        AND CHAR_LENGTH(idempotency_key) > 0
    ),
    CONSTRAINT ck_resource_review_invalidation CHECK (
        (invalidated_at IS NULL AND invalidation_reason IS NULL)
        OR (
            invalidated_at IS NOT NULL
            AND invalidation_reason IS NOT NULL
            AND CHAR_LENGTH(invalidation_reason) > 0
        )
    )
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE workflow_definition (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(26) NOT NULL,
    resource_version_id BIGINT UNSIGNED NOT NULL,
    definition_schema_version VARCHAR(32) NOT NULL,
    definition_json JSON NOT NULL,
    input_schema_json JSON NOT NULL,
    output_schema_json JSON NOT NULL,
    graph_hash VARCHAR(80) NOT NULL,
    is_deterministic BOOLEAN NOT NULL DEFAULT FALSE,
    compiler_version VARCHAR(64) NULL,
    compiled_artifact_ref VARCHAR(500) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (id),
    CONSTRAINT uq_workflow_definition_public_id UNIQUE (public_id),
    CONSTRAINT uq_workflow_definition_version UNIQUE (resource_version_id),
    KEY idx_workflow_graph_hash (graph_hash),
    CONSTRAINT fk_workflow_definition_version FOREIGN KEY (resource_version_id)
        REFERENCES resource_version (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_workflow_definition_public_id CHECK (
        public_id REGEXP '^[0-9A-HJKMNP-TV-Z]{26}$'
    ),
    CONSTRAINT ck_workflow_definition_json CHECK (
        JSON_TYPE(definition_json) = 'OBJECT'
        AND JSON_TYPE(input_schema_json) = 'OBJECT'
        AND JSON_TYPE(output_schema_json) = 'OBJECT'
    ),
    CONSTRAINT ck_workflow_definition_hash CHECK (
        graph_hash REGEXP '^sha256:[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_workflow_definition_schema CHECK (
        CHAR_LENGTH(definition_schema_version) > 0
    ),
    CONSTRAINT ck_workflow_definition_compiled CHECK (
        compiled_artifact_ref IS NULL OR compiler_version IS NOT NULL
    ),
    CONSTRAINT ck_workflow_definition_row_version CHECK (row_version >= 1)
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE workflow_run (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(26) NOT NULL,
    workflow_definition_id BIGINT UNSIGNED NOT NULL,
    definition_graph_hash VARCHAR(80) NOT NULL,
    resolution_snapshot_json JSON NOT NULL,
    resolution_snapshot_hash VARCHAR(80) NOT NULL,
    requested_by VARCHAR(160) NOT NULL,
    runtime VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    correlation_id VARCHAR(128) NOT NULL,
    input_json JSON NOT NULL,
    input_hash VARCHAR(80) NOT NULL,
    output_json JSON NULL,
    output_hash VARCHAR(80) NULL,
    checkpoint_json JSON NULL,
    random_seed BIGINT NULL,
    timeout_seconds INT UNSIGNED NOT NULL,
    approval_gate VARCHAR(160) NULL,
    approval_reason TEXT NULL,
    approved_by VARCHAR(160) NULL,
    approved_at DATETIME(6) NULL,
    started_at DATETIME(6) NULL,
    finished_at DATETIME(6) NULL,
    error_code VARCHAR(100) NULL,
    error_summary VARCHAR(1000) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (id),
    CONSTRAINT uq_workflow_run_public_id UNIQUE (public_id),
    CONSTRAINT uq_workflow_run_idempotency UNIQUE (
        workflow_definition_id, requested_by, idempotency_key
    ),
    KEY idx_workflow_run_status (status, created_at, id),
    KEY idx_workflow_run_definition (workflow_definition_id, created_at),
    KEY idx_workflow_run_correlation (correlation_id),
    KEY idx_workflow_run_approval (status, approval_gate, created_at),
    KEY idx_workflow_run_resolution_hash (resolution_snapshot_hash),
    CONSTRAINT fk_workflow_run_definition FOREIGN KEY (workflow_definition_id)
        REFERENCES workflow_definition (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_workflow_run_public_id CHECK (
        public_id REGEXP '^[0-9A-HJKMNP-TV-Z]{26}$'
    ),
    CONSTRAINT ck_workflow_run_hashes CHECK (
        definition_graph_hash REGEXP '^sha256:[0-9a-f]{64}$'
        AND resolution_snapshot_hash REGEXP '^sha256:[0-9a-f]{64}$'
        AND input_hash REGEXP '^sha256:[0-9a-f]{64}$'
        AND (output_hash IS NULL OR output_hash REGEXP '^sha256:[0-9a-f]{64}$')
    ),
    CONSTRAINT ck_workflow_run_snapshot CHECK (
        JSON_TYPE(resolution_snapshot_json) = 'OBJECT'
    ),
    CONSTRAINT ck_workflow_run_runtime CHECK (
        runtime IN (
            'kiro', 'claude-code', 'cursor', 'dify',
            'openai-compatible-agent-runtime'
        )
    ),
    CONSTRAINT ck_workflow_run_status CHECK (
        status IN (
            'queued', 'running', 'awaiting_approval', 'succeeded',
            'failed', 'cancelled', 'timed_out'
        )
    ),
    CONSTRAINT ck_workflow_run_required CHECK (
        CHAR_LENGTH(requested_by) > 0
        AND CHAR_LENGTH(idempotency_key) > 0
        AND CHAR_LENGTH(correlation_id) > 0
        AND timeout_seconds > 0
    ),
    CONSTRAINT ck_workflow_run_output_pair CHECK (
        (output_json IS NULL AND output_hash IS NULL)
        OR (output_json IS NOT NULL AND output_hash IS NOT NULL)
    ),
    CONSTRAINT ck_workflow_run_approval_pair CHECK (
        (approved_at IS NULL AND approved_by IS NULL)
        OR (approved_at IS NOT NULL AND approved_by IS NOT NULL)
    ),
    CONSTRAINT ck_workflow_run_awaiting_gate CHECK (
        status <> 'awaiting_approval'
        OR (approval_gate IS NOT NULL AND CHAR_LENGTH(approval_gate) > 0)
    ),
    CONSTRAINT ck_workflow_run_terminal_time CHECK (
        status NOT IN ('succeeded', 'failed', 'cancelled', 'timed_out')
        OR finished_at IS NOT NULL
    ),
    CONSTRAINT ck_workflow_run_success_output CHECK (
        status <> 'succeeded' OR output_hash IS NOT NULL
    ),
    CONSTRAINT ck_workflow_run_failure_code CHECK (
        status NOT IN ('failed', 'timed_out') OR error_code IS NOT NULL
    ),
    CONSTRAINT ck_workflow_run_time_order CHECK (
        started_at IS NULL OR finished_at IS NULL OR started_at <= finished_at
    ),
    CONSTRAINT ck_workflow_run_row_version CHECK (row_version >= 1)
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE job (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(26) NOT NULL,
    job_kind VARCHAR(64) NOT NULL,
    queue_name VARCHAR(100) NOT NULL,
    workflow_run_id BIGINT UNSIGNED NULL,
    resource_version_id BIGINT UNSIGNED NULL,
    parent_job_id BIGINT UNSIGNED NULL,
    status VARCHAR(32) NOT NULL,
    priority SMALLINT NOT NULL DEFAULT 0,
    attempt INT UNSIGNED NOT NULL DEFAULT 0,
    max_attempts INT UNSIGNED NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    correlation_id VARCHAR(128) NOT NULL,
    payload_json JSON NOT NULL,
    payload_hash VARCHAR(80) NOT NULL,
    result_json JSON NULL,
    result_hash VARCHAR(80) NULL,
    worker_id VARCHAR(160) NULL,
    lease_token_hash VARCHAR(80) NULL,
    available_at DATETIME(6) NOT NULL,
    leased_until DATETIME(6) NULL,
    heartbeat_at DATETIME(6) NULL,
    cancel_requested_at DATETIME(6) NULL,
    started_at DATETIME(6) NULL,
    finished_at DATETIME(6) NULL,
    error_code VARCHAR(100) NULL,
    error_summary VARCHAR(1000) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (id),
    CONSTRAINT uq_job_public_id UNIQUE (public_id),
    CONSTRAINT uq_job_queue_idempotency UNIQUE (queue_name, idempotency_key),
    KEY idx_job_claim (queue_name, status, available_at, priority, id),
    KEY idx_job_lease (status, leased_until),
    KEY idx_job_workflow (workflow_run_id, created_at),
    KEY idx_job_resource (resource_version_id, created_at),
    KEY idx_job_parent (parent_job_id),
    KEY idx_job_correlation (correlation_id),
    CONSTRAINT fk_job_workflow_run FOREIGN KEY (workflow_run_id)
        REFERENCES workflow_run (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_job_resource_version FOREIGN KEY (resource_version_id)
        REFERENCES resource_version (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_job_parent FOREIGN KEY (parent_job_id)
        REFERENCES job (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_job_public_id CHECK (
        public_id REGEXP '^[0-9A-HJKMNP-TV-Z]{26}$'
    ),
    CONSTRAINT ck_job_status CHECK (
        status IN (
            'queued', 'leased', 'running', 'retry_scheduled',
            'succeeded', 'failed', 'cancelled', 'dead_letter'
        )
    ),
    CONSTRAINT ck_job_required CHECK (
        CHAR_LENGTH(job_kind) > 0
        AND CHAR_LENGTH(queue_name) > 0
        AND CHAR_LENGTH(idempotency_key) > 0
        AND CHAR_LENGTH(correlation_id) > 0
        AND max_attempts > 0
        AND attempt <= max_attempts
    ),
    CONSTRAINT ck_job_payload_hash CHECK (
        payload_hash REGEXP '^sha256:[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_job_result_pair CHECK (
        (result_json IS NULL AND result_hash IS NULL)
        OR (
            result_json IS NOT NULL
            AND result_hash REGEXP '^sha256:[0-9a-f]{64}$'
        )
    ),
    -- MySQL forbids CHECK expressions over AUTO_INCREMENT columns.
    -- The self-parent rule is enforced by migration triggers below.
    CONSTRAINT ck_job_lease_fields CHECK (
        status NOT IN ('leased', 'running')
        OR (
            worker_id IS NOT NULL
            AND lease_token_hash REGEXP '^sha256:[0-9a-f]{64}$'
            AND leased_until IS NOT NULL
        )
    ),
    CONSTRAINT ck_job_terminal_time CHECK (
        status NOT IN ('succeeded', 'failed', 'cancelled', 'dead_letter')
        OR finished_at IS NOT NULL
    ),
    CONSTRAINT ck_job_success_result CHECK (
        status <> 'succeeded' OR result_hash IS NOT NULL
    ),
    CONSTRAINT ck_job_failure_code CHECK (
        status NOT IN ('failed', 'dead_letter') OR error_code IS NOT NULL
    ),
    CONSTRAINT ck_job_time_order CHECK (
        started_at IS NULL OR finished_at IS NULL OR started_at <= finished_at
    ),
    CONSTRAINT ck_job_row_version CHECK (row_version >= 1)
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE job_event (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    job_id BIGINT UNSIGNED NOT NULL,
    sequence_no BIGINT UNSIGNED NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    from_status VARCHAR(32) NULL,
    to_status VARCHAR(32) NULL,
    actor_type VARCHAR(32) NOT NULL,
    actor_id VARCHAR(160) NOT NULL,
    payload_json JSON NOT NULL,
    occurred_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    CONSTRAINT uq_job_event_sequence UNIQUE (job_id, sequence_no),
    KEY idx_job_event_time (job_id, occurred_at, id),
    KEY idx_job_event_type (event_type, occurred_at),
    CONSTRAINT fk_job_event_job FOREIGN KEY (job_id)
        REFERENCES job (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_job_event_sequence CHECK (sequence_no >= 1),
    CONSTRAINT ck_job_event_actor_type CHECK (
        actor_type IN ('user', 'service', 'worker', 'system')
    ),
    CONSTRAINT ck_job_event_required CHECK (
        CHAR_LENGTH(event_type) > 0 AND CHAR_LENGTH(actor_id) > 0
    )
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE resource_usage (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(26) NOT NULL,
    resource_version_id BIGINT UNSIGNED NOT NULL,
    workflow_run_id BIGINT UNSIGNED NULL,
    job_id BIGINT UNSIGNED NULL,
    actor_id VARCHAR(160) NOT NULL,
    action VARCHAR(32) NOT NULL,
    runtime VARCHAR(64) NULL,
    adapter_version VARCHAR(64) NULL,
    status VARCHAR(24) NOT NULL,
    request_id VARCHAR(128) NULL,
    correlation_id VARCHAR(128) NOT NULL,
    input_hash VARCHAR(80) NULL,
    output_hash VARCHAR(80) NULL,
    metrics_json JSON NOT NULL,
    error_code VARCHAR(100) NULL,
    started_at DATETIME(6) NOT NULL,
    finished_at DATETIME(6) NULL,
    duration_ms BIGINT UNSIGNED NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    CONSTRAINT uq_resource_usage_public_id UNIQUE (public_id),
    KEY idx_usage_resource_time (resource_version_id, started_at),
    KEY idx_usage_workflow (workflow_run_id, started_at),
    KEY idx_usage_job (job_id),
    KEY idx_usage_actor_time (actor_id, started_at),
    KEY idx_usage_correlation (correlation_id),
    KEY idx_usage_status_time (status, started_at),
    CONSTRAINT fk_usage_resource_version FOREIGN KEY (resource_version_id)
        REFERENCES resource_version (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_usage_workflow_run FOREIGN KEY (workflow_run_id)
        REFERENCES workflow_run (id) ON DELETE SET NULL ON UPDATE RESTRICT,
    CONSTRAINT fk_usage_job FOREIGN KEY (job_id)
        REFERENCES job (id) ON DELETE SET NULL ON UPDATE RESTRICT,
    CONSTRAINT ck_resource_usage_public_id CHECK (
        public_id REGEXP '^[0-9A-HJKMNP-TV-Z]{26}$'
    ),
    CONSTRAINT ck_resource_usage_action CHECK (
        action IN (
            'discover', 'resolve', 'validate', 'execute', 'publish',
            'deprecation_warning'
        )
    ),
    CONSTRAINT ck_resource_usage_runtime CHECK (
        runtime IS NULL OR runtime IN (
            'kiro', 'claude-code', 'cursor', 'dify',
            'openai-compatible-agent-runtime'
        )
    ),
    CONSTRAINT ck_resource_usage_status CHECK (
        status IN ('started', 'succeeded', 'failed', 'denied', 'cancelled')
    ),
    CONSTRAINT ck_resource_usage_hashes CHECK (
        (input_hash IS NULL OR input_hash REGEXP '^sha256:[0-9a-f]{64}$')
        AND (output_hash IS NULL OR output_hash REGEXP '^sha256:[0-9a-f]{64}$')
    ),
    CONSTRAINT ck_resource_usage_required CHECK (
        CHAR_LENGTH(actor_id) > 0 AND CHAR_LENGTH(correlation_id) > 0
    ),
    CONSTRAINT ck_resource_usage_terminal CHECK (
        status = 'started'
        OR (finished_at IS NOT NULL AND duration_ms IS NOT NULL)
    ),
    CONSTRAINT ck_resource_usage_failure CHECK (
        status NOT IN ('failed', 'denied') OR error_code IS NOT NULL
    ),
    CONSTRAINT ck_resource_usage_time_order CHECK (
        finished_at IS NULL OR started_at <= finished_at
    )
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE resource_index_status (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    resource_version_id BIGINT UNSIGNED NOT NULL,
    index_name VARCHAR(100) NOT NULL,
    index_kind VARCHAR(24) NOT NULL,
    status VARCHAR(24) NOT NULL,
    source_artifact_hash VARCHAR(80) NOT NULL,
    index_revision VARCHAR(100) NULL,
    embedding_provider VARCHAR(100) NULL,
    embedding_model VARCHAR(160) NULL,
    embedding_dimensions INT UNSIGNED NULL,
    attempt_count INT UNSIGNED NOT NULL DEFAULT 0,
    last_error_code VARCHAR(100) NULL,
    last_error_message VARCHAR(1000) NULL,
    last_job_id BIGINT UNSIGNED NULL,
    indexed_at DATETIME(6) NULL,
    next_attempt_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (id),
    CONSTRAINT uq_index_status_version_name UNIQUE (
        resource_version_id, index_name
    ),
    KEY idx_index_queue (status, next_attempt_at, id),
    KEY idx_index_name_status (index_name, status),
    KEY idx_index_job (last_job_id),
    CONSTRAINT fk_index_status_version FOREIGN KEY (resource_version_id)
        REFERENCES resource_version (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_index_status_job FOREIGN KEY (last_job_id)
        REFERENCES job (id) ON DELETE SET NULL ON UPDATE RESTRICT,
    CONSTRAINT ck_index_status_kind CHECK (
        index_kind IN ('keyword', 'vector', 'graph', 'catalog')
    ),
    CONSTRAINT ck_index_status_state CHECK (
        status IN ('pending', 'indexing', 'ready', 'failed', 'stale', 'excluded')
    ),
    CONSTRAINT ck_index_status_hash CHECK (
        source_artifact_hash REGEXP '^sha256:[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_index_status_required CHECK (CHAR_LENGTH(index_name) > 0),
    CONSTRAINT ck_index_status_vector_config CHECK (
        index_kind <> 'vector'
        OR (
            embedding_provider IS NOT NULL
            AND embedding_model IS NOT NULL
            AND embedding_dimensions IS NOT NULL
            AND embedding_dimensions > 0
        )
    ),
    CONSTRAINT ck_index_status_ready CHECK (
        status <> 'ready'
        OR (index_revision IS NOT NULL AND indexed_at IS NOT NULL)
    ),
    CONSTRAINT ck_index_status_failed CHECK (
        status <> 'failed' OR last_error_code IS NOT NULL
    ),
    CONSTRAINT ck_index_status_row_version CHECK (row_version >= 1)
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE resource_duplicate_candidate (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(26) NOT NULL,
    left_resource_version_id BIGINT UNSIGNED NOT NULL,
    right_resource_version_id BIGINT UNSIGNED NOT NULL,
    algorithm VARCHAR(100) NOT NULL,
    algorithm_version VARCHAR(64) NOT NULL,
    score DECIMAL(7,6) NOT NULL,
    status VARCHAR(24) NOT NULL,
    evidence_json JSON NOT NULL,
    detected_at DATETIME(6) NOT NULL,
    reviewed_by VARCHAR(160) NULL,
    reviewed_at DATETIME(6) NULL,
    review_rationale TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (id),
    CONSTRAINT uq_duplicate_public_id UNIQUE (public_id),
    CONSTRAINT uq_duplicate_candidate UNIQUE (
        left_resource_version_id,
        right_resource_version_id,
        algorithm,
        algorithm_version
    ),
    KEY idx_duplicate_review (status, score, detected_at),
    KEY idx_duplicate_left (left_resource_version_id, status),
    KEY idx_duplicate_right (right_resource_version_id, status),
    CONSTRAINT fk_duplicate_left_version FOREIGN KEY (left_resource_version_id)
        REFERENCES resource_version (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_duplicate_right_version FOREIGN KEY (right_resource_version_id)
        REFERENCES resource_version (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_duplicate_public_id CHECK (
        public_id REGEXP '^[0-9A-HJKMNP-TV-Z]{26}$'
    ),
    CONSTRAINT ck_duplicate_pair_order CHECK (
        left_resource_version_id < right_resource_version_id
    ),
    CONSTRAINT ck_duplicate_score CHECK (score >= 0 AND score <= 1),
    CONSTRAINT ck_duplicate_status CHECK (
        status IN (
            'pending_review', 'not_duplicate', 'related', 'near_duplicate',
            'exact_duplicate', 'dismissed'
        )
    ),
    CONSTRAINT ck_duplicate_required CHECK (
        CHAR_LENGTH(algorithm) > 0 AND CHAR_LENGTH(algorithm_version) > 0
    ),
    CONSTRAINT ck_duplicate_review_fields CHECK (
        (
            reviewed_at IS NULL
            AND reviewed_by IS NULL
            AND review_rationale IS NULL
        )
        OR (
            reviewed_at IS NOT NULL
            AND reviewed_by IS NOT NULL
            AND review_rationale IS NOT NULL
            AND CHAR_LENGTH(review_rationale) > 0
        )
    ),
    CONSTRAINT ck_duplicate_reviewed_status CHECK (
        status = 'pending_review' OR reviewed_at IS NOT NULL
    ),
    CONSTRAINT ck_duplicate_row_version CHECK (row_version >= 1)
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE resource_evaluation (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(26) NOT NULL,
    resource_version_id BIGINT UNSIGNED NOT NULL,
    workflow_run_id BIGINT UNSIGNED NULL,
    evaluation_kind VARCHAR(32) NOT NULL,
    dataset_id VARCHAR(160) NOT NULL,
    dataset_version VARCHAR(64) NOT NULL,
    dataset_snapshot_hash VARCHAR(80) NOT NULL,
    evaluator_id VARCHAR(160) NOT NULL,
    evaluator_version VARCHAR(64) NOT NULL,
    assessor_type VARCHAR(32) NOT NULL,
    adjudication_status VARCHAR(32) NOT NULL,
    candidate_artifact_hash VARCHAR(80) NOT NULL,
    status VARCHAR(24) NOT NULL,
    metrics_json JSON NOT NULL,
    passed_count INT UNSIGNED NULL,
    failed_count INT UNSIGNED NULL,
    excluded_count INT UNSIGNED NULL,
    result_artifact_ref VARCHAR(500) NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    started_at DATETIME(6) NULL,
    completed_at DATETIME(6) NULL,
    error_code VARCHAR(100) NULL,
    error_summary VARCHAR(1000) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    CONSTRAINT uq_resource_evaluation_public_id UNIQUE (public_id),
    CONSTRAINT uq_resource_evaluation_idempotency UNIQUE (
        resource_version_id, evaluation_kind, idempotency_key
    ),
    KEY idx_eval_resource_kind (
        resource_version_id, evaluation_kind, completed_at
    ),
    KEY idx_eval_dataset (dataset_id, dataset_version, status),
    KEY idx_eval_status (status, created_at),
    KEY idx_eval_workflow_run (workflow_run_id),
    CONSTRAINT fk_evaluation_resource_version FOREIGN KEY (resource_version_id)
        REFERENCES resource_version (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_evaluation_workflow_run FOREIGN KEY (workflow_run_id)
        REFERENCES workflow_run (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_evaluation_public_id CHECK (
        public_id REGEXP '^[0-9A-HJKMNP-TV-Z]{26}$'
    ),
    CONSTRAINT ck_evaluation_kind CHECK (
        evaluation_kind IN (
            'retrieval', 'duplicate', 'recommendation', 'workflow',
            'contract', 'security', 'runtime'
        )
    ),
    CONSTRAINT ck_evaluation_assessor CHECK (
        assessor_type IN ('none', 'human', 'model', 'imported', 'mixed')
    ),
    CONSTRAINT ck_evaluation_adjudication CHECK (
        adjudication_status IN (
            'not_applicable', 'draft', 'single_reviewed', 'adjudicated'
        )
    ),
    CONSTRAINT ck_evaluation_provenance CHECK (
        (assessor_type = 'none' AND adjudication_status = 'not_applicable')
        OR (assessor_type = 'model' AND adjudication_status = 'draft')
        OR assessor_type IN ('human', 'imported', 'mixed')
    ),
    CONSTRAINT ck_evaluation_status CHECK (
        status IN ('queued', 'running', 'passed', 'failed', 'error', 'incomplete')
    ),
    CONSTRAINT ck_evaluation_hashes CHECK (
        dataset_snapshot_hash REGEXP '^sha256:[0-9a-f]{64}$'
        AND candidate_artifact_hash REGEXP '^sha256:[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_evaluation_required CHECK (
        CHAR_LENGTH(dataset_id) > 0
        AND CHAR_LENGTH(dataset_version) > 0
        AND CHAR_LENGTH(evaluator_id) > 0
        AND CHAR_LENGTH(evaluator_version) > 0
        AND CHAR_LENGTH(idempotency_key) > 0
    ),
    CONSTRAINT ck_evaluation_counts CHECK (
        (passed_count IS NULL AND failed_count IS NULL AND excluded_count IS NULL)
        OR (passed_count IS NOT NULL AND failed_count IS NOT NULL AND excluded_count IS NOT NULL)
    ),
    CONSTRAINT ck_evaluation_terminal_time CHECK (
        status NOT IN ('passed', 'failed', 'error', 'incomplete')
        OR completed_at IS NOT NULL
    ),
    CONSTRAINT ck_evaluation_error CHECK (
        status <> 'error' OR error_code IS NOT NULL
    ),
    CONSTRAINT ck_evaluation_time_order CHECK (
        started_at IS NULL OR completed_at IS NULL OR started_at <= completed_at
    )
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE audit_log (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(26) NOT NULL,
    occurred_at DATETIME(6) NOT NULL,
    actor_type VARCHAR(32) NOT NULL,
    actor_id VARCHAR(160) NOT NULL,
    action VARCHAR(100) NOT NULL,
    outcome VARCHAR(24) NOT NULL,
    entity_type VARCHAR(64) NOT NULL,
    entity_public_id VARCHAR(160) NOT NULL,
    resource_version_id BIGINT UNSIGNED NULL,
    request_id VARCHAR(128) NULL,
    correlation_id VARCHAR(128) NOT NULL,
    from_status VARCHAR(32) NULL,
    to_status VARCHAR(32) NULL,
    artifact_hash VARCHAR(80) NULL,
    manifest_snapshot_hash_before VARCHAR(80) NULL,
    manifest_snapshot_hash_after VARCHAR(80) NULL,
    reason_code VARCHAR(100) NOT NULL,
    reason_summary VARCHAR(2000) NOT NULL,
    evidence_refs_json JSON NOT NULL,
    metadata_json JSON NOT NULL,
    previous_event_hash VARCHAR(80) NULL,
    event_hash VARCHAR(80) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    CONSTRAINT uq_audit_log_public_id UNIQUE (public_id),
    CONSTRAINT uq_audit_log_event_hash UNIQUE (event_hash),
    KEY idx_audit_entity (entity_type, entity_public_id, occurred_at),
    KEY idx_audit_resource (resource_version_id, occurred_at),
    KEY idx_audit_request (request_id),
    KEY idx_audit_correlation (correlation_id),
    KEY idx_audit_action_outcome (action, outcome, occurred_at),
    CONSTRAINT fk_audit_resource_version FOREIGN KEY (resource_version_id)
        REFERENCES resource_version (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_audit_public_id CHECK (
        public_id REGEXP '^[0-9A-HJKMNP-TV-Z]{26}$'
    ),
    CONSTRAINT ck_audit_outcome CHECK (
        outcome IN ('succeeded', 'rejected', 'failed')
    ),
    CONSTRAINT ck_audit_hashes CHECK (
        (artifact_hash IS NULL OR artifact_hash REGEXP '^sha256:[0-9a-f]{64}$')
        AND (
            manifest_snapshot_hash_before IS NULL
            OR manifest_snapshot_hash_before REGEXP '^sha256:[0-9a-f]{64}$'
        )
        AND (
            manifest_snapshot_hash_after IS NULL
            OR manifest_snapshot_hash_after REGEXP '^sha256:[0-9a-f]{64}$'
        )
        AND (
            previous_event_hash IS NULL
            OR previous_event_hash REGEXP '^sha256:[0-9a-f]{64}$'
        )
        AND event_hash REGEXP '^sha256:[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_audit_json CHECK (
        JSON_TYPE(evidence_refs_json) = 'ARRAY'
        AND JSON_TYPE(metadata_json) = 'OBJECT'
    ),
    CONSTRAINT ck_audit_required CHECK (
        CHAR_LENGTH(actor_type) > 0
        AND CHAR_LENGTH(actor_id) > 0
        AND CHAR_LENGTH(action) > 0
        AND CHAR_LENGTH(entity_type) > 0
        AND CHAR_LENGTH(entity_public_id) > 0
        AND CHAR_LENGTH(correlation_id) > 0
        AND CHAR_LENGTH(reason_code) > 0
        AND CHAR_LENGTH(reason_summary) > 0
    )
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

-- MySQL 8 rejects CHECK constraints that reference AUTO_INCREMENT columns.
-- These triggers close that engine-specific gap for the two self-FK rules.
DELIMITER $$

CREATE TRIGGER trg_rv_no_self_replacement_insert
AFTER INSERT ON resource_version
FOR EACH ROW
BEGIN
    IF NEW.replacement_version_id IS NOT NULL
       AND NEW.replacement_version_id = NEW.id THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'resource_version cannot replace itself';
    END IF;
END$$

CREATE TRIGGER trg_rv_no_self_replacement_update
BEFORE UPDATE ON resource_version
FOR EACH ROW
BEGIN
    IF NEW.replacement_version_id IS NOT NULL
       AND NEW.replacement_version_id = NEW.id THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'resource_version cannot replace itself';
    END IF;
END$$

CREATE TRIGGER trg_job_no_self_parent_insert
AFTER INSERT ON job
FOR EACH ROW
BEGIN
    IF NEW.parent_job_id IS NOT NULL AND NEW.parent_job_id = NEW.id THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'job cannot be its own parent';
    END IF;
END$$

CREATE TRIGGER trg_job_no_self_parent_update
BEFORE UPDATE ON job
FOR EACH ROW
BEGIN
    IF NEW.parent_job_id IS NOT NULL AND NEW.parent_job_id = NEW.id THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'job cannot be its own parent';
    END IF;
END$$

DELIMITER ;
