"""Country-aware parsing and SQL drafting."""

from __future__ import annotations

from data_request_agent.proposers import (
    ParsedAsk,
    catalog_keyword_parser,
    catalog_sql_drafter,
)


def test_us_revenue_maps_to_filtered_metric():
    metrics = [
        "total_revenue_usd",
        "revenue_by_country",
        "users_by_country",
        "active_subscriptions",
        "latest_dau",
    ]
    parsed = catalog_keyword_parser(
        "how much money did usa bring in?",
        metric_names=metrics,
    )
    assert parsed.status == "ok"
    assert parsed.metric_name == "revenue_by_country"
    assert parsed.country_filter == "US"
    draft = catalog_sql_drafter(parsed)
    assert "WHERE country = 'US'" in draft.sql
    assert "user_revenue_summary" in draft.sql


def test_us_users_maps_to_users_by_country():
    metrics = ["users_by_country", "revenue_by_country", "total_revenue_usd"]
    parsed = catalog_keyword_parser(
        "what about users from us?",
        metric_names=metrics,
    )
    assert parsed.status == "ok"
    assert parsed.metric_name == "users_by_country"
    assert parsed.country_filter == "US"


def test_revenue_from_us_users_phrase():
    metrics = ["users_by_country", "revenue_by_country", "total_revenue_usd"]
    parsed = catalog_keyword_parser(
        "like how much revenue did us users bring in?",
        metric_names=metrics,
    )
    assert parsed.status == "ok"
    assert parsed.metric_name == "revenue_by_country"
    assert parsed.country_filter == "US"
