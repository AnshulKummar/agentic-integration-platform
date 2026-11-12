"""The README's quickstart must keep working: run it end to end in both modes."""

from __future__ import annotations

import pytest

from app.platform.quickstart import run

pytestmark = pytest.mark.slow


async def test_quickstart_with_learning_reuses_what_loop1_taught() -> None:
    s = await run(learning=True, quiet=True)
    assert s.loop1_status == "approved" and s.loop1_human_edits == 3
    assert s.production_runs_ok >= 6 and s.billing_customers_created >= 1
    assert s.kg_nodes_after > s.kg_nodes_before
    assert s.loop2_roadmap_item == "Contract-to-cash automation"
    assert (s.loop2_reused_from_kg, s.loop2_mappings) == (7, 9)
    assert any("signer_email" in d for d in s.loop2_deprecated_flagged)


async def test_quickstart_without_learning_reuses_nothing() -> None:
    s = await run(learning=False, quiet=True)
    assert s.kg_nodes_after == s.kg_nodes_before
    assert s.loop2_reused_from_kg == 0 and s.loop2_mappings == 9
