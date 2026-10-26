"""Composition root: builds settings, providers, services, and dependencies."""

from __future__ import annotations

from dataclasses import dataclass, replace

from fastapi import Depends, Request

from .auth import AuthContext, DevTokenAuth
from .application.catalog import CatalogService
from .application.discovery_service import DiscoveryService
from .application.diffs import DiffService
from .application.governance import GovernanceService
from .application.job_service import JobService
from .application.resource_commands import ResourceCommandService
from .application.resource_queries import ResourceQueryService
from .application.rollback import RollbackService
from .application.search_service import DocumentCache, SearchServiceFactory
from .application.snapshots import SnapshotService
from .application.versions import VersionQueryService
from .config import Settings, get_settings
from .domain.discovery import build_decider
from .domain.errors import AuthenticationRequiredError, ServiceUnavailableError
from .middleware.context import RequestContext
from .providers.git_repository import GitProvider
from .providers.llm import build_llm_client
from .providers.manifest_loader import ManifestLoader
from .repositories.contracts import UnitOfWorkFactory
from .search.cache import CachingEmbeddingProvider, CachingRerankProvider, LRUCache
from .search.factory import build_embedding_provider, build_rerank_provider
from .search.vector import DocumentVectorCache


def _cached_embedding(settings) -> object:
    """Build the embedding provider and cache query vectors.

    A capacity of 0 returns the provider unwrapped, which keeps the historical
    behaviour available for benchmarking and incident triage.
    """

    provider = build_embedding_provider(settings)
    if settings.query_cache_size <= 0:
        return provider
    return CachingEmbeddingProvider(provider, LRUCache(settings.query_cache_size))


def _cached_rerank(settings) -> object:
    provider = build_rerank_provider(settings)
    if settings.rerank_cache_size <= 0:
        return provider
    return CachingRerankProvider(provider, LRUCache(settings.rerank_cache_size))

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
    search: SearchServiceFactory
    # Capability discovery. Optional so tests can build a container without it;
    # the endpoint fails closed when it is absent.
    discovery: DiscoveryService | None = None
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
    container = Container(
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
        # Providers are built from settings only. With remote access disabled
        # they report unavailable and never read the API key.
        #
        # Providers are wrapped once per container so their caches span
        # requests, and the document cache reuses projections across requests.
        # Both are pure performance layers: they cannot make an unauthorized
        # document visible, because visibility is resolved by the repository
        # before either cache is consulted.
        search=SearchServiceFactory(
            uow_factory,
            manifest_loader,
            embedding=_cached_embedding(settings.search),
            rerank=_cached_rerank(settings.search),
            document_cache=(
                DocumentCache(settings.search.document_cache_size)
                if settings.search.document_cache_size > 0
                else None
            ),
            document_vectors=(
                DocumentVectorCache(settings.search.document_cache_size)
                if settings.search.document_cache_size > 0
                else None
            ),
        ),
        mysql_engine=mysql_engine,
    )
    # Discovery is built after search because it resolves candidates through the
    # same service. The decider is constructed from settings only; with the LLM
    # disabled the heuristic answers and every response says so in ``degraded``.
    llm = build_llm_client(settings.external_llm)
    decider = build_decider(llm)
    return replace(container, discovery=DiscoveryService(container.search, decider))


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
