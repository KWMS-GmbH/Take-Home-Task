#!/bin/sh

set -eu

stack_name=${STACK_NAME:-kwms}
service=${1:-all}
follow=${2:-}
tail_lines=${TAIL_LINES:-200}

case "$stack_name" in
    ''|*[!a-z0-9_-]*|[!a-z0-9]*)
        printf 'ERROR: invalid STACK_NAME: %s\n' "$stack_name" >&2
        exit 2
        ;;
esac
case "$tail_lines" in
    ''|*[!0-9]*)
        printf '%s\n' 'ERROR: TAIL_LINES must be a non-negative integer.' >&2
        exit 2
        ;;
esac
if [ "$tail_lines" -gt 10000 ]; then
    printf '%s\n' 'ERROR: TAIL_LINES is capped at 10000.' >&2
    exit 2
fi
case "$service" in
    backend|frontend|db|all) ;;
    *)
        printf '%s\n' 'Usage: scripts/logs.sh [backend|frontend|db|all] [--follow]' >&2
        exit 2
        ;;
esac
if [ -n "$follow" ] && [ "$follow" != "--follow" ]; then
    printf '%s\n' 'Usage: scripts/logs.sh [backend|frontend|db|all] [--follow]' >&2
    exit 2
fi
if [ "$service" = "all" ] && [ "$follow" = "--follow" ]; then
    printf '%s\n' 'ERROR: --follow requires one service to avoid unbounded mixed output.' >&2
    exit 2
fi

show_logs() {
    selected_service=$1
    if [ "$follow" = "--follow" ]; then
        docker service logs --timestamps --tail "$tail_lines" --follow \
            "${stack_name}_${selected_service}"
    else
        docker service logs --timestamps --tail "$tail_lines" \
            "${stack_name}_${selected_service}"
    fi
}

if [ "$service" = "all" ]; then
    for selected_service in backend frontend db; do
        printf '\n== %s ==\n' "$selected_service"
        show_logs "$selected_service"
    done
else
    show_logs "$service"
fi
