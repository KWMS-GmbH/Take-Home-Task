"""Recovery startup errors must be useful without exposing secrets or unsafe writes."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from scripts import recovery_check

TOKEN = "kwms_recovery_abcdefgh_123"


@pytest.mark.asyncio
async def test_unreadable_secret_reports_type_and_validated_failure_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(recovery_check, "control_scope", lambda: (tmp_path, TOKEN))

    def unreadable(_path: Path, **_kwargs: str) -> str:
        raise PermissionError("sensitive password or host path")

    monkeypatch.setattr(Path, "read_text", unreadable)
    run = AsyncMock()
    monkeypatch.setattr(recovery_check, "run", run)
    with pytest.raises(PermissionError):
        await recovery_check.main()
    run.assert_not_awaited()
    output = capsys.readouterr()
    assert output.out == "FAIL: recovery checker PermissionError\n"
    assert output.err == ""
    # read_text is intentionally denied; only validate the resulting token bytes.
    assert (tmp_path / "failed").read_bytes() == TOKEN.encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["not-a-generated-token", TOKEN])
async def test_invalid_scope_never_writes_a_failure_marker(
    token: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("RECOVERY_RUN_TOKEN", token)
    monkeypatch.setenv("RECOVERY_CONTROL_DIRECTORY", "/untrusted-sensitive-path")
    marker = Mock()
    monkeypatch.setattr(recovery_check, "signal", marker)
    with pytest.raises(ValueError):
        await recovery_check.main()
    marker.assert_not_called()
    assert capsys.readouterr().out == "FAIL: recovery checker ValueError\n"


@pytest.mark.asyncio
async def test_unwritable_failure_marker_preserves_original_sanitized_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(recovery_check, "control_scope", lambda: (tmp_path, TOKEN))
    monkeypatch.setattr(recovery_check, "configuration", Mock(side_effect=ValueError("secret")))
    monkeypatch.setattr(recovery_check, "signal", Mock(side_effect=PermissionError("private")))
    with pytest.raises(ValueError, match="secret"):
        await recovery_check.main()
    assert capsys.readouterr().out == (
        "FAIL: recovery checker ValueError\nFAIL: recovery checker failure marker unavailable\n"
    )


def test_cli_reports_configuration_failure_without_traceback() -> None:
    environment = os.environ | {
        "RECOVERY_RUN_TOKEN": "sensitive-invalid-token",
        "RECOVERY_CONTROL_DIRECTORY": "/not-permitted",
    }
    result = subprocess.run(
        [sys.executable, "-m", "scripts.recovery_check"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == "FAIL: recovery checker ValueError\n"
    assert result.stderr == ""
