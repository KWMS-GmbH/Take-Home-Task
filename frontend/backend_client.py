"""Bounded management HTTP client and injectable frontend network contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

import httpx

Authorizer = Callable[[str, str, str], Awaitable[Mapping[str, Any]]]
Simulator = Callable[[str, str, str, str, str], Awaitable[Mapping[str, Any]]]


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
