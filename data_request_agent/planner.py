"""Planner: definitions · LLM/template draft · sqlglot inspect · trial."""

from __future__ import annotations

import inspect
from typing import Any

from data_request_agent.governance import Governance
from data_request_agent.proposers import ParsedAsk, Proposers
from data_request_agent.sql_inspect import inspect_sql, load_catalog_objects
from data_request_agent.state import AgentState
from data_request_agent.stores import TabularStore


def _call_drafter(drafter: Any, parsed: ParsedAsk, *, feedback: str | None) -> Any:
    """Support drafters with or without a feedback kwarg."""
    try:
        sig = inspect.signature(drafter)
        if "feedback" in sig.parameters:
            return drafter(parsed, feedback=feedback)
    except (TypeError, ValueError):
        pass
    return drafter(parsed)


def plan_query(
    state: AgentState,
    *,
    gov: Governance,
    store: TabularStore,
    proposers: Proposers,
    max_retries: int = 3,
) -> AgentState:
    request = state.get("request") or {}
    parsed = ParsedAsk.model_validate(request.get("parsed") or {})
    if not parsed.intent:
        parsed.intent = request.get("raw_text") or state.get("raw_text") or ""
    catalog = load_catalog_objects(gov)
    last_notes: list[str] = []
    feedback: str | None = None

    for attempt in range(1, max_retries + 1):
        try:
            draft = _call_drafter(proposers.draft_sql, parsed, feedback=feedback)
        except Exception as exc:  # noqa: BLE001 — surface to user honestly
            return {
                **state,
                "phase": "plan_failed",
                "error": "draft_failed",
                "delivery_message": f"I couldn't draft a query: {exc}",
            }

        result = inspect_sql(draft.sql, catalog=catalog)
        last_notes = result.notes
        if not result.ok:
            feedback = (
                f"SQL failed inspection: {'; '.join(result.notes)}\n"
                f"Rejected SQL was:\n{draft.sql}"
            )
            gov.audit(
                "plan_inspect_failed",
                {"attempt": attempt, "notes": result.notes, "sql": draft.sql},
                actor_slack_id=state.get("requester_slack_id"),
            )
            continue

        try:
            headings = store.headings(draft.sql)
            estimate = store.estimate_rows(draft.sql)
        except Exception as exc:  # noqa: BLE001
            last_notes = [f"trial failed: {exc}"]
            feedback = (
                f"SQL failed trial run: {exc}\nRejected SQL was:\n{draft.sql}"
            )
            gov.audit(
                "plan_trial_failed",
                {"attempt": attempt, "error": str(exc), "sql": draft.sql},
                actor_slack_id=state.get("requester_slack_id"),
            )
            continue

        columns = headings or draft.columns or result.columns
        plan = {
            "definitions": draft.definitions,
            "draft_sql": draft.sql,
            "plain_language_plan": draft.plain_language,
            "inspection_notes": result.notes,
            "trial_columns": columns,
            "trial_row_estimate": estimate,
            "touches_personal_data": result.touches_personal,
            "approved_columns": columns,
            "wants_analysis": parsed.wants_analysis,
        }
        gov.audit(
            "plan_ready",
            {
                "attempt": attempt,
                "sql": draft.sql,
                "columns": columns,
                "estimate": estimate,
                "touches_personal_data": result.touches_personal,
            },
            actor_slack_id=state.get("requester_slack_id"),
        )
        return {**state, "plan": plan, "phase": "planned"}

    return {
        **state,
        "phase": "plan_failed",
        "error": "inspect_or_trial_failed",
        "delivery_message": (
            "I couldn't produce a safe query after several tries. "
            f"Last notes: {'; '.join(last_notes)}"
        ),
    }
