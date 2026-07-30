"""Analysis-intent detection for delivery branching."""

from __future__ import annotations

from data_request_agent.proposers import detect_wants_analysis


def test_trend_and_stats_trigger_analysis():
    assert detect_wants_analysis("show revenue trend over time")
    assert detect_wants_analysis("YoY growth in subscriptions")
    assert detect_wants_analysis("session duration distribution by device")
    assert detect_wants_analysis("any stats on DAU?")
    assert detect_wants_analysis("chart average session length")


def test_plain_extract_does_not_trigger_analysis():
    assert not detect_wants_analysis("top 10 users by session times")
    assert not detect_wants_analysis("how much revenue did the US bring in?")
    assert not detect_wants_analysis("active subscriptions")
