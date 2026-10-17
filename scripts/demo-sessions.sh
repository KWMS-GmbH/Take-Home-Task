#!/bin/sh
set -eu
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
stack_name=${STACK_NAME:-kwms}
case "$stack_name" in
    ''|*[!a-z0-9_-]*|[!a-z0-9]*) printf '%s\n' 'Invalid STACK_NAME' >&2; exit 2 ;;
esac
frontend_container=$(docker ps \
    --filter "label=com.docker.swarm.service.name=${stack_name}_frontend" \
    --filter status=running --format '{{.ID}}' | sed -n '1p')
if [ -z "$frontend_container" ]; then
    printf '%s\n' 'Deploy the stack before adding demo sessions.' >&2
    exit 1
fi
docker exec -i "$frontend_container" python - < "$repo_root/scripts/demo_sessions.py"
