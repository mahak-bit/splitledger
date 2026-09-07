"""Application factory and process-level wiring."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from splitledger import __version__
from splitledger.api.router import api_router
from splitledger.config import Settings, get_settings
from splitledger.db import dispose_engine, get_session_factory, init_engine
from splitledger.errors import install_exception_handlers
from splitledger.middleware import RequestContextMiddleware
from splitledger.schemas import HealthResponse

DESCRIPTION = """
Share expenses across a group, then settle up in as few payments as possible.

**How it works**

* Record an expense and split it four ways: `equal`, `exact`, `percentage`, or `shares`.
  Money is stored in integer minor units, and every split is proven to add back up to
  the total before it is written.
* `GET /api/v1/groups/{group_id}/balances` reduces the whole ledger to one number per
  member -- positive means the group owes them.
* `GET /api/v1/groups/{group_id}/settlement-plan` turns those balances into the
  smallest set of payments that clears them. For groups up to a configurable size the
  plan is *provably* minimal; beyond that it is a greedy approximation, and the
  response says which you got.

**Auth**: register, then POST to `/api/v1/auth/token` and send the result as
`Authorization: Bearer <token>`.
"""

TAGS_METADATA = [
    {"name": "auth", "description": "Registration and access tokens."},
    {"name": "groups", "description": "Groups, membership, balances and settlement plans."},
    {"name": "expenses", "description": "Recording and splitting expenses."},
    {"name": "settlements", "description": "Payments members have made to each other."},
    {"name": "ops", "description": "Liveness and readiness probes."},
]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application. Tests call this directly with their own settings."""
    config = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        init_engine(config.database_url, echo=config.database_echo)
        try:
            yield
        finally:
            await dispose_engine()

    app = FastAPI(
        title=config.app_name,
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        debug=config.debug,
        lifespan=lifespan,
    )

    app.add_middleware(RequestContextMiddleware)
    if config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    install_exception_handlers(app)
    app.include_router(api_router)

    @app.get("/healthz", response_model=HealthResponse, tags=["ops"], summary="Liveness")
    async def healthz() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    @app.get("/readyz", response_model=HealthResponse, tags=["ops"], summary="Readiness")
    async def readyz(response: Response) -> HealthResponse:
        """Report 503 when the database cannot serve a trivial query."""
        try:
            factory = get_session_factory()
            async with factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception:  # any failure at all means the process is not ready
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return HealthResponse(status="degraded", version=__version__, database="down")
        return HealthResponse(status="ok", version=__version__, database="up")

    return app


app = create_app()
