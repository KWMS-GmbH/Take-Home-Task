"""Opt-in HTTP/OCPP review examples. No DB access, resets or automatic seeding."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
from websockets.asyncio.client import ClientConnection, connect

START = datetime(2026, 10, 17, 8, tzinfo=UTC)
TAGS = {"Accepted": "DEMO-ALLOWED", "Blocked": "DEMO-BLOCKED", "Invalid": "DEMO-UNKNOWN-TAG"}


@dataclass(frozen=True)
class Example:
    station: str
    authorization: str
    start_wh: int
    sample: str | None
    stop_wh: int | None
    minutes: int
    reboot: bool
    lifecycle: str
    quality: str
    energy: float | None

    @property
    def expected(self) -> dict[str, Any]:
        completed = self.lifecycle == "completed"
        return {
            "stationId": self.station,
            "connectorId": 1,
            "idTag": TAGS[self.authorization],
            "startedAt": stamp(START),
            "stoppedAt": stamp(START + timedelta(minutes=self.minutes)) if completed else None,
            "durationSeconds": self.minutes * 60 if completed else None,
            "authorizationStatus": self.authorization,
            "lifecycle": self.lifecycle,
            "meterQuality": self.quality,
            "energyKWh": self.energy,
            "energyProvisional": not completed and self.energy is not None,
            "billingEligible": completed
            and self.authorization == "Accepted"
            and self.quality == "valid",
        }


EXAMPLES = (
    Example("DEMO-HOME", "Accepted", 1000, None, 8200, 120, False, "completed", "valid", 7.2),
    Example("DEMO-OFFICE", "Accepted", 1000, None, 23500, 90, False, "completed", "valid", 22.5),
    Example("DEMO-CHARGING", "Accepted", 1000, "3400", None, 25, False, "active", "valid", 2.4),
    Example("DEMO-REBOOT", "Accepted", 1000, "7000", None, 40, True, "interrupted", "valid", 6.0),
    Example(
        "DEMO-BAD-METER", "Accepted", 5000, None, 4000, 45, False, "completed", "suspicious", None
    ),
    Example(
        "DEMO-BLOCKED",
        "Blocked",
        1000,
        "not-a-meter",
        None,
        10,
        True,
        "interrupted",
        "suspicious",
        None,
    ),
    Example("DEMO-UNKNOWN", "Invalid", 1000, None, None, 0, False, "active", "unknown", None),
)


def stamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def verify(example: Example, record: dict[str, Any]) -> None:
    if any(record.get(key) != value for key, value in example.expected.items()):
        raise RuntimeError(
            f"{example.station}: demo data is partial or changed; left untouched. "
            "Inspect its session rather than resetting existing records."
        )


async def call(ws: ClientConnection, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    message_id = uuid4().hex
    await ws.send(json.dumps([2, message_id, action, payload]))
    frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    if not isinstance(frame, list) or len(frame) != 3 or frame[:2] != [3, message_id]:
        raise RuntimeError(f"Unexpected OCPP response for {action}")
    if not isinstance(frame[2], dict):
        raise RuntimeError(f"Invalid OCPP result for {action}")
    return frame[2]


async def seed_demo_sessions(
    http: httpx.AsyncClient, api_url: str, ocpp_url: str
) -> list[dict[str, Any]]:
    """Create seven reserved examples once; verify/reuse them on subsequent runs."""
    api_url, ocpp_url = api_url.rstrip("/"), ocpp_url.rstrip("/")

    async def get(path: str) -> Any:
        response = await http.get(api_url + path)
        response.raise_for_status()
        return response.json()

    async def create(path: str, identifier: str) -> None:
        response = await http.post(api_url + path, json={"id": identifier})
        if response.status_code != 409:
            response.raise_for_status()

    stations = {record["id"] for record in await get("/api/stations")}
    tags = {record["id"] for record in await get("/api/id-tags")}
    if TAGS["Invalid"] in tags:
        raise RuntimeError("DEMO-UNKNOWN-TAG must remain unregistered; no records were changed")

    existing: dict[str, dict[str, Any]] = {}
    # Inspect every reserved station before writes. Never silently repair altered data.
    for example in EXAMPLES:
        if example.station in stations:
            history = await get(f"/api/stations/{example.station}/sessions")
            if history:
                if len(history) != 1:
                    raise RuntimeError(f"{example.station}: expected one reserved demo session")
                verify(example, history[0])
                existing[example.station] = history[0]

    results = []
    for example in EXAMPLES:
        if example.station in existing:
            results.append(existing[example.station])
            continue
        if example.station not in stations:
            await create("/api/stations", example.station)
        tag = TAGS[example.authorization]
        if example.authorization != "Invalid":
            if tag not in tags:
                await create("/api/id-tags", tag)
                tags.add(tag)
            response = await http.put(
                f"{api_url}/api/stations/{example.station}/permissions/{tag}",
                json={"enabled": example.authorization == "Accepted"},
            )
            response.raise_for_status()
        async with connect(
            ocpp_url + "/" + example.station,
            subprotocols=["ocpp1.6"],
            open_timeout=3,
            close_timeout=1,
            max_size=65536,
            max_queue=1,
            compression=None,
            proxy=None,
        ) as ws:
            authorized = await call(ws, "Authorize", {"idTag": tag})
            if authorized != {"idTagInfo": {"status": example.authorization}}:
                raise RuntimeError(f"{example.station}: unexpected authorization result")
            started = await call(
                ws,
                "StartTransaction",
                {
                    "connectorId": 1,
                    "idTag": tag,
                    "meterStart": example.start_wh,
                    "timestamp": stamp(START),
                },
            )
            transaction_id = started["transactionId"]
            if (
                type(transaction_id) is not int
                or started["idTagInfo"]["status"] != example.authorization
            ):
                raise RuntimeError(f"{example.station}: invalid Start result")
            if example.sample is not None:
                await call(
                    ws,
                    "MeterValues",
                    {
                        "connectorId": 1,
                        "transactionId": transaction_id,
                        "meterValue": [
                            {
                                "timestamp": stamp(START + timedelta(minutes=example.minutes)),
                                "sampledValue": [{"value": example.sample}],
                            }
                        ],
                    },
                )
            if example.stop_wh is not None:
                await call(
                    ws,
                    "StopTransaction",
                    {
                        "transactionId": transaction_id,
                        "meterStop": example.stop_wh,
                        "timestamp": stamp(START + timedelta(minutes=example.minutes)),
                    },
                )
            elif example.reboot:
                await call(
                    ws,
                    "BootNotification",
                    {"chargePointVendor": "Demo", "chargePointModel": "Review examples"},
                )
        record = await get(f"/api/sessions/{transaction_id}")
        verify(example, record)
        results.append(record)
    return results


async def main() -> None:
    async with asyncio.timeout(60), httpx.AsyncClient(timeout=5) as http:
        records = await seed_demo_sessions(http, "http://backend:8000", "ws://backend:9000/ocpp")
    print("Illustrative event times: 2026-10-17 UTC; active does not mean a live socket.")
    for record in records:
        energy = "unknown" if record["energyKWh"] is None else f"{record['energyKWh']} kWh"
        print(
            f"{record['stationId']}: {record['lifecycle']}, {record['authorizationStatus']}, "
            f"meter={record['meterQuality']}, energy={energy}, "
            f"http://localhost:8080/sessions/{record['transactionId']}"
        )


if __name__ == "__main__":
    asyncio.run(main())
