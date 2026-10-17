"""Explicit simulation form submission and trustworthy report presentation."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from flask import Flask, flash, render_template, request

from .backend_client import BackendAPI, BackendUnavailable, Simulator
from .simulation_report import simulation_report
from .view_models import (
    MAX_SIMULATION_STATION_LENGTH,
    load_stations,
    record_list,
    tag_identifier,
    validate_identifier,
)


async def _default_simulator(
    api_url: str, ocpp_url: str, station_id: str, id_tag: str, blocked_id_tag: str
) -> Mapping[str, Any]:
    from frontend.simulation import run_simulation

    return await run_simulation(api_url, ocpp_url, station_id, id_tag, blocked_id_tag)


async def _call_simulator(
    simulator: Simulator,
    api_url: str,
    ocpp_url: str,
    station_id: str,
    id_tag: str,
    blocked_id_tag: str,
) -> Mapping[str, Any]:
    return await simulator(api_url, ocpp_url, station_id, id_tag, blocked_id_tag)


def register_simulation_routes(app: Flask, api: BackendAPI, simulate: Simulator) -> None:
    @app.route("/simulation", methods=["GET", "POST"])
    def simulation() -> Any:
        # Permission is checked over OCPP inside the run, never inferred from the
        # dashboard matrix. Avoid an unrelated permission-list request here too.
        stations = load_stations(api)
        tags = record_list(api.get("/api/id-tags"), resource="ID tag")
        if any(not isinstance(tag.get("id"), str) for tag in tags):
            raise BackendUnavailable("Unexpected ID tag response")
        selected_station = request.form.get("station_id", request.args.get("station_id", ""))
        selected_tag = request.form.get("id_tag", "")
        blocked_tag = request.form.get("blocked_id_tag", "")
        context = {
            "stations": stations,
            "tags": tags,
            "selected_station": selected_station,
            "selected_tag": selected_tag,
            "blocked_tag": blocked_tag,
        }
        if request.method == "GET":
            return render_template("simulation.html", **context)
        try:
            station_id = validate_identifier(
                selected_station,
                label="Simulation station ID",
                max_length=MAX_SIMULATION_STATION_LENGTH,
            )
            tag_id = tag_identifier(selected_tag)
            blocked_id = tag_identifier(blocked_tag)
            if not any(station["id"] == station_id for station in stations):
                raise ValueError("Choose an existing station before running scenarios.")
            if tag_id == blocked_id:
                raise ValueError("Choose a different non-permitted tag for scenario 08.")
            if request.form.get("acknowledge_boot") != "yes":
                raise ValueError("Acknowledge the Boot and connection takeover effects first.")
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("simulation.html", **context), 400
        try:
            raw = asyncio.run(
                _call_simulator(
                    simulate,
                    app.config["BACKEND_API_URL"],
                    app.config["OCPP_URL"],
                    station_id,
                    tag_id,
                    blocked_id,
                )
            )
            report = simulation_report(raw, station_id, tag_id, blocked_id)
        except Exception as exc:
            # No raw frames, tokens, tags or internal endpoint details enter the log.
            app.logger.error("simulation_failed", extra={"error_type": type(exc).__name__})
            return (
                render_template(
                    "error.html",
                    title="Simulation unavailable",
                    message="The run could not produce a trustworthy report. Charging records "
                    "already committed may remain in Sessions. Return to Simulate to try again.",
                ),
                503,
            )
        response_status = {"busy": 409, "unavailable": 503, "timed_out": 504}.get(
            report["status"], 200
        )
        return render_template("simulation_results.html", report=report), response_status
