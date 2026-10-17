"""Owned Compose checker: retain apps/sockets while its controller restarts only DB."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import AsyncExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine
from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import serve

from backend.api import create_app
from backend.config import Settings
from backend.db import check_database, get_engine, get_session_factory, migrate_database
from backend.protocol import OcppServer
from backend.service import Service

STATION = "RECOVERY-CP"
TAG = "RECOVERY-TAG"
START_TIME = "2026-10-17T10:00:00Z"
METER_TIME = "2026-10-17T10:30:00Z"
STOP_TIME = "2026-10-17T11:00:00Z"
TABLES = (
    "stations",
    "id_tags",
    "permissions",
    "charging_sessions",
    "meter_evidence",
    "ocpp_inbox",
    "audit_events",
    "connector_status",
    "simulation_slots",
)


def control_scope() -> tuple[Path, str]:
    """Validate scope before any failure marker can be written."""
    token = os.environ.get("RECOVERY_RUN_TOKEN", "")
    if re.fullmatch(r"kwms_recovery_[a-z0-9]{8}_[0-9]+", token) is None:
        raise ValueError("explicit generated recovery token required")
    control = Path(os.environ.get("RECOVERY_CONTROL_DIRECTORY", ""))
    if control != Path("/control") or not control.is_dir():
        raise ValueError("only the isolated /control mount is accepted")
    return control, token


def configuration(token: str) -> Settings:
    """Only the generated secret and fixed isolated database are accepted."""
    password = Path("/run/secrets/recovery_db_password").read_text(encoding="utf-8")
    if password != token:
        raise ValueError("recovery secret/token mismatch")
    url = URL.create(
        "postgresql+psycopg",
        username="charging_recovery",
        password=password,
        host="db",
        port=5432,
        database="charging_recovery_test",
    ).render_as_string(hide_password=False)
    parsed = make_url(url)
    assert parsed.host == "db" and parsed.database == "charging_recovery_test"
    return Settings(database_url=url, instance_id="recovery-0")


def signal(control: Path, token: str, phase: str) -> None:
    (control / phase).write_text(token, encoding="utf-8")


async def wait_signal(control: Path, token: str, phase: str) -> None:
    async with asyncio.timeout(20):
        # This event comes from the host controller, not another asyncio task.
        while not (control / phase).exists():  # noqa: ASYNC110
            await asyncio.sleep(0.1)
        assert (control / phase).read_text(encoding="utf-8") == token


async def call(
    socket: ClientConnection, unique_id: str, action: str, payload: dict[str, Any]
) -> list[Any]:
    await socket.send(json.dumps([2, unique_id, action, payload]))
    raw = await asyncio.wait_for(socket.recv(), timeout=5)
    assert isinstance(raw, str) and len(raw) <= 65_536
    response: list[Any] = json.loads(raw)
    assert response[1] == unique_id
    return response


async def snapshot(engine: AsyncEngine) -> dict[str, list[Any]]:
    async with engine.connect() as connection:
        result = {}
        for table in TABLES:
            # Constants only: no user SQL/table interpolation or destructive reset.
            rows = await connection.execute(text(f"SELECT to_jsonb(t) FROM {table} AS t"))
            result[table] = sorted(rows.scalars(), key=lambda row: json.dumps(row, sort_keys=True))
        return result


async def recovered(clients: list[AsyncClient]) -> None:
    async with asyncio.timeout(15):
        while True:
            responses = await asyncio.gather(*(client.get("/health/ready") for client in clients))
            if all(response.status_code == 200 for response in responses):
                return
            await asyncio.sleep(0.2)


async def run(settings: Settings, control: Path, token: str) -> None:
    await asyncio.to_thread(migrate_database, settings)
    async with AsyncExitStack() as stack:
        engines = []
        services = []
        listeners = []
        clients = []
        urls = []
        for index in range(3):
            instance_id = f"recovery-{index}"
            engine = get_engine(replace(settings, instance_id=instance_id))
            stack.push_async_callback(engine.dispose)
            engines.append(engine)
            service = Service(get_session_factory(engine), instance_id=instance_id)
            services.append(service)
            protocol = OcppServer(service, instance_id)
            listener = await stack.enter_async_context(
                serve(
                    protocol.handle,
                    "127.0.0.1",
                    0,
                    subprotocols=["ocpp1.6"],
                    process_request=protocol.handshake,
                    origins=[None],
                    max_size=65_536,
                    max_queue=8,
                    close_timeout=1,
                    compression=None,
                )
            )
            listeners.append(listener)
            urls.append(f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}/ocpp/{STATION}")

            async def ready(current: AsyncEngine = engine) -> None:
                await check_database(current)

            app = create_app(
                service,
                readiness_check=ready,
                liveness_check=listener.is_serving,
                instance_id=instance_id,
            )
            clients.append(
                await stack.enter_async_context(
                    AsyncClient(transport=ASGITransport(app=app), base_url=f"http://{instance_id}")
                )
            )
        await recovered(clients)
        assert (await clients[0].post("/api/stations", json={"id": STATION})).status_code == 201
        assert (await clients[1].post("/api/id-tags", json={"id": TAG})).status_code == 201
        assert (
            await clients[2].put(
                f"/api/stations/{STATION}/permissions/{TAG}", json={"enabled": True}
            )
        ).status_code == 200
        socket = await stack.enter_async_context(
            connect(urls[0], subprotocols=["ocpp1.6"], open_timeout=3, close_timeout=1)
        )
        assert (
            await call(
                socket,
                "boot",
                "BootNotification",
                {"chargePointVendor": "Recovery", "chargePointModel": "Isolated"},
            )
        )[0] == 3
        assert (await call(socket, "auth", "Authorize", {"idTag": TAG}))[2] == {
            "idTagInfo": {"status": "Accepted"}
        }
        start = await call(
            socket,
            "start",
            "StartTransaction",
            {"connectorId": 1, "idTag": TAG, "meterStart": 1000, "timestamp": START_TIME},
        )
        assert start[0] == 3
        transaction_id = start[2]["transactionId"]
        assert type(transaction_id) is int
        assert (
            await call(
                socket,
                "meter",
                "MeterValues",
                {
                    "connectorId": 1,
                    "transactionId": transaction_id,
                    "meterValue": [{"timestamp": METER_TIME, "sampledValue": [{"value": "4000"}]}],
                },
            )
        ) == [3, "meter", {}]
        baseline = await snapshot(engines[2])
        assert len(baseline["charging_sessions"]) == 1
        assert len(baseline["meter_evidence"]) == 1
        assert len(baseline["ocpp_inbox"]) == 4
        assert baseline["charging_sessions"][0]["lifecycle"] == "active"
        assert baseline["audit_events"] and baseline["permissions"][0]["enabled"] is True
        original_listener_ids = [id(listener) for listener in listeners]
        original_engine_ids = [id(engine) for engine in engines]
        print("Recovery: committed active session and exact pre-outage evidence.", flush=True)
        signal(control, token, "ready_to_stop")
        await wait_signal(control, token, "db_down")

        # Requests target all three pre-existing pools; listener health does no DB I/O.
        for client in clients:
            live = await asyncio.wait_for(client.get("/health/live"), timeout=5)
            ready_response = await asyncio.wait_for(client.get("/health/ready"), timeout=5)
            unavailable = await asyncio.wait_for(
                client.post("/api/id-tags", json={"id": "OUTAGE-TAG"}), timeout=5
            )
            assert live.status_code == 200
            assert ready_response.status_code == 503 and ready_response.json()["status"] == (
                "unavailable"
            )
            assert unavailable.status_code == 503
            assert unavailable.json() == {"detail": "database operation unavailable"}
        assert await call(
            socket,
            "outage-stop",
            "StopTransaction",
            {"transactionId": transaction_id, "meterStop": 11_000, "timestamp": STOP_TIME},
        ) == [4, "outage-stop", "InternalError", "Database temporarily unavailable", {}]
        print("Recovery: all three apps live/unready; API and OCPP refuse mutations.", flush=True)
        signal(control, token, "checked_down")
        await wait_signal(control, token, "db_restarted")
        await recovered(clients)
        assert [id(listener) for listener in listeners] == original_listener_ids
        assert [id(engine) for engine in engines] == original_engine_ids
        assert all(listener.is_serving() for listener in listeners)
        assert await snapshot(engines[1]) == baseline
        print(
            "Recovery: identical app instances and exact persisted baseline recovered.", flush=True
        )
        for client in clients:
            response = await client.get(f"/api/sessions/{transaction_id}")
            assert response.status_code == 200
            view = response.json()
            assert view["lifecycle"] == "active" and view["stoppedAt"] is None
            assert view["lastMeterWh"] == 4000 and view["energyKWh"] == 3
            assert view["energyProvisional"] is True
        # Retained socket/generation retries an outage-failed mutation only after recovery.
        stop_payload = {
            "transactionId": transaction_id,
            "meterStop": 11_000,
            "timestamp": STOP_TIME,
        }
        stopped = await call(socket, "outage-stop", "StopTransaction", stop_payload)
        assert stopped == [3, "outage-stop", {"idTagInfo": {"status": "Accepted"}}]
        assert await call(socket, "outage-stop", "StopTransaction", stop_payload) == stopped
        final = await snapshot(engines[2])
        assert len(final["charging_sessions"]) == 1
        assert len(final["meter_evidence"]) == 1
        assert len(final["ocpp_inbox"]) == 5
        assert len(final["stations"]) == 1 and len(final["id_tags"]) == 1
        assert final["permissions"] == baseline["permissions"]
        assert sum(row["kind"] == "session_completed" for row in final["audit_events"]) == 1
        for client in clients:
            response = await client.get(f"/api/sessions/{transaction_id}")
            assert response.status_code == 200
            view = response.json()
            assert view["lifecycle"] == "completed" and view["billingEligible"] is True
            assert view["transactionId"] == transaction_id
            assert view["energyKWh"] == 10 and view["durationSeconds"] == 3600
            assert view["stoppedAt"] == STOP_TIME and view["meterStopWh"] == 11_000
        print(
            "PASS: owned volume-backed DB stop/restart; same three app/pool/listener "
            "instances remained live, returned bounded unavailable outcomes, retained "
            "exact committed data, then completed/replayed one session exactly once.",
            flush=True,
        )
    signal(control, token, "passed")


async def main() -> None:
    control = None
    token = ""
    try:
        control, token = control_scope()
        settings = configuration(token)
        async with asyncio.timeout(90):
            await run(settings, control, token)
    except BaseException as exc:
        print(f"FAIL: recovery checker {type(exc).__name__}", flush=True)
        if control is not None:
            try:
                signal(control, token, "failed")
            except OSError:
                print("FAIL: recovery checker failure marker unavailable", flush=True)
        raise


if __name__ == "__main__":
    # SQLAlchemy/driver tracebacks can include connection details. The generic
    # failure type is enough; assertions still fail this one-command gate.
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(main())
    except BaseException:
        raise SystemExit(1) from None
