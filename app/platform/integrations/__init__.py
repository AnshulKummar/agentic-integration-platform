"""Integration lifecycle API: KG persistence, drafting, review, dual approval, API releases.

OWNED BY: integrations area (F).

Other areas use ``app.platform.integrations.service`` (e.g. deployments calls
``learn_from_version`` when a version goes live).
"""

from app.platform.integrations import models as _models  # noqa: F401  (registers tables)
