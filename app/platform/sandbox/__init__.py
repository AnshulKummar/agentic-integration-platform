"""Generated-code validation, rendering and isolated execution. OWNED BY: sandbox area (C).

- ``codegen.render_runnable`` / ``codegen.required_tools``: mapping rows -> reviewable code + allowed tools
- ``validate.validate_code``: AST gate run before every execution
- ``broker.DefaultToolBroker``: allowed-tools + budget gate in front of connectors
- ``build_sandbox(settings)``: the configured :class:`~app.platform.interfaces.Sandbox`
- ``runner.DefaultIntegrationRunner``: load version -> run -> ExecutionRecord + metrics
"""

from __future__ import annotations

from app.platform.config import ConfigError, PlatformSettings
from app.platform.interfaces import Sandbox


def build_sandbox(settings: PlatformSettings) -> Sandbox:
    """The sandbox backend selected by ``settings.sandbox_backend``.

    The subprocess backend is refused in production even if configuration
    validation was bypassed: it is not a security boundary.
    """
    # Each tool call may retry; leave room for every attempt plus backoff.
    tool_timeout = settings.connector_timeout_s * (settings.connector_max_retries + 1) + 5.0
    if settings.sandbox_backend == "docker":
        from app.platform.sandbox.docker_backend import DockerSandbox

        return DockerSandbox(settings.sandbox_image, tool_call_timeout_s=tool_timeout)
    if settings.env == "production":
        raise ConfigError("the subprocess sandbox is not allowed in production; set PLATFORM_SANDBOX_BACKEND=docker")
    from app.platform.sandbox.subprocess_backend import SubprocessSandbox

    return SubprocessSandbox(tool_call_timeout_s=tool_timeout)


__all__ = ["build_sandbox"]
