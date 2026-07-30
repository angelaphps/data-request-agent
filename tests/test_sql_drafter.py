"""LLM SQL drafter helpers (no live API required)."""

from __future__ import annotations

from data_request_agent.proposers import DraftPlan, ParsedAsk
from data_request_agent.sql_drafter import _strip_fences


def test_strip_markdown_fences():
    raw = "```sql\nSELECT 1 AS n\n```"
    assert _strip_fences(raw) == "SELECT 1 AS n"


def test_template_drafter_still_works_for_tests():
    from data_request_agent.proposers import catalog_sql_drafter

    draft = catalog_sql_drafter(
        ParsedAsk(
            status="ok",
            intent="total_revenue_usd",
            metric_name="total_revenue_usd",
        )
    )
    assert isinstance(draft, DraftPlan)
    assert "payments" in draft.sql.lower()
