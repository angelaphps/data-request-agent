"""Same-thread follow-ups after an analysis delivery (why / explain / what about).

Uses stored aggregate stats + summary table only — not a fresh full-table dump
into the model. Clear new data extracts still start a full graph run.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_FOLLOWUP_RE = re.compile(
    r"\b("
    r"why|how come|explain|weird|strange|surprising|interesting|"
    r"higher|lower|bigger|smaller|difference|compared|versus|\bvs\b|"
    r"what about|how about|and for|break( that)? down|dig into|"
    r"that chart|this chart|that graph|this graph|that result|"
    r"makes sense|anomaly|outlier"
    r")\b",
    re.IGNORECASE,
)

_FRESH_ASK_RE = re.compile(
    r"\b("
    r"top\s+\d+|list of|give me|export|csv|download|"
    r"how many|how much|total revenue|active subscriptions|"
    r"users from|revenue by|session times"
    r")\b",
    re.IGNORECASE,
)

FOLLOWUP_SYSTEM = """
You answer a short follow-up about a prior analytics result in Slack.

You receive the original question, the prior answer, aggregate stats, and a
small summary table — not a raw warehouse dump. Stay honest: if the numbers
do not support a causal claim, say what the data shows and what would need
another query. Do not invent columns or tables.
""".strip()


class FollowupAnswer(BaseModel):
    reply: str = Field(description="Short Slack reply (a few sentences).")
    needs_new_request: bool = False
    suggested_ask: str | None = None


def looks_like_followup(text: str, *, has_context: bool) -> bool:
    if not has_context:
        return False
    raw = (text or "").strip()
    if not raw:
        return False
    if _FRESH_ASK_RE.search(raw) and not _FOLLOWUP_RE.search(raw):
        return False
    if _FOLLOWUP_RE.search(raw):
        return True
    # Short reply in an analysis thread → treat as follow-up
    return len(raw) <= 120


def build_followup_prompt(*, question: str, context: dict[str, Any]) -> str:
    import json

    stats = context.get("stats") or {}
    table = context.get("table_markdown") or ""
    schema = context.get("schema_slice") or []
    schema_lines = [
        f"- {c.get('name')}: {c.get('description')}" for c in schema[:30]
    ]
    return (
        f"Original ask:\n{context.get('original_ask') or ''}\n\n"
        f"Prior answer:\n{context.get('answer') or ''}\n\n"
        f"Plan that was run:\n{context.get('plain_language_plan') or ''}\n\n"
        f"Aggregate stats (JSON):\n{json.dumps(stats, default=str)[:4000]}\n\n"
        f"Summary table:\n{table[:3000]}\n\n"
        f"Column descriptions:\n" + "\n".join(schema_lines) + "\n\n"
        f"Follow-up from user:\n{question}\n\n"
        "Answer the follow-up. Set needs_new_request=true only if they need "
        "data outside this summary (then suggest a short new ask)."
    )


def answer_followup(
    question: str,
    context: dict[str, Any],
    *,
    settings,
) -> FollowupAnswer:
    if not settings.openai_api_key:
        return _heuristic_followup(question, context)
    os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    try:
        from pydantic_ai import Agent

        agent = Agent(
            "openai:gpt-4o-mini",
            output_type=FollowupAnswer,
            system_prompt=FOLLOWUP_SYSTEM,
        )
        result = agent.run_sync(
            build_followup_prompt(question=question, context=context)
        )
        return result.output
    except Exception:  # noqa: BLE001
        logger.exception("follow-up LLM failed")
        return _heuristic_followup(question, context)


def _heuristic_followup(question: str, context: dict[str, Any]) -> FollowupAnswer:
    stats = context.get("stats") or {}
    top = stats.get("top") or []
    if top:
        return FollowupAnswer(
            reply=(
                "From the last summary: leading groups were "
                f"{top[:3]}. I can't prove causation from this cut alone — "
                "ask for another breakdown (e.g. by country or activity) if you "
                f"want to dig into: {question!r}."
            ),
            needs_new_request=False,
        )
    return FollowupAnswer(
        reply=(
            "I still have the last analysis in this thread, but not enough "
            f"detail to answer {question!r}. Try asking for a specific "
            "breakdown as a new request."
        ),
        needs_new_request=True,
        suggested_ask=question,
    )


def try_answer_followup(
    text: str,
    *,
    gov: Any,
    settings: Any,
    channel_id: str,
    thread_ts: str,
    requester_slack_id: str,
) -> FollowupAnswer | None:
    """If this DM looks like a follow-up and context exists, answer it.

    Returns ``None`` when the message should start (or continue) the full
    request graph instead.
    """
    key = gov.thread_key(channel_id, thread_ts)
    row = gov.get_thread_context(key)
    if not row:
        return None
    if row.get("requester_slack_id") and row["requester_slack_id"] != requester_slack_id:
        return None
    context = row.get("context") or {}
    if not looks_like_followup(text, has_context=True):
        return None
    return answer_followup(text, context, settings=settings)


def build_thread_context_payload(
    *,
    original_ask: str,
    answer: str,
    table_markdown: str,
    plain_language_plan: str,
    stats: dict[str, Any],
    schema_slice: list[dict[str, Any]],
    data_as_of: str,
) -> dict[str, Any]:
    """Context for follow-ups — aggregates/summary only, no personal schema cols.

    High-cardinality personal extracts are already stripped from the analysis
    frame before this runs. Catalog ``personal`` columns are omitted from the
    stored schema list so follow-up LMs do not treat them as queryable fields.
    """
    safe_schema = [
        {
            "name": c.get("name"),
            "description": c.get("description"),
            "dtype": c.get("dtype"),
            "dataset": c.get("dataset"),
        }
        for c in schema_slice
        if (c.get("sensitivity") or "none") != "personal"
    ]
    return {
        "original_ask": original_ask,
        "answer": answer,
        "table_markdown": table_markdown,
        "plain_language_plan": plain_language_plan,
        "stats": _json_safe(stats),
        "schema_slice": safe_schema,
        "column_names": [c.get("name") for c in safe_schema if c.get("name")],
        "data_as_of": data_as_of,
    }


def _json_safe(value: Any) -> Any:
    """Make analysis stats JSON-serializable (numpy / pandas scalars)."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:  # noqa: BLE001
            return str(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
