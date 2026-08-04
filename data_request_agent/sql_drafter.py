"""LLM SQL drafter — proposes one read-only SELECT from catalog descriptions.

Templates remain available for tests. Live MVP uses this drafter; sqlglot + trial
still dispose of unsafe SQL.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Callable

from data_request_agent.proposers import DraftPlan, ParsedAsk, catalog_sql_drafter
from data_request_agent.scope_parser import catalog_text_from_governance

logger = logging.getLogger(__name__)

SQL_SYSTEM = """
You write ONE PostgreSQL read-only SELECT for an internal analytics bot.

Rules:
- Use ONLY tables and columns listed in the catalog (schema.table and column names).
- Single SELECT (WITH allowed). No INSERT/UPDATE/DELETE/DROP/GRANT/COPY.
- Prefer metrics' definitions when they fit; otherwise compose from tables/columns.
- JOIN KEYS and GOLDEN QUERY EXAMPLES in the catalog are authoritative for joins —
  use listed relationships (e.g. sessions.user_id → users.user_id); do not invent FKs.
- Joins across catalog tables are fine when a JOIN KEY exists.
- Country values: US, EU, India, Rest.
- device_type values: Android, iOS, Web only. Map "desktop"/"browser" → Web.
- Top-N / ranked lists are ordinary SQL: GROUP BY, ORDER BY DESC, LIMIT N
  (e.g. top 10 user_id by MAX(duration_minutes) from public.sessions).
- Qualify tables with schema (public.users, public.sessions, marts.*, etc.).
- Keep results bounded when listing rows (ORDER BY + LIMIT ≤ 10000).
- Aggregates (MIN/MAX/AVG/COUNT) need no row LIMIT unless returning many groups;
  for "top N" use LIMIT N.
- plain_language: one or two sentences a non-engineer can confirm.
- definitions: short bullets of which catalog meanings you used.
- columns: output column names you expect.
- sql: the query only, no markdown fences.
""".strip()


def _strip_fences(sql: str) -> str:
    text = sql.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:sql)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip().rstrip(";")


def make_llm_sql_drafter(
    *,
    model: str = "openai:gpt-4o-mini",
    catalog_text_provider: Callable[[], str],
    fallback: Callable[[ParsedAsk], DraftPlan] = catalog_sql_drafter,
) -> Callable[..., DraftPlan]:
    """Return a SqlDrafter that uses pydantic-ai; falls back to templates if needed."""

    def draft_sql(parsed: ParsedAsk, *, feedback: str | None = None) -> DraftPlan:
        ask = parsed.intent or ""
        try:
            from pydantic_ai import Agent

            agent = Agent(model, output_type=DraftPlan, system_prompt=SQL_SYSTEM)
            catalog = catalog_text_provider()
            extra = ""
            if parsed.metric_name:
                extra += f"\nSuggested metric (optional hint): {parsed.metric_name}"
            if parsed.country_filter:
                extra += f"\nCountry filter hint: {parsed.country_filter}"
            if parsed.wants_analysis:
                extra += (
                    "\nUser also wants analysis later; still return the data query."
                )
            if feedback:
                extra += f"\nPrevious draft failed checks — fix this:\n{feedback}"

            prompt = (
                f"Catalog (descriptions only — no data rows):\n{catalog}\n\n"
                f"User request:\n{ask}\n"
                f"{extra}\n\n"
                "Draft one safe read-only SQL query that answers the request."
            )
            result = agent.run_sync(prompt)
            draft: DraftPlan = result.output
            draft.sql = _strip_fences(draft.sql)
            if not draft.plain_language:
                draft.plain_language = ask or "Catalog query"
            return draft
        except Exception as exc:  # noqa: BLE001
            logger.exception("LLM SQL draft failed; trying template fallback")
            try:
                return fallback(parsed)
            except Exception:
                raise RuntimeError(f"SQL draft failed: {exc}") from exc

    return draft_sql


def build_live_sql_drafter(*, settings, gov) -> Callable[..., DraftPlan]:
    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY unset — using SQL templates only")
        return catalog_sql_drafter
    os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    return make_llm_sql_drafter(
        catalog_text_provider=lambda: catalog_text_from_governance(gov),
    )
