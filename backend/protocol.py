"""Bounded OCPP ingress using the library's public message/schema APIs.

Filter raw protocol names before any transformation: the brief deliberately accepts
extra fields while the library's strict schemas don't. No private dispatcher hooks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Mapping
from typing import Any

from ocpp.exceptions import OCPPError
from ocpp.messages import Call, validate_payload
from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from backend.service import (
    DependencyUnavailable,
    RequestConflict,
    Service,
    StaleConnection,
    ValidationError,
)

LOGGER = logging.getLogger(__name__)
STATION_PATH = re.compile(r"/ocpp/([A-Za-z0-9][A-Za-z0-9._:-]{0,63})\Z")
FIELDS = {
    "BootNotification": ("chargePointVendor", "chargePointModel"),
    "Authorize": ("idTag",),
    "StatusNotification": ("connectorId", "status", "errorCode", "timestamp"),
    "StartTransaction": ("connectorId", "idTag", "meterStart", "timestamp"),
    "MeterValues": ("connectorId", "transactionId", "meterValue"),
    "StopTransaction": ("transactionId", "meterStop", "timestamp", "reason"),
}
SUPPORTED = frozenset({"BootNotification", "Authorize"})


class InputError(ValueError):
    def __init__(self, code: str, description: str, unique_id: str | None = None):
        super().__init__(description)
        self.code = code
        self.unique_id = unique_id


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


def filter_payload(action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Only exact names listed in the brief matter, including inside meters."""
    filtered = {key: payload[key] for key in FIELDS[action] if key in payload}
    if action == "MeterValues" and isinstance(filtered.get("meterValue"), list):
        meters = []
        for meter in filtered["meterValue"]:
            if not isinstance(meter, dict):
                meters.append(meter)  # Retain bad types for schema validation.
                continue
            item = {key: meter[key] for key in ("timestamp", "sampledValue") if key in meter}
            if isinstance(item.get("sampledValue"), list):
                item["sampledValue"] = [
                    {"value": sample["value"]}
                    if isinstance(sample, dict) and "value" in sample
                    else {}
                    if isinstance(sample, dict)
                    else sample
                    for sample in item["sampledValue"]
                ]
            meters.append(item)
        filtered["meterValue"] = meters
    return filtered


def parse_call(raw: str) -> Call:
    try:
        frame = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    except (ValueError, RecursionError) as exc:
        raise InputError("FormationViolation", "Invalid JSON") from exc
    unique_id = None
    if isinstance(frame, list) and len(frame) > 1:
        candidate = frame[1]
        if isinstance(candidate, str) and 1 <= len(candidate) <= 36:
            unique_id = candidate
    if (
        not isinstance(frame, list)
        or len(frame) != 4
        or type(frame[0]) is not int
        or frame[0] != 2
        or unique_id is None
        or not isinstance(frame[2], str)
        or not isinstance(frame[3], dict)
    ):
        raise InputError("ProtocolError", "Expected a valid CALL frame", unique_id)
    action = frame[2]
    if action not in FIELDS:
        raise InputError("NotSupported", "Action outside assignment scope", unique_id)
    if action not in SUPPORTED:
        raise InputError("NotImplemented", "Action belongs to a later work package", unique_id)
    payload = filter_payload(action, frame[3])
    try:
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except UnicodeError as exc:
        raise InputError("PropertyConstraintViolation", "Invalid Unicode text", unique_id) from exc
    return Call(unique_id, action, payload)


def error_frame(unique_id: str, code: str, description: str) -> str:
    # Never echo library validation details or arbitrary untrusted payloads.
    return json.dumps([4, unique_id, code, description, {}], separators=(",", ":"))


class OcppServer:
    def __init__(self, service: Service, instance_id: str, *, max_connections: int = 128):
        self.service = service
        self.instance_id = instance_id
        self.max_connections = max_connections
        self.connections: set[ServerConnection] = set()

    def handshake(self, connection: ServerConnection, request: Request) -> Response | None:
        if STATION_PATH.fullmatch(request.path) is None:
            return connection.respond(404, "Unknown OCPP path\n")
        if len(self.connections) >= self.max_connections:
            return connection.respond(503, "Connection capacity reached\n")
        return None

    async def handle(self, connection: ServerConnection) -> None:
        if len(self.connections) >= self.max_connections:
            await connection.close(1013, "Connection capacity reached")
            return
        self.connections.add(connection)
        connection_id = str(uuid.uuid4())
        assert connection.request is not None
        match = STATION_PATH.fullmatch(connection.request.path)
        assert match is not None
        station_id = match[1]
        context = {"station": station_id, "connection": connection_id}
        try:
            generation = await self.service.register_connection(
                station_id, connection_id, self.instance_id
            )
            LOGGER.info("ocpp_connected", extra=context)
            tokens = 40.0
            previous = time.monotonic()
            for _ in range(10_000):
                try:
                    raw = await asyncio.wait_for(connection.recv(), timeout=300)
                except TimeoutError:
                    await connection.close(1001, "Idle timeout; reconnect")
                    return
                now = time.monotonic()
                tokens = min(40.0, tokens + (now - previous) * 20)
                previous = now
                if tokens < 1:
                    await connection.close(1008, "Message rate exceeded")
                    return
                tokens -= 1
                if not isinstance(raw, str):
                    await connection.close(1003, "Text frames required")
                    return
                try:
                    call = parse_call(raw)
                except InputError as exc:
                    if exc.unique_id is not None:
                        await connection.send(error_frame(exc.unique_id, exc.code, str(exc)))
                    continue
                try:
                    await validate_payload(call, "1.6")
                except OCPPError as exc:
                    await connection.send(
                        error_frame(call.unique_id, exc.code, "Invalid request payload")
                    )
                    continue

                async def validate_response(payload: dict[str, Any], request: Call = call) -> None:
                    await validate_payload(request.create_call_result(payload), "1.6")

                try:
                    result = await self.service.handle_ocpp(
                        station_id,
                        generation,
                        connection_id,
                        self.instance_id,
                        call.unique_id,
                        call.action,
                        call.payload,
                        validate_response,
                    )
                except StaleConnection:
                    await connection.close(1008, "Superseded station connection")
                    return
                except RequestConflict:
                    await connection.send(
                        error_frame(call.unique_id, "ProtocolError", "Conflicting request ID reuse")
                    )
                    continue
                except ValidationError:
                    await connection.send(
                        error_frame(
                            call.unique_id, "PropertyConstraintViolation", "Invalid request payload"
                        )
                    )
                    continue
                except DependencyUnavailable:
                    await connection.send(
                        error_frame(
                            call.unique_id, "InternalError", "Database temporarily unavailable"
                        )
                    )
                    continue
                # The application has committed and released its transaction before send.
                await connection.send(call.create_call_result(result).to_json())
                LOGGER.info(
                    "ocpp_response",
                    extra={**context, "message_id": call.unique_id, "action": call.action},
                )
            await connection.close(1001, "Request limit; reconnect")
        except ConnectionClosed:
            pass
        except Exception as exc:
            LOGGER.error(
                "ocpp_connection_failed", extra={**context, "error_type": type(exc).__name__}
            )
            await connection.close(1011, "Service unavailable; reconnect")
        finally:
            self.connections.discard(connection)
            LOGGER.info("ocpp_disconnected", extra=context)
