"""Non-destructive MySQL connectivity and schema conformance checks.

These helpers only inspect; they never migrate, repair, seed, or alter a
database.  A failed check returns a structured result rather than raising,
so the readiness endpoint can report component state honestly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import Engine, inspect, select, text

from .models_governance import AuditLog, Job, ResourceReview
from .models_resource import Resource, ResourceVersion

__all__ = ["HealthReport", "check_database", "check_schema_conformance"]

_EXPECTED_TABLES = frozenset(
    {
        "resource",
        "resource_version",
        "resource_tag",
        "resource_category",
        "resource_runtime",
        "resource_dependency",
        "resource_review",
        "resource_usage",
        "resource_index_status",
        "resource_duplicate_candidate",
        "resource_evaluation",
        "workflow_definition",
        "workflow_run",
        "job",
        "job_event",
        "audit_log",
    }
)


@dataclass(frozen=True, slots=True)
class HealthReport:
    available: bool
    checked_at: str
    version: str | None = None
    missing_tables: tuple[str, ...] = ()
    error: str | None = None


def check_database(engine: Engine | None) -> HealthReport:
    """Probe connectivity and read-only conformance of the relational store."""

    checked_at = datetime.now(timezone.utc).isoformat()
    if engine is None:
        return HealthReport(available=False, checked_at=checked_at, error="engine not configured")

    try:
        with engine.connect() as connection:
            version = connection.execute(text("SELECT VERSION()")).scalar()
            tables = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT TABLE_NAME FROM information_schema.TABLES "
                        "WHERE TABLE_SCHEMA = DATABASE()"
                    )
                )
            }
            missing = tuple(sorted(_EXPECTED_TABLES - tables))
            return HealthReport(
                available=True,
                checked_at=checked_at,
                version=str(version),
                missing_tables=missing,
            )
    except Exception as error:  # pragma: no cover - depends on driver/runtime
        return HealthReport(
            available=False,
            checked_at=checked_at,
            error=f"{type(error).__name__}: {error}",
        )


def check_schema_conformance(engine: Engine | None) -> dict[str, object]:
    """Return a read-only conformance summary of mapped tables and columns."""

    if engine is None:
        return {"mapped_tables": 0, "mismatches": []}

    inspector = inspect(engine)
    expected_columns = {
        "resource": [column.key for column in Resource.__table__.columns],
        "resource_version": [column.key for column in ResourceVersion.__table__.columns],
        "resource_review": [column.key for column in ResourceReview.__table__.columns],
        "job": [column.key for column in Job.__table__.columns],
        "audit_log": [column.key for column in AuditLog.__table__.columns],
    }
    mismatches: list[str] = []
    for table, columns in expected_columns.items():
        if table not in inspector.get_table_names():
            continue
        actual = {column["name"] for column in inspector.get_columns(table)}
        missing = [column for column in columns if column not in actual]
        if missing:
            mismatches.append(f"{table}: missing columns {missing}")
    return {
        "mapped_tables": len(_EXPECTED_TABLES),
        "mismatches": mismatches,
        "idempotency_check": select(Resource.public_id).limit(1).compile().string,
    }
