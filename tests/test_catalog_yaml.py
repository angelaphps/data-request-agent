"""Catalog YAML normalize — relation-aware format."""

from __future__ import annotations

from data_request_agent.catalog_yaml import normalize_dataset_doc


def test_normalize_sessions_style_relations():
    doc = {
        "name": "sessions",
        "table": "sessions",
        "schema": "public",
        "description": "sessions",
        "fields": [
            {
                "name": "user_id",
                "type": "integer",
                "description": "fk",
                "role": "relationship",
                "related_table": "users",
                "related_field": "user_id",
            },
            {
                "name": "duration_minutes",
                "type": "integer",
                "role": "measure",
                "description": "mins",
            },
        ],
        "measures": [
            {"name": "avg_session_duration", "formula": "AVG(duration_minutes)", "description": "avg"}
        ],
        "golden_queries": [
            {
                "name": "by_device",
                "description": "avg by device",
                "sql": "SELECT 1",
            }
        ],
    }
    norm = normalize_dataset_doc(doc)
    assert norm["table_schema"] == "public"
    assert norm["table_name"] == "sessions"
    assert norm["relationships"][0]["to_dataset"] == "users"
    assert norm["measures"][0]["expression"] == "AVG(duration_minutes)"
    assert norm["golden_queries"][0]["name"] == "by_device"
    sens = {c["name"]: c["sensitivity"] for c in norm["columns"]}
    assert sens["duration_minutes"] == "internal"


def test_normalize_legacy_columns_still_works():
    doc = {
        "name": "payments",
        "description": "pay",
        "tables": [{"schema": "public", "name": "payments"}],
        "columns": [
            {"name": "amount_usd", "description": "usd", "sensitivity": "internal"}
        ],
    }
    norm = normalize_dataset_doc(doc)
    assert norm["table_name"] == "payments"
    assert norm["columns"][0]["name"] == "amount_usd"
    assert norm["relationships"] == []
