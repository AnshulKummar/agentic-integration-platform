"""DockerSandbox: the production backend. Same child protocol, inside a locked-down container.

    docker run --rm -i --name <unique> --network none --read-only
        --tmpfs /tmp:rw,size=16m --memory <m>m --memory-swap <m>m --cpus 0.5
        --pids-limit 64 --user 65534:65534 --cap-drop ALL
        --security-opt no-new-privileges --workdir /tmp
        <settings.sandbox_image> python -I -c <bootstrap>

No network, no writable filesystem except a small tmpfs, unprivileged user, no
capabilities, bounded memory/CPU/PIDs, and no environment variables from the
host. The container is named so a timeout can ``docker kill`` it (killing the
``docker`` CLI alone would leave the container running).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid

from app.platform.interfaces import SandboxLimits
from app.platform.sandbox.process import Child, ProcessSandbox, bootstrap_source, stream_limit

log = logging.getLogger(__name__)

_SECRETISH_ENV = re.compile(r"(SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|PRIVATE|CREDENTIAL|MASTER_KEY)", re.IGNORECASE)


def docker_cli_env() -> dict[str, str]:
    """Environment for the ``docker`` CLI itself (never forwarded into the container).

    Keeps what the CLI needs (PATH, HOME/USERPROFILE, DOCKER_*), drops platform and
    LLM settings and anything that looks like a credential.
    """
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper.startswith(("PLATFORM_", "ANTHROPIC_")):
            continue
        if _SECRETISH_ENV.search(upper) and not upper.startswith("DOCKER_"):
            continue
        env[key] = value
    return env


class DockerSandbox(ProcessSandbox):
    backend = "docker"

    def __init__(
        self,
        image: str,
        *,
        docker_bin: str = "docker",
        cpus: float = 0.5,
        pids_limit: int = 64,
        tmpfs_mb: int = 16,
        startup_timeout_s: float = 60.0,
        tool_call_timeout_s: float = 60.0,
    ) -> None:
        super().__init__(startup_timeout_s=startup_timeout_s, tool_call_timeout_s=tool_call_timeout_s)
        self.image = image
        self.docker_bin = docker_bin
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.tmpfs_mb = tmpfs_mb
        self.exit_grace_s = 1.0

    def command(self, limits: SandboxLimits, name: str, script: str | None = None) -> list[str]:
        mem = f"{limits.memory_mb}m"
        return [
            self.docker_bin, "run", "--rm", "-i",
            "--name", name,
            "--network", "none",
            "--read-only",
            "--tmpfs", f"/tmp:rw,size={self.tmpfs_mb}m",
            "--memory", mem,
            "--memory-swap", mem,
            "--cpus", str(self.cpus),
            "--pids-limit", str(self.pids_limit),
            "--user", "65534:65534",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--workdir", "/tmp",
            self.image,
            "python", "-I", "-c", script if script is not None else bootstrap_source(),
        ]  # fmt: skip

    async def launch(self, limits: SandboxLimits, *, script: str | None = None) -> Child:
        name = f"pmaip-sbx-{uuid.uuid4().hex[:20]}"
        proc = await asyncio.create_subprocess_exec(
            *self.command(limits, name, script),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=docker_cli_env(),
            limit=stream_limit(limits),
        )
        killed = False

        async def kill() -> None:
            nonlocal killed
            if proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.close()
            if proc.returncode is None and not killed:
                killed = True
                try:  # after a result the container exits by itself; don't race it with `docker kill`
                    await asyncio.wait_for(proc.wait(), timeout=self.exit_grace_s)
                    return
                except TimeoutError:
                    pass  # still running (timeout/abort path): kill it below
                await self._docker_kill(name)
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass  # the CLI exited on its own after the container stopped
            await proc.wait()

        async def cleanup() -> None:
            await kill()

        return Child(proc=proc, kill=kill, cleanup=cleanup)

    async def _docker_kill(self, name: str) -> None:
        killer = await asyncio.create_subprocess_exec(
            self.docker_bin, "kill", name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=docker_cli_env(),
        )  # fmt: skip
        _, err = await killer.communicate()
        if killer.returncode not in (0, None):
            text = err.decode("utf-8", errors="replace").strip()
            # "No such container" just means it already exited (--rm).
            if "No such container" not in text and "is not running" not in text:
                log.warning("sandbox.docker_kill_failed name=%s error=%s", name, text[:300])
