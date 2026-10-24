-- SuperCoder Stage 1 relational extensions
-- Migration: 0002_stage1_extensions
-- Target: MySQL 8.0.16+
--
-- Adds the Stage 1 governance projections that extend the frozen Stage 0
-- contract. This forward-only migration contains no DROP statements.

SET NAMES utf8mb4 COLLATE utf8mb4_0900_as_cs;

CREATE TABLE user_identity_projection (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    subject_id VARCHAR(160) NOT NULL,
    issuer VARCHAR(160) NOT NULL,
    external_subject VARCHAR(255) NOT NULL,
    display_name VARCHAR(200) NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    directory_revision VARCHAR(128) NOT NULL,
    valid_from DATETIME(6) NOT NULL,
    valid_until DATETIME(6) NULL,
    refreshed_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (id),
    CONSTRAINT uq_user_identity_subject UNIQUE (subject_id),
    CONSTRAINT uq_user_identity_external UNIQUE (issuer, external_subject),
    KEY idx_user_identity_active (is_active, valid_until, refreshed_at),
    CONSTRAINT ck_user_identity_required CHECK (
        CHAR_LENGTH(subject_id) > 0
        AND CHAR_LENGTH(issuer) > 0
        AND CHAR_LENGTH(external_subject) > 0
        AND CHAR_LENGTH(directory_revision) > 0
    ),
    CONSTRAINT ck_user_identity_validity CHECK (
        valid_until IS NULL OR valid_from < valid_until
    ),
    CONSTRAINT ck_user_identity_row_version CHECK (row_version >= 1)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE team_identity_projection (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    team_id VARCHAR(160) NOT NULL,
    display_name VARCHAR(200) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    directory_revision VARCHAR(128) NOT NULL,
    valid_from DATETIME(6) NOT NULL,
    valid_until DATETIME(6) NULL,
    refreshed_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (id),
    CONSTRAINT uq_team_identity_team UNIQUE (team_id),
    KEY idx_team_identity_active (is_active, valid_until, refreshed_at),
    CONSTRAINT ck_team_identity_required CHECK (
        CHAR_LENGTH(team_id) > 0
        AND CHAR_LENGTH(display_name) > 0
        AND CHAR_LENGTH(directory_revision) > 0
    ),
    CONSTRAINT ck_team_identity_validity CHECK (
        valid_until IS NULL OR valid_from < valid_until
    ),
    CONSTRAINT ck_team_identity_row_version CHECK (row_version >= 1)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE team_membership_projection (
    user_identity_id BIGINT UNSIGNED NOT NULL,
    team_identity_id BIGINT UNSIGNED NOT NULL,
    ui_role VARCHAR(32) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    source_revision VARCHAR(128) NOT NULL,
    valid_from DATETIME(6) NOT NULL,
    valid_until DATETIME(6) NULL,
    refreshed_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (user_identity_id, team_identity_id),
    KEY idx_team_membership_team (team_identity_id, is_active, valid_until),
    KEY idx_team_membership_user (user_identity_id, is_active, valid_until),
    CONSTRAINT ck_team_membership_ui_role CHECK (
        ui_role IN ('member', 'maintainer', 'administrator')
    ),
    CONSTRAINT ck_team_membership_revision CHECK (CHAR_LENGTH(source_revision) > 0),
    CONSTRAINT ck_team_membership_validity CHECK (
        valid_until IS NULL OR valid_from < valid_until
    ),
    CONSTRAINT ck_team_membership_row_version CHECK (row_version >= 1),
    CONSTRAINT fk_team_membership_user FOREIGN KEY (user_identity_id)
        REFERENCES user_identity_projection (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_team_membership_team FOREIGN KEY (team_identity_id)
        REFERENCES team_identity_projection (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE role_capability_projection (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    policy_version VARCHAR(64) NOT NULL,
    ui_role VARCHAR(32) NOT NULL,
    governance_role VARCHAR(32) NOT NULL,
    action VARCHAR(32) NOT NULL,
    effect VARCHAR(16) NOT NULL,
    requires_team BOOLEAN NOT NULL DEFAULT TRUE,
    requires_ownership BOOLEAN NOT NULL DEFAULT FALSE,
    requires_review_independence BOOLEAN NOT NULL DEFAULT FALSE,
    source_revision VARCHAR(128) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from DATETIME(6) NOT NULL,
    valid_until DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (id),
    CONSTRAINT uq_role_capability_policy UNIQUE (
        policy_version, ui_role, governance_role, action
    ),
    KEY idx_role_capability_lookup (policy_version, ui_role, action, effect),
    CONSTRAINT ck_role_capability_ui_role CHECK (
        ui_role IN ('member', 'maintainer', 'administrator')
    ),
    CONSTRAINT ck_role_capability_governance_role CHECK (
        governance_role IN ('owner', 'validator', 'reviewer', 'publisher')
    ),
    CONSTRAINT ck_role_capability_action CHECK (
        action IN ('read', 'create', 'edit', 'submit', 'review', 'publish',
                   'deprecate', 'retire', 'rollback')
    ),
    CONSTRAINT ck_role_capability_effect CHECK (effect IN ('allow', 'deny')),
    CONSTRAINT ck_role_capability_required CHECK (
        CHAR_LENGTH(policy_version) > 0
        AND CHAR_LENGTH(source_revision) > 0
    ),
    CONSTRAINT ck_role_capability_row_version CHECK (row_version >= 1)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE resource_permission (
    resource_version_id BIGINT UNSIGNED NOT NULL,
    ordinal SMALLINT UNSIGNED NOT NULL,
    permission_name VARCHAR(200) NOT NULL,
    scope VARCHAR(32) NULL,
    effect VARCHAR(16) NOT NULL DEFAULT 'allow',
    approval_required BOOLEAN NOT NULL DEFAULT FALSE,
    reason VARCHAR(500) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (resource_version_id, ordinal),
    KEY idx_resource_permission_lookup (
        permission_name, scope, effect, resource_version_id
    ),
    CONSTRAINT ck_resource_permission_name CHECK (
        permission_name REGEXP '^[a-z][a-z0-9_.-]*:[a-z][a-z0-9_.-]*$'
    ),
    CONSTRAINT ck_resource_permission_scope CHECK (
        scope IS NULL OR scope IN ('resource', 'project', 'team',
                                   'organization', 'external')
    ),
    CONSTRAINT ck_resource_permission_effect CHECK (effect IN ('allow', 'deny')),
    CONSTRAINT ck_resource_permission_reason CHECK (
        reason IS NULL OR CHAR_LENGTH(reason) > 0
    ),
    CONSTRAINT ck_resource_permission_row_version CHECK (row_version >= 1),
    CONSTRAINT fk_resource_permission_version FOREIGN KEY (resource_version_id)
        REFERENCES resource_version (id)
        ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE authorization_grant_projection (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id CHAR(26) NOT NULL,
    principal_type VARCHAR(16) NOT NULL,
    user_identity_id BIGINT UNSIGNED NULL,
    team_identity_id BIGINT UNSIGNED NULL,
    permission_name VARCHAR(200) NOT NULL,
    scope VARCHAR(32) NOT NULL,
    effect VARCHAR(16) NOT NULL,
    resource_id BIGINT UNSIGNED NULL,
    owner_team VARCHAR(160) NULL,
    principal_key VARCHAR(64)
        GENERATED ALWAYS AS (
            CASE
                WHEN principal_type = 'user'
                    THEN CONCAT('user:', CAST(user_identity_id AS CHAR))
                ELSE CONCAT('team:', CAST(team_identity_id AS CHAR))
            END
        ) STORED NOT NULL,
    scope_target_key VARCHAR(200)
        GENERATED ALWAYS AS (
            CASE
                WHEN scope = 'resource'
                    THEN CONCAT('resource:', CAST(resource_id AS CHAR))
                WHEN scope = 'team'
                    THEN CONCAT('team:', owner_team)
                ELSE CONCAT(scope, ':*')
            END
        ) STORED NOT NULL,
    source_revision VARCHAR(128) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from DATETIME(6) NOT NULL,
    valid_until DATETIME(6) NULL,
    revoked_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (id),
    CONSTRAINT uq_authorization_grant_public_id UNIQUE (public_id),
    CONSTRAINT uq_authorization_grant_fact UNIQUE (
        principal_key, permission_name, scope_target_key, source_revision
    ),
    KEY idx_authorization_grant_user (
        user_identity_id, permission_name, scope, effect, is_active
    ),
    KEY idx_authorization_grant_resource (resource_id, permission_name, effect),
    KEY idx_authorization_grant_team (
        team_identity_id, permission_name, scope, effect, is_active
    ),
    CONSTRAINT ck_authorization_grant_public_id CHECK (
        public_id REGEXP '^[0-9A-HJKMNP-TV-Z]{26}$'
    ),
    CONSTRAINT ck_authorization_grant_principal_type CHECK (
        principal_type IN ('user', 'team')
    ),
    CONSTRAINT ck_authorization_grant_principal CHECK (
        (principal_type = 'user'
            AND user_identity_id IS NOT NULL
            AND team_identity_id IS NULL)
        OR (principal_type = 'team'
            AND team_identity_id IS NOT NULL
            AND user_identity_id IS NULL)
    ),
    CONSTRAINT ck_authorization_grant_permission CHECK (
        permission_name REGEXP '^[a-z][a-z0-9_.-]*:[a-z][a-z0-9_.-]*$'
    ),
    CONSTRAINT ck_authorization_grant_scope CHECK (
        scope IN ('resource', 'project', 'team', 'organization', 'external')
    ),
    CONSTRAINT ck_authorization_grant_effect CHECK (effect IN ('allow', 'deny')),
    CONSTRAINT ck_authorization_grant_scope_target CHECK (
        (scope <> 'resource' OR resource_id IS NOT NULL)
        AND (scope <> 'team' OR owner_team IS NOT NULL)
    ),
    CONSTRAINT ck_authorization_grant_required CHECK (
        CHAR_LENGTH(source_revision) > 0
        AND (owner_team IS NULL OR CHAR_LENGTH(owner_team) > 0)
    ),
    CONSTRAINT ck_authorization_grant_validity CHECK (
        valid_until IS NULL OR valid_from < valid_until
    ),
    CONSTRAINT ck_authorization_grant_revocation CHECK (
        revoked_at IS NULL OR revoked_at >= valid_from
    ),
    CONSTRAINT ck_authorization_grant_row_version CHECK (row_version >= 1),
    CONSTRAINT fk_authorization_grant_user FOREIGN KEY (user_identity_id)
        REFERENCES user_identity_projection (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_authorization_grant_team FOREIGN KEY (team_identity_id)
        REFERENCES team_identity_projection (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_authorization_grant_resource FOREIGN KEY (resource_id)
        REFERENCES resource (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_as_cs;
