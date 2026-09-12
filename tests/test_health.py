"""Contract tests for the FastAPI operational health endpoint."""

import asyncio

import httpx

from apps.api.main import app


def test_health_endpoint() -> None:
    """The liveness endpoint should be stable and dependency-free."""

    async def request_health() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/health")

    response = asyncio.run(request_health())

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "travel-api",
        "version": "0.1.0",
    }
