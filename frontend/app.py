"""Flask assembly point: configuration, security and explicit route dependencies."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from flask import Flask
from flask_wtf import CSRFProtect

from .authorization import default_authorizer as _default_authorizer
from .backend_client import AuthorizationProbeUnavailable as AuthorizationProbeUnavailable
from .backend_client import Authorizer, BackendAPI, HttpBackendAPI, Simulator
from .backend_client import BackendConflict as BackendConflict
from .backend_client import BackendNotFound as BackendNotFound
from .backend_client import BackendRejected as BackendRejected
from .backend_client import BackendUnavailable as BackendUnavailable
from .logging import configure_logging
from .management_routes import register_management_routes
from .scenario_catalog import FILES
from .session_routes import register_session_routes
from .simulation_routes import _default_simulator, register_simulation_routes
from .web import register_web

SCENARIO_FILES = FILES


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


def create_app(
    test_config: Mapping[str, Any] | None = None,
    *,
    backend_client: BackendAPI | None = None,
    authorizer: Authorizer | None = None,
    simulator: Simulator | None = None,
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
    simulate = simulator or _default_simulator
    app.extensions["backend_api"] = api
    app.extensions["ocpp_authorizer"] = probe
    app.extensions["ocpp_simulator"] = simulate
    register_management_routes(app, api, probe)
    register_simulation_routes(app, api, simulate)
    register_session_routes(app, api)
    register_web(app, api)
    return app
