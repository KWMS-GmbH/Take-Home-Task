"""Request-local suite coordinator: lease, real preflight, pacing and cleanup.

Reports are temporary; committed charging sessions/audit remain durable. Normal
snapshots stay inside the execution deadline; post-deadline partial-evidence
collection and lease release have separate bounds.
"""

from __future__ import annotations

import asyncio
import math
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from .scenario_catalog import FILES as FILES
from .scenario_replay import Collector as Collector
from .scenario_replay import TextBudget as TextBudget
from .scenario_replay import empty_case as empty_case
from .scenario_replay import execute_case, fixture, replay_module
from .scenario_replay import render as render
from .simulation_api import APIError, api_request, snapshot


async def authorization(
    ocpp_url: str,
    station: str,
    tag: str,
    token: str,
    budget: TextBudget,
    state: dict[str, Any],
) -> dict[str, Any]:
    spec = {
        "steps": [
            {"conn": "auth", "open": station},
            {"conn": "auth", "call": "Authorize", "payload": {"idTag": tag}},
        ]
    }
    case = empty_case(FILES[0], {"name": "Authorization"}, station)
    collector = Collector(case, budget)
    state.update(status=None, error="Authorization did not finish", frames=case["events"])
    await execute_case(spec, collector, ocpp_url, {}, datetime.now(UTC), token)
    status = None
    for call in collector.calls:
        if call["responseType"] == 3:
            info = call["response"].get("idTagInfo")
            if isinstance(info, dict):
                status = info.get("status")
    state.update(
        status=status,
        error=None if status in {"Accepted", "Blocked", "Invalid"} else "Authorization unavailable",
    )
    return state


def _validate_run(
    station_id: str, id_tag: str, blocked_id_tag: str, timeout_seconds: float
) -> None:
    identifiers = ((station_id, 58), (id_tag, 20), (blocked_id_tag, 20))
    if (
        any(
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", value) is None
            for value, maximum in identifiers
        )
        or id_tag == blocked_id_tag
    ):
        raise ValueError("Simulation needs a short station ID and two distinct tags")
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 20:
        raise ValueError("Simulation timeout must be finite and at most twenty seconds")


async def run_simulation(
    api_url: str,
    ocpp_url: str,
    station_id: str,
    id_tag: str,
    blocked_id_tag: str,
    *,
    timeout_seconds: float = 20,
    http: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Run one bounded suite; an injected HTTP client supports real API integration tests."""
    _validate_run(station_id, id_tag, blocked_id_tag, timeout_seconds)
    if http is None:
        async with httpx.AsyncClient(timeout=2, follow_redirects=False) as client:
            return await run_simulation(
                api_url,
                ocpp_url,
                station_id,
                id_tag,
                blocked_id_tag,
                timeout_seconds=timeout_seconds,
                http=client,
            )
    specs = [fixture(file) for file in FILES]
    cases = [empty_case(file, spec, station_id) for file, spec in zip(FILES, specs, strict=True)]
    report: dict[str, Any] = {
        "stationId": station_id,
        "idTag": id_tag,
        "blockedIdTag": blocked_id_tag,
        "status": "unavailable",
        "error": None,
        "authorization": {"status": None, "error": None, "frames": []},
        "secondaryAuthorization": {"status": None, "error": None, "frames": []},
        "scenarios": cases,
    }
    token: str | None = None
    active: Collector | None = None
    seen_ids: set[int] = set()
    budget = TextBudget()
    lease_path = f"/api/stations/{quote(station_id, safe='')}/simulation-lease"
    try:
        async with asyncio.timeout(timeout_seconds):
            lease = await api_request(http, api_url, lease_path, payload={})
            if not isinstance(lease, dict) or not all(
                isinstance(lease.get(key), str) for key in ("token", "notBefore", "expiresAt")
            ):
                raise ValueError("Invalid admission response")
            # Retain the token so malformed timing metadata still permits release.
            token = lease["token"]
            not_before = datetime.fromisoformat(lease["notBefore"].replace("Z", "+00:00"))
            expiry = datetime.fromisoformat(lease["expiresAt"].replace("Z", "+00:00"))
            if (
                not isinstance(token, str)
                or not_before.utcoffset() is None
                or expiry.utcoffset() is None
            ):
                raise ValueError("Invalid admission response")
            UUID(token)
            report["authorization"] = await authorization(
                ocpp_url, station_id, id_tag, token, budget, report["authorization"]
            )
            primary = report["authorization"]["status"]
            if primary != "Accepted":
                report["status"] = "denied" if primary in {"Blocked", "Invalid"} else "unavailable"
                report["error"] = "Primary tag must receive Accepted before scenarios can run."
                return report
            report["secondaryAuthorization"] = await authorization(
                ocpp_url,
                station_id,
                blocked_id_tag,
                token,
                budget,
                report["secondaryAuthorization"],
            )
            secondary = report["secondaryAuthorization"]["status"]
            if secondary not in {"Blocked", "Invalid"}:
                report["status"] = "setup_error" if secondary == "Accepted" else "unavailable"
                report["error"] = (
                    "Choose a distinct second tag that is Blocked or Invalid; "
                    "permissions were not changed."
                )
                return report
            await asyncio.sleep(max(0, (not_before - datetime.now(UTC)).total_seconds()))
            previous_start: float | None = None
            for case, spec in zip(cases, specs, strict=True):
                if previous_start is not None:
                    await asyncio.sleep(
                        max(0, previous_start + 1.1 - asyncio.get_running_loop().time())
                    )
                previous_start = asyncio.get_running_loop().time()
                instant = datetime.now(UTC).replace(microsecond=0)
                params = replay_module().make_params(station_id, id_tag, blocked_id_tag)
                active = Collector(case, budget)
                await execute_case(spec, active, ocpp_url, params, instant, token)
                await snapshot(active, http, api_url, station_id, id_tag, blocked_id_tag, seen_ids)
                if case["executionStatus"] != "completed":
                    case["dataStatus"] = "unverified"
                    report.update(
                        status="unavailable",
                        error=(
                            "A scenario could not finish. Later cases were not run; "
                            "committed records remain."
                        ),
                    )
                    return report
                active = None
            report["status"] = "completed"
    except TimeoutError:
        report.update(
            status="timed_out",
            error="Run deadline reached. Committed charging data was not rolled back.",
        )
        if active is not None:
            try:
                async with asyncio.timeout(2):
                    await snapshot(
                        active, http, api_url, station_id, id_tag, blocked_id_tag, seen_ids
                    )
                if active.case["executionStatus"] != "completed":
                    active.case["dataStatus"] = "unverified"
            except (TimeoutError, httpx.HTTPError):
                active.case["dataStatus"] = "unverified"
    except APIError as exc:
        report["status"] = (
            "busy"
            if exc.status == 409
            else "setup_error"
            if exc.status in {404, 422}
            else "unavailable"
        )
        report["error"] = "Simulation admission unavailable or busy; no permissions were changed."
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        report.update(
            status="unavailable",
            error="Simulation could not verify its required service responses.",
        )
    finally:
        if token is not None:
            try:
                async with asyncio.timeout(2):
                    await api_request(
                        http, api_url, lease_path + "/release", payload={"token": token}
                    )
            except (TimeoutError, APIError, httpx.HTTPError, ValueError):
                report["error"] = (
                    report["error"] or ""
                ) + " Admission release could not be confirmed; the short lease will expire."
    return report
