"""PMAIP production platform.

Multi-tenant service around the ``app.demo`` domain logic (schema alignment,
mapping drafting, KG learning, REST->MCP tools), adding identity, persistence,
secrets, sandboxed execution, health-checked deployments, monitoring and evals.

Entry points:
    uvicorn app.platform.main:app          # API
    python -m app.platform.worker          # deployment watcher / job runner
"""
