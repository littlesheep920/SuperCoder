"""FastAPI composition root.

The app wires middleware, routers, and the dependency container.  No business
logic lives here.  The module-level ``app`` is the ``apps.api.main:app`` entry
point required by the Stage 1 package boundary.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import Settings, get_settings
from .dependencies import Container, build_container
from .middleware.context import RequestContextMiddleware, register_exception_handlers
from .routers import discovery, governance, resources, search, system, versions

__all__ = ["app", "create_app"]

_log = logging.getLogger(__name__)


def _make_lifespan(container: Container):
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Pay the provider TLS handshakes at startup rather than during the
        # operator's first search. Measured cost of not doing it: 1664 ms on the
        # first query versus ~230 ms afterwards.
        try:
            outcomes = container.search.warm_connections()
            _log.info("search provider warm-up: %s", outcomes)
        except Exception:  # noqa: BLE001 - warm-up must never block startup
            _log.warning("search provider warm-up failed; first query may be slower", exc_info=True)
        yield

    return lifespan


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    container = container or build_container(settings)
    app = FastAPI(
        title="SuperCoder Control Plane API",
        version="0.2.0-stage2",
        description=(
            "Stage 1 resource asset center (governance, versions, reviews, jobs, audit) "
            "and Stage 2 search (BM25 + semantic recall, RRF fusion, rerank)."
        ),
        lifespan=_make_lifespan(container),
    )
    app.state.settings = settings
    app.state.container = container

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    prefix = settings.api_prefix
    app.include_router(system.router, prefix=prefix)
    app.include_router(resources.router, prefix=prefix)
    app.include_router(versions.router, prefix=prefix)
    app.include_router(governance.router, prefix=prefix)
    app.include_router(search.router, prefix=prefix)
    app.include_router(discovery.router, prefix=prefix)
    return app


app = create_app()
