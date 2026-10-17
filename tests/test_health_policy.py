"""Restart health is listener liveness; readiness also requires the database."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api import create_app
from backend.service import Service

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_database_loss_changes_readiness_not_liveness() -> None:
    ready = AsyncMock(side_effect=RuntimeError("private driver credentials"))
    listener = True
    app = create_app(
        AsyncMock(spec=Service),
        readiness_check=ready,
        instance_id="health-instance",
        liveness_check=lambda: listener,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://health") as client:
        live = await client.get("/health/live")
        assert live.status_code == 200
        ready.assert_not_awaited()
        unavailable = await client.get("/health/ready")
        assert unavailable.status_code == 503
        assert unavailable.json() == {"status": "unavailable", "instanceId": "health-instance"}
        assert "private" not in unavailable.text
        ready.side_effect = None
        assert (await client.get("/health/ready")).status_code == 200
        listener = False
        calls = ready.await_count
        for endpoint in ("live", "ready"):
            response = await client.get(f"/health/{endpoint}")
            assert response.status_code == 503
            assert response.json()["status"] == "unavailable"
        assert ready.await_count == calls


@pytest.mark.asyncio
async def test_listener_probe_errors_are_generic_and_do_not_touch_database() -> None:
    def broken() -> bool:
        raise RuntimeError("sensitive listener exception")

    ready = AsyncMock()
    app = create_app(
        AsyncMock(spec=Service),
        readiness_check=ready,
        instance_id="health-instance",
        liveness_check=broken,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://health") as client:
        for endpoint in ("live", "ready"):
            response = await client.get(f"/health/{endpoint}")
            assert response.status_code == 503 and "sensitive" not in response.text
        ready.assert_not_awaited()


def test_swarm_probes_liveness_but_deploy_explicitly_checks_readiness() -> None:
    # Docker performs full stack parsing in make check; this guards the policy
    # without adding a YAML parser solely for a test.
    stack = (ROOT / "stack.yml").read_text()
    assert stack.count("/health/live") == 2 and "/health/ready" not in stack
    deployment = (ROOT / "deploy.sh").read_text()
    assert "/health/ready" in deployment and 'docker exec "$container_id"' in deployment
    assert "live and ready" in deployment
