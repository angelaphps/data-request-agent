"""Intake: identity · parse · clarification loop (≤2 questions)."""

from __future__ import annotations

from langgraph.types import interrupt

from data_request_agent.catalog import get_semantic_catalog
from data_request_agent.governance import Governance
from data_request_agent.proposers import AskParser, catalog_keyword_parser
from data_request_agent.state import AgentState

PUBLIC_REDIRECT = (
    "I handle data requests privately — message me directly."
)


def is_dm_channel(channel_id: str) -> bool:
    """Slack DM channels start with ``D``; public/private channels do not."""
    return (channel_id or "").startswith("D")


def normalize_slack_text(text: str) -> str:
    """Strip common Slack mrkdwn wrappers (italic/bold) from pasted asks."""
    raw = (text or "").strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "_*":
        inner = raw[1:-1].strip()
        if inner:
            return inner
    return raw


def identify(state: AgentState, *, gov: Governance) -> AgentState:
    user_id = state.get("requester_slack_id") or ""
    is_admin = gov.is_admin(user_id)
    raw_text = normalize_slack_text(state.get("raw_text") or "")
    if not state.get("clarify_count"):
        gov.audit(
            "request_received",
            {
                "raw_text": raw_text,
                "channel_id": state.get("channel_id"),
                "thread_ts": state.get("thread_ts"),
                "is_admin": is_admin,
            },
            actor_slack_id=user_id,
        )
    return {
        **state,
        "raw_text": raw_text,
        "is_admin": is_admin,
        "phase": "identified",
        "clarify_count": int(state.get("clarify_count") or 0),
    }


def parse(
    state: AgentState,
    *,
    gov: Governance,
    parse_ask: AskParser = catalog_keyword_parser,
    max_clarify: int = 2,
) -> AgentState:
    metrics = get_semantic_catalog().metric_names()
    text = state.get("raw_text") or ""
    parsed = parse_ask(text, metric_names=metrics)
    request = {
        "requester_slack_id": state.get("requester_slack_id"),
        "channel_id": state.get("channel_id"),
        "thread_ts": state.get("thread_ts"),
        "raw_text": text,
        "clarified_intent": parsed.intent,
        "identity_ok": True,
        "is_admin": state.get("is_admin", False),
        "parsed": parsed.model_dump(),
    }
    clarify_count = int(state.get("clarify_count") or 0)

    if parsed.status == "ok":
        return {
            **state,
            "request": request,
            "phase": "parsed",
            "clarify_question": None,
        }

    # Out of scope / unsafe: stop immediately — do not spend clarify turns.
    if parsed.status == "out_of_scope":
        message = parsed.decline_message or (
            "That request is outside what I can help with. "
            f"I can answer: {', '.join(parsed.valid_options or metrics)}."
        )
        gov.audit(
            "request_out_of_scope",
            {"message": message, "raw_text": text},
            actor_slack_id=state.get("requester_slack_id"),
        )
        return {
            **state,
            "request": request,
            "phase": "declined",
            "delivery_message": message,
            "error": "out_of_scope",
        }

    if parsed.status == "ambiguous" and clarify_count < max_clarify:
        options = parsed.valid_options or metrics
        question = (
            "Which of these did you mean? Reply with the metric name.\n"
            + "\n".join(f"• `{name}`" for name in options)
        )
        gov.audit(
            "clarify_asked",
            {"count": clarify_count + 1, "options": options},
            actor_slack_id=state.get("requester_slack_id"),
        )
        return {
            **state,
            "request": request,
            "phase": "needs_clarify",
            "clarify_question": question,
            "clarify_options": options,
        }

    # Out of scope, or still ambiguous after max clarifies
    message = parsed.decline_message or (
        "I still can't match that to the catalog after clarifying. "
        f"I can answer: {', '.join(parsed.valid_options or metrics)}."
    )
    gov.audit(
        "request_declined",
        {"reason": parsed.status, "message": message, "clarify_count": clarify_count},
        actor_slack_id=state.get("requester_slack_id"),
    )
    return {
        **state,
        "request": request,
        "phase": "declined",
        "delivery_message": message,
        "error": parsed.status,
    }


def clarify_once(state: AgentState, *, gov: Governance) -> AgentState:
    """One interrupt per visit — graph loops back to parse (no while-True)."""
    question = state.get("clarify_question") or "Please clarify your request."
    answer = interrupt(
        {
            "kind": "clarify",
            "prompt": question,
            "options": state.get("clarify_options") or [],
            "clarify_count": int(state.get("clarify_count") or 0) + 1,
        }
    )
    if isinstance(answer, dict):
        text = str(answer.get("text") or answer.get("action") or answer.get("value") or "")
    else:
        text = str(answer)

    count = int(state.get("clarify_count") or 0) + 1
    gov.audit(
        "clarify_answered",
        {"count": count, "answer": text},
        actor_slack_id=state.get("requester_slack_id"),
    )
    # Append clarification into the ask text for the next parse pass
    prior = state.get("raw_text") or ""
    combined = f"{prior}\n{text}".strip()
    return {
        **state,
        "raw_text": combined,
        "clarify_count": count,
        "phase": "clarified",
        "clarify_question": None,
    }
