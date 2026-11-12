"""Imports every table module so Base.metadata is complete (create_all, Alembic)."""

from app.platform import core_models  # noqa: F401
from app.platform.auth import models as auth_models  # noqa: F401
from app.platform.connectors import models as connector_models  # noqa: F401
from app.platform.deployments import models as deployment_models  # noqa: F401
from app.platform.evals import models as eval_models  # noqa: F401
from app.platform.secrets import models as secret_models  # noqa: F401
