"""Composition root: builds settings, providers, services, and dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request

from .auth import AuthContext, DevTokenAuth
from .application.catalog import CatalogService
from .application.diffs import DiffService
from .application.governance import GovernanceService
from .application.job_service import JobService
from .application.resource_commands import ResourceCommandService
from .application.resource_queries import ResourceQueryService
from .application.rollback import RollbackService
from .application.snapshots import SnapshotService
from .application.versions import VersionQueryService
from .config import Settings, get_settings
from .domain.errors import AuthenticationRequiredError, ServiceUnavailableError
from .middleware.context import RequestContext
from .providers.git_repository import GitProvider
from .providers.manifest_loader import ManifestLoader
from .repositories.contracts import UnitOfWorkFactory

__all__ = ["Container", "build_container", "get_auth", "get_container", "get_context"]


class UnavailableUnitOfWorkFactory:
    """Fail-closed unit-of-work factory used when MySQL is disabled."""

    def __call__(self):
        raise ServiceUnavailableError(
            message="MySQL is disabled; relational operations are unavailable."
        )


@dataclass(frozen=True)
class Container:
    settings: Settings
    git: GitProvider
    manifest_loader: ManifestLoader
    uow_factory: UnitOfWorkFactory
    auth: DevTokenAuth
    snapshots: SnapshotService
    resource_commands: ResourceCommandService
    resource_queries: ResourceQueryService
    versions: VersionQueryService
    diffs: DiffService
    rollback: RollbackService
    governance: GovernanceService
    catalog: CatalogService
    jobs: JobService
    mysql_engine: object = None


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or get_settings()
    git = GitProvider(settings.git.repository_root, read_only=settings.git.read_only)
    manifest_loader = ManifestLoader(git)
    auth = DevTokenAuth(settings.dev_auth_tokens_file)

    if settings.mysql.enabled:
        from .db.session import build_uow_factory

        uow_factory: UnitOfWorkFactory = build_uow_factory(settings)
        mysql_engine = _engine_from_factory(uow_factory)
    else:
        uow_factory = UnavailableUnitOfWorkFactory()
        mysql_engine = None

    snapshots = SnapshotService(manifest_loader)
    return Container(
        settings=settings,
        git=git,
        manifest_loader=manifest_loader,
        uow_factory=uow_factory,
        auth=auth,
        snapshots=snapshots,
        resource_commands=ResourceCommandService(uow_factory, snapshots),
        resource_queries=ResourceQueryService(uow_factory),
        versions=VersionQueryService(uow_factory),
        diffs=DiffService(uow_factory),
        rollback=RollbackService(uow_factory, snapshots),
        governance=GovernanceService(uow_factory),
        catalog=CatalogService(uow_factory),
        jobs=JobService(uow_factory),
        mysql_engine=mysql_engine,
    )


def _engine_from_factory(factory) -> object:
    try:
        return factory._sessionmaker.kw["bind"]  # type: ignore[union-attr]
    except Exception:
        return None


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_context(request: Request) -> RequestContext:
    return request.state.context


def get_auth(
    request: Request, container: Container = Depends(get_container)
) -> AuthContext:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise AuthenticationRequiredError()
    token = header[len("Bearer "):].strip()
    if not token:
        raise AuthenticationRequiredError()
    auth = container.auth.resolve(token)
    if auth is None:
        raise AuthenticationRequiredError(message="invalid or unknown bearer token")
    return auth
