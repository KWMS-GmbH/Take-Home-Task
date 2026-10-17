#!/bin/sh

set -eu

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
    printf '%s\n' 'ERROR: test.sh must run inside the project Git worktree.' >&2
    exit 2
}
cd "$repo_root"

test_directory=$(mktemp -d "${TMPDIR:-/tmp}/kwms-test.XXXXXX")
test_secret_file="$test_directory/db_password"
test_project="kwms_test_$$"

cleanup() {
    TEST_DB_PASSWORD_FILE="$test_secret_file" \
        docker compose --project-name "$test_project" -f compose.test.yml \
        down --volumes --remove-orphans >/dev/null 2>&1 || true
    rm -rf -- "$test_directory"
}
trap cleanup EXIT INT TERM

printf '%s' 'test-only-password' >"$test_secret_file"
chmod 600 "$test_secret_file"
export TEST_DB_PASSWORD_FILE="$test_secret_file"

docker compose --project-name "$test_project" -f compose.test.yml build tooling
docker compose --project-name "$test_project" -f compose.test.yml up --detach --wait db
docker compose --project-name "$test_project" -f compose.test.yml \
    run --rm --no-deps tooling python -m pytest -q
