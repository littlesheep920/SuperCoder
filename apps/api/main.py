"""FastAPI composition root.

The app wires middleware, routers, and the dependency container.  No business
logic lives here.  The module-level ``app`` is the ``apps.api.main:app`` entry
point required by the Stage 1 package boundary.
"""

from __future__ import annotations

from fastapi import FastAPI

from .config import Settings, get_settings
from .dependencies import Container, build_container
from .middleware.context import RequestContextMiddleware, register_exception_handlers
from .routers import governance, resources, search, system, versions

__all__ = ["app", "create_app"]


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="SuperCoder Control Plane API",
        version="0.2.0-stage2",
        description=(
            "Stage 1 resource asset center (governance, versions, reviews, jobs, audit) "
            "and Stage 2 search (BM25 + semantic recall, RRF fusion, rerank)."
        ),
    )
    app.state.settings = settings
    app.state.container = container or build_container(settings)

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    prefix = settings.api_prefix
    app.include_router(system.router, prefix=prefix)
    app.include_router(resources.router, prefix=prefix)
    app.include_router(versions.router, prefix=prefix)
    app.include_router(governance.router, prefix=prefix)
    app.include_router(search.router, prefix=prefix)
    return app


app = create_app()
