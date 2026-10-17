"""Application-wide response protections, dependency health and safe error pages."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flask import Flask, jsonify, render_template
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import RequestEntityTooLarge

from .backend_client import (
    BackendAPI,
    BackendConflict,
    BackendNotFound,
    BackendRejected,
    BackendUnavailable,
)


def register_web(app: Flask, api: BackendAPI) -> None:
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
