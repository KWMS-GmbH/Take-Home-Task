"""Real PostgreSQL and explicit listener routing, rather than random load balancing."""

import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import replace
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from backend.config import Settings
from backend.db import get_engine, get_session_factory
from backend.service import Service
from tests.conftest import DatabaseContext
from tests.test_protocol import running_server


async def authorize(ws: ClientConnection, unique_id: str, tag: str = "TAG-1") -> str:
    await ws.send(json.dumps([2, unique_id, "Authorize", {"idTag": tag}]))
    result: list[Any] = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
    assert result[:2] == [3, unique_id]
    return str(result[2]["idTagInfo"]["status"])


@pytest.mark.asyncio
async def test_three_listeners_share_permissions_and_fence_stale_sockets(
    database_context: DatabaseContext,
    test_settings: Settings,
) -> None:
    async with AsyncExitStack() as stack:
        services = []
        urls = []
        for index in range(3):
            instance = f"instance-{index}"
            engine = get_engine(replace(test_settings, instance_id=instance))
            stack.push_async_callback(engine.dispose)
            service = Service(get_session_factory(engine), instance_id=instance)
            services.append(service)
            urls.append(await stack.enter_async_context(running_server(service, instance)))

        admin = services[0]
        await admin.create_id_tag("TAG-1")
        first = await stack.enter_async_context(
            connect(urls[0] + "/ocpp/CP-1", subprotocols=["ocpp1.6"])
        )
        # Raw connect, even without Boot, must make the station immediately visible.
        for _ in range(20):
            if any(station["id"] == "CP-1" for station in await admin.list_stations()):
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("connected station did not become visible")
        await services[1].set_permission("CP-1", "TAG-1", enabled=True)
        assert await authorize(first, "same-id") == "Accepted"

        second = await stack.enter_async_context(
            connect(urls[1] + "/ocpp/CP-1", subprotocols=["ocpp1.6"])
        )
        await second.send("malformed JSON")
        # Mere raw connection or invalid input must not replace a valid owner.
        assert await authorize(first, "still-owner") == "Accepted"
        await services[2].set_permission("CP-1", "TAG-1", enabled=False)
        # Message ID reuse on a NEW socket is legal and reads fresh permission.
        assert await authorize(second, "same-id") == "Blocked"
        await first.send('[2,"old","Authorize",{"idTag":"TAG-1"}]')
        with pytest.raises(ConnectionClosed) as closed:
            await first.recv()
        assert closed.value.rcvd is not None and closed.value.rcvd.code == 1008

        third = await stack.enter_async_context(
            connect(urls[2] + "/ocpp/CP-1", subprotocols=["ocpp1.6"])
        )
        assert await authorize(third, "same-id", "UNREGISTERED") == "Invalid"
        await second.send('[2,"old-again","Authorize",{"idTag":"TAG-1"}]')
        with pytest.raises(ConnectionClosed):
            await second.recv()
        # A separate station remains unaffected by takeover and malformed input.
        other = await stack.enter_async_context(
            connect(urls[0] + "/ocpp/CP-2", subprotocols=["ocpp1.6"])
        )
        assert await authorize(other, "same-id") == "Blocked"


@pytest.mark.asyncio
async def test_boot_contract_replay_conflict_and_current_permission(
    service: Service,
    instance_id: str,
) -> None:
    await service.create_id_tag("TAG-1")
    async with running_server(service, instance_id) as url:
        async with connect(url + "/ocpp/CP-1", subprotocols=["ocpp1.6"]) as ws:
            boot = [
                2,
                "boot",
                "BootNotification",
                {
                    "chargePointVendor": "Vendor",
                    "chargePointModel": "Model",
                    "charge_point_vendor": "INJECT",
                    "callUniqueId": "INJECT",
                },
            ]
            await ws.send(json.dumps(boot))
            first = json.loads(await ws.recv())
            assert first[:2] == [3, "boot"]
            assert first[2]["interval"] == 300
            assert first[2]["status"] == "Accepted"
            assert first[2]["currentTime"].endswith("Z")
            await ws.send(json.dumps(boot))
            assert json.loads(await ws.recv()) == first
            boot[3]["chargePointVendor"] = "Changed"
            await ws.send(json.dumps(boot))
            assert json.loads(await ws.recv())[:3] == [4, "boot", "ProtocolError"]
            assert (await service.list_stations())[0]["vendor"] == "Vendor"
            await service.set_permission("CP-1", "TAG-1", enabled=True)
            assert await authorize(ws, "auth") == "Accepted"
            await service.set_permission("CP-1", "TAG-1", enabled=False)
            # A retry is the prior request; a fresh request reads the new state.
            assert await authorize(ws, "auth") == "Accepted"
            assert await authorize(ws, "fresh") == "Blocked"
            assert await authorize(ws, "unknown-punctuation", "tag / unknown") == "Invalid"
