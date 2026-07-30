"""Delivery: personal-data guard · analysis or file · destination.

Stage 3 analysis reply: threaded text answer + inline markdown summary
table (≤ settings.analysis_summary_max_rows) + chart PNG. Full row
dumps remain file delivery when analysis is not requested.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd

from data_request_agent.analysis import (
    AnalysisPlan,
    build_live_analysis_planner,
    run_analysis,
    schema_slice_from_governance,
)
from data_request_agent.config import Settings
from data_request_agent.destinations import Destination, rows_to_csv
from data_request_agent.governance import Governance
from data_request_agent.sql_inspect import load_catalog_objects
from data_request_agent.state import AgentState

# Kept for older tests that still mention the Stage 1–2 mock notice.
ANALYSIS_MOCK_NOTICE = (
    "analysis replies aren't ready yet — here's the data as a file instead"
)


def guard_and_deliver(
    state: AgentState,
    *,
    gov: Governance,
    destination: Destination,
    settings: Settings | None = None,
    plan_analysis: Callable[..., AnalysisPlan] | None = None,
) -> AgentState:
    plan = (state.get("approved") or {}).get("plan") or state.get("plan") or {}
    rows: list[dict[str, Any]] = list(state.get("result_rows") or [])
    approved_cols = [
        c.lower()
        for c in (plan.get("approved_columns") or plan.get("trial_columns") or [])
    ]

    catalog = load_catalog_objects(gov)
    personal_cols: set[str] = set()
    for obj in catalog.values():
        for name, sens in obj.column_sensitivity.items():
            if sens == "personal":
                personal_cols.add(name.lower())

    guarded: list[dict[str, Any]] = []
    hidden: set[str] = set()
    for row in rows:
        new_row: dict[str, Any] = {}
        for key, value in row.items():
            kl = key.lower()
            if approved_cols and kl not in approved_cols:
                if kl in personal_cols:
                    hidden.add(key)
                    continue
                if approved_cols:
                    continue
            if kl in personal_cols and approved_cols and kl not in approved_cols:
                hidden.add(key)
                continue
            new_row[key] = value
        guarded.append(new_row)

    wants_analysis = bool(plan.get("wants_analysis"))
    read_at = datetime.now(timezone.utc).isoformat()
    settings = settings or Settings()
    hidden_note = (
        f" (hidden personal columns: {sorted(hidden)})" if hidden else ""
    )

    if wants_analysis:
        return _deliver_analysis(
            state,
            gov=gov,
            destination=destination,
            settings=settings,
            plan=plan,
            guarded=guarded,
            hidden_note=hidden_note,
            read_at=read_at,
            plan_analysis=plan_analysis,
        )

    csv_text = rows_to_csv(guarded)
    text = (
        f"Ran: {plan.get('plain_language_plan')}\n"
        f"Rows: {len(guarded)}"
        f"{hidden_note}\n"
        f"Data as of: {read_at}"
    )
    ref = destination.deliver(
        {
            "channel_id": state.get("channel_id"),
            "thread_ts": state.get("thread_ts"),
            "text": text,
            "csv": csv_text,
            "filename": "data_request.csv",
            "rows": guarded,
            "analysis_mock": False,
        }
    )
    gov.audit(
        "delivered",
        {
            "result_ref": ref,
            "row_count": len(guarded),
            "hidden_personal": sorted(hidden),
            "analysis_mock": False,
            "wants_analysis": False,
        },
        actor_slack_id=state.get("requester_slack_id"),
    )
    return {
        **state,
        "phase": "delivered",
        "result_ref": ref,
        "delivery_message": text,
        "guarded_rows": guarded,
        "analysis_mock": False,
    }


def _deliver_analysis(
    state: AgentState,
    *,
    gov: Governance,
    destination: Destination,
    settings: Settings,
    plan: dict[str, Any],
    guarded: list[dict[str, Any]],
    hidden_note: str,
    read_at: str,
    plan_analysis: Callable[..., AnalysisPlan] | None,
) -> AgentState:
    frame = pd.DataFrame(guarded)
    dtypes = {c: str(frame[c].dtype) for c in frame.columns} if not frame.empty else {}
    schema = schema_slice_from_governance(
        gov,
        column_names=list(frame.columns),
        dtypes=dtypes,
    )
    ask = (
        (state.get("request") or {}).get("raw_text")
        or state.get("raw_text")
        or plan.get("plain_language_plan")
        or ""
    )
    planner = plan_analysis or build_live_analysis_planner(settings=settings)
    try:
        analysis_plan = planner(
            ask,
            schema,
            frame_for_leak_check=frame,
        )
    except TypeError:
        analysis_plan = planner(ask, schema)
    except Exception as exc:  # noqa: BLE001
        gov.audit(
            "analysis_plan_failed",
            {"error": str(exc)},
            actor_slack_id=state.get("requester_slack_id"),
        )
        # Fall back to file rather than failing the whole request silently.
        csv_text = rows_to_csv(guarded)
        text = (
            f"I couldn't complete the analysis ({exc}). "
            f"Here's the data as a file instead.\n"
            f"Ran: {plan.get('plain_language_plan')}\n"
            f"Rows: {len(guarded)}{hidden_note}\n"
            f"Data as of: {read_at}"
        )
        ref = destination.deliver(
            {
                "channel_id": state.get("channel_id"),
                "thread_ts": state.get("thread_ts"),
                "text": text,
                "csv": csv_text,
                "filename": "data_request.csv",
                "rows": guarded,
                "analysis_mock": False,
            }
        )
        return {
            **state,
            "phase": "delivered",
            "result_ref": ref,
            "delivery_message": text,
            "guarded_rows": guarded,
            "analysis_mock": False,
            "error": "analysis_plan_failed",
        }

    result = run_analysis(
        frame,
        analysis_plan,
        max_table_rows=settings.analysis_summary_max_rows,
    )
    text = (
        f"{result.answer}\n\n"
        f"{result.table_markdown}\n\n"
        f"Ran: {plan.get('plain_language_plan')}\n"
        f"Rows analysed: {len(guarded)}{hidden_note}\n"
        f"Data as of: {read_at}"
    )
    ref = destination.deliver(
        {
            "channel_id": state.get("channel_id"),
            "thread_ts": state.get("thread_ts"),
            "text": text,
            "chart_png": result.chart_png,
            "chart_filename": "analysis_chart.png",
            "rows": result.table_rows,
            "analysis_mock": False,
            "wants_analysis": True,
        }
    )
    gov.audit(
        "delivered",
        {
            "result_ref": ref,
            "row_count": len(guarded),
            "analysis_mock": False,
            "wants_analysis": True,
            "analysis_stats": result.stats,
            "chart": bool(result.chart_png),
        },
        actor_slack_id=state.get("requester_slack_id"),
    )
    return {
        **state,
        "phase": "delivered",
        "result_ref": ref,
        "delivery_message": text,
        "guarded_rows": guarded,
        "analysis_mock": False,
        "analysis_answer": result.answer,
        "analysis_table_markdown": result.table_markdown,
    }
