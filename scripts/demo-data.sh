#!/bin/sh

set -eu

stack_name=${STACK_NAME:-kwms}
station_id=${DEMO_STATION_ID:-CP-001}
allowed_id_tag=${DEMO_ALLOWED_ID_TAG:-TAG-42}
blocked_id_tag=${DEMO_BLOCKED_ID_TAG:-TAG-BLOCKED}
if [ "$allowed_id_tag" = "$blocked_id_tag" ]; then
    printf '%s\n' 'The allowed and blocked demo tags must be different.' >&2
    exit 2
fi

case "$stack_name" in
    ''|*[!a-z0-9_-]*|[!a-z0-9]*)
        printf 'ERROR: invalid STACK_NAME: %s\n' "$stack_name" >&2
        exit 2
        ;;
esac

frontend_container=$(docker ps \
    --filter "label=com.docker.swarm.service.name=${stack_name}_frontend" \
    --filter status=running \
    --format '{{.ID}}' | sed -n '1p')
if [ -z "$frontend_container" ]; then
    printf 'ERROR: no running %s_frontend task found. Deploy the stack first.\n' \
        "$stack_name" >&2
    exit 1
fi

docker exec \
    --env DEMO_API_URL=http://backend:8000 \
    --env DEMO_STATION_ID="$station_id" \
    --env DEMO_ALLOWED_ID_TAG="$allowed_id_tag" \
    --env DEMO_BLOCKED_ID_TAG="$blocked_id_tag" \
    "$frontend_container" \
    python -c '
import json
import os
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

base_url = os.environ["DEMO_API_URL"].rstrip("/")


def request(method, path, payload):
    body = json.dumps(payload).encode()
    call = Request(
        base_url + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urlopen(call, timeout=5) as response:
            print(f"{method} {path}: {response.status}")
    except HTTPError as error:
        if method == "POST" and error.code == 409:
            print(f"{method} {path}: already exists")
            return
        detail = error.read(500).decode(errors="replace")
        raise RuntimeError(f"{method} {path}: HTTP {error.code}: {detail}") from error


station = os.environ["DEMO_STATION_ID"]
allowed = os.environ["DEMO_ALLOWED_ID_TAG"]
blocked = os.environ["DEMO_BLOCKED_ID_TAG"]
request("POST", "/api/stations", {"id": station})
request("POST", "/api/id-tags", {"id": allowed})
request("POST", "/api/id-tags", {"id": blocked})
station_path = quote(station, safe="")
request(
    "PUT",
    f"/api/stations/{station_path}/permissions/{quote(allowed, safe="")}",
    {"enabled": True},
)
request(
    "PUT",
    f"/api/stations/{station_path}/permissions/{quote(blocked, safe="")}",
    {"enabled": False},
)
'

printf 'Demo data ready: station=%s allowed=%s blocked=%s\n' \
    "$station_id" "$allowed_id_tag" "$blocked_id_tag"
