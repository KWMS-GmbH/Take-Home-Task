#!/bin/sh

set -eu

stack_name=${STACK_NAME:-kwms}
case "$stack_name" in
    ''|*[!a-z0-9_-]*|[!a-z0-9]*)
        printf 'ERROR: invalid STACK_NAME: %s\n' "$stack_name" >&2
        exit 2
        ;;
esac

if docker stack ls --format '{{.Name}}' | grep -F -x "$stack_name" >/dev/null 2>&1; then
    docker stack rm "$stack_name"
    printf 'Removed stack %s.\n' "$stack_name"
else
    printf 'Stack %s is not deployed.\n' "$stack_name"
fi

printf 'Preserved volume %s_db_data and external secrets.\n' "$stack_name"
printf '%s\n' 'This script never leaves the Swarm or deletes persistent data.'
