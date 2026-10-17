#!/bin/sh

set -eu

BASELINE_COMMIT=1b8ccff372d81691f50480a4d8d4e1f17a839095
ASSIGNMENT_PATH=take-home

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
    printf '%s\n' 'ERROR: verify-assignment must run inside a Git worktree.' >&2
    exit 2
}
cd "$repo_root"

if ! git cat-file -e "${BASELINE_COMMIT}^{commit}" 2>/dev/null; then
    printf 'ERROR: assignment baseline commit %s is unavailable.\n' "$BASELINE_COMMIT" >&2
    exit 2
fi

if ! indexed_changes=$(git diff --cached --no-ext-diff --name-status --no-renames \
    "$BASELINE_COMMIT" -- "$ASSIGNMENT_PATH"); then
    printf '%s\n' 'ERROR: could not compare the assignment index with its baseline.' >&2
    exit 2
fi

if ! working_tree_changes=$(git diff --no-ext-diff --name-status --no-renames \
    "$BASELINE_COMMIT" -- "$ASSIGNMENT_PATH"); then
    printf '%s\n' 'ERROR: could not compare the assignment working tree with its baseline.' >&2
    exit 2
fi

if ! untracked_files=$(git ls-files --others -- "$ASSIGNMENT_PATH"); then
    printf '%s\n' 'ERROR: could not inspect untracked assignment files.' >&2
    exit 2
fi

if [ -z "$indexed_changes" ] && [ -z "$working_tree_changes" ] && \
    [ -z "$untracked_files" ]; then
    printf 'Assignment baseline verified: %s/ matches %s.\n' \
        "$ASSIGNMENT_PATH" "$BASELINE_COMMIT"
    exit 0
fi

printf 'ERROR: %s/ differs from assignment baseline %s.\n' \
    "$ASSIGNMENT_PATH" "$BASELINE_COMMIT" >&2

if [ -n "$indexed_changes" ]; then
    printf '%s\n' 'Indexed changes relative to the baseline:' >&2
    printf '%s\n' "$indexed_changes" >&2
fi

if [ -n "$working_tree_changes" ]; then
    printf '%s\n' 'Working-tree changes relative to the baseline:' >&2
    printf '%s\n' "$working_tree_changes" >&2
fi

if [ -n "$untracked_files" ]; then
    printf '%s\n' 'Untracked additions:' >&2
    printf '%s\n' "$untracked_files" | while IFS= read -r path; do
        printf 'A\t%s (untracked)\n' "$path" >&2
    done
fi

printf '%s\n' 'The supplied take-home files must remain verbatim.' >&2
exit 1
