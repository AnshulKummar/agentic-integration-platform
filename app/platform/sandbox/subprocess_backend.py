"""SubprocessSandbox: a local ``python -I`` child. Development and tests ONLY.

This is not a security boundary (same kernel, same user, network available to a
process that escapes the in-process restrictions); production configuration
refuses it (``PlatformSettings.production_problems``) and ``build_sandbox``
refuses it again. Isolation it does provide:

- ``python -I``: isolated mode (no PYTHON* env vars, no user site, no cwd on sys.path);
- the *base* interpreter (``sys._base_executable``), not a venv launcher, so the
  process we limit is the one running the code;
- a minimal environment: no PLATFORM_*/ANTHROPIC_* or any other parent
  variable, only what Windows needs to start (``SYSTEMROOT``) or ``PATH`` on POSIX;
- a fresh temporary working directory, deleted afterwards;
- wall-clock timeout with kill (see ``process.py``);
- memory cap: ``RLIMIT_AS`` on POSIX (plus ``RLIMIT_CORE=0``, ``RLIMIT_FSIZE``);
  on Windows, best effort via a Job Object with a per-process memory limit,
  an active-process limit of 1 (no child processes) and kill-on-close. If the
  Job Object cannot be created the run continues without a memory cap and a
  warning is logged.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import tempfile
from collections.abc import Callable

from app.platform.interfaces import SandboxLimits
from app.platform.sandbox.process import Child, ProcessSandbox, bootstrap_source, stream_limit

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
MAX_FILE_BYTES = 1_000_000


def base_python() -> str:
    """The real interpreter (a Windows venv's python.exe is a launcher that spawns it)."""
    base = getattr(sys, "_base_executable", None)
    return base if isinstance(base, str) and base and os.path.exists(base) else sys.executable


def child_env(workdir: str) -> dict[str, str]:
    """Allowlisted environment for the child: nothing from the parent beyond OS essentials."""
    if IS_WINDOWS:
        env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows")}
    else:
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    env["TMP"] = env["TEMP"] = env["TMPDIR"] = workdir
    return env


def _posix_limits(memory_mb: int) -> Callable[[], None]:
    def apply() -> None:  # runs in the child between fork and exec
        import resource

        mem = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES))

    return apply


class _WindowsJob:
    """A Job Object capping one process's memory; closing it kills the process."""

    def __init__(self, pid: int, memory_mb: int) -> None:
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in ("Read", "Write", "Other", "ReadBytes", "WriteBytes", "OtherBytes")
            ]

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        job_extended_limit_information = 9
        limit_active_process = 0x0008
        limit_process_memory = 0x0100
        limit_kill_on_job_close = 0x2000
        process_set_quota = 0x0100
        process_terminate = 0x0001

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        self._k32 = k32

        job = k32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        self._job: int | None = job
        info = ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = limit_process_memory | limit_kill_on_job_close | limit_active_process
        info.BasicLimitInformation.ActiveProcessLimit = 1
        info.ProcessMemoryLimit = memory_mb * 1024 * 1024
        try:
            if not k32.SetInformationJobObject(job, job_extended_limit_information, ctypes.byref(info),
                                               ctypes.sizeof(info)):
                raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
            proc = k32.OpenProcess(process_set_quota | process_terminate, False, pid)
            if not proc:
                raise OSError(ctypes.get_last_error(), "OpenProcess failed")
            try:
                if not k32.AssignProcessToJobObject(job, proc):
                    raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
            finally:
                k32.CloseHandle(proc)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Closing the last handle kills every process in the job (KILL_ON_JOB_CLOSE)."""
        if self._job:
            self._k32.CloseHandle(self._job)
            self._job = None


class SubprocessSandbox(ProcessSandbox):
    backend = "subprocess"

    def __init__(
        self,
        *,
        python: str | None = None,
        startup_timeout_s: float = 15.0,
        tool_call_timeout_s: float = 60.0,
    ) -> None:
        super().__init__(startup_timeout_s=startup_timeout_s, tool_call_timeout_s=tool_call_timeout_s)
        self.python = python or base_python()

    async def launch(self, limits: SandboxLimits, *, script: str | None = None) -> Child:
        workdir = tempfile.mkdtemp(prefix="pmaip-sbx-")
        kwargs: dict[str, object] = {}
        if IS_WINDOWS:
            import subprocess

            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["preexec_fn"] = _posix_limits(limits.memory_mb)
            kwargs["start_new_session"] = True
        try:
            proc = await asyncio.create_subprocess_exec(
                self.python,
                "-I",
                "-c",
                script if script is not None else bootstrap_source(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
                env=child_env(workdir),
                limit=stream_limit(limits),
                **kwargs,  # type: ignore[arg-type]
            )
        except BaseException:
            _remove(workdir)
            raise

        job: _WindowsJob | None = None
        if IS_WINDOWS:
            # The child blocks on stdin before running any integration code, so
            # assigning the job here (after spawn) is not racy for our purposes.
            try:
                job = _WindowsJob(proc.pid, limits.memory_mb)
            except OSError as exc:
                log.warning("sandbox.windows_job_unavailable: running without a memory cap (%s)", exc)

        async def kill() -> None:
            if proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.close()
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass  # already exited between the check and the kill
            await proc.wait()
            if job is not None:
                job.close()

        async def cleanup() -> None:
            await kill()
            _remove(workdir)

        return Child(proc=proc, kill=kill, cleanup=cleanup)


def _remove(path: str) -> None:
    try:
        shutil.rmtree(path)
    except OSError as exc:
        log.warning("sandbox.workdir_cleanup_failed path=%s error=%s", path, exc)
