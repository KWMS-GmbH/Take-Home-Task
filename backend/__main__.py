"""One process, separate public OCPP and internal management HTTP listeners."""

import asyncio
import logging
import os

import uvicorn
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeout
from websockets.asyncio.server import serve
from websockets.typing import Subprotocol

from backend.api import create_app
from backend.config import Settings
from backend.db import check_database, get_engine, get_session_factory, migrate_database
from backend.logging import configure_logging
from backend.protocol import OcppServer
from backend.service import Service

LOGGER = logging.getLogger(__name__)


async def main() -> None:
    configure_logging()
    settings = Settings.from_env()
    # No depends_on: all replicas can start before PostgreSQL and retry safely.
    retry_delay = 2
    while True:
        try:
            await asyncio.to_thread(migrate_database, settings)
            break
        except (OperationalError, DatabaseTimeout) as exc:
            LOGGER.warning("database_startup_retry", extra={"error_type": type(exc).__name__})
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 15)
        except Exception as exc:
            LOGGER.critical("migration_startup_failed", extra={"error_type": type(exc).__name__})
            raise SystemExit(1) from None
    engine = get_engine(settings)
    service = Service(get_session_factory(engine), instance_id=settings.instance_id)
    transport = OcppServer(service, settings.instance_id)
    try:
        async with serve(
            transport.handle,
            "0.0.0.0",
            int(os.environ.get("OCPP_PORT", "9000")),
            subprotocols=[Subprotocol("ocpp1.6")],
            process_request=transport.handshake,
            origins=[None],
            max_size=65_536,
            max_queue=8,
            open_timeout=5,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=3,
            compression=None,
        ) as listener:

            async def ready() -> None:
                if not listener.is_serving():
                    raise RuntimeError("OCPP listener not serving")
                await check_database(engine)

            app = create_app(service, readiness_check=ready, instance_id=settings.instance_id)
            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    host="0.0.0.0",
                    port=int(os.environ.get("HTTP_PORT", "8000")),
                    log_config=None,
                    access_log=False,
                    timeout_graceful_shutdown=10,
                    limit_concurrency=64,
                    limit_max_requests=None,
                )
            )
            await server.serve()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
