"""Internal HTTP management and health API."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .service import (
    AlreadyExists,
    DependencyUnavailable,
    NotFound,
    RequestConflict,
    Service,
    ServiceError,
    StaleConnection,
    ValidationError,
)

StationId = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
IdTagValue = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=20,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class StationCreate(StrictModel):
    id: StationId


class StationResult(StrictModel):
    id: StationId
    vendor: str | None
    model: str | None
    activeGeneration: int = Field(ge=0)


class IdTagCreate(StrictModel):
    id: IdTagValue


class IdTagResult(StrictModel):
    id: IdTagValue


class PermissionUpdate(StrictModel):
    enabled: bool


class PermissionResult(StrictModel):
    stationId: StationId
    idTag: IdTagValue
    enabled: bool


class SimulationLeaseResult(StrictModel):
    token: str
    expiresAt: str
    notBefore: str


class SimulationLeaseRelease(StrictModel):
    token: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=36)]


class SimulationReleaseResult(StrictModel):
    released: bool


class SessionResult(StrictModel):
    transactionId: int
    stationId: str
    connectorId: int
    idTag: str
    lifecycle: str
    authorizationStatus: str
    meterQuality: str
    reviewReasons: list[str]
    startedAt: str
    stoppedAt: str | None
    durationSeconds: float | None
    energyKWh: float | None
    energyProvisional: bool
    billingEligible: bool
    meterStartWh: float
    meterStopWh: float | None
    lastMeterWh: float | None
    lastMeterAt: str | None


def _error_status(exc: ServiceError) -> int:
    if isinstance(exc, DependencyUnavailable):
        return 503
    if isinstance(exc, NotFound):
        return 404
    if isinstance(exc, (AlreadyExists, RequestConflict, StaleConnection)):
        return 409
    if isinstance(exc, ValidationError):
        return 422
    return 500


def create_app(
    service: Service,
    *,
    readiness_check: Callable[[], Awaitable[None]],
    instance_id: str,
    liveness_check: Callable[[], bool] | None = None,
) -> FastAPI:
    app = FastAPI(title="Charging Session Management API", version="0.1.0")

    def require_live() -> None:
        if liveness_check is not None and not liveness_check():
            raise RuntimeError("application listener is not serving")

    @app.exception_handler(ServiceError)
    async def service_error_handler(_request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=_error_status(exc),
            content={"detail": str(exc) or exc.__class__.__name__},
        )

    @app.get("/health/live", response_model=None)
    async def live() -> JSONResponse | dict[str, str]:
        try:
            require_live()
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "instanceId": instance_id},
            )
        return {"status": "ok", "instanceId": instance_id}

    @app.get("/health/ready", response_model=None)
    async def ready() -> JSONResponse | dict[str, str]:
        try:
            require_live()
            await readiness_check()
        except Exception:  # Readiness must not leak driver/credential details.
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "instanceId": instance_id},
            )
        return {"status": "ok", "instanceId": instance_id}

    @app.get("/api/stations", response_model=list[StationResult])
    async def list_stations() -> list[dict[str, Any]]:
        return await service.list_stations()

    @app.post("/api/stations", status_code=201, response_model=StationResult)
    async def create_station(body: StationCreate) -> dict[str, Any]:
        return await service.create_station(body.id)

    @app.get("/api/id-tags", response_model=list[IdTagResult])
    async def list_id_tags() -> list[dict[str, str]]:
        return await service.list_id_tags()

    @app.post("/api/id-tags", status_code=201, response_model=IdTagResult)
    async def create_id_tag(body: IdTagCreate) -> dict[str, str]:
        return await service.create_id_tag(body.id)

    @app.get("/api/permissions", response_model=list[PermissionResult])
    async def list_permissions() -> list[dict[str, Any]]:
        return await service.list_permissions()

    @app.put(
        "/api/stations/{station_id}/permissions/{id_tag}",
        response_model=PermissionResult,
    )
    async def update_permission(
        station_id: StationId, id_tag: IdTagValue, body: PermissionUpdate
    ) -> dict[str, Any]:
        return await service.set_permission(station_id, id_tag, enabled=body.enabled)

    @app.post(
        "/api/stations/{station_id}/simulation-lease",
        response_model=SimulationLeaseResult,
    )
    async def acquire_simulation_lease(station_id: StationId) -> dict[str, str]:
        return await service.acquire_simulation_lease(station_id)

    @app.post(
        "/api/stations/{station_id}/simulation-lease/release",
        response_model=SimulationReleaseResult,
    )
    async def release_simulation_lease(
        station_id: StationId, body: SimulationLeaseRelease
    ) -> dict[str, bool]:
        return await service.release_simulation_lease(station_id, body.token)

    @app.get("/api/stations/{station_id}/sessions", response_model=list[SessionResult])
    async def list_sessions(
        station_id: StationId,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
        before: Annotated[int | None, Query(ge=1, le=2_147_483_647)] = None,
    ) -> list[dict[str, Any]]:
        return await service.list_sessions(station_id, limit=limit, before=before)

    @app.get("/api/sessions/{transaction_id}", response_model=SessionResult)
    async def get_session(
        transaction_id: Annotated[int, Field(gt=0, le=2_147_483_647)],
    ) -> dict[str, Any]:
        return await service.get_session(transaction_id)

    return app
