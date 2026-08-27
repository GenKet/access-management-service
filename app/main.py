from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import access_requests, health, resources
from app.api.errors import register_error_handlers
from app.infrastructure.db import session as db
from app.infrastructure.seed import seed
from app.logging_config import configure_logging


def create_app(run_seed: bool = True) -> FastAPI:
    configure_logging()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if run_seed:
            async with db.session_factory() as session:
                await seed(session)
        yield

    app = FastAPI(title="Access Management Service", version="1.0.0", lifespan=lifespan)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(resources.router)
    app.include_router(access_requests.router)
    return app


app = create_app()
