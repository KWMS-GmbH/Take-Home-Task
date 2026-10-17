"""Audit record construction and OCPP correlation; never commits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from .models import AuditEvent


def audit_event(
    *,
    actor: str,
    source: str,
    kind: str,
    instance_id: str | None,
    station_id: str | None = None,
    connection_id: UUID | None = None,
    message_id: str | None = None,
    action: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditEvent:
    return AuditEvent(
        actor=actor,
        source=source,
        kind=kind,
        instance_id=instance_id,
        station_id=station_id,
        connection_id=connection_id,
        message_id=message_id,
        action=action,
        before_data=before,
        after_data=after,
    )


@dataclass(frozen=True)
class OcppContext:
    station_id: str
    connection_id: UUID
    instance_id: str
    message_id: str
    action: str

    def event(
        self,
        kind: str,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> AuditEvent:
        return AuditEvent(
            actor="charge_point",
            source="ocpp",
            kind=kind,
            station_id=self.station_id,
            connection_id=self.connection_id,
            instance_id=self.instance_id,
            message_id=self.message_id,
            action=self.action,
            before_data=before,
            after_data=after,
        )
