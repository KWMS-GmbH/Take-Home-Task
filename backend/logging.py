"""Small structured operational log format; business audit is stored separately."""

import json
import logging
import os
import socket
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            "logger": record.name,
            "instance": os.getenv("INSTANCE_ID", socket.gethostname()),
        }
        for key in ("instance", "station", "connection", "message_id", "action", "error_type"):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        return json.dumps(data, separators=(",", ":"))


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # Library frame/SQL debug output may contain tags, passwords, or attacker input.
    logging.getLogger("ocpp").disabled = True
    logging.getLogger("websockets.server").setLevel(logging.CRITICAL)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
