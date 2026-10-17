#!/bin/sh

set -eu

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
    printf '%s\n' 'ERROR: check.sh must run inside the project Git worktree.' >&2
    exit 2
}
cd "$repo_root"

tooling_image=${TOOLING_IMAGE:-kwms-tooling:local}

if [ "${KWMS_TOOLING_CONTAINER:-0}" != "1" ]; then
    docker build --target tooling --tag "$tooling_image" .
    docker run --rm \
        --env KWMS_TOOLING_CONTAINER=1 \
        "$tooling_image" \
        ./scripts/check.sh
    TEST_DB_PASSWORD_FILE=/dev/null docker compose -f compose.test.yml config --quiet
    STACK_NAME=kwms docker stack config --compose-file stack.yml >/dev/null
    exit 0
fi

./scripts/verify-assignment.sh
sh -n deploy.sh scripts/*.sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover \
    -s tests -p 'test_assignment_guard.py' -v
python -m pip check
ruff format --check backend frontend tests scripts
ruff check backend frontend tests scripts
mypy backend frontend
