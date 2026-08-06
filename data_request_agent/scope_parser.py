"""Scope / boundary assessment for intake.

Cheap deterministic rejects for clear jokes, malice, and irrelevance save LLM
tokens. Borderline asks go through a pydantic-ai parser grounded only in
catalog descriptions (never data rows). Clear catalog keyword hits skip the LLM.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Callable

from data_request_agent.proposers import ParsedAsk, catalog_keyword_parser

logger = logging.getLogger(__name__)

# Obvious off-domain / abuse — reject without calling an LLM.
_OUT_OF_SCOPE_PATTERNS = [
    r"\bweather\b",
    r"\bforecast\b",
    r"\bjoke\b",
    r"\briddle\b",
    r"\bpoem\b",
    r"\blyrics\b",
    r"\bwrite (me )?a story\b",
    r"\bwho (won|will win) the (game|match|election)\b",
    r"\bignore (all )?(previous|prior|above) (instructions|rules)\b",
    r"\bjailbreak\b",
    r"\bDAN\b",
    r"\bdrop\s+table\b",
    r"\btruncate\b",
    r"\bdelete\s+from\b",
    r"\binsert\s+into\b",
    r"\bgrant\b",
    r"\bpassword\b",
    r"\bapi[ _-]?key\b",
    r"\bssn\b",
    r"\bsocial security\b",
    r"\bdump (all|the) (data|database|users|tables)\b",
    r"\bexfiltrat",
    r"\bhack\b",
    r"\bbypass (approval|auth|permission)\b",
]

_OUT_OF_SCOPE_RE = re.compile("|".join(_OUT_OF_SCOPE_PATTERNS), re.IGNORECASE)


SCOPE_SYSTEM = """
You classify data requests for an internal analytics Slack bot.

You may ONLY allow requests that can be answered from the provided catalog of
datasets and metrics (tabular product analytics). Descriptions only — you never
see data rows.

Countries present in the data include: US, EU, India, Rest.
Questions about revenue or users for those countries ARE in scope — map them to
revenue_by_country or users_by_country and set country_filter (US/EU/India/Rest).

Classify status as exactly one of:
- ok: answerable from catalog tables/columns/metrics (metric_name optional if
  the ask clearly uses listed tables/columns; set a short intent paraphrase)
- ambiguous: related to the catalog but too unclear to draft safely
- out_of_scope: jokes, chit-chat, weather, sports, creative writing, general
  knowledge, OR malicious / unsafe intent

Rules:
- "How much money did the US bring in?" → ok (revenue by country / US filter)
- "Users from the US?" → ok
- Session duration / shortest / longest / average sessions → ok via
  public.sessions.duration_minutes (metric_name optional or
  session_duration_by_device).
- Session duration by device / desktop / Web / iOS / Android → ok by joining
  sessions to users.device_type. Catalog device values are Android, iOS, Web
  only — "desktop" means Web.
- Ranked / top-N lists from catalog tables are ok, e.g. "top 10 users by
  longest session" → ok (sessions GROUP BY user_id ORDER BY MAX(duration)
  LIMIT 10). Return user_id; do not invent name/email columns.
- Aggregations (SUM/COUNT/MIN/MAX/AVG/GROUP BY/ORDER BY/LIMIT) over catalog
  columns are normal analytics — status=ok, not out_of_scope.
- Do NOT refuse merely because the ask is a "list of users" if it uses
  catalogued user_id / session fields.
- Joins across listed catalog tables are allowed when columns exist.
- You do NOT write SQL here — only classify. A later step drafts SQL.
- Prefer out_of_scope only when the ask is not about this catalogued product data.
- Prefer ambiguous over inventing tables/metrics that are not listed.
- metric_name may be a listed metric or null.
- country_filter must be one of US, EU, India, Rest, or null.
- valid_options: relevant metric/dataset names when ambiguous.
- decline_message: short, honest; mention sessions/users/payments etc. when relevant,
  not only revenue metrics. Never claim you cannot list user_id rankings when
  sessions are in the catalog.
- wants_analysis: set true when the user wants interpretation, charts, or
  insight — not only a raw file extract. Always true for trend / time-series /
  stats-style asks (trend, over time, YoY/MoM, growth, distribution,
  correlation, statistics, chart/plot/analyze/why). Simple extracts like
  "top 10 users by session" or "total revenue for US" stay wants_analysis=false
  unless they also ask to analyze/chart/explain.
- Never invent tables, columns, or metrics that are not in the catalog.
""".strip()


def cheap_out_of_scope(raw_text: str, *, metric_names: list[str]) -> ParsedAsk | None:
    """Return ParsedAsk if clearly out of scope; else None (caller continues)."""
    text = (raw_text or "").strip()
    if not text:
        return ParsedAsk(
            status="out_of_scope",
            decline_message="Please send a data question about our catalogued metrics.",
            valid_options=sorted(metric_names),
        )
    if _OUT_OF_SCOPE_RE.search(text):
        options = sorted(metric_names)
        return ParsedAsk(
            status="out_of_scope",
            decline_message=(
                "That request is outside what I can help with "
                "(not a catalogued analytics question, or it looks unsafe). "
                f"I can answer: {', '.join(options)}."
            ),
            valid_options=options,
        )
    return None


def build_catalog_brief(*, metric_names: list[str], catalog_text: str) -> str:
    return (
        "Catalog metrics (names):\n"
        + "\n".join(f"- {m}" for m in sorted(metric_names))
        + "\n\nCatalog detail (descriptions only):\n"
        + catalog_text
    )


def make_llm_scope_parser(
    *,
    model: str = "openai:gpt-4o-mini",
    catalog_text_provider: Callable[[], str],
) -> Callable[..., ParsedAsk]:
    """Return an AskParser that uses pydantic-ai for borderline asks."""

    def parse_ask(raw_text: str, *, metric_names: list[str]) -> ParsedAsk:
        cheap = cheap_out_of_scope(raw_text, metric_names=metric_names)
        if cheap is not None:
            return cheap

        # Exact/alias catalog hit — skip LLM to save tokens.
        keyword = catalog_keyword_parser(raw_text, metric_names=metric_names)
        if keyword.status == "ok":
            return keyword

        from pydantic_ai import Agent

        agent = Agent(model, output_type=ParsedAsk, system_prompt=SCOPE_SYSTEM)
        brief = build_catalog_brief(
            metric_names=metric_names,
            catalog_text=catalog_text_provider(),
        )
        prompt = (
            f"{brief}\n\nUser message:\n{raw_text}\n\n"
            "Classify this request against the catalog. "
            "If it is a reasonable analytics question over listed tables/columns, "
            "status=ok even when no single metric name fits perfectly."
        )
        try:
            result = agent.run_sync(prompt)
            parsed: ParsedAsk = result.output
        except Exception as exc:  # noqa: BLE001
            logger.exception("scope LLM failed; falling back to keyword parser")
            fallback = catalog_keyword_parser(raw_text, metric_names=metric_names)
            if fallback.status == "ok":
                return fallback
            return ParsedAsk(
                status="ambiguous",
                intent=raw_text.strip(),
                valid_options=sorted(metric_names),
                decline_message=(
                    "I couldn't classify that request just now. "
                    f"Try rephrasing as a data question about: "
                    f"{', '.join(sorted(metric_names))}. "
                    f"(Classifier error: {exc})"
                ),
            )

        from data_request_agent.proposers import detect_wants_analysis, extract_country_filter

        if not parsed.intent:
            parsed.intent = raw_text.strip()
        if parsed.metric_name and parsed.metric_name not in metric_names:
            parsed.metric_name = None
        # ok without metric_name is allowed — SQL drafter will compose from tables.
        if not parsed.country_filter:
            parsed.country_filter = extract_country_filter(raw_text)
        if parsed.country_filter and parsed.country_filter not in {
            "US",
            "EU",
            "India",
            "Rest",
        }:
            parsed.country_filter = extract_country_filter(raw_text)
        # Deterministic: trend / time-series / stats always request analysis.
        if detect_wants_analysis(raw_text):
            parsed.wants_analysis = True
        if not parsed.valid_options:
            parsed.valid_options = sorted(metric_names)
        if parsed.status == "out_of_scope" and not parsed.decline_message:
            parsed.decline_message = (
                "That isn't something I can answer from our data catalog. "
                f"I can answer questions about: {', '.join(parsed.valid_options)}."
            )
        return parsed

    return parse_ask


def catalog_text_from_yaml(semantic_layer_dir: str | None = None) -> str:
    """Build description-only catalog brief from ``semantic_layer/`` YAML."""
    from data_request_agent.catalog import get_semantic_catalog

    return get_semantic_catalog(semantic_layer_dir).catalog_brief_text()


def catalog_text_from_governance(gov=None) -> str:
    """Deprecated alias — YAML only; ``gov`` ignored."""
    return catalog_text_from_yaml()


def build_live_parse_ask(*, settings, gov) -> Callable[..., ParsedAsk]:
    """Production parser: cheap reject + keyword fast-path + LLM scope."""
    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY unset — using keyword parser only")
        return catalog_keyword_parser

    os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    layer = settings.semantic_layer_dir
    return make_llm_scope_parser(
        catalog_text_provider=lambda: catalog_text_from_yaml(layer),
    )
