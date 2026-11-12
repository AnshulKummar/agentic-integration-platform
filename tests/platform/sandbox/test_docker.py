"""DockerSandbox: command hardening (always) and real container runs (marked ``docker``)."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from functools import cache
from typing import Any

import pytest

from app.platform.config import ConfigError, PlatformSettings
from app.platform.interfaces import SandboxLimits
from app.platform.sandbox import build_sandbox
from app.platform.sandbox.docker_backend import DockerSandbox, docker_cli_env
from app.platform.sandbox.subprocess_backend import SubprocessSandbox

IMAGE = PlatformSettings().sandbox_image


@cache
def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


needs_docker = pytest.mark.skipif(not docker_available(), reason="docker binary/daemon not available")


def test_command_is_locked_down() -> None:
    cmd = DockerSandbox(IMAGE).command(SandboxLimits(memory_mb=200), "pmaip-sbx-test")
    joined = " ".join(cmd[:-1])
    for flag in (
        "run --rm -i", "--network none", "--read-only", "--tmpfs /tmp:rw,size=16m", "--memory 200m",
        "--memory-swap 200m", "--cpus 0.5", "--pids-limit 64", "--user 65534:65534", "--cap-drop ALL",
        "--security-opt no-new-privileges", "--name pmaip-sbx-test",
    ):
        assert flag in joined, flag
    assert cmd[-5:-1] == [IMAGE, "python", "-I", "-c"]
    assert "-e" not in cmd and "--env" not in cmd and "--privileged" not in cmd
    assert "def main()" in cmd[-1]


def test_docker_cli_env_drops_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM_MASTER_KEY", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    monkeypatch.setenv("GITHUB_TOKEN", "z")
    monkeypatch.setenv("DOCKER_HOST", "npipe:////./pipe/docker_engine")
    env = docker_cli_env()
    assert "PLATFORM_MASTER_KEY" not in env and "ANTHROPIC_API_KEY" not in env and "GITHUB_TOKEN" not in env
    assert env["DOCKER_HOST"] == "npipe:////./pipe/docker_engine"


def test_build_sandbox_selects_backend(settings: PlatformSettings) -> None:
    assert isinstance(build_sandbox(settings), SubprocessSandbox)
    docker = build_sandbox(settings.model_copy(update={"sandbox_backend": "docker", "sandbox_image": "img:1"}))
    assert isinstance(docker, DockerSandbox) and docker.image == "img:1"
    with pytest.raises(ConfigError):
        build_sandbox(settings.model_copy(update={"env": "production"}))


@pytest.mark.docker
@needs_docker
async def test_docker_runs_code_with_tool_calls(fake_broker: Any) -> None:
    code = "def run(event, tools):\n    print('hi')\n    return {'r': tools.call('svc.echo', {'n': event['n']})}\n"
    broker = fake_broker({"svc.echo": {"ok": 1}})
    res = await DockerSandbox(IMAGE).run(code, {"n": 1}, broker, SandboxLimits(timeout_s=20))
    assert res.ok, (res.error, res.logs)
    assert res.output == {"r": {"ok": 1}}
    assert res.logs == ["hi"]


@pytest.mark.docker
@needs_docker
async def test_docker_timeout_kills_container(fake_broker: Any) -> None:
    code = "def run(event, tools):\n    while True:\n        pass\n"
    res = await DockerSandbox(IMAGE).run(code, {}, fake_broker({}), SandboxLimits(timeout_s=2))
    assert not res.ok and "timed out" in (res.error or "")
    names = subprocess.run(["docker", "ps", "--filter", "name=pmaip-sbx-", "-q"], capture_output=True, text=True)
    assert names.stdout.strip() == ""


@pytest.mark.docker
@needs_docker
async def test_docker_container_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM_MASTER_KEY", "must-not-leak")
    probe = (
        "import json, os, socket\n"
        "out = {'env': dict(os.environ), 'uid': os.getuid()}\n"
        "try:\n    socket.create_connection(('1.1.1.1', 53), 3); out['net'] = True\n"
        "except OSError:\n    out['net'] = False\n"
        "try:\n    open('/etc/pwned', 'w'); out['rootfs_writable'] = True\n"
        "except OSError:\n    out['rootfs_writable'] = False\n"
        "print(json.dumps(out))\n"
    )
    child = await DockerSandbox(IMAGE).launch(SandboxLimits(), script=probe)
    try:
        assert child.proc.stdout is not None
        out = json.loads(await asyncio.wait_for(child.proc.stdout.readline(), 120))
    finally:
        await child.cleanup()
    assert out["net"] is False
    assert out["rootfs_writable"] is False
    assert out["uid"] == 65534
    assert "PLATFORM_MASTER_KEY" not in out["env"]
