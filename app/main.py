import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.api import agent, alerts, data, health, maintenance, simulation, transformers
from app.config.settings import Settings, get_settings
from app.database.db import DatabaseNotConfigured, get_engine
from app.services.errors import BadRequest, PayloadTooLarge, ResourceNotFound
from app.simulation.simulator import SimulationManager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # No connection or table creation on startup. Alembic owns the schema.
    if get_engine.cache_info().currsize:
        get_engine().dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # Keep framework traceback responses disabled even in local DEBUG mode.
    application = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
    application.state.settings = settings
    application.state.simulator = SimulationManager()
    if settings.debug:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],  # Development-only; restrict origins for production.
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @application.get("/")
    def root():
        return {"service": settings.app_name, "status": "running"}

    for router in (health.router, data.router, alerts.router, transformers.router, simulation.router, agent.router, maintenance.router):
        application.include_router(router, prefix=settings.api_v1_prefix)

    async def not_found(request: Request, exc: ResourceNotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    async def bad_request(request: Request, exc: BadRequest):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    async def too_large(request: Request, exc: PayloadTooLarge):
        return JSONResponse(status_code=413, content={"detail": str(exc)})

    async def database_error(request: Request, exc: Exception):
        # Exception strings can contain credentials or SQL payloads; log only the type.
        logger.error("Database request failed (%s)", type(exc).__name__)
        return JSONResponse(status_code=503, content={"detail": "Database unavailable or not configured"})

    async def unexpected_error(request: Request, exc: Exception):
        logger.error("Unexpected request failure (%s)", type(exc).__name__)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    async def validation_error(request: Request, exc: RequestValidationError):
        # Avoid echoing raw sensor payloads or uploaded content in validation errors.
        errors = [
            {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    application.add_exception_handler(ResourceNotFound, not_found)
    application.add_exception_handler(BadRequest, bad_request)
    application.add_exception_handler(PayloadTooLarge, too_large)
    application.add_exception_handler(DatabaseNotConfigured, database_error)
    application.add_exception_handler(OperationalError, database_error)
    application.add_exception_handler(SQLAlchemyError, unexpected_error)
    application.add_exception_handler(RequestValidationError, validation_error)
    application.add_exception_handler(Exception, unexpected_error)
    return application


app = create_app()
