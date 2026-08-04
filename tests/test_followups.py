"""Stage 4 follow-ups — aggregates only; no personal schema / row-level PII in LM."""

from __future__ import annotations

from data_request_agent.followups import (
    build_followup_prompt,
    build_thread_context_payload,
    looks_like_followup,
)


def test_looks_like_followup_vs_fresh_extract():
    assert looks_like_followup("why is Web higher?", has_context=True)
    assert not looks_like_followup(
        "top 10 users by session times", has_context=True
    )
    assert not looks_like_followup(
        "session duration trend by device", has_context=True
    )
    assert not looks_like_followup(
        "list of users with country and device most recent 20",
        has_context=True,
    )
    assert not looks_like_followup("why is Web higher?", has_context=False)


def test_thread_context_omits_personal_schema_columns():
    ctx = build_thread_context_payload(
        original_ask="analyze by device",
        answer="iOS leads",
        table_markdown="| device_type | mean |\n| iOS | 12 |",
        plain_language_plan="avg by device",
        stats={"top": [{"device_type": "iOS", "mean": 12}]},
        schema_slice=[
            {
                "name": "device_type",
                "description": "device",
                "sensitivity": "personal",
                "dtype": "object",
                "dataset": "users",
            },
            {
                "name": "avg_duration_minutes",
                "description": "minutes",
                "sensitivity": "none",
                "dtype": "float64",
                "dataset": "sessions",
            },
        ],
        data_as_of="2026-01-01T00:00:00Z",
    )
    assert "device_type" not in (ctx.get("column_names") or [])
    names = [c["name"] for c in ctx["schema_slice"]]
    assert "avg_duration_minutes" in names
    assert "device_type" not in names


def test_followup_prompt_excludes_personal_schema_fields():
    """Personal catalog columns are omitted from follow-up LM schema context."""
    secret = "SECRET_EMAIL_alice@example.com"
    ctx = build_thread_context_payload(
        original_ask="analyze duration by country",
        answer="US leads",
        table_markdown="| country | mean |\n| US | 12 |",
        plain_language_plan="avg by country",
        stats={"top": [{"country": "US", "mean_avg_duration_minutes": 12.0}]},
        schema_slice=[
            {
                "name": "country",
                "description": "geo",
                "sensitivity": "none",
                "dtype": "object",
                "dataset": "users",
            },
            {
                "name": "email",
                "description": "contact",
                "sensitivity": "personal",
                "dtype": "object",
                "dataset": "users",
            },
        ],
        data_as_of="2026-01-01T00:00:00Z",
    )
    # Schema list excludes personal; stored summary never included the secret.
    assert secret not in str(ctx)
    assert "email" not in (ctx.get("column_names") or [])
    prompt = build_followup_prompt(question="why is US higher?", context=ctx)
    assert secret not in prompt
    assert "- email:" not in prompt
