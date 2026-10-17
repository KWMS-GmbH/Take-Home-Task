"""Exercise deploy.sh gates with an isolated fake Docker CLI, not a real stack."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

DOCKER = r"""
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
mode = os.environ["FAKE_DEPLOY_MODE"]
with Path(os.environ["FAKE_DEPLOY_LOG"]).open("a") as log:
    log.write(json.dumps(args) + "\n")
joined = " ".join(args)
if args[0] == "info":
    print("active true")
elif args[:2] == ["image", "inspect"]:
    print("sha256:fakebackend" if "backend" in joined else "sha256:fakefrontend")
elif args[:2] == ["service", "ls"]:
    print("3/3" if "backend" in joined else "1/1")
elif args[:2] == ["service", "inspect"]:
    if "UpdateStatus" in joined:
        print({"rollback": "rollback_completed", "updating": "updating"}.get(mode, "completed"))
    elif "ContainerSpec.Image" in joined:
        print("kwms-backend:fakebackend" if args[-1].endswith("_backend") else
              "kwms-frontend:fakefrontend" if args[-1].endswith("_frontend") else "postgres:fake")
    else:
        raise SystemExit(90)
elif args[0] == "ps":
    print("b1\nb2\nb3" if "_backend" in joined else "f1" if "_frontend" in joined else "d1")
elif args[0] == "inspect":
    identity = args[-1]
    if "Config.Image" in joined:
        print("old:backend" if mode == "old-image" and identity.startswith("b") else
              "kwms-backend:fakebackend" if identity.startswith("b") else
              "kwms-frontend:fakefrontend" if identity == "f1" else "postgres:fake")
    else:
        print("healthy")
elif args[0] == "exec":
    assert "/health/ready" in joined
    raise SystemExit(1 if mode == "unready" else 0)
elif args[0] in {"build", "tag", "secret", "stack"}:
    pass
else:
    raise SystemExit(91)
"""


@pytest.mark.parametrize("mode", ["ready", "unready", "old-image", "rollback", "updating"])
def test_deploy_requires_completed_current_image_and_each_tasks_readiness(
    tmp_path: Path, mode: str
) -> None:
    tools = tmp_path / "bin"
    tools.mkdir()
    for name, program in {
        "docker": DOCKER,
        "openssl": "raise SystemExit(92)\n",  # Existing fake secrets must be reused.
        "date": (
            "import os\nfrom pathlib import Path\n"
            'p=Path(os.environ["FAKE_DEPLOY_CLOCK"])\n'
            "n=int(p.read_text()) if p.exists() else 0\n"
            "p.write_text(str(n+1))\nprint(n)\n"
        ),
    }.items():
        executable = tools / name
        executable.write_text(f"#!{sys.executable}\n{program}")
        executable.chmod(0o700)
    environment = os.environ.copy()
    environment.update(
        PATH=f"{tools}:{environment['PATH']}",
        STACK_NAME="kwms_deploy_unit",
        DEPLOY_TIMEOUT_SECONDS="1",
        FAKE_DEPLOY_MODE=mode,
        FAKE_DEPLOY_LOG=str(tmp_path / "calls"),
        FAKE_DEPLOY_CLOCK=str(tmp_path / "clock"),
    )
    result = subprocess.run(
        ["sh", str(ROOT / "deploy.sh")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if mode == "ready":
        assert result.returncode == 0, result.stderr
        assert "is live and ready" in result.stdout
        calls = (tmp_path / "calls").read_text()
        for identity in ("b1", "b2", "b3", "f1"):
            assert f'["exec", "{identity}"' in calls
    else:
        assert result.returncode != 0
        assert "is live and ready" not in result.stdout
        assert "ERROR:" in result.stderr
