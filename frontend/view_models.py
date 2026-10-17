"""HTTP-backed dashboard data and view validation; no charging decisions."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from .backend_client import BackendAPI, BackendUnavailable

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
MAX_STATION_ID_LENGTH = 64
MAX_ID_TAG_LENGTH = 20
MAX_AUTH_FRAMES = 20
MAX_AUTH_FRAME_TEXT_LENGTH = 2_000
MAX_SIMULATION_STATION_LENGTH = 58


def validate_identifier(value: str, *, label: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} is required.")
    if len(normalized) > max_length:
        raise ValueError(f"{label} must be at most {max_length} characters.")
    if not IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(
            f"{label} must start with a letter or number and may also contain dots, "
            "underscores, colons, and hyphens."
        )
    return normalized


def station_identifier(value: str) -> str:
    return validate_identifier(
        value,
        label="Station ID",
        max_length=MAX_STATION_ID_LENGTH,
    )


def tag_identifier(value: str) -> str:
    return validate_identifier(value, label="ID tag", max_length=MAX_ID_TAG_LENGTH)


def record_list(value: Any, *, resource: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise BackendUnavailable(f"Unexpected {resource} response")
    return value


def load_stations(api: BackendAPI) -> list[dict[str, Any]]:
    stations_raw = record_list(api.get("/api/stations"), resource="station")
    stations: list[dict[str, Any]] = []
    for record in stations_raw:
        station_id = record.get("id")
        active_generation = record.get("activeGeneration")
        if (
            not isinstance(station_id, str)
            or type(active_generation) is not int
            or active_generation < 0
        ):
            raise BackendUnavailable("Unexpected station response")
        stations.append(
            {
                "id": station_id,
                "vendor": record.get("vendor") if isinstance(record.get("vendor"), str) else None,
                "model": record.get("model") if isinstance(record.get("model"), str) else None,
                "activeGeneration": active_generation,
            }
        )
    return sorted(stations, key=lambda item: item["id"])


def dashboard_data(api: BackendAPI) -> dict[str, Any]:
    stations = load_stations(api)
    tags_raw = record_list(api.get("/api/id-tags"), resource="ID tag")
    permissions_raw = record_list(api.get("/api/permissions"), resource="permission")

    tags: list[dict[str, str]] = []
    for record in tags_raw:
        tag_id = record.get("id")
        if not isinstance(tag_id, str):
            raise BackendUnavailable("Unexpected ID tag response")
        tags.append({"id": tag_id})

    permissions: dict[tuple[str, str], bool] = {}
    for record in permissions_raw:
        station_id = record.get("stationId")
        tag_id = record.get("idTag")
        enabled = record.get("enabled")
        if (
            not isinstance(station_id, str)
            or not isinstance(tag_id, str)
            or not isinstance(enabled, bool)
        ):
            raise BackendUnavailable("Unexpected permission response")
        permissions[(station_id, tag_id)] = enabled

    tags.sort(key=lambda item: item["id"])
    return {"stations": stations, "tags": tags, "permissions": permissions}


def session_record(value: Any) -> dict[str, Any]:
    """Fail safely on a broken internal contract instead of inventing session facts."""

    if not isinstance(value, Mapping):
        raise BackendUnavailable("Unexpected session response")
    record = dict(value)
    for key in ("transactionId", "connectorId"):
        if type(record.get(key)) is not int or record[key] < 0:
            raise BackendUnavailable("Unexpected session identifier")
    for key in ("stationId", "idTag", "startedAt"):
        if not isinstance(record.get(key), str):
            raise BackendUnavailable("Unexpected session response")
    for key, allowed in (
        ("lifecycle", {"active", "completed", "interrupted"}),
        ("authorizationStatus", {"Accepted", "Blocked", "Invalid"}),
        ("meterQuality", {"valid", "unknown", "suspicious"}),
    ):
        if not isinstance(record.get(key), str) or record[key] not in allowed:
            raise BackendUnavailable("Unexpected session status")
    for key in ("stoppedAt", "lastMeterAt"):
        if record.get(key) is not None and not isinstance(record[key], str):
            raise BackendUnavailable("Unexpected session timestamp")
    for key in (
        "durationSeconds",
        "energyKWh",
        "meterStartWh",
        "meterStopWh",
        "lastMeterWh",
    ):
        number = record.get(key)
        if number is not None and (
            type(number) not in {int, float}
            or not math.isfinite(number)
            or (key in {"durationSeconds", "energyKWh"} and number < 0)
        ):
            raise BackendUnavailable("Unexpected session measurement")
    for key in ("energyProvisional", "billingEligible"):
        if type(record.get(key)) is not bool:
            raise BackendUnavailable("Unexpected session eligibility")
    reasons = record.get("reviewReasons")
    if not isinstance(reasons, list) or not all(isinstance(reason, str) for reason in reasons):
        raise BackendUnavailable("Unexpected session review reasons")
    return record


def bounded_text(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def normalize_auth_result(raw: Mapping[str, Any]) -> dict[str, Any]:
    status = raw.get("status")
    if not isinstance(status, str) or not status:
        status = "Unavailable"
    status = bounded_text(status, 40)

    error = raw.get("error")
    display_error = bounded_text(error, 240) if error else None

    raw_frames = raw.get("frames")
    frames: list[dict[str, str | None]] = []
    if isinstance(raw_frames, list):
        for frame in raw_frames[:MAX_AUTH_FRAMES]:
            if not isinstance(frame, Mapping):
                continue
            kind = frame.get("kind")
            if kind not in {"send", "recv", "info", "pass", "warn", "fail"}:
                kind = "info"
            connection = frame.get("conn")
            frames.append(
                {
                    "kind": kind,
                    "conn": bounded_text(connection, 32) if connection else None,
                    "text": bounded_text(frame.get("text", ""), MAX_AUTH_FRAME_TEXT_LENGTH),
                }
            )

    return {"status": status, "error": display_error, "frames": frames}
