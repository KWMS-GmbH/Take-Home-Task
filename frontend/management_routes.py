"""Dashboard, station/tag permissions and explicit authorization-check routes."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from flask import Flask, flash, redirect, render_template, request, url_for

from .backend_client import (
    AuthorizationProbeUnavailable,
    Authorizer,
    BackendAPI,
    BackendConflict,
    BackendRejected,
)
from .view_models import dashboard_data, normalize_auth_result, station_identifier, tag_identifier


async def _call_authorizer(
    probe: Authorizer, url: str, station_id: str, id_tag: str
) -> Mapping[str, Any]:
    return await probe(url, station_id, id_tag)


def register_management_routes(app: Flask, api: BackendAPI, probe: Authorizer) -> None:
    def render_dashboard(
        *,
        authorization_result: dict[str, Any] | None = None,
        selected_station: str = "",
        selected_tag: str = "",
    ) -> Any:
        data = dashboard_data(api)
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

    @app.post("/stations")
    def create_station() -> Any:
        try:
            station_id = station_identifier(request.form.get("station_id", ""))
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
            tag_id = tag_identifier(request.form.get("id_tag", ""))
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
            station_id = station_identifier(request.form.get("station_id", ""))
            tag_id = tag_identifier(request.form.get("id_tag", ""))
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
            station_id = station_identifier(request.form.get("station_id", ""))
            tag_id = tag_identifier(request.form.get("id_tag", ""))
            raw_result = asyncio.run(
                _call_authorizer(probe, app.config["OCPP_URL"], station_id, tag_id)
            )
            result = normalize_auth_result(raw_result)
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
