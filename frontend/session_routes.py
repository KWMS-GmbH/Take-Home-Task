"""Read-only, station-scoped session pages backed exclusively by HTTP."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from flask import Flask, render_template, request

from .backend_client import BackendAPI, BackendNotFound, BackendUnavailable
from .view_models import load_stations, record_list, session_record, station_identifier


def register_session_routes(app: Flask, api: BackendAPI) -> None:
    @app.get("/sessions")
    def sessions() -> Any:
        stations = load_stations(api)
        selected_station = request.args.get("station_id", "")
        records: list[dict[str, Any]] = []
        if selected_station:
            try:
                selected_station = station_identifier(selected_station)
            except ValueError:
                raise BackendNotFound from None
            path = f"/api/stations/{quote(selected_station, safe='')}/sessions"
            records = [
                session_record(item) for item in record_list(api.get(path), resource="session")
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
        record = session_record(api.get(f"/api/sessions/{transaction_id}"))
        if record["transactionId"] != transaction_id:
            raise BackendUnavailable("Unexpected transaction response")
        return render_template("session_detail.html", session=record)
