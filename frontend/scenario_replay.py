"""Bounded playback of fixed fixtures using the unchanged public replay helpers.

Correlation sees full frames before display truncation; saved values and clocks
are local to each case. No admission, permission management or database access.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import quote

from websockets.asyncio.client import ClientConnection, connect
from websockets.typing import Subprotocol

from .scenario_catalog import FILES

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
        # Display limits must not hide the transaction IDs needed for API verification.
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
