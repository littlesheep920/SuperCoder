"""SQLAlchemy engine construction and the MySQL unit-of-work boundary.

The engine is only built when MySQL is explicitly enabled.  Repository
instances share one session per unit of work; application services must call
``commit`` explicitly.  Leaving an uncommitted unit of work rolls back.
"""

from __future__ import annotations

from types import TracebackType

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import MysqlSettings, Settings
from ..repositories.sql_governance import (
    SqlAuditRepository,
    SqlJobRepository,
    SqlPermissionRepository,
    SqlReviewRepository,
)
from ..repositories.sql_resources import (
    SqlMetadataRepository,
    SqlResourceRepository,
    SqlVersionRepository,
)

__all__ = ["SqlSessionFactory", "SqlUnitOfWork", "build_engine"]


def build_engine(mysql: MysqlSettings) -> Engine:
    """Build a MySQL engine. Requires the PyMySQL driver at connect time."""

    connect_args = {"connect_timeout": mysql.connect_timeout_seconds}
    if mysql.ssl_mode != "disabled":
        connect_args["ssl"] = {"ssl_mode": mysql.ssl_mode}
    return create_engine(
        mysql.url(),
        pool_size=mysql.pool_size,
        max_overflow=mysql.max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args=connect_args,
        future=True,
    )


class SqlSessionFactory:
    """Thin wrapper over a ``sessionmaker`` bound to one engine."""

    def __init__(self, engine: Engine) -> None:
        self._sessionmaker = sessionmaker(bind=engine, future=True, expire_on_commit=False)

    def __call__(self) -> Session:
        return self._sessionmaker()


class SqlUnitOfWorkFactory:
    """Build one transactional SqlUnitOfWork per application operation."""

    def __init__(self, engine: Engine) -> None:
        self._session_factory = SqlSessionFactory(engine)
        # Kept for the composition-root health probe.
        self._sessionmaker = self._session_factory._sessionmaker

    def __call__(self) -> SqlUnitOfWork:
        return SqlUnitOfWork(self._session_factory)


class SqlUnitOfWork:
    """One explicit transaction containing all authoritative repositories."""

    def __init__(self, session_factory: SqlSessionFactory) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._committed = False
        self._rolled_back = False
        self.resources: SqlResourceRepository | None = None
        self.versions: SqlVersionRepository | None = None
        self.permissions: SqlPermissionRepository | None = None
        self.reviews: SqlReviewRepository | None = None
        self.metadata: SqlMetadataRepository | None = None
        self.jobs: SqlJobRepository | None = None
        self.audit: SqlAuditRepository | None = None

    @property
    def committed(self) -> bool:
        return self._committed

    @property
    def rolled_back(self) -> bool:
        return self._rolled_back

    def _require_session(self) -> Session:
        if self._session is None:
            raise RuntimeError("unit of work is not active")
        return self._session

    def __enter__(self) -> "SqlUnitOfWork":
        self._session = self._session_factory()
        session = self._session
        self.resources = SqlResourceRepository(session)
        self.versions = SqlVersionRepository(session)
        self.permissions = SqlPermissionRepository(session)
        self.reviews = SqlReviewRepository(session)
        self.metadata = SqlMetadataRepository(session)
        self.jobs = SqlJobRepository(session)
        self.audit = SqlAuditRepository(session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        if self._session is not None:
            if exc_type is None and not (self._committed or self._rolled_back):
                self.rollback()
            self._session.close()
            self._session = None
        return None

    def flush(self) -> None:
        self._require_session().flush()

    def commit(self) -> None:
        self._require_session().commit()
        self._committed = True

    def rollback(self) -> None:
        self._require_session().rollback()
        self._rolled_back = True


def build_uow_factory(settings: Settings) -> SqlSessionFactory:
    """Build the production SQLAlchemy session factory from settings.

    Raises when MySQL is disabled so the composition root can fail closed
    rather than silently swapping in an in-memory fallback.
    """

    if not settings.mysql.enabled:
        raise RuntimeError("MySQL is disabled; cannot build the relational unit of work")
    return SqlUnitOfWorkFactory(build_engine(settings.mysql))
