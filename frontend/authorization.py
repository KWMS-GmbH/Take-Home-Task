"""The real, bounded Authorize-only probe; never declares a station reboot."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException
from websockets.typing import Subprotocol

from .backend_client import AuthorizationProbeUnavailable, Authorizer


def default_authorizer(timeout_seconds: float) -> Authorizer:
    """Send one real Authorize, never an incidental Boot/reboot declaration."""

    async def authorize(ocpp_url: str, station_id: str, id_tag: str) -> Mapping[str, Any]:
        frames: list[dict[str, str]] = []

        async def exchange() -> str:
            message_id = uuid4().hex
            wire = json.dumps([2, message_id, "Authorize", {"idTag": id_tag}])
            # The base URL is deployment configuration, never supplied by the browser.
            # Approved punctuation is legal inside this path component; the OCPP
            # listener deliberately rejects encoded/ambiguous station paths.
            endpoint = f"{ocpp_url.rstrip('/')}/{quote(station_id, safe='._:-')}"
            async with connect(
                endpoint,
                subprotocols=[Subprotocol("ocpp1.6")],
                open_timeout=min(timeout_seconds, 3.0),
                close_timeout=1.0,
                max_size=64 * 1024,
                max_queue=1,
                compression=None,
                proxy=None,
            ) as socket:
                if socket.subprotocol != "ocpp1.6":
                    raise AuthorizationProbeUnavailable
                await socket.send(wire)
                frames.append({"kind": "send", "conn": "probe", "text": wire})
                raw = await socket.recv()
                if not isinstance(raw, str):
                    raise AuthorizationProbeUnavailable
                frames.append({"kind": "recv", "conn": "probe", "text": raw})
                frame = json.loads(raw)
                if (
                    not isinstance(frame, list)
                    or len(frame) != 3
                    or type(frame[0]) is not int
                    or frame[0] != 3
                    or frame[1] != message_id
                    or not isinstance(frame[2], dict)
                    or not isinstance(frame[2].get("idTagInfo"), dict)
                ):
                    raise AuthorizationProbeUnavailable
                status = frame[2]["idTagInfo"].get("status")
                if not isinstance(status, str) or status not in {"Accepted", "Blocked", "Invalid"}:
                    raise AuthorizationProbeUnavailable
                return str(status)

        try:
            status = await asyncio.wait_for(exchange(), timeout=timeout_seconds)
        except (
            AuthorizationProbeUnavailable,
            TimeoutError,
            OSError,
            ValueError,
            WebSocketException,
        ):
            return {
                "status": "Unavailable",
                "error": "The station returned no valid authorization result. Try again shortly.",
                "frames": frames,
            }
        return {"status": status, "error": None, "frames": frames}

    return authorize
