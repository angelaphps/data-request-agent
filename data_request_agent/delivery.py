"""Delivery: personal-data guard · analysis or file · destination.

Stage 3 analysis reply: threaded text answer + inline markdown summary
table (≤ settings.analysis_summary_max_rows) + chart PNG. Full row
dumps remain file delivery when analysis is not requested.

Personal columns: CSV may keep them when the approval covers them (e.g. a
contact list). Analysis strips **row-level** personal extracts (many distinct
values). Low-cardinality personal *dimensions* (e.g. 3 device types) are kept
so charts still have a group axis.
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
from data_request_agent.followups import build_thread_context_payload
from data_request_agent.governance import Governance
from data_request_agent.sql_inspect import load_catalog_objects
from data_request_agent.state import AgentState

# Kept for older tests that still mention the Stage 1–2 mock notice.
ANALYSIS_MOCK_NOTICE = (
    "analysis replies aren't ready yet — here's the data as a file instead"
)


def _drop_personal_columns(
    rows: list[dict[str, Any]],
    personal_cols: set[str],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Return rows with catalog personal columns removed, plus names dropped."""
    hidden: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        new_row: dict[str, Any] = {}
        for key, value in row.items():
            if key.lower() in personal_cols:
                hidden.add(key)
                continue
            new_row[key] = value
        out.append(new_row)
    return out, hidden


def _personal_is_row_level_extract(
    rows: list[dict[str, Any]],
    personal_cols: set[str],
    *,
    max_distinct: int = 10,
) -> bool:
    """True when personal cols look like a contact-list extract (many values).

    Low-cardinality dimensions (e.g. 3 device types) are kept for charts.
    """
    if not rows or not personal_cols:
        return False
    frame = pd.DataFrame(rows)
    for col in frame.columns:
        if col.lower() not in personal_cols:
            continue
        if int(frame[col].nunique(dropna=False)) > max_distinct:
            return True
    return False


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

    # File path: keep personal columns only when the approval covers them.
    file_guarded: list[dict[str, Any]] = []
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
        file_guarded.append(new_row)

    wants_analysis = bool(plan.get("wants_analysis"))
    read_at = datetime.now(timezone.utc).isoformat()
    settings = settings or Settings()
    hidden_note = (
        f" (hidden personal columns: {sorted(hidden)})" if hidden else ""
    )

    if wants_analysis:
        # Strip row-level personal extracts (many distinct values). Keep
        # low-cardinality personal dimensions (e.g. 3 device types) for charts.
        if _personal_is_row_level_extract(file_guarded, personal_cols):
            analysis_rows, analysis_hidden = _drop_personal_columns(
                file_guarded, personal_cols
            )
            analysis_hidden |= hidden
        else:
            analysis_rows = file_guarded
            analysis_hidden = set(hidden)
        analysis_note = (
            f" (hidden personal columns: {sorted(analysis_hidden)})"
            if analysis_hidden
            else ""
        )
        return _deliver_analysis(
            state,
            gov=gov,
            destination=destination,
            settings=settings,
            plan=plan,
            analysis_rows=analysis_rows,
            file_fallback_rows=file_guarded,
            hidden_note=analysis_note,
            file_hidden_note=hidden_note,
            hidden_personal=sorted(analysis_hidden),
            read_at=read_at,
            plan_analysis=plan_analysis,
        )

    csv_text = rows_to_csv(file_guarded)
    text = (
        f"Ran: {plan.get('plain_language_plan')}\n"
        f"Rows: {len(file_guarded)}"
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
            "rows": file_guarded,
            "analysis_mock": False,
        }
    )
    gov.audit(
        "delivered",
        {
            "result_ref": ref,
            "row_count": len(file_guarded),
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
        "guarded_rows": file_guarded,
        "analysis_mock": False,
    }


def _deliver_analysis(
    state: AgentState,
    *,
    gov: Governance,
    destination: Destination,
    settings: Settings,
    plan: dict[str, Any],
    analysis_rows: list[dict[str, Any]],
    file_fallback_rows: list[dict[str, Any]],
    hidden_note: str,
    file_hidden_note: str,
    hidden_personal: list[str],
    read_at: str,
    plan_analysis: Callable[..., AnalysisPlan] | None,
) -> AgentState:
    frame = pd.DataFrame(analysis_rows)
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
        import logging

        logging.getLogger(__name__).exception(
            "analysis planner failed; falling back to heuristic plan"
        )
        gov.audit(
            "analysis_plan_failed",
            {
                "error": str(exc),
                "error_type": type(exc).__name__,
                "fallback": "heuristic_analysis_plan",
            },
            actor_slack_id=state.get("requester_slack_id"),
        )
        # Prefer a local heuristic plan (still analysis) over dumping CSV.
        try:
            from data_request_agent.analysis import heuristic_analysis_plan

            analysis_plan = heuristic_analysis_plan(ask, schema)
        except Exception as heur_exc:  # noqa: BLE001
            gov.audit(
                "analysis_plan_failed",
                {
                    "error": str(heur_exc),
                    "error_type": type(heur_exc).__name__,
                    "fallback": "csv",
                    "prior_error": str(exc),
                },
                actor_slack_id=state.get("requester_slack_id"),
            )
            csv_text = rows_to_csv(file_fallback_rows)
            text = (
                f"I couldn't complete the analysis ({exc}). "
                f"Here's the data as a file instead.\n"
                f"Ran: {plan.get('plain_language_plan')}\n"
                f"Rows: {len(file_fallback_rows)}{file_hidden_note}\n"
                f"Data as of: {read_at}"
            )
            ref = destination.deliver(
                {
                    "channel_id": state.get("channel_id"),
                    "thread_ts": state.get("thread_ts"),
                    "text": text,
                    "csv": csv_text,
                    "filename": "data_request.csv",
                    "rows": file_fallback_rows,
                    "analysis_mock": False,
                }
            )
            return {
                **state,
                "phase": "delivered",
                "result_ref": ref,
                "delivery_message": text,
                "guarded_rows": file_fallback_rows,
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
        f"Rows analysed: {len(analysis_rows)}{hidden_note}\n"
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
            "row_count": len(analysis_rows),
            "analysis_mock": False,
            "wants_analysis": True,
            "analysis_stats": result.stats,
            "chart": bool(result.chart_png),
            "hidden_personal": hidden_personal,
        },
        actor_slack_id=state.get("requester_slack_id"),
    )
    channel_id = state.get("channel_id") or ""
    thread_ts = state.get("thread_ts") or ""
    requester = state.get("requester_slack_id") or ""
    if channel_id and thread_ts and requester:
        try:
            ctx = build_thread_context_payload(
                original_ask=ask,
                answer=result.answer,
                table_markdown=result.table_markdown,
                plain_language_plan=str(plan.get("plain_language_plan") or ""),
                stats=result.stats,
                schema_slice=schema,
                data_as_of=read_at,
            )
            gov.save_thread_context(
                channel_id=channel_id,
                thread_ts=thread_ts,
                requester_slack_id=requester,
                context=ctx,
            )
            gov.audit(
                "thread_context_saved",
                {
                    "thread_key": gov.thread_key(channel_id, thread_ts),
                    "column_names": ctx.get("column_names"),
                },
                actor_slack_id=requester,
            )
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).exception(
                "failed to save thread_context for follow-ups"
            )
            gov.audit(
                "thread_context_save_failed",
                {
                    "error": str(exc),
                    "channel_id": channel_id,
                    "thread_ts": thread_ts,
                },
                actor_slack_id=requester,
            )
    return {
        **state,
        "phase": "delivered",
        "result_ref": ref,
        "delivery_message": text,
        "guarded_rows": analysis_rows,
        "hidden_personal": hidden_personal,
        "analysis_mock": False,
        "analysis_answer": result.answer,
        "analysis_table_markdown": result.table_markdown,
    }
