"""Run inside the frontend task: exercise actual Swarm DNS, HTTP and OCPP.

Explicitly creates/reuses CHECK-CP and CHECK-TAG, leaving their permission enabled.
No database access, destructive cleanup, or dependency on host Python.
"""

import asyncio
import html
import json
import re
import socket

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


def csrf(page: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert match is not None, "UI form has no CSRF token"
    return html.unescape(match[1])


async def main() -> None:
    addresses = sorted(
        {item[4][0] for item in socket.getaddrinfo("tasks.backend", 8000, type=socket.SOCK_STREAM)}
    )
    assert len(addresses) == 3, f"Expected exactly three backend tasks, found {len(addresses)}"
    async with httpx.AsyncClient(timeout=5, follow_redirects=False) as http:
        instances = set()
        for address in addresses:
            response = await http.get(f"http://{address}:8000/health/ready")
            response.raise_for_status()
            instances.add(response.json()["instanceId"])
        assert len(instances) == 3, "Expected three distinct backend instances"
        base = "http://backend:8000"
        stations = (await http.get(base + "/api/stations")).json()
        tags = (await http.get(base + "/api/id-tags")).json()
        page = await http.get("http://frontend:8000/")
        page.raise_for_status()
        for path, values, exists in (
            ("/stations", {"station_id": "CHECK-CP"}, any(s["id"] == "CHECK-CP" for s in stations)),
            ("/id-tags", {"id_tag": "CHECK-TAG"}, any(t["id"] == "CHECK-TAG" for t in tags)),
        ):
            if not exists:
                response = await http.post(
                    "http://frontend:8000" + path, data={**values, "csrf_token": csrf(page.text)}
                )
                assert response.status_code == 303, f"UI creation failed: {response.status_code}"
                page = await http.get("http://frontend:8000/")
                page.raise_for_status()
        denied = await http.post(
            "http://frontend:8000/permissions",
            data={
                "station_id": "CHECK-CP",
                "id_tag": "CHECK-TAG",
                "enabled": "true",
            },
        )
        assert denied.status_code == 400, "Missing CSRF token was not rejected"
        response = await http.post(
            "http://frontend:8000/permissions",
            data={
                "station_id": "CHECK-CP",
                "id_tag": "CHECK-TAG",
                "enabled": "true",
                "csrf_token": csrf(page.text),
            },
        )
        assert response.status_code == 303

        async def auth(ws, message_id):
            await ws.send(json.dumps([2, message_id, "Authorize", {"idTag": "CHECK-TAG"}]))
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert frame[:2] == [3, message_id], frame
            return frame[2]["idTagInfo"]["status"]

        async with connect(
            f"ws://{addresses[0]}:9000/ocpp/CHECK-CP", subprotocols=["ocpp1.6"]
        ) as old:
            assert await auth(old, "reused-id") == "Accepted"
            async with connect(
                f"ws://{addresses[1]}:9000/ocpp/CHECK-CP", subprotocols=["ocpp1.6"]
            ) as new:
                await new.send("invalid JSON")
                assert await auth(old, "still-current") == "Accepted"
                response = await http.put(
                    f"http://{addresses[2]}:8000/api/stations/CHECK-CP/permissions/CHECK-TAG",
                    json={"enabled": False},
                )
                response.raise_for_status()
                assert await auth(new, "reused-id") == "Blocked"
                await old.send('[2,"stale","Authorize",{"idTag":"CHECK-TAG"}]')
                try:
                    await asyncio.wait_for(old.recv(), timeout=5)
                except ConnectionClosed as exc:
                    assert exc.rcvd is not None and exc.rcvd.code == 1008
                else:
                    raise AssertionError("Old replica accepted a stale socket")
        response = await http.put(
            base + "/api/stations/CHECK-CP/permissions/CHECK-TAG", json={"enabled": True}
        )
        response.raise_for_status()
        page = await http.get("http://frontend:8000/")
        response = await http.post(
            "http://frontend:8000/authorization/check",
            data={
                "station_id": "CHECK-CP",
                "id_tag": "CHECK-TAG",
                "csrf_token": csrf(page.text),
            },
        )
        response.raise_for_status()
        assert 'class="badge badge-accepted">Accepted</strong>' in response.text
        assert (await http.get("http://frontend:8000/health/ready")).status_code == 200
    print(
        "PASS: three actual replicas, UI/CSRF, shared permissions, "
        "replay scope, stale fencing, real OCPP probe"
    )


asyncio.run(main())
