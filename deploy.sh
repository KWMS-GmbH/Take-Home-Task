#!/bin/sh

set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$repo_root"

stack_name=${STACK_NAME:-kwms}
wait_timeout_seconds=${DEPLOY_TIMEOUT_SECONDS:-240}

case "$stack_name" in
    ''|*[!a-z0-9_-]*|[!a-z0-9]*)
        printf 'ERROR: invalid STACK_NAME %s; use lowercase letters, digits, _ or -.\n' \
            "$stack_name" >&2
        exit 2
        ;;
esac
case "$wait_timeout_seconds" in
    ''|0|*[!0-9]*)
        printf '%s\n' 'ERROR: DEPLOY_TIMEOUT_SECONDS must be a positive integer.' >&2
        exit 2
        ;;
esac

for required_command in docker openssl; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
        printf 'ERROR: required command not found: %s\n' "$required_command" >&2
        exit 2
    fi
done

swarm_state=$(docker info --format '{{.Swarm.LocalNodeState}} {{.Swarm.ControlAvailable}}' 2>/dev/null) || {
    printf '%s\n' 'ERROR: Docker is unavailable. Start Docker Desktop and retry.' >&2
    exit 2
}
if [ "$swarm_state" != "active true" ]; then
    printf '%s\n' 'ERROR: this Docker Engine is not an active Swarm manager.' >&2
    printf '%s\n' 'Run: docker swarm init' >&2
    exit 2
fi

ensure_secret() {
    secret_name=$1
    if docker secret inspect "$secret_name" >/dev/null 2>&1; then
        printf 'Using existing Docker secret %s.\n' "$secret_name"
        return
    fi

    secret_value=$(openssl rand -hex 32)
    printf '%s' "$secret_value" | docker secret create "$secret_name" - >/dev/null
    unset secret_value
    printf 'Created Docker secret %s.\n' "$secret_name"
}

wait_for_stack() {
    started_at=$(date +%s)
    while :; do
        all_ready=1
        for service_and_count in backend:3 frontend:1 db:1; do
            service=${service_and_count%:*}
            expected=${service_and_count#*:}
            service_name="${stack_name}_${service}"

            update_state=$(docker service inspect --format \
                '{{if .UpdateStatus}}{{.UpdateStatus.State}}{{end}}' "$service_name")
            case "$update_state" in
                updating)
                    all_ready=0
                    continue
                    ;;
                paused|rollback_*)
                    printf 'ERROR: service %s deployment ended in %s.\n' \
                        "$service_name" "$update_state" >&2
                    return 1
                    ;;
            esac

            replicas=$(docker service ls --filter "name=$service_name" --format '{{.Replicas}}')
            if [ "$replicas" != "$expected/$expected" ]; then
                all_ready=0
                continue
            fi

            healthy=0
            expected_image=$(docker service inspect --format \
                '{{.Spec.TaskTemplate.ContainerSpec.Image}}' "$service_name")
            container_ids=$(docker ps \
                --filter "label=com.docker.swarm.service.name=$service_name" \
                --filter status=running \
                --format '{{.ID}}')
            for container_id in $container_ids; do
                actual_image=$(docker inspect --format '{{.Config.Image}}' "$container_id")
                if [ "$actual_image" != "$expected_image" ]; then
                    # Old healthy tasks must not satisfy a new image rollout.
                    continue
                fi
                container_state=$(docker inspect --format \
                    '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
                    "$container_id")
                if [ "$container_state" = "healthy" ]; then
                    # Swarm restart health is liveness. Deployment success still
                    # requires every app task to pass its separate readiness probe.
                    if [ "$service" = "db" ] || docker exec "$container_id" python -c \
                        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2).read()" \
                        >/dev/null 2>&1; then
                        healthy=$((healthy + 1))
                    fi
                fi
            done
            if [ "$healthy" -lt "$expected" ]; then
                all_ready=0
            fi
        done

        if [ "$all_ready" -eq 1 ]; then
            return 0
        fi
        now=$(date +%s)
        if [ $((now - started_at)) -ge "$wait_timeout_seconds" ]; then
            printf 'ERROR: stack did not become live and ready within %s seconds.\n' \
                "$wait_timeout_seconds" >&2
            docker stack services "$stack_name" >&2 || true
            docker stack ps --no-trunc "$stack_name" >&2 || true
            return 1
        fi
        sleep 2
    done
}

printf '%s\n' 'Building application images...'
docker build --pull --target backend --tag kwms-backend:local .
docker build --pull --target frontend --tag kwms-frontend:local .

# Swarm must see an immutable image reference to roll out a rebuilt local image.
backend_id=$(docker image inspect --format '{{.Id}}' kwms-backend:local)
frontend_id=$(docker image inspect --format '{{.Id}}' kwms-frontend:local)
BACKEND_IMAGE="kwms-backend:${backend_id#sha256:}"
FRONTEND_IMAGE="kwms-frontend:${frontend_id#sha256:}"
export BACKEND_IMAGE FRONTEND_IMAGE
docker tag kwms-backend:local "$BACKEND_IMAGE"
docker tag kwms-frontend:local "$FRONTEND_IMAGE"

ensure_secret "${stack_name}_db_password"
ensure_secret "${stack_name}_frontend_secret_key"

printf 'Deploying stack %s...\n' "$stack_name"
STACK_NAME="$stack_name" docker stack deploy \
    --compose-file stack.yml \
    --resolve-image never \
    "$stack_name"

printf '%s\n' 'Waiting for all service replicas to become live and ready...'
wait_for_stack

printf 'Stack %s is live and ready. Open http://localhost:8080\n' "$stack_name"
printf '%s\n' 'A fresh volume starts empty; existing data is preserved. Optional: make demo-data'
