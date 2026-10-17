"""Flask operator UI backed exclusively by the backend HTTP and OCPP APIs."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote
from uuid import uuid4

import httpx
from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException
from websockets.typing import Subprotocol
from werkzeug.exceptions import RequestEntityTooLarge

from frontend.logging import configure_logging

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
MAX_STATION_ID_LENGTH = 64
MAX_ID_TAG_LENGTH = 20
MAX_AUTH_FRAMES = 20
MAX_AUTH_FRAME_TEXT_LENGTH = 2_000

Authorizer = Callable[[str, str, str], Awaitable[Mapping[str, Any]]]


async def _call_authorizer(
    probe: Authorizer, url: str, station_id: str, id_tag: str
) -> Mapping[str, Any]:
    return await probe(url, station_id, id_tag)


class BackendUnavailable(RuntimeError):
    """The internal backend cannot currently serve a valid response."""


class BackendConflict(RuntimeError):
    """A requested resource already exists."""


class BackendRejected(RuntimeError):
    """The backend rejected a management request."""


class BackendNotFound(RuntimeError):
    """A requested station or session does not exist."""


class AuthorizationProbeUnavailable(RuntimeError):
    """The OCPP authorization probe could not be completed."""


class BackendAPI(Protocol):
    """Small interface used by the UI and its in-memory test double."""

    def get(self, path: str) -> Any: ...

    def post(self, path: str, payload: Mapping[str, Any]) -> Any: ...

    def put(self, path: str, payload: Mapping[str, Any]) -> Any: ...


class HttpBackendAPI:
    """Bounded HTTP client for the internal management API."""

    def __init__(self, base_url: str, timeout_seconds: float = 3.0) -> None:
        timeout = httpx.Timeout(timeout_seconds, connect=timeout_seconds)
        limits = httpx.Limits(
            max_connections=10,
            max_keepalive_connections=5,
            keepalive_expiry=15.0,
        )
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            timeout=timeout,
            limits=limits,
            follow_redirects=False,
        )

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, payload: Mapping[str, Any]) -> Any:
        return self._request("POST", path, payload)

    def put(self, path: str, payload: Mapping[str, Any]) -> Any:
        return self._request("PUT", path, payload)

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        try:
            response = self._client.request(method, path.lstrip("/"), json=payload)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise BackendUnavailable from exc

        if response.status_code == 404:
            raise BackendNotFound
        if response.status_code == 409:
            raise BackendConflict
        if response.status_code >= 500:
            raise BackendUnavailable
        if response.status_code >= 400:
            raise BackendRejected
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise BackendUnavailable from exc


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.casefold() in {"1", "true", "yes", "on"}


def _read_secret(path: str) -> bytes:
    try:
        secret = Path(path).read_bytes().strip()
    except OSError as exc:
        raise RuntimeError(
            "Frontend secret is unavailable; provide SECRET_KEY for tests or mount "
            "FRONTEND_SECRET_KEY_FILE."
        ) from exc
    if len(secret) < 32:
        raise RuntimeError("Frontend secret must contain at least 32 bytes.")
    return secret


def _validate_identifier(value: str, *, label: str, max_length: int) -> str:
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


def _station_id(value: str) -> str:
    return _validate_identifier(
        value,
        label="Station ID",
        max_length=MAX_STATION_ID_LENGTH,
    )


def _id_tag(value: str) -> str:
    return _validate_identifier(value, label="ID tag", max_length=MAX_ID_TAG_LENGTH)


def _records(value: Any, *, resource: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise BackendUnavailable(f"Unexpected {resource} response")
    return value


def _stations(api: BackendAPI) -> list[dict[str, Any]]:
    stations_raw = _records(api.get("/api/stations"), resource="station")
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


def _dashboard_data(api: BackendAPI) -> dict[str, Any]:
    stations = _stations(api)
    tags_raw = _records(api.get("/api/id-tags"), resource="ID tag")
    permissions_raw = _records(api.get("/api/permissions"), resource="permission")

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


def _default_authorizer(timeout_seconds: float) -> Authorizer:
    """Send one real Authorize, never an incidental Boot/reboot declaration."""

    async def authorize(ocpp_url: str, station_id: str, id_tag: str) -> Mapping[str, Any]:
        frames: list[dict[str, str]] = []

        async def exchange() -> str:
            message_id = uuid4().hex
            wire = json.dumps([2, message_id, "Authorize", {"idTag": id_tag}])
            # The base URL is deployment configuration, never supplied by the browser.
            # Approved punctuation is legal inside this path component; the OCPP
            # listener deliberately rejects encoded/ambiguous station paths.
            endpoint = f"{ocpp_url.rstrip('/')}/{quote(station_id, safe='._:-')}"
            async with connect(
                endpoint,
                subprotocols=[Subprotocol("ocpp1.6")],
                open_timeout=min(timeout_seconds, 3.0),
                close_timeout=1.0,
                max_size=64 * 1024,
                max_queue=1,
                compression=None,
                proxy=None,
            ) as socket:
                if socket.subprotocol != "ocpp1.6":
                    raise AuthorizationProbeUnavailable
                await socket.send(wire)
                frames.append({"kind": "send", "conn": "probe", "text": wire})
                raw = await socket.recv()
                if not isinstance(raw, str):
                    raise AuthorizationProbeUnavailable
                frames.append({"kind": "recv", "conn": "probe", "text": raw})
                frame = json.loads(raw)
                if (
                    not isinstance(frame, list)
                    or len(frame) != 3
                    or type(frame[0]) is not int
                    or frame[0] != 3
                    or frame[1] != message_id
                    or not isinstance(frame[2], dict)
                    or not isinstance(frame[2].get("idTagInfo"), dict)
                ):
                    raise AuthorizationProbeUnavailable
                status = frame[2]["idTagInfo"].get("status")
                if not isinstance(status, str) or status not in {"Accepted", "Blocked", "Invalid"}:
                    raise AuthorizationProbeUnavailable
                return str(status)

        try:
            status = await asyncio.wait_for(exchange(), timeout=timeout_seconds)
        except (
            AuthorizationProbeUnavailable,
            TimeoutError,
            OSError,
            ValueError,
            WebSocketException,
        ):
            return {
                "status": "Unavailable",
                "error": "The station returned no valid authorization result. Try again shortly.",
                "frames": frames,
            }
        return {"status": status, "error": None, "frames": frames}

    return authorize


def _session_record(value: Any) -> dict[str, Any]:
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


def _bounded_text(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _normalize_auth_result(raw: Mapping[str, Any]) -> dict[str, Any]:
    status = raw.get("status")
    if not isinstance(status, str) or not status:
        status = "Unavailable"
    status = _bounded_text(status, 40)

    error = raw.get("error")
    display_error = _bounded_text(error, 240) if error else None

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
                    "conn": _bounded_text(connection, 32) if connection else None,
                    "text": _bounded_text(frame.get("text", ""), MAX_AUTH_FRAME_TEXT_LENGTH),
                }
            )

    return {"status": status, "error": display_error, "frames": frames}


def create_app(
    test_config: Mapping[str, Any] | None = None,
    *,
    backend_client: BackendAPI | None = None,
    authorizer: Authorizer | None = None,
) -> Flask:
    """Create the frontend with injectable network boundaries for focused tests."""

    app = Flask(__name__)
    configure_logging(app)
    app.config.from_mapping(
        BACKEND_API_URL=os.getenv("BACKEND_API_URL", "http://backend:8000"),
        OCPP_URL=os.getenv("OCPP_URL", "ws://backend:9000/ocpp"),
        FRONTEND_SECRET_KEY_FILE=os.getenv(
            "FRONTEND_SECRET_KEY_FILE",
            "/run/secrets/frontend_secret",
        ),
        AUTHORIZATION_TIMEOUT_SECONDS=10.0,
        MAX_CONTENT_LENGTH=16 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_env_flag("SESSION_COOKIE_SECURE"),
    )
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _read_secret(app.config["FRONTEND_SECRET_KEY_FILE"])

    CSRFProtect(app)
    api = backend_client or HttpBackendAPI(app.config["BACKEND_API_URL"])
    probe = authorizer or _default_authorizer(float(app.config["AUTHORIZATION_TIMEOUT_SECONDS"]))
    app.extensions["backend_api"] = api
    app.extensions["ocpp_authorizer"] = probe

    @app.after_request
    def security_headers(response: Any) -> Any:
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "style-src 'self'; img-src 'self' data:; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    def render_dashboard(
        *,
        authorization_result: dict[str, Any] | None = None,
        selected_station: str = "",
        selected_tag: str = "",
    ) -> Any:
        data = _dashboard_data(api)
        return render_template(
            "dashboard.html",
            **data,
            authorization_result=authorization_result,
            selected_station=selected_station,
            selected_tag=selected_tag,
        )

    @app.get("/")
    @app.get("/dashboard")
    def dashboard() -> Any:
        return render_dashboard()

    @app.get("/sessions")
    def sessions() -> Any:
        stations = _stations(api)
        selected_station = request.args.get("station_id", "")
        records: list[dict[str, Any]] = []
        if selected_station:
            try:
                selected_station = _station_id(selected_station)
            except ValueError:
                raise BackendNotFound from None
            path = f"/api/stations/{quote(selected_station, safe='')}/sessions"
            records = [
                _session_record(item) for item in _records(api.get(path), resource="session")
            ]
            if len(records) > 100 or any(item["stationId"] != selected_station for item in records):
                raise BackendUnavailable("Unexpected station session response")
        return render_template(
            "sessions.html",
            stations=stations,
            selected_station=selected_station,
            sessions=records,
        )

    @app.get("/sessions/<int:transaction_id>")
    def session_detail(transaction_id: int) -> Any:
        if not 1 <= transaction_id <= 2_147_483_647:
            raise BackendNotFound
        record = _session_record(api.get(f"/api/sessions/{transaction_id}"))
        if record["transactionId"] != transaction_id:
            raise BackendUnavailable("Unexpected transaction response")
        return render_template("session_detail.html", session=record)

    @app.post("/stations")
    def create_station() -> Any:
        try:
            station_id = _station_id(request.form.get("station_id", ""))
            api.post("/api/stations", {"id": station_id})
        except ValueError as exc:
            flash(str(exc), "error")
        except BackendConflict:
            flash("That station already exists.", "warning")
        except BackendRejected:
            flash("The station was not accepted by the backend.", "error")
        else:
            flash(f"Station {station_id} created.", "success")
        return redirect(url_for("dashboard"), code=303)

    @app.post("/id-tags")
    def create_id_tag() -> Any:
        try:
            tag_id = _id_tag(request.form.get("id_tag", ""))
            api.post("/api/id-tags", {"id": tag_id})
        except ValueError as exc:
            flash(str(exc), "error")
        except BackendConflict:
            flash("That ID tag already exists.", "warning")
        except BackendRejected:
            flash("The ID tag was not accepted by the backend.", "error")
        else:
            flash(f"ID tag {tag_id} created.", "success")
        return redirect(url_for("dashboard"), code=303)

    @app.post("/permissions")
    def set_permission() -> Any:
        try:
            station_id = _station_id(request.form.get("station_id", ""))
            tag_id = _id_tag(request.form.get("id_tag", ""))
            enabled_raw = request.form.get("enabled")
            if enabled_raw not in {"true", "false"}:
                raise ValueError("Permission value is invalid.")
            enabled = enabled_raw == "true"
            path = (
                f"/api/stations/{quote(station_id, safe='')}/permissions/{quote(tag_id, safe='')}"
            )
            api.put(path, {"enabled": enabled})
        except ValueError as exc:
            flash(str(exc), "error")
        except (BackendRejected, BackendConflict):
            flash("The permission change was not accepted by the backend.", "error")
        else:
            outcome = "allowed" if enabled else "blocked"
            flash(f"{tag_id} is now {outcome} at {station_id}.", "success")
        return redirect(url_for("dashboard"), code=303)

    @app.post("/authorization/check")
    def check_authorization() -> Any:
        response_status = 200
        try:
            station_id = _station_id(request.form.get("station_id", ""))
            tag_id = _id_tag(request.form.get("id_tag", ""))
            raw_result = asyncio.run(
                _call_authorizer(probe, app.config["OCPP_URL"], station_id, tag_id)
            )
            result = _normalize_auth_result(raw_result)
            if result["status"] not in {"Accepted", "Blocked", "Invalid"}:
                response_status = 503
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard"), code=303)
        except (AuthorizationProbeUnavailable, TimeoutError, OSError):
            response_status = 503
            result = {
                "status": "Unavailable",
                "error": "The authorization service could not be reached. Try again shortly.",
                "frames": [],
            }
        except Exception as exc:
            # Keep attacker-controlled protocol content and RFID values out of logs.
            app.logger.error(
                "authorization_probe_failed",
                extra={"error_type": type(exc).__name__},
            )
            response_status = 503
            result = {
                "status": "Unavailable",
                "error": "The authorization probe failed safely. Try again shortly.",
                "frames": [],
            }
        return render_dashboard(
            authorization_result=result,
            selected_station=station_id,
            selected_tag=tag_id,
        ), response_status

    @app.get("/health/live")
    def health_live() -> Any:
        return jsonify(status="ok")

    @app.get("/health/ready")
    def health_ready() -> Any:
        try:
            status = api.get("/health/ready")
        except (BackendUnavailable, BackendRejected, BackendConflict, BackendNotFound):
            return jsonify(status="not_ready"), 503
        if not isinstance(status, Mapping) or status.get("status") not in {"ok", "ready"}:
            return jsonify(status="not_ready"), 503
        return jsonify(status="ready")

    @app.errorhandler(BackendUnavailable)
    @app.errorhandler(BackendRejected)
    def backend_unavailable(_error: RuntimeError) -> tuple[str, int]:
        return (
            render_template(
                "error.html",
                title="Backend unavailable",
                message="The management service could not be reached. Try again shortly.",
            ),
            503,
        )

    @app.errorhandler(BackendNotFound)
    def resource_not_found(_error: BackendNotFound) -> tuple[str, int]:
        return (
            render_template(
                "error.html",
                title="Record not found",
                message="This station or transaction does not exist.",
            ),
            404,
        )

    @app.errorhandler(CSRFError)
    def csrf_error(_error: CSRFError) -> tuple[str, int]:
        return (
            render_template(
                "error.html",
                title="Request expired",
                message="This form is no longer valid. Reload the dashboard and try again.",
            ),
            400,
        )

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error: RequestEntityTooLarge) -> tuple[str, int]:
        return (
            render_template(
                "error.html",
                title="Request too large",
                message="The submitted form exceeded the 16 KB request limit.",
            ),
            413,
        )

    return app
