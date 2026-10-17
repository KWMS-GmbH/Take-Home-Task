"""Sanitized application events; Gunicorn's own startup messages remain text."""

import json
import logging
import socket
from datetime import UTC, datetime

from flask import Flask


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "time": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "event": record.getMessage(),
                "instance": socket.gethostname(),
                "error_type": getattr(record, "error_type", None),
            },
            separators=(",", ":"),
        )


def configure_logging(app: Flask) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    app.logger.handlers = [handler]
    app.logger.setLevel(logging.INFO)
    app.logger.propagate = False
    logging.getLogger("httpx").setLevel(logging.WARNING)
