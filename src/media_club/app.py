import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from media_club.config import settings
from media_club.runtime import ApplicationRuntime


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start and stop long-lived application services."""
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    runtime = ApplicationRuntime(settings)
    app.state.runtime = runtime
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(title="Ghoul Bot", version="0.2.0", lifespan=lifespan)


@app.get("/health", tags=["operations"])
async def health() -> dict[str, str]:
    """Return process health for Railway and deployment checks."""
    return {"status": "ok", "environment": settings.app_env}


@app.get("/ready", tags=["operations"])
async def ready(request: Request) -> JSONResponse:
    """Return dependency readiness for operators and smoke tests."""
    runtime: ApplicationRuntime = request.app.state.runtime
    is_ready, checks = await runtime.readiness()
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={
            "status": "ready" if is_ready else "not_ready",
            "environment": settings.app_env,
            "checks": checks,
        },
    )
