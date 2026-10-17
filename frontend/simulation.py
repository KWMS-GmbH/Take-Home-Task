"""Bounded request-local adapter around the preserved replay helpers.

Only fixed bundled scenarios are executed. PostgreSQL owns admission/fencing;
reports are temporary, while the resulting sessions/audit stay durable.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.typing import Subprotocol

from frontend.scenario_checks import check_scenario

FILES = (
    "01_happy_path.json",
    "02_duplicate_stop.json",
    "03_reboot_no_stop.json",
    "04_late_stop.json",
    "05_timezone_offsets.json",
    "06_meter_goes_backwards.json",
    "07_two_connections_and_garbage.json",
    "08_blocked_idtag.json",
)
MAX_TEXT = 4096
MAX_EVENTS = 128
MAX_REPORT_TEXT = 256_000


@lru_cache(maxsize=1)
def replay_module() -> ModuleType:
    directory = Path("/app/provided")
    if not directory.is_dir():
        directory = Path(__file__).resolve().parents[1] / "take-home"
    spec = importlib.util.spec_from_file_location(
        "provided_scenario_replay", directory / "replay.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Bundled replay helper unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(file: str) -> dict[str, Any]:
    if file not in FILES:
        raise ValueError("Only bundled scenarios are supported")
    path = Path(replay_module().__file__ or "").parent / "scenarios" / file
    result: dict[str, Any] = json.loads(path.read_text())
    return result


def render(value: Any, saved: dict[str, Any], params: dict[str, Any], instant: datetime) -> Any:
    """Same relative-time semantics, one stable clock per scenario, no global patch."""
    replay = replay_module()
    if isinstance(value, dict):
        return {key: render(item, saved, params, instant) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, saved, params, instant) for item in value]
    if not isinstance(value, str):
        return value
    if value.startswith("$") and value[1:] in saved:
        return saved[value[1:]]
    for name in sorted(params, key=len, reverse=True):
        value = value.replace(f"${name}", str(params[name]))

    def timestamp(match: Any) -> str:
        amount, unit, offset = int(match[1]), match[2], match[3]
        stamp = instant + timedelta(**{replay.UNITS[unit]: amount})
        if not offset:
            return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
        sign = 1 if offset[0] == "+" else -1
        hours, minutes = map(int, offset[1:].split(":"))
        return stamp.astimezone(
            timezone(sign * timedelta(hours=hours, minutes=minutes))
        ).isoformat()

    rendered: str = replay.TS_RE.sub(timestamp, value)
    return rendered


@dataclass
class TextBudget:
    remaining: int = MAX_REPORT_TEXT


class Collector:
    """Duck-typed replay Result, preserving partial evidence and exact correlation."""

    def __init__(self, case: dict[str, Any], budget: TextBudget):
        self.case, self.budget = case, budget
        self.fails = 0
        self.warns = 0
        self.stations: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []
        self.pending: dict[tuple[str, str], dict[str, Any]] = {}

    def log(self, kind: str, conn: str | None, text: str) -> None:
        self._correlate(kind, conn, text)
        if len(self.case["events"]) >= MAX_EVENTS or self.budget.remaining <= 0:
            self.case["framesTruncated"] = True
            return
        cap = min(MAX_TEXT, self.budget.remaining)
        bounded = text[:cap]
        if len(text) > cap:
            bounded += " … [truncated]"
            self.case["framesTruncated"] = True
        self.budget.remaining -= len(bounded)
        self.case["events"].append({"kind": kind, "conn": conn, "text": bounded})

    def _correlate(self, kind: str, conn: str | None, text: str) -> None:
        if conn is None or kind not in {"send", "recv"}:
            return
        try:
            frame = json.loads(text)
        except (ValueError, RecursionError):
            return
        if not isinstance(frame, list) or len(frame) < 3 or not isinstance(frame[1], str):
            return
        key = (conn, frame[1])
        if kind == "send" and frame[0] == 2 and len(frame) == 4 and isinstance(frame[3], dict):
            call = {
                "conn": conn,
                "stationId": self.stations.get(conn, ""),
                "messageId": frame[1],
                "action": frame[2],
                "request": frame[3],
                "response": None,
                "responseType": None,
            }
            self.calls.append(call)
            self.pending[key] = call
        elif kind == "recv" and key in self.pending:
            call = self.pending[key]
            if frame[0] == 3 and len(frame) == 3 and isinstance(frame[2], dict):
                call.update(response=frame[2], responseType=3)
            elif frame[0] == 4 and len(frame) == 5:
                call.update(
                    response={"errorCode": frame[2], "description": frame[3]}, responseType=4
                )

    def ok(self, message: str) -> None:
        self.log("pass", None, message)

    def fail(self, message: str) -> None:
        self.fails += 1
        self.log("fail", None, message)

    def warn(self, message: str) -> None:
        self.warns += 1
        self.log("warn", None, message)

    def finish(self) -> None:
        self.case.update(fails=self.fails, warns=self.warns, passed=self.fails == 0)


def empty_case(file: str, spec: dict[str, Any], station: str) -> dict[str, Any]:
    return {
        "file": file,
        "name": spec["name"],
        "description": spec.get("description", ""),
        "check": [line.replace("$station", station) for line in spec.get("check", [])],
        "charge_points": [],
        "fails": 0,
        "warns": 0,
        "passed": False,
        "events": [],
        "framesTruncated": False,
        "executionStatus": "not_run",
        "dataStatus": "not_run",
        "dataChecks": [],
        "sessions": [],
        "transactionIds": [],
    }


async def execute_case(
    spec: dict[str, Any],
    result: Collector,
    ocpp_url: str,
    params: dict[str, Any],
    instant: datetime,
    token: str,
) -> None:
    replay = replay_module()
    connections: dict[str, ClientConnection] = {}
    saved: dict[str, Any] = {}
    result.case["check"] = render(spec.get("check", []), {}, params, instant)
    result.case["executionStatus"] = "partial"
    try:
        for raw_step in spec["steps"]:
            step = render(raw_step, saved, params, instant)
            if "sleep" in step:
                await asyncio.sleep(step["sleep"])
            elif "open" in step:
                station = step["open"]
                result.log("info", step["conn"], f"open {station}")
                ws = await connect(
                    ocpp_url.rstrip("/") + "/" + quote(station, safe="._:-"),
                    subprotocols=[Subprotocol("ocpp1.6")],
                    additional_headers={"X-Simulation-Token": token},
                    open_timeout=2,
                    close_timeout=0.25,
                    max_size=65536,
                    max_queue=8,
                    compression=None,
                    proxy=None,
                )
                if ws.subprotocol != "ocpp1.6":
                    await ws.close()
                    raise ValueError("OCPP subprotocol was not negotiated")
                connections[step["conn"]] = ws
                result.stations[step["conn"]] = station
                result.case["charge_points"] = sorted(set(result.stations.values()))
            elif step.get("close"):
                await connections.pop(step["conn"]).close()
                result.log("info", step["conn"], "close")
            elif step.get("conn") not in connections:
                raise ValueError("Scenario connection is not open")
            elif "raw" in step:
                await replay.do_raw(step, connections, result, 1.5)
            elif "call" in step:
                await replay.do_call(step, connections, saved, params, result, 1.5)
            if step.get("conn") in connections:
                if connections[step["conn"]].close_code is not None:
                    if not step.get("may_close"):
                        result.warn("Server closed the scenario connection")
                    connections.pop(step["conn"])
        result.case["executionStatus"] = "completed"
    except asyncio.CancelledError:
        result.fail("Scenario interrupted by the overall run deadline")
        raise
    except Exception as exc:
        result.fail(f"Scenario could not finish ({type(exc).__name__})")
    finally:
        try:
            async with asyncio.timeout(2):
                await asyncio.gather(*(ws.close() for ws in connections.values()))
        except TimeoutError:
            result.warn("Connection cleanup exceeded its bound")
        result.finish()


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
    seen_ids.update(identifiers)


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
