from fastapi import FastAPI

from media_club.config import settings

app = FastAPI(title="Media Club", version="0.1.0")


@app.get("/health", tags=["operations"])
async def health() -> dict[str, str]:
    """Return process health for Railway and deployment checks."""
    return {"status": "ok", "environment": settings.app_env}

