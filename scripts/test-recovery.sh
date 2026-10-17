#!/bin/sh

# Failures are injected only into this generated, label-verified Compose project.
set -eu

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
    printf '%s\n' 'ERROR: test-recovery.sh must run inside the project Git worktree.' >&2
    exit 2
}
cd "$repo_root"

recovery_directory=$(mktemp -d "${TMPDIR:-/tmp}/kwms-recovery.XXXXXXXX")
recovery_suffix=$(basename "$recovery_directory" | tr '[:upper:].' '[:lower:]_')
recovery_project="kwms_recovery_${recovery_suffix#kwms-recovery_}"
recovery_token="${recovery_project}_$$"
recovery_volume="${recovery_project}_data"
recovery_network="${recovery_project}_recovery"
recovery_db_name="${recovery_project}_db"
recovery_checker_name="${recovery_project}_checker"
recovery_db_id=
recovery_checker_id=
recovery_log="$recovery_directory/command.log"
recovery_command_id=
recovery_timer_id=
recovery_cleaning=0
recovery_start_attempted=0

case "$recovery_project" in
    kwms_recovery_????????) ;;
    *) printf '%s\n' 'ERROR: unsafe generated recovery project name.' >&2; exit 2 ;;
esac
case "$recovery_token" in
    *[!a-z0-9_]*) printf '%s\n' 'ERROR: unsafe generated recovery token.' >&2; exit 2 ;;
esac

mkdir "$recovery_directory/control"
# Parent mktemp directory remains private (0700); only its isolated bind mount
# needs to be writable by the unprivileged container app uid and host controller.
chmod 777 "$recovery_directory/control"
printf '%s' "$recovery_token" >"$recovery_directory/db_password"
# Compose file secrets are read-only bind mounts, not Swarm secret objects: on
# Linux their host ownership/mode remain. Tooling runs as uid 10001, not the host
# runner's uid. The 0700 parent still excludes other host users from this test file.
chmod 444 "$recovery_directory/db_password"
export RECOVERY_RUN_TOKEN="$recovery_token"
export RECOVERY_VOLUME_NAME="$recovery_volume"
export RECOVERY_CONTROL_DIRECTORY="$recovery_directory/control"
export RECOVERY_SECRET_FILE="$recovery_directory/db_password"
export RECOVERY_DB_CONTAINER_NAME="$recovery_db_name"

stop_watchdog() {
    if [ -n "$recovery_timer_id" ]; then
        kill -TERM "$recovery_timer_id" 2>/dev/null || true
        wait "$recovery_timer_id" 2>/dev/null || true
        recovery_timer_id=
    fi
}

# macOS has no built-in timeout(1). The passive timer measures real seconds even
# when tests accelerate observation sleeps; only this parent signals its children.
bounded() {
    recovery_seconds=$1
    shift
    "$@" >"$recovery_log" 2>&1 &
    recovery_command_id=$!
    /bin/sleep "$recovery_seconds" &
    recovery_timer_id=$!
    while kill -0 "$recovery_command_id" 2>/dev/null; do
        if ! kill -0 "$recovery_timer_id" 2>/dev/null; then
            # A finished command's real status wins if both children have exited.
            kill -0 "$recovery_command_id" 2>/dev/null || break
            stop_watchdog
            kill -TERM "$recovery_command_id" 2>/dev/null || true
            sleep 1
            kill -KILL "$recovery_command_id" 2>/dev/null || true
            wait "$recovery_command_id" 2>/dev/null || true
            recovery_command_id=
            printf '%s\n' 'ERROR: recovery Docker command exceeded its deadline.' >&2
            return 124
        fi
        sleep 1
    done
    stop_watchdog
    recovery_status=0
    wait "$recovery_command_id" || recovery_status=$?
    recovery_command_id=
    return "$recovery_status"
}

compose() {
    # bounded() always runs this function in its own background child; exec makes
    # the handle it can terminate the actual Docker client rather than a shell.
    exec docker compose --project-name "$recovery_project" -f compose.recovery.yml "$@"
}

safe_container() {
    recovery_target=$1
    recovery_service=$2
    case "$recovery_target" in
        ''|*[!0-9a-f]*) return 1 ;;
    esac
    [ "${#recovery_target}" -eq 64 ] || return 1
    bounded 5 docker inspect --type container --format \
        '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}|{{ index .Config.Labels "io.kwms.recovery.token" }}' \
        "$recovery_target" || return 1
    [ "$(cat "$recovery_log")" = "$recovery_project|$recovery_service|$recovery_token" ]
}

safe_volume() {
    [ "$recovery_volume" = "${recovery_project}_data" ] || return 1
    bounded 5 docker volume inspect --format \
        '{{ index .Labels "com.docker.compose.project" }}|{{ index .Labels "io.kwms.recovery.token" }}' \
        "$recovery_volume" || return 1
    [ "$(cat "$recovery_log")" = "$recovery_project|$recovery_token" ]
}

require_unused_targets() {
    # Never let Compose reconcile an older target or attach pre-existing data
    # before ownership is known. Each read failure also aborts, not "assume absent".
    bounded 5 docker ps --all --filter "label=com.docker.compose.project=$recovery_project" \
        --format '{{ .ID }}' || return 1
    [ ! -s "$recovery_log" ] || return 1
    bounded 5 docker ps --all --format '{{ .Names }}' || return 1
    while IFS= read -r recovery_existing; do
        [ "$recovery_existing" != "$recovery_db_name" ] || return 1
        [ "$recovery_existing" != "$recovery_checker_name" ] || return 1
    done <"$recovery_log"
    bounded 5 docker volume ls --filter "label=com.docker.compose.project=$recovery_project" \
        --format '{{ .Name }}' || return 1
    [ ! -s "$recovery_log" ] || return 1
    bounded 5 docker volume ls --format '{{ .Name }}' || return 1
    while IFS= read -r recovery_existing; do
        [ "$recovery_existing" != "$recovery_volume" ] || return 1
    done <"$recovery_log"
    bounded 5 docker network ls --filter "label=com.docker.compose.project=$recovery_project" \
        --format '{{ .Name }}' || return 1
    [ ! -s "$recovery_log" ] || return 1
    bounded 5 docker network ls --format '{{ .Name }}' || return 1
    while IFS= read -r recovery_existing; do
        [ "$recovery_existing" != "$recovery_network" ] || return 1
    done <"$recovery_log"
}

resolve_container() {
    recovery_name=$1
    bounded 5 docker inspect --type container --format '{{ .Id }}' "$recovery_name" || return 1
    cat "$recovery_log"
}

cleanup() {
    recovery_exit=$?
    trap - EXIT INT TERM
    [ "$recovery_cleaning" -eq 0 ] || exit "$recovery_exit"
    recovery_cleaning=1
    stop_watchdog
    if [ -n "$recovery_command_id" ]; then
        kill -TERM "$recovery_command_id" 2>/dev/null || true
        sleep 1
        kill -KILL "$recovery_command_id" 2>/dev/null || true
        wait "$recovery_command_id" 2>/dev/null || true
        recovery_command_id=
    fi
    # A timed-out create client may already have created its exact named target.
    # Resolve those names again before guards, rather than relying on returned IDs.
    if [ "$recovery_start_attempted" -eq 1 ]; then
        recovery_checker_id=$(resolve_container "$recovery_checker_name") || recovery_checker_id=
        recovery_db_id=$(resolve_container "$recovery_db_name") || recovery_db_id=
    fi
    # Capture checker evidence before deleting only the exact owned containers.
    if [ -n "$recovery_checker_id" ] && safe_container "$recovery_checker_id" tooling; then
        bounded 5 docker logs "$recovery_checker_id" || true
        cat "$recovery_log"
        bounded 8 docker rm --force "$recovery_checker_id" || recovery_exit=1
    elif [ -n "$recovery_checker_id" ]; then
        printf '%s\n' 'ERROR: refusing unverified recovery-checker cleanup.' >&2
        recovery_exit=1
    fi
    if [ -n "$recovery_db_id" ] && safe_container "$recovery_db_id" db; then
        bounded 10 docker stop --time 5 "$recovery_db_id" || recovery_exit=1
        bounded 5 docker rm "$recovery_db_id" || recovery_exit=1
    elif [ -n "$recovery_db_id" ]; then
        printf '%s\n' 'ERROR: refusing unverified recovery-database cleanup.' >&2
        recovery_exit=1
    fi
    if [ "$recovery_start_attempted" -eq 1 ] && safe_volume; then
        bounded 5 docker volume rm "$recovery_volume" || recovery_exit=1
    fi
    # Networks carry the same generated token; do not remove an unverified match.
    if [ "$recovery_start_attempted" -eq 1 ] && bounded 5 docker network inspect --format \
        '{{ index .Labels "com.docker.compose.project" }}|{{ index .Labels "io.kwms.recovery.token" }}' \
        "$recovery_network" && [ "$(cat "$recovery_log")" = "$recovery_project|$recovery_token" ]; then
        bounded 5 docker network rm "$recovery_network" || recovery_exit=1
    fi
    # mktemp supplied this exact narrow path, never a caller-provided directory.
    case "$recovery_directory" in
        */kwms-recovery.????????) rm -rf -- "$recovery_directory" ;;
        *) printf '%s\n' 'ERROR: refusing unsafe recovery-directory cleanup.' >&2; recovery_exit=1 ;;
    esac
    exit "$recovery_exit"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

marker() {
    recovery_marker=$1
    recovery_limit=$2
    recovery_waited=0
    while [ ! -f "$RECOVERY_CONTROL_DIRECTORY/$recovery_marker" ]; do
        if [ -f "$RECOVERY_CONTROL_DIRECTORY/failed" ]; then
            printf '%s\n' 'ERROR: recovery checker failed; see its bounded evidence below.' >&2
            return 1
        fi
        if ! safe_container "$recovery_checker_id" tooling || ! bounded 5 docker inspect \
            --type container --format '{{ .State.Status }}' "$recovery_checker_id"; then
            printf '%s\n' 'ERROR: recovery checker state could not be verified.' >&2
            return 1
        fi
        case "$(cat "$recovery_log")" in
            running|created) ;;
            *)
                # It may publish its final marker between the loop check and
                # exiting. Recheck before declaring an early-exit failure.
                if [ ! -f "$RECOVERY_CONTROL_DIRECTORY/$recovery_marker" ]; then
                    printf 'ERROR: recovery checker exited before phase: %s\n' \
                        "$recovery_marker" >&2
                    return 1
                fi
                ;;
        esac
        if [ "$recovery_waited" -ge "$recovery_limit" ]; then
            printf 'ERROR: recovery checker phase timed out: %s\n' "$recovery_marker" >&2
            return 1
        fi
        sleep 1
        recovery_waited=$((recovery_waited + 1))
    done
    [ "$(cat "$RECOVERY_CONTROL_DIRECTORY/$recovery_marker")" = "$recovery_token" ]
}

printf '%s\n' 'Recovery: building current tooling and starting isolated volume-backed PostgreSQL.'
bounded 180 compose build tooling
if ! require_unused_targets; then
    printf '%s\n' 'ERROR: recovery targets already exist or absence could not be verified.' >&2
    exit 1
fi
recovery_start_attempted=1
bounded 45 compose up --detach --wait --wait-timeout 35 db
bounded 5 compose ps --all --quiet db
recovery_db_id=$(cat "$recovery_log")
if ! safe_container "$recovery_db_id" db || ! safe_volume; then
    printf '%s\n' 'ERROR: refusing unverified recovery database/volume.' >&2
    exit 1
fi
bounded 10 compose run --detach --no-deps --name "$recovery_checker_name" tooling
recovery_checker_id=$(tail -n 1 "$recovery_log")
if ! safe_container "$recovery_checker_id" tooling; then
    printf '%s\n' 'ERROR: refusing unverified recovery checker.' >&2
    exit 1
fi

marker ready_to_stop 30
if ! safe_container "$recovery_db_id" db || ! safe_volume; then
    printf '%s\n' 'ERROR: refusing to stop unverified recovery database/volume.' >&2
    exit 1
fi
bounded 10 docker stop --time 5 "$recovery_db_id"
printf '%s' "$recovery_token" >"$RECOVERY_CONTROL_DIRECTORY/db_down"
marker checked_down 30
if ! safe_container "$recovery_db_id" db || ! safe_volume; then
    printf '%s\n' 'ERROR: refusing to restart unverified recovery database/volume.' >&2
    exit 1
fi
bounded 10 docker start "$recovery_db_id"
printf '%s' "$recovery_token" >"$RECOVERY_CONTROL_DIRECTORY/db_restarted"
marker passed 40
bounded 5 docker wait "$recovery_checker_id"
[ "$(cat "$recovery_log")" = '0' ]
