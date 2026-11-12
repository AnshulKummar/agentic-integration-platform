"""Prometheus metrics, on a dedicated registry. Any area may import and record.

The observability area exposes them at GET /metrics and adds HTTP middleware.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

_LATENCY = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)

HTTP_REQUESTS = Counter(
    "pmaip_http_requests_total", "HTTP requests", ["method", "route", "status"], registry=REGISTRY
)
HTTP_LATENCY = Histogram(
    "pmaip_http_request_duration_seconds", "HTTP latency", ["method", "route"], buckets=_LATENCY, registry=REGISTRY
)
EXECUTIONS = Counter(
    "pmaip_integration_executions_total",
    "Integration runs",
    ["tenant", "integration", "stage", "outcome"],
    registry=REGISTRY,
)
EXECUTION_LATENCY = Histogram(
    "pmaip_integration_execution_duration_seconds",
    "Integration run latency",
    ["stage"],
    buckets=_LATENCY,
    registry=REGISTRY,
)
TOOL_CALLS = Counter("pmaip_tool_calls_total", "Tool calls via broker", ["tool", "outcome"], registry=REGISTRY)
CONNECTOR_RETRIES = Counter(
    "pmaip_connector_retries_total", "Connector retries", ["system", "reason"], registry=REGISTRY
)
SANDBOX_RUNS = Counter("pmaip_sandbox_runs_total", "Sandbox runs", ["backend", "outcome"], registry=REGISTRY)
DEPLOYMENTS = Counter("pmaip_deployments_total", "Deployments finished", ["outcome"], registry=REGISTRY)
ROLLBACKS = Counter("pmaip_rollbacks_total", "Automatic or manual rollbacks", ["reason"], registry=REGISTRY)
LLM_TOKENS = Counter("pmaip_llm_tokens_total", "LLM tokens", ["model", "kind"], registry=REGISTRY)
LLM_COST = Counter("pmaip_llm_cost_usd_total", "Estimated LLM spend (USD)", ["model"], registry=REGISTRY)
AUTH_EVENTS = Counter("pmaip_auth_events_total", "Auth events", ["event", "outcome"], registry=REGISTRY)
