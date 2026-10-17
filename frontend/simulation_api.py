"""Bounded simulation HTTP calls and exact post-case session snapshots."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .scenario_catalog import FILES
from .scenario_checks import check_scenario
from .scenario_replay import Collector


class APIError(RuntimeError):
    def __init__(self, status: int):
        self.status = status
        super().__init__("Simulation management API request failed")


async def api_request(
    http: httpx.AsyncClient,
    base: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
) -> Any:
    method = "GET" if payload is None else "POST"
    async with http.stream(method, base.rstrip("/") + path, json=payload) as response:
        if response.status_code >= 400:
            raise APIError(response.status_code)
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > 65536:
                raise ValueError("Simulation API result exceeded its bound")
        return json.loads(content)


async def snapshot(
    result: Collector,
    http: httpx.AsyncClient,
    api_url: str,
    station: str,
    tag: str,
    blocked: str,
    seen_ids: set[int],
) -> None:
    identifiers = []
    for call in result.calls:
        payload = call["response"]
        if call["action"] == "StartTransaction" and call["responseType"] == 3:
            identifier = payload.get("transactionId") if isinstance(payload, dict) else None
            if type(identifier) is int and 1 <= identifier <= 2_147_483_647:
                identifiers.append(identifier)
    identifiers = sorted(set(identifiers))
    result.case["transactionIds"] = identifiers
    result.case["sessions"] = []
    try:
        for identifier in identifiers:
            record = await api_request(http, api_url, f"/api/sessions/{identifier}")
            if not isinstance(record, dict):
                raise ValueError("Session API did not return an object")
            result.case["sessions"].append(record)
        checks = check_scenario(
            result.case["file"], result.calls, result.case["sessions"], station, tag, blocked
        )
        checks.append(
            {
                "name": "Distinct scenario transaction identity",
                "passed": bool(identifiers) and not seen_ids.intersection(identifiers),
                "detail": "Different scenarios must not accidentally share a Start retry identity.",
            }
        )
        if result.case["file"] == FILES[6]:
            health = await api_request(http, api_url, "/health/ready")
            checks.append(
                {
                    "name": "Backend remains ready after garbage",
                    "passed": isinstance(health, dict) and health.get("status") == "ok",
                    "detail": "Separate HTTP readiness observation after malformed calls.",
                }
            )
        result.case["dataChecks"] = checks
        result.case["dataStatus"] = (
            "passed" if checks and all(item["passed"] is True for item in checks) else "failed"
        )
    except (APIError, httpx.HTTPError, ValueError, KeyError, TypeError):
        result.case["dataStatus"] = "unverified"
        result.case["dataChecks"] = [
            {
                "name": "Session data available and valid",
                "passed": None,
                "detail": (
                    "Exact session API data could not be verified; "
                    "protocol success is insufficient."
                ),
            }
        ]
    # An unverified snapshot must still reserve its observed Start identity.
    seen_ids.update(identifiers)
