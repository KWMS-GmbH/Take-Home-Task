#!/bin/sh

set -eu

stack_name=${STACK_NAME:-kwms}
case "$stack_name" in
    ''|*[!a-z0-9_-]*|[!a-z0-9]*)
        printf '%s\n' 'ERROR: invalid STACK_NAME.' >&2
        exit 2
        ;;
esac

# Select one exact existing local task. No arbitrary image, host Python, remote
# Docker shell or command interpolation; normal backend settings/secrets apply.
backend_container=$(docker ps \
    --filter "label=com.docker.swarm.service.name=${stack_name}_backend" \
    --filter status=running --format '{{.ID}}' | sed -n '1p')
case "$backend_container" in
    ''|*[!a-f0-9]*)
        printf '%s\n' 'ERROR: no valid local backend task found. Deploy the stack first.' >&2
        exit 1
        ;;
esac

exec docker exec "$backend_container" python -m backend.replay_maintenance "$@"
