"""SQLAlchemy 2 mappings for core resource governance facts.

The six Stage 0 tables are mapped directly from
``infra/migrations/0001_stage0_contract.sql``. ``resource_permission`` is the
Stage 1 normalized projection of the frozen manifest ``permissions_json``
declarations requested by the Stage 1 architecture; the JSON snapshot remains
the immutable contract evidence. This module declares metadata only and never
creates or migrates a database on import.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    FetchedValue,
    ForeignKey,
    Index,
    MetaData,
    PrimaryKeyConstraint,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, JSON, SMALLINT, TEXT, VARCHAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = [
    "Base",
    "Resource",
    "ResourceCategory",
    "ResourceDependency",
    "ResourcePermission",
    "ResourceRuntime",
    "ResourceTag",
    "ResourceVersion",
]

MYSQL_TABLE_OPTIONS: Final[dict[str, str]] = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_as_cs",
}

metadata = MetaData()


class Base(DeclarativeBase):
    """Shared declarative base for all Stage 1 MySQL mappings."""

    metadata = metadata


class Resource(Base):
    """Stable resource identity; lifecycle state belongs to ResourceVersion."""

    __tablename__ = "resource"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_resource_public_id"),
        UniqueConstraint("kind", "slug", name="uq_resource_kind_slug"),
        Index("idx_resource_owner", "owner_team", "kind"),
        Index("idx_resource_created", "created_at", "id"),
        CheckConstraint(
            "public_id REGEXP '^[0-9A-HJKMNP-TV-Z]{26}$'",
            name="ck_resource_public_id",
        ),
        CheckConstraint(
            "kind IN ('skill', 'mcp', 'prompt', 'agent', 'workflow', 'tool')",
            name="ck_resource_kind",
        ),
        CheckConstraint(
            "slug REGEXP '^[a-z0-9]+([._-][a-z0-9]+)*$'",
            name="ck_resource_slug",
        ),
        CheckConstraint("CHAR_LENGTH(owner_team) > 0", name="ck_resource_owner_nonempty"),
        CheckConstraint("CHAR_LENGTH(created_by) > 0", name="ck_resource_creator_nonempty"),
        CheckConstraint("row_version >= 1", name="ck_resource_row_version"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), primary_key=True, autoincrement=True
    )
    public_id: Mapped[str] = mapped_column(CHAR(26), nullable=False)
    kind: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    slug: Mapped[str] = mapped_column(VARCHAR(160), nullable=False)
    owner_team: Mapped[str] = mapped_column(VARCHAR(160), nullable=False)
    created_by: Mapped[str] = mapped_column(VARCHAR(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        nullable=False,
        server_default=text(
            "CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"
        ),
        server_onupdate=FetchedValue(),
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )

    __mapper_args__ = {
        "version_id_col": row_version,
        "version_id_generator": lambda current: (current or 0) + 1,
    }


class ResourceVersion(Base):
    """Exact immutable artifact identity and authoritative lifecycle record."""

    __tablename__ = "resource_version"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_rv_public_id"),
        UniqueConstraint("resource_id", "version", name="uq_rv_resource_version"),
        Index("idx_rv_resource_status", "resource_id", "status", "version"),
        Index("idx_rv_status_changed", "status", "status_changed_at", "id"),
        Index("idx_rv_artifact_hash", "artifact_hash"),
        Index("idx_rv_replacement", "replacement_version_id"),
        Index("idx_rv_owner_status", "owner_team", "status"),
        CheckConstraint(
            "public_id REGEXP '^[0-9A-HJKMNP-TV-Z]{26}$'",
            name="ck_rv_public_id",
        ),
        CheckConstraint("schema_version = '1'", name="ck_rv_schema_version"),
        CheckConstraint(
            r"version REGEXP '^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\."
            r"(0|[1-9][0-9]*)(-[0-9A-Za-z-]+(\\.[0-9A-Za-z-]+)*)?"
            r"(\\+[0-9A-Za-z-]+(\\.[0-9A-Za-z-]+)*)?$'",
            name="ck_rv_semver",
        ),
        CheckConstraint(
            "status IN ('draft', 'validating', 'pending_review', 'approved', "
            "'published', 'deprecated', 'retired')",
            name="ck_rv_status",
        ),
        CheckConstraint(
            "artifact_hash REGEXP '^sha256:[0-9a-f]{64}$'",
            name="ck_rv_artifact_hash",
        ),
        CheckConstraint(
            "manifest_snapshot_hash REGEXP '^sha256:[0-9a-f]{64}$'",
            name="ck_rv_manifest_hash",
        ),
        CheckConstraint(
            "JSON_TYPE(aliases_json) = 'ARRAY' "
            "AND JSON_TYPE(inputs_json) = 'ARRAY' "
            "AND JSON_TYPE(outputs_json) = 'ARRAY' "
            "AND JSON_TYPE(permissions_json) = 'ARRAY' "
            "AND JSON_TYPE(type_spec_json) = 'OBJECT' "
            "AND JSON_TYPE(manifest_json) = 'OBJECT'",
            name="ck_rv_json_shapes",
        ),
        CheckConstraint(
            "CHAR_LENGTH(name) > 0 "
            "AND CHAR_LENGTH(description) > 0 "
            "AND CHAR_LENGTH(owner_team) > 0 "
            "AND CHAR_LENGTH(entrypoint) > 0 "
            "AND LEFT(entrypoint, 1) NOT IN ('/', '\\\\') "
            "AND entrypoint NOT LIKE '%..%' "
            "AND CHAR_LENGTH(status_changed_by) > 0 "
            "AND CHAR_LENGTH(status_reason) > 0 "
            "AND CHAR_LENGTH(created_by) > 0",
            name="ck_rv_required_text",
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'published', 'deprecated', 'retired') "
            "OR validated_at IS NOT NULL",
            name="ck_rv_validated_state_time",
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'published', 'deprecated', 'retired') "
            "OR approved_at IS NOT NULL",
            name="ck_rv_approved_state_time",
        ),
        CheckConstraint(
            "status NOT IN ('published', 'deprecated', 'retired') "
            "OR published_at IS NOT NULL",
            name="ck_rv_published_state_time",
        ),
        CheckConstraint(
            "status NOT IN ('deprecated', 'retired') OR deprecated_at IS NOT NULL",
            name="ck_rv_deprecated_state_time",
        ),
        CheckConstraint(
            "status <> 'retired' OR retired_at IS NOT NULL",
            name="ck_rv_retired_state_time",
        ),
        CheckConstraint(
            "(validated_at IS NULL OR approved_at IS NULL OR validated_at <= approved_at) "
            "AND (approved_at IS NULL OR published_at IS NULL OR approved_at <= published_at) "
            "AND (published_at IS NULL OR deprecated_at IS NULL "
            "OR published_at <= deprecated_at) "
            "AND (deprecated_at IS NULL OR retired_at IS NULL "
            "OR deprecated_at <= retired_at)",
            name="ck_rv_time_order",
        ),
        CheckConstraint("row_version >= 1", name="ck_rv_row_version"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), primary_key=True, autoincrement=True
    )
    public_id: Mapped[str] = mapped_column(CHAR(26), nullable=False)
    resource_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "resource.id",
            name="fk_rv_resource",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    schema_version: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    version: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    name: Mapped[str] = mapped_column(VARCHAR(200), nullable=False)
    description: Mapped[str] = mapped_column(TEXT, nullable=False)
    owner_team: Mapped[str] = mapped_column(VARCHAR(160), nullable=False)
    entrypoint: Mapped[str] = mapped_column(VARCHAR(500), nullable=False)
    aliases_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    inputs_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    outputs_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    permissions_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    type_spec_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    artifact_hash: Mapped[str] = mapped_column(VARCHAR(80), nullable=False)
    manifest_snapshot_hash: Mapped[str] = mapped_column(VARCHAR(80), nullable=False)
    status: Mapped[str] = mapped_column(
        VARCHAR(32), nullable=False, server_default=text("'draft'")
    )
    status_changed_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    status_changed_by: Mapped[str] = mapped_column(VARCHAR(160), nullable=False)
    status_reason: Mapped[str] = mapped_column(VARCHAR(2000), nullable=False)
    validated_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    deprecated_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    retire_after: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    replacement_version_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "resource_version.id",
            name="fk_rv_replacement",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    created_by: Mapped[str] = mapped_column(VARCHAR(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        nullable=False,
        server_default=text(
            "CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"
        ),
        server_onupdate=FetchedValue(),
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )

    __mapper_args__ = {
        "version_id_col": row_version,
        "version_id_generator": lambda current: (current or 0) + 1,
    }


class ResourceTag(Base):
    """Ordered, normalized tag projection for an exact resource version."""

    __tablename__ = "resource_tag"
    __table_args__ = (
        PrimaryKeyConstraint(
            "resource_version_id", "normalized_tag", name="pk_resource_tag"
        ),
        UniqueConstraint(
            "resource_version_id", "ordinal", name="uq_resource_tag_ordinal"
        ),
        Index("idx_resource_tag_lookup", "normalized_tag", "resource_version_id"),
        CheckConstraint(
            "CHAR_LENGTH(tag) > 0 AND CHAR_LENGTH(normalized_tag) > 0",
            name="ck_resource_tag_nonempty",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    resource_version_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "resource_version.id",
            name="fk_resource_tag_version",
            ondelete="CASCADE",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    tag: Mapped[str] = mapped_column(VARCHAR(100), nullable=False)
    normalized_tag: Mapped[str] = mapped_column(VARCHAR(100), nullable=False)
    ordinal: Mapped[int] = mapped_column(SMALLINT(unsigned=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )


class ResourceCategory(Base):
    """Ordered, normalized category-path projection for a resource version."""

    __tablename__ = "resource_category"
    __table_args__ = (
        PrimaryKeyConstraint(
            "resource_version_id", "normalized_category", name="pk_resource_category"
        ),
        UniqueConstraint(
            "resource_version_id", "ordinal", name="uq_resource_category_ordinal"
        ),
        Index(
            "idx_resource_category_lookup", "normalized_category", "resource_version_id"
        ),
        CheckConstraint(
            "category REGEXP "
            "'^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$' "
            "AND normalized_category REGEXP "
            "'^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)*$'",
            name="ck_resource_category_format",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    resource_version_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "resource_version.id",
            name="fk_resource_category_version",
            ondelete="CASCADE",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(VARCHAR(160), nullable=False)
    normalized_category: Mapped[str] = mapped_column(VARCHAR(160), nullable=False)
    ordinal: Mapped[int] = mapped_column(SMALLINT(unsigned=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )


class ResourceRuntime(Base):
    """Declared runtime and hash-bound compatibility-validation projection."""

    __tablename__ = "resource_runtime"
    __table_args__ = (
        PrimaryKeyConstraint(
            "resource_version_id", "runtime", name="pk_resource_runtime"
        ),
        UniqueConstraint(
            "resource_version_id", "ordinal", name="uq_resource_runtime_ordinal"
        ),
        Index("idx_runtime_compat", "runtime", "validation_status", "resource_version_id"),
        CheckConstraint(
            "runtime IN ('kiro', 'claude-code', 'cursor', 'dify', "
            "'openai-compatible-agent-runtime')",
            name="ck_resource_runtime_name",
        ),
        CheckConstraint(
            "validation_status IN "
            "('unvalidated', 'validating', 'validated', 'failed', 'stale')",
            name="ck_resource_runtime_status",
        ),
        CheckConstraint(
            "validated_artifact_hash IS NULL OR "
            "validated_artifact_hash REGEXP '^sha256:[0-9a-f]{64}$'",
            name="ck_resource_runtime_hash",
        ),
        CheckConstraint(
            "validation_status <> 'validated' OR (adapter_version IS NOT NULL "
            "AND validated_artifact_hash IS NOT NULL "
            "AND validation_evidence_ref IS NOT NULL AND validated_at IS NOT NULL)",
            name="ck_resource_runtime_validated",
        ),
        CheckConstraint("row_version >= 1", name="ck_resource_runtime_row_version"),
        MYSQL_TABLE_OPTIONS,
    )

    resource_version_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "resource_version.id",
            name="fk_resource_runtime_version",
            ondelete="CASCADE",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    runtime: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(SMALLINT(unsigned=True), nullable=False)
    validation_status: Mapped[str] = mapped_column(
        VARCHAR(24), nullable=False, server_default=text("'unvalidated'")
    )
    adapter_version: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    validated_artifact_hash: Mapped[str | None] = mapped_column(VARCHAR(80), nullable=True)
    validation_evidence_ref: Mapped[str | None] = mapped_column(VARCHAR(500), nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        nullable=False,
        server_default=text(
            "CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"
        ),
        server_onupdate=FetchedValue(),
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )

    __mapper_args__ = {
        "version_id_col": row_version,
        "version_id_generator": lambda current: (current or 0) + 1,
    }


class ResourcePermission(Base):
    """Ordered relational projection of one frozen manifest permission entry.

    An omitted scope stays NULL and therefore cannot grant a scoped operation;
    policy evaluation must infer a unique narrow scope or fail closed. A NULL
    scope on a deny declaration remains a capability-wide explicit denial.
    """

    __tablename__ = "resource_permission"
    __table_args__ = (
        PrimaryKeyConstraint(
            "resource_version_id", "ordinal", name="pk_resource_permission"
        ),
        Index(
            "idx_resource_permission_lookup",
            "permission_name",
            "scope",
            "effect",
            "resource_version_id",
        ),
        CheckConstraint(
            "permission_name REGEXP "
            "'^[a-z][a-z0-9_.-]*:[a-z][a-z0-9_.-]*$'",
            name="ck_resource_permission_name",
        ),
        CheckConstraint(
            "scope IS NULL OR scope IN "
            "('resource', 'project', 'team', 'organization', 'external')",
            name="ck_resource_permission_scope",
        ),
        CheckConstraint(
            "effect IN ('allow', 'deny')", name="ck_resource_permission_effect"
        ),
        CheckConstraint(
            "reason IS NULL OR CHAR_LENGTH(reason) > 0",
            name="ck_resource_permission_reason",
        ),
        CheckConstraint("row_version >= 1", name="ck_resource_permission_row_version"),
        MYSQL_TABLE_OPTIONS,
    )

    resource_version_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "resource_version.id",
            name="fk_resource_permission_version",
            ondelete="CASCADE",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(SMALLINT(unsigned=True), nullable=False)
    permission_name: Mapped[str] = mapped_column(VARCHAR(200), nullable=False)
    scope: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True)
    effect: Mapped[str] = mapped_column(
        VARCHAR(16), nullable=False, server_default=text("'allow'")
    )
    approval_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    reason: Mapped[str | None] = mapped_column(VARCHAR(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        nullable=False,
        server_default=text(
            "CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"
        ),
        server_onupdate=FetchedValue(),
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )

    __mapper_args__ = {
        "version_id_col": row_version,
        "version_id_generator": lambda current: (current or 0) + 1,
    }


class ResourceDependency(Base):
    """Declared dependency and its exact managed or pinned external resolution."""

    __tablename__ = "resource_dependency"
    __table_args__ = (
        UniqueConstraint(
            "resource_version_id",
            "dependency_kind",
            "dependency_slug",
            name="uq_dependency_identity",
        ),
        UniqueConstraint(
            "resource_version_id", "ordinal", name="uq_dependency_ordinal"
        ),
        Index(
            "idx_dependency_reverse",
            "resolved_resource_version_id",
            "resource_version_id",
        ),
        Index("idx_dependency_resolution", "resolution_status", "resource_version_id"),
        Index("idx_dependency_declared", "dependency_kind", "dependency_slug"),
        CheckConstraint(
            "dependency_kind IN "
            "('skill', 'mcp', 'prompt', 'agent', 'workflow', 'tool', 'external')",
            name="ck_dependency_kind",
        ),
        CheckConstraint("source IN ('managed', 'external')", name="ck_dependency_source"),
        CheckConstraint(
            "resolution_status IN ('unresolved', 'resolved', 'failed', 'stale')",
            name="ck_dependency_resolution_status",
        ),
        CheckConstraint(
            "CHAR_LENGTH(dependency_slug) > 0 "
            "AND CHAR_LENGTH(version_constraint) > 0",
            name="ck_dependency_slug_nonempty",
        ),
        CheckConstraint(
            "resolved_resource_version_id IS NULL "
            "OR resolved_resource_version_id <> resource_version_id",
            name="ck_dependency_not_self",
        ),
        CheckConstraint(
            "(source = 'managed' AND dependency_kind <> 'external' "
            "AND external_locator IS NULL AND external_digest IS NULL) "
            "OR (source = 'external' AND dependency_kind = 'external' "
            "AND resolved_resource_version_id IS NULL "
            "AND external_locator IS NOT NULL AND external_digest IS NOT NULL "
            "AND CHAR_LENGTH(external_locator) > 0 "
            "AND CHAR_LENGTH(external_digest) > 0)",
            name="ck_dependency_source_fields",
        ),
        CheckConstraint(
            "resolution_status <> 'resolved' OR "
            "((source = 'managed' AND resolved_resource_version_id IS NOT NULL) "
            "OR (source = 'external' AND external_digest IS NOT NULL))",
            name="ck_dependency_resolved_target",
        ),
        CheckConstraint(
            "resolution_status <> 'failed' OR resolution_error_code IS NOT NULL",
            name="ck_dependency_error",
        ),
        CheckConstraint("row_version >= 1", name="ck_dependency_row_version"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), primary_key=True, autoincrement=True
    )
    resource_version_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "resource_version.id",
            name="fk_dependency_owner_version",
            ondelete="CASCADE",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(SMALLINT(unsigned=True), nullable=False)
    dependency_kind: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    dependency_slug: Mapped[str] = mapped_column(VARCHAR(160), nullable=False)
    version_constraint: Mapped[str] = mapped_column(VARCHAR(255), nullable=False)
    source: Mapped[str] = mapped_column(VARCHAR(16), nullable=False)
    is_optional: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    purpose: Mapped[str | None] = mapped_column(VARCHAR(500), nullable=True)
    resolved_resource_version_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "resource_version.id",
            name="fk_dependency_resolved_version",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    external_locator: Mapped[str | None] = mapped_column(VARCHAR(1000), nullable=True)
    external_digest: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    resolution_status: Mapped[str] = mapped_column(
        VARCHAR(24), nullable=False, server_default=text("'unresolved'")
    )
    resolution_error_code: Mapped[str | None] = mapped_column(VARCHAR(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        nullable=False,
        server_default=text(
            "CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"
        ),
        server_onupdate=FetchedValue(),
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )

    __mapper_args__ = {
        "version_id_col": row_version,
        "version_id_generator": lambda current: (current or 0) + 1,
    }
