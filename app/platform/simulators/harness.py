"""Serve a simulator over a real TCP socket (uvicorn in a background thread).

    with run_simulator(create_crm_app(...)) as base_url:
        ...  # http://127.0.0.1:<ephemeral port>

Used by end-to-end tests so connectors exercise real sockets, timeouts and
connection errors rather than an in-process ASGI transport.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn
from fastapi import FastAPI


class SimulatorStartError(RuntimeError):
    pass


@contextmanager
def run_simulator(app: FastAPI, *, host: str = "127.0.0.1", startup_timeout_s: float = 10.0) -> Iterator[str]:
    config = uvicorn.Config(app, host=host, port=0, log_level="warning", access_log=False, lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name=f"sim-{app.title}", daemon=True)
    thread.start()
    deadline = time.monotonic() + startup_timeout_s
    while not server.started:
        if not thread.is_alive():
            raise SimulatorStartError(f"simulator {app.title!r} exited during startup")
        if time.monotonic() > deadline:
            server.should_exit = True
            raise SimulatorStartError(f"simulator {app.title!r} did not start within {startup_timeout_s}s")
        time.sleep(0.01)
    try:
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=startup_timeout_s)
        if thread.is_alive():
            raise SimulatorStartError(f"simulator {app.title!r} did not shut down")
