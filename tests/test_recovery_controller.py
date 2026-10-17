"""Run the real host controller against a fake CLI; never contact Docker."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE_DOCKER = r"""#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"
project=${RECOVERY_RUN_TOKEN%_*}
db_id=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
checker_id=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
case "$1" in
    ps)
        case "${FAKE_COLLISION:-}:$*" in
            project:*'--filter label='*) printf 'pre-existing-project-container\n' ;;
            container:*'{{ .Names }}'*) printf '%s_db\n' "$project" ;;
            error:*) exit 1 ;;
        esac
        exit 0 ;;
    compose)
        case "$*" in
            *' build tooling') exit 0 ;;
            *' up --detach --wait --wait-timeout 35 db')
                : > "$FAKE_STATE/db_created"; exit 0 ;;
            *' ps --all --quiet db') printf '%s\n' "$db_id"; exit 0 ;;
            *' run --detach --no-deps --name '* )
                : > "$FAKE_STATE/checker_created"
                printf '%s' "$RECOVERY_RUN_TOKEN" > "$RECOVERY_CONTROL_DIRECTORY/ready_to_stop"
                printf '%s\n' "$checker_id"; exit 0 ;;
        esac ;;
    inspect)
        for argument do target=$argument; done
        case "$*" in
            *'{{ .Id }}'*)
                case "$target" in
                    *_db) printf '%s\n' "$db_id"; exit 0 ;;
                    *_checker)
                        [ -f "$FAKE_STATE/checker_created" ] || exit 1
                        printf '%s\n' "$checker_id"; exit 0 ;;
                esac ;;
            *)
                if [ "$target" = "$db_id" ]; then
                    count=0
                    [ ! -f "$FAKE_STATE/guards" ] || count=$(cat "$FAKE_STATE/guards")
                    count=$((count + 1))
                    printf '%s' "$count" > "$FAKE_STATE/guards"
                    if [ "$count" -ge "$FAKE_BAD_AT" ]; then
                        printf 'wrong-project|db|%s\n' "$RECOVERY_RUN_TOKEN"
                    else
                        printf '%s|db|%s\n' "$project" "$RECOVERY_RUN_TOKEN"
                    fi
                    exit 0
                fi
                if [ "$target" = "$checker_id" ]; then
                    printf '%s|tooling|%s\n' "$project" "$RECOVERY_RUN_TOKEN"; exit 0
                fi ;;
        esac ;;
    volume)
        case "$2" in
            ls)
                if [ "${FAKE_COLLISION:-}" = volume ]; then
                    printf '%s_data\n' "$project"
                fi
                exit 0 ;;
            inspect) printf '%s|%s\n' "$project" "$RECOVERY_RUN_TOKEN"; exit 0 ;;
            rm) exit 0 ;;
        esac ;;
    network)
        case "$2" in
            ls)
                if [ "${FAKE_COLLISION:-}" = network ]; then
                    printf '%s_recovery\n' "$project"
                fi
                exit 0 ;;
            inspect) printf '%s|%s\n' "$project" "$RECOVERY_RUN_TOKEN"; exit 0 ;;
            rm) exit 0 ;;
        esac ;;
    stop)
        printf '%s' "$RECOVERY_RUN_TOKEN" > "$RECOVERY_CONTROL_DIRECTORY/checked_down"
        exit 0 ;;
    start)
        printf '%s' "$RECOVERY_RUN_TOKEN" > "$RECOVERY_CONTROL_DIRECTORY/passed"
        exit 0 ;;
    logs|rm) exit 0 ;;
    wait) printf '0\n'; exit 0 ;;
esac
printf 'Unexpected fake Docker call: %s\n' "$*" >&2
exit 99
"""


def run_controller(
    tmp_path: Path, *, bad_at: int = 99, collision: str = ""
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    fake = bin_directory / "docker"
    fake.write_text(FAKE_DOCKER, encoding="utf-8")
    fake.chmod(0o755)
    # Accelerate polling without turning bounded loops into scheduling-sensitive spins.
    sleeper = bin_directory / "sleep"
    sleeper.write_text("#!/bin/sh\nexec /bin/sleep 0.05\n", encoding="utf-8")
    sleeper.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    command_log = tmp_path / "docker.log"
    environment = os.environ | {
        "PATH": str(bin_directory) + os.pathsep + os.environ["PATH"],
        "TMPDIR": str(temporary),
        "FAKE_DOCKER_LOG": str(command_log),
        "FAKE_STATE": str(state),
        "FAKE_BAD_AT": str(bad_at),
        "FAKE_COLLISION": collision,
    }
    result = subprocess.run(
        ["sh", str(ROOT / "scripts/test-recovery.sh")],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    commands = command_log.read_text(encoding="utf-8").splitlines()
    return result, commands, temporary


@pytest.mark.parametrize("bad_at", [1, 2, 3], ids=["initial", "before-stop", "before-restart"])
def test_recovery_controller_aborts_when_owned_database_guard_fails(
    tmp_path: Path, bad_at: int
) -> None:
    result, commands, temporary = run_controller(tmp_path, bad_at=bad_at)
    assert result.returncode != 0
    assert "refusing" in result.stderr
    # Incorrect labels stay incorrect during cleanup, so even cleanup cannot stop
    # or remove that database. Only the two earlier verified phases may run.
    assert sum(command.startswith("stop ") for command in commands) == (bad_at == 3)
    assert not any(command.startswith("start ") for command in commands)
    assert not any(command.startswith("rm ") and "aaaaaaaa" in command for command in commands)
    if bad_at == 1:
        assert not any(" run --detach " in command for command in commands)
    assert list(temporary.iterdir()) == []


@pytest.mark.parametrize("collision", ["project", "container", "volume", "network", "error"])
def test_recovery_controller_never_reconciles_preexisting_or_unknown_targets(
    tmp_path: Path, collision: str
) -> None:
    result, commands, temporary = run_controller(tmp_path, collision=collision)
    assert result.returncode != 0
    assert "absence could not be verified" in result.stderr
    assert not any(
        " up --detach " in command
        or command.startswith(("start ", "stop ", "rm ", "volume rm ", "network rm "))
        for command in commands
    )
    assert list(temporary.iterdir()) == []
