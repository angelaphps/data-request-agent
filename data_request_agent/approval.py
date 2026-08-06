"""Approval: preview Submit/Cancel · admin skip · requester card · authority at click."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from langgraph.types import interrupt

from data_request_agent.config import Settings
from data_request_agent.governance import Governance
from data_request_agent.state import AgentState

# Resume action for "Approve without personal data"
APPROVE_WITHOUT_PERSONAL = "approve_without_personal"


def personal_column_names(gov: Governance | None = None) -> set[str]:
    """Lowercased column names marked sensitivity=personal in YAML catalog."""
    from data_request_agent.catalog import get_semantic_catalog

    return get_semantic_catalog().personal_column_names()


def plan_without_personal(plan: dict[str, Any], *, gov: Governance) -> dict[str, Any]:
    """Copy plan with personal columns removed from the release envelope.

    SQL is unchanged; the delivery guard drops personal fields not in
    approved_columns (strip, not scramble).
    """
    personal = personal_column_names(gov)
    out = dict(plan)
    cols = list(out.get("approved_columns") or out.get("trial_columns") or [])
    kept = [c for c in cols if c.lower() not in personal]
    redacted = [c for c in cols if c.lower() in personal]
    out["approved_columns"] = kept
    out["personal_columns_redacted"] = redacted
    # Envelope no longer authorizes personal release.
    out["touches_personal_data"] = False
    return out


def wait_for_run_cancel(state: AgentState) -> AgentState:
    """Preview interrupt — requester confirms Submit or Cancel."""
    plan = state.get("plan") or {}
    decision = interrupt(
        {
            "kind": "plan_preview",
            "plain_language_plan": plan.get("plain_language_plan"),
            "definitions": plan.get("definitions"),
            "trial_columns": plan.get("trial_columns"),
            "trial_row_estimate": plan.get("trial_row_estimate"),
            "touches_personal_data": plan.get("touches_personal_data"),
            "draft_sql_omitted": True,
        }
    )
    action = _action(decision)
    if action == "cancel":
        return {
            **state,
            "phase": "cancelled",
            "human_decision": "cancel",
            "delivery_message": "Cancelled — nothing was run.",
        }
    return {**state, "human_decision": "run", "phase": "run_confirmed"}


def ensure_permission(
    state: AgentState,
    *,
    gov: Governance,
    settings: Settings,
) -> AgentState:
    """Admins: record auto-approved permission. Requesters: wait for admin."""
    plan = state.get("plan") or {}
    request = state.get("request") or {}
    approval_id = str(uuid.uuid4())
    request_id = (
        f"{state.get('channel_id')}:{state.get('thread_ts')}:{approval_id[:8]}"
    )
    expires = datetime.now(timezone.utc) + timedelta(
        hours=settings.approval_expiry_hours
    )

    if state.get("is_admin"):
        gov.create_approval(
            approval_id=approval_id,
            request_id=request_id,
            requester_slack_id=state.get("requester_slack_id") or "",
            channel_id=state.get("channel_id") or "",
            thread_ts=state.get("thread_ts") or "",
            plan=plan,
            touches_personal_data=bool(plan.get("touches_personal_data")),
            expires_at=expires,
            status="approved",
            decided_by=[state.get("requester_slack_id") or ""],
        )
        gov.audit(
            "approval_auto_admin",
            {"approval_id": approval_id},
            actor_slack_id=state.get("requester_slack_id"),
        )
        approved = {
            "plan": plan,
            "approval_id": approval_id,
            "approved_by": [state.get("requester_slack_id")],
            "status": "approved",
        }
        return {**state, "approved": approved, "phase": "approved"}

    gov.create_approval(
        approval_id=approval_id,
        request_id=request_id,
        requester_slack_id=state.get("requester_slack_id") or "",
        channel_id=state.get("channel_id") or "",
        thread_ts=state.get("thread_ts") or "",
        plan=plan,
        touches_personal_data=bool(plan.get("touches_personal_data")),
        expires_at=expires,
        status="pending",
    )
    gov.audit(
        "approval_requested",
        {
            "approval_id": approval_id,
            "touches_personal_data": plan.get("touches_personal_data"),
            # Card metadata only — never data rows
            "columns": plan.get("trial_columns"),
            "estimate": plan.get("trial_row_estimate"),
            "verbatim_ask": request.get("raw_text"),
        },
        actor_slack_id=state.get("requester_slack_id"),
    )

    approval_payload = {
        "kind": "admin_approval",
        "approval_id": approval_id,
        "verbatim_ask": request.get("raw_text"),
        "plain_language_plan": plan.get("plain_language_plan"),
        "trial_columns": plan.get("trial_columns"),
        "trial_row_estimate": plan.get("trial_row_estimate"),
        "touches_personal_data": plan.get("touches_personal_data"),
        "definitions": plan.get("definitions"),
    }

    approve_actions = {"approve", APPROVE_WITHOUT_PERSONAL}

    # Keep waiting until an admin Approve/Reject. Non-admin clicks are audited
    # and ignored — same interrupt stays open (do not end the request).
    while True:
        decision = interrupt(approval_payload)
        actor = _actor(decision)
        action = _action(decision)

        if action in approve_actions and not gov.is_admin(actor):
            gov.audit(
                "approval_refused_non_admin",
                {"approval_id": approval_id, "actor": actor, "action": action},
                actor_slack_id=actor,
            )
            approval_payload = {
                **approval_payload,
                "notice": (
                    "Only administrators can approve data requests. "
                    "That click was recorded and ignored — still waiting."
                ),
            }
            continue

        if action in approve_actions:
            release_plan = plan
            redact_personal = action == APPROVE_WITHOUT_PERSONAL
            if redact_personal:
                release_plan = plan_without_personal(plan, gov=gov)
            gov.record_approval_decision(
                approval_id,
                decided_by=actor,
                status="approved",
                plan=release_plan,
                touches_personal_data=bool(
                    release_plan.get("touches_personal_data")
                ),
            )
            gov.audit(
                "approval_approved",
                {
                    "approval_id": approval_id,
                    "without_personal": redact_personal,
                    "personal_columns_redacted": release_plan.get(
                        "personal_columns_redacted"
                    )
                    or [],
                },
                actor_slack_id=actor,
            )
            return {
                **state,
                "phase": "approved",
                "approved": {
                    "plan": release_plan,
                    "approval_id": approval_id,
                    "approved_by": [actor],
                    "status": "approved",
                    "without_personal": redact_personal,
                },
            }

        if action != "reject":
            # Unknown action — keep waiting.
            approval_payload = {
                **approval_payload,
                "notice": f"Unknown action {action!r}; still waiting for Approve/Reject.",
            }
            continue

        gov.record_approval_decision(
            approval_id, decided_by=actor or "unknown", status="rejected"
        )
        gov.audit(
            "approval_rejected",
            {"approval_id": approval_id},
            actor_slack_id=actor,
        )
        return {
            **state,
            "phase": "rejected",
            "delivery_message": "Your request was declined by an administrator.",
            "approved": {
                "plan": plan,
                "approval_id": approval_id,
                "approved_by": [actor] if actor else [],
                "status": "rejected",
            },
        }


def _action(decision: Any) -> str:
    if isinstance(decision, dict):
        return str(decision.get("action") or decision.get("value") or "").lower()
    return str(decision).lower()


def _actor(decision: Any) -> str:
    if isinstance(decision, dict):
        return str(decision.get("actor_slack_id") or "")
    return ""
