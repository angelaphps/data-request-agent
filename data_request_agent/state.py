"""Shared graph state: RequestSpec · PlanBundle · ApprovedPlan · skeleton fields."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel, Field


class RequestSpec(BaseModel):
    """Parsed / clarified ask from intake."""

    requester_slack_id: str
    channel_id: str
    thread_ts: str
    raw_text: str
    clarified_intent: str | None = None
    identity_ok: bool = False
    is_admin: bool = False


class PlanBundle(BaseModel):
    """Planner output before admin approval."""

    definitions: list[str] = Field(default_factory=list)
    draft_sql: str | None = None
    plain_language_plan: str | None = None
    inspection_notes: list[str] = Field(default_factory=list)
    trial_columns: list[str] = Field(default_factory=list)
    trial_row_estimate: int | None = None
    touches_personal_data: bool = False
    approved_columns: list[str] = Field(default_factory=list)


class ApprovedPlan(BaseModel):
    """Plan after governance approval is recorded."""

    plan: PlanBundle
    approval_id: str
    approved_by: list[str] = Field(default_factory=list)
    status: Literal["pending", "approved", "rejected", "expired"] = "pending"


class AgentState(TypedDict):
    """LangGraph state (total=False fields via NotRequired)."""

    phase: NotRequired[str]
    human_decision: NotRequired[str | None]
    error: NotRequired[str | None]

    requester_slack_id: NotRequired[str]
    channel_id: NotRequired[str]
    thread_ts: NotRequired[str]
    raw_text: NotRequired[str]
    is_admin: NotRequired[bool]

    request: NotRequired[dict[str, Any] | None]
    plan: NotRequired[dict[str, Any] | None]
    approved: NotRequired[dict[str, Any] | None]
    result_ref: NotRequired[str | None]
    delivery_message: NotRequired[str | None]
    result_rows: NotRequired[list[dict[str, Any]] | None]
    result_row_count: NotRequired[int | None]
    guarded_rows: NotRequired[list[dict[str, Any]] | None]
    analysis_mock: NotRequired[bool]
    analysis_answer: NotRequired[str | None]
    analysis_table_markdown: NotRequired[str | None]
    clarify_count: NotRequired[int]
    clarify_question: NotRequired[str | None]
    clarify_options: NotRequired[list[str] | None]
    results_retry_count: NotRequired[int]
    force_results_mismatch: NotRequired[bool]
