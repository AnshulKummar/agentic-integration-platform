"""Run one vendor simulator: ``python -m app.platform.simulators --app crm|esign|billing --port N``.

Credentials come from the environment (``SIM_ADMIN_TOKEN``, ``SIM_CRM_CLIENT_ID``,
``SIM_CRM_CLIENT_SECRET``, ``SIM_ESIGN_API_KEY``, ``SIM_ESIGN_WEBHOOK_SECRET``,
``SIM_BILLING_API_TOKEN``). Any that are unset are generated randomly and
printed once to stdout: they are throwaway simulator credentials that the
operator needs to configure the platform's secrets/connections, never platform secrets.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from collections.abc import Sequence

import uvicorn
from fastapi import FastAPI

from app.platform.simulators.billing import create_billing_app
from app.platform.simulators.crm import create_crm_app
from app.platform.simulators.esign import create_esign_app

APPS = ("crm", "esign", "billing")


def _cred(env: str, generated: dict[str, str]) -> str:
    value = os.environ.get(env)
    if value:
        return value
    value = secrets.token_urlsafe(24)
    generated[env] = value
    return value


def build_app(name: str, generated: dict[str, str]) -> FastAPI:
    admin = _cred("SIM_ADMIN_TOKEN", generated)
    if name == "crm":
        return create_crm_app(
            client_id=_cred("SIM_CRM_CLIENT_ID", generated),
            client_secret=_cred("SIM_CRM_CLIENT_SECRET", generated),
            admin_token=admin,
        )
    if name == "esign":
        return create_esign_app(
            api_key=_cred("SIM_ESIGN_API_KEY", generated),
            webhook_secret=_cred("SIM_ESIGN_WEBHOOK_SECRET", generated),
            admin_token=admin,
        )
    if name == "billing":
        return create_billing_app(api_token=_cred("SIM_BILLING_API_TOKEN", generated), admin_token=admin)
    raise ValueError(f"unknown simulator {name!r}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.platform.simulators", description=__doc__)
    parser.add_argument("--app", choices=APPS, required=True)
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    generated: dict[str, str] = {}
    app = build_app(args.app, generated)
    if generated:
        print("Generated simulator-only credentials (set the SIM_* env vars to pin them):", file=sys.stdout)
        for env, value in generated.items():
            print(f"  {env}={value}", file=sys.stdout)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
