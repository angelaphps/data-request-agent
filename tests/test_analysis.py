"""Analysis path — no data rows in LM context; runner produces table/chart."""

from __future__ import annotations

import pandas as pd

from data_request_agent.analysis import (
    AnalysisPlan,
    assert_prompt_has_no_data_rows,
    build_analysis_prompt,
    heuristic_analysis_plan,
    run_analysis,
    schema_slice_from_governance,
)
from data_request_agent.config import Settings
from data_request_agent.governance import Governance


def test_analysis_context_has_no_data_rows(settings: Settings, gov: Governance):
    frame = pd.DataFrame(
        {
            "device_type": ["Web", "iOS", "SECRET_TOKEN_XYZ"],
            "avg_duration_minutes": [10.5, 12.0, 99.0],
        }
    )
    schema = schema_slice_from_governance(
        gov,
        column_names=list(frame.columns),
        dtypes={c: str(frame[c].dtype) for c in frame.columns},
    )
    prompt = build_analysis_prompt(
        ask="analyze session duration by device",
        schema_slice=schema,
    )
    assert "SECRET_TOKEN_XYZ" not in prompt
    assert "10.5" not in prompt
    assert_prompt_has_no_data_rows(prompt, frame, schema_slice=schema)
    for col in schema:
        assert "description" in col
        assert "name" in col


def test_run_analysis_builds_table_and_chart():
    frame = pd.DataFrame(
        {
            "device_type": ["Web", "Web", "iOS", "iOS", "Android"],
            "duration_minutes": [10, 20, 30, 40, 15],
        }
    )
    plan = AnalysisPlan(
        title="Duration by device",
        chart_type="bar",
        groupby_column="device_type",
        y_column="duration_minutes",
        aggregation="mean",
    )
    result = run_analysis(frame, plan, max_table_rows=10)
    assert result.answer
    assert "device_type" in result.table_markdown
    assert result.chart_png is not None
    assert result.chart_png[:8] == b"\x89PNG\r\n\x1a\n"
    assert result.context_had_no_rows is True


def test_heuristic_planner_picks_columns():
    schema = [
        {"name": "device_type", "description": "device", "sensitivity": "personal", "dtype": "object", "dataset": "users"},
        {"name": "duration_minutes", "description": "mins", "sensitivity": "internal", "dtype": "int64", "dataset": "sessions"},
    ]
    plan = heuristic_analysis_plan("trend of session duration by device", schema)
    assert plan.y_column == "duration_minutes"
    assert plan.groupby_column == "device_type"
