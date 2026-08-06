"""Execution: permission re-check · run via business TabularStore."""

from __future__ import annotations

from data_request_agent.config import Settings
from data_request_agent.governance import Governance
from data_request_agent.sql_inspect import inspect_sql, load_catalog_objects
from data_request_agent.state import AgentState
from data_request_agent.stores import TabularStore


def recheck_and_run(
    state: AgentState,
    *,
    gov: Governance,
    store: TabularStore,
    settings: Settings,
) -> AgentState:
    approved = state.get("approved") or {}
    plan = approved.get("plan") or state.get("plan") or {}
    approval_id = approved.get("approval_id")
    sql = plan.get("draft_sql")
    if not approval_id or not sql:
        return {
            **state,
            "phase": "exec_failed",
            "error": "missing_approval_or_sql",
            "delivery_message": "I can't run this — approval or SQL is missing.",
        }

    record = gov.get_approval(approval_id)
    # Sweep expiries before re-check so long waits cannot run stale grants.
    gov.expire_stale_approvals()
    record = gov.get_approval(approval_id)

    if record is None or record["status"] != "approved":
        status = (record or {}).get("status")
        gov.audit(
            "permission_recheck_failed",
            {"approval_id": approval_id, "status": status},
            actor_slack_id=state.get("requester_slack_id"),
        )
        if status == "expired":
            return {
                **state,
                "phase": "recheck_failed",
                "error": "approval_expired",
                "delivery_message": (
                    f"This approval has expired "
                    f"({settings.approval_expiry_hours} hours). "
                    "Please start a new request if you still need the data."
                ),
            }
        return {
            **state,
            "phase": "recheck_failed",
            "error": "permission_recheck_failed",
            "delivery_message": (
                "I won't run this query — the recorded permission is missing "
                "or no longer approved."
            ),
        }

    recorded_plan = record.get("plan") or {}
    if isinstance(recorded_plan, str):
        import json

        recorded_plan = json.loads(recorded_plan)
    recorded_sql = (recorded_plan.get("draft_sql") or "").strip()
    if recorded_sql and sql.strip() != recorded_sql:
        gov.audit(
            "permission_recheck_sql_mismatch",
            {"approval_id": approval_id},
            actor_slack_id=state.get("requester_slack_id"),
        )
        return {
            **state,
            "phase": "recheck_failed",
            "error": "sql_mismatch",
            "delivery_message": (
                "I won't run this query — it no longer matches the recorded permission."
            ),
        }

    # Re-inspect SQL against current catalog (retries / stale waits cannot widen).
    catalog = load_catalog_objects()
    inspected = inspect_sql(sql, catalog=catalog)
    if not inspected.ok:
        gov.audit(
            "permission_recheck_inspect_failed",
            {"approval_id": approval_id, "notes": inspected.notes},
            actor_slack_id=state.get("requester_slack_id"),
        )
        return {
            **state,
            "phase": "recheck_failed",
            "error": "recheck_inspect_failed",
            "delivery_message": (
                "I won't run this query — it no longer passes inspection "
                f"({'; '.join(inspected.notes)})."
            ),
        }

    try:
        rows = store.execute(
            sql,
            row_cap=settings.row_cap,
            timeout_ms=settings.statement_timeout_ms,
        )
    except Exception as exc:  # noqa: BLE001
        gov.audit(
            "execute_failed",
            {"approval_id": approval_id, "error": str(exc)},
            actor_slack_id=state.get("requester_slack_id"),
        )
        return {
            **state,
            "phase": "exec_failed",
            "error": "execute_failed",
            "delivery_message": f"The query failed to run: {exc}",
        }

    gov.audit(
        "executed",
        {"approval_id": approval_id, "row_count": len(rows)},
        actor_slack_id=state.get("requester_slack_id"),
    )
    return {
        **state,
        "phase": "executed",
        "result_rows": rows,
        "result_row_count": len(rows),
    }
