"""FastAPI application entry point and operational health endpoint."""

from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from apps.api.model_routes import router as model_router
from apps.api.run_routes import router as run_router
from apps.api.settings import get_settings


class HealthResponse(BaseModel):
    """Stable response returned by the API liveness endpoint."""

    status: Literal["ok"]
    service: str
    version: str


settings = get_settings()

app = FastAPI(
    title="Multi-Agent Travel Planner API",
    version="1.0.0",
    description="API surface for the self-hosted travel planning harness.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)
app.include_router(run_router)
app.include_router(model_router)


@app.get("/health", response_model=HealthResponse, tags=["operations"])
async def health() -> HealthResponse:
    """Return a dependency-free liveness signal for local runtime checks."""

    return HealthResponse(status="ok", service="travel-api", version=app.version)
