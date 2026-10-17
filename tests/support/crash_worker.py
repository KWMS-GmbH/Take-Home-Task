"""An owned test process paused at a real PostgreSQL commit/reply boundary.

This module intentionally has no production fault switch. Its parent supplies an
explicit test database and kills only the subprocess handle it created.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import threading
from collections.abc import Mapping
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from websockets.asyncio.server import serve

from backend.config import Settings
from backend.db import get_engine, get_session_factory
from backend.models import AuditEvent, ChargingSession, OcppInbox
from backend.protocol import OcppServer
from backend.service import ResponseValidator, Service

INSTANCE = "crash-child"
WATCHDOG_SECONDS = 12


def require_test_database(raw: str | None) -> str:
    """Reject fallback/runtime databases before creating any database engine."""

    if raw is None:
        raise ValueError("an explicit test database is required")
    try:
        url = make_url(raw)
    except Exception as exc:
        raise ValueError("invalid test database") from exc
    if (
        url.get_backend_name() != "postgresql"
        or url.database is None
        or not url.database.endswith("_test")
    ):
        raise ValueError("refusing a database without the explicit test suffix")
    return raw


def emit(message: dict[str, Any]) -> None:
    print(json.dumps(message, separators=(",", ":")), flush=True)


def pause_at_boundary(state: dict[str, Any], boundary: str, action: str) -> None:
    # A finite synchronization wait, not a scheduling sleep. The parent must kill
    # this process; proceeding after the watchdog is always a test failure.
    emit({"event": "barrier", "boundary": boundary, "action": action, **state})
    threading.Event().wait(WATCHDOG_SECONDS)
    raise RuntimeError("crash parent did not kill the owned worker before its watchdog")


def transaction_state(session: Session, station_id: str, message_id: str) -> dict[str, Any]:
    """Read back flushed SQL state, rather than trusting pending ORM objects."""

    inbox = session.execute(
        select(OcppInbox.connection_id, OcppInbox.action, OcppInbox.response).where(
            OcppInbox.station_id == station_id, OcppInbox.unique_id == message_id
        )
    ).one()
    audits = session.execute(
        select(AuditEvent.kind, AuditEvent.after_data).where(
            AuditEvent.station_id == station_id,
            AuditEvent.message_id == message_id,
            AuditEvent.kind.in_(["session_started", "session_completed"]),
        )
    ).all()
    expected_kind = "session_started" if inbox.action == "StartTransaction" else "session_completed"
    assert len(audits) == 1 and audits[0].kind == expected_kind
    transaction_id = audits[0].after_data["transactionId"]
    record = session.execute(
        select(ChargingSession.id, ChargingSession.lifecycle).where(
            ChargingSession.id == transaction_id, ChargingSession.station_id == station_id
        )
    ).one()
    assert record.lifecycle == ("active" if inbox.action == "StartTransaction" else "completed")
    if inbox.action == "StartTransaction":
        assert inbox.response["transactionId"] == transaction_id
    return {
        "transactionId": record.id,
        "lifecycle": record.lifecycle,
        "connectionId": str(inbox.connection_id),
        "auditKind": expected_kind,
        "inboxAction": inbox.action,
    }


async def run_worker(arguments: argparse.Namespace) -> None:
    require_test_database(os.getenv("TEST_DATABASE_URL"))
    settings = Settings.from_env(testing=True)
    require_test_database(settings.database_url)
    engine = get_engine(settings)
    sessions = get_session_factory(engine)

    class PausedService(Service):
        async def handle_ocpp(
            self,
            station_id: str,
            generation: int,
            connection_id: str,
            instance_id: str,
            unique_id: str,
            action: str,
            payload: Mapping[str, Any],
            response_validator: ResponseValidator,
            *,
            simulation_token: str | None = None,
        ) -> dict[str, Any]:
            result = await super().handle_ocpp(
                station_id,
                generation,
                connection_id,
                instance_id,
                unique_id,
                action,
                payload,
                response_validator,
                simulation_token=simulation_token,
            )
            if arguments.boundary == "after_commit" and unique_id == arguments.message_id:
                assert action == arguments.action and station_id == arguments.station
                # The original service returned only after releasing its transaction.
                async with sessions() as database:
                    state = await database.run_sync(
                        lambda sync: transaction_state(sync, station_id, unique_id)
                    )
                pause_at_boundary(state, arguments.boundary, action)
            return result

    def before_commit(database: Session) -> None:
        if arguments.boundary != "before_commit":
            return
        target = next(
            (
                item
                for item in database.new
                if isinstance(item, OcppInbox) and item.unique_id == arguments.message_id
            ),
            None,
        )
        if target is None:
            return
        assert target.action == arguments.action and target.station_id == arguments.station
        database.flush()
        state = transaction_state(database, arguments.station, arguments.message_id)
        pause_at_boundary(state, arguments.boundary, arguments.action)

    event.listen(Session, "before_commit", before_commit)
    try:
        service = PausedService(sessions, instance_id=INSTANCE)
        boundary = OcppServer(service, INSTANCE)
        async with serve(
            boundary.handle,
            "127.0.0.1",
            0,
            subprotocols=["ocpp1.6"],
            process_request=boundary.handshake,
            origins=[None],
            max_size=65_536,
            max_queue=8,
            compression=None,
        ) as listener:
            port = listener.sockets[0].getsockname()[1]
            emit({"event": "ready", "port": port, "instanceId": INSTANCE})
            await asyncio.Event().wait()
    finally:
        event.remove(Session, "before_commit", before_commit)
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--boundary", choices=("before_commit", "after_commit"), required=True)
    parser.add_argument("--action", choices=("StartTransaction", "StopTransaction"), required=True)
    parser.add_argument("--station", required=True)
    parser.add_argument("--message-id", required=True)
    arguments = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(run_worker(arguments))
    except Exception as exc:
        # Never emit a driver exception, environment or database URL/credentials.
        emit({"event": "failed", "errorType": type(exc).__name__})
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
