"""Injectable proposers — LLM in production, fixed fixtures in tests (A5)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Literal, Protocol

from pydantic import BaseModel, Field


class ParsedAsk(BaseModel):
    status: Literal["ok", "ambiguous", "out_of_scope"] = "ok"
    intent: str | None = None
    metric_name: str | None = None
    dataset_name: str | None = None
    columns: list[str] = Field(default_factory=list)
    country_filter: str | None = None  # e.g. US, EU, India, Rest
    wants_analysis: bool = False
    decline_message: str | None = None
    valid_options: list[str] = Field(default_factory=list)


class DraftPlan(BaseModel):
    sql: str
    plain_language: str
    definitions: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)


class AskParser(Protocol):
    def __call__(self, raw_text: str, *, metric_names: list[str]) -> ParsedAsk: ...


class SqlDrafter(Protocol):
    def __call__(self, parsed: ParsedAsk) -> DraftPlan: ...


# Known country codes/labels in beam_neb0
COUNTRY_ALIASES: dict[str, str] = {
    "us": "US",
    "usa": "US",
    "u.s.": "US",
    "u.s": "US",
    "united states": "US",
    "america": "US",
    "eu": "EU",
    "europe": "EU",
    "india": "India",
    "rest": "Rest",
    "other": "Rest",
}


def extract_country_filter(raw_text: str) -> str | None:
    text = raw_text.lower()
    # Longer phrases first
    for alias in sorted(COUNTRY_ALIASES.keys(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", text):
            return COUNTRY_ALIASES[alias]
    return None


# Explicit analysis asks + trend / time-series / stats intent (file path is for extracts).
_ANALYSIS_PHRASES = (
    "analyze",
    "analyse",
    "analysis",
    "chart",
    "plot",
    "graph",
    "visuali",  # visualize / visualisation
    "why ",
    "trend",
    "time series",
    "timeseries",
    "over time",
    "over the year",
    "year over year",
    "year-on-year",
    "month over month",
    "month-on-month",
    "week over week",
    "day over day",
    "yoy",
    "mom",
    "wow",
    "growth",
    "decline",
    "distribution",
    "histogram",
    "correlation",
    "regression",
    "stats",
    "statistic",
    "standard deviation",
    "percentile",
    "forecast",  # product analytics forecast — not weather (cheap reject handles weather)
)


def detect_wants_analysis(raw_text: str) -> bool:
    """True when the ask wants interpretation/charts, not only a raw extract."""
    text = (raw_text or "").lower()
    if not text:
        return False
    return any(p in text for p in _ANALYSIS_PHRASES)


def catalog_keyword_parser(raw_text: str, *, metric_names: list[str]) -> ParsedAsk:
    """Deterministic parser: metrics + simple country/revenue intent."""
    text = raw_text.lower()
    country = extract_country_filter(raw_text)
    wants_analysis = detect_wants_analysis(raw_text)

    hit = None
    for name in sorted(metric_names, key=len, reverse=True):
        token = name.replace("_", " ")
        if name in text or token in text:
            hit = name
            break

    aliases = {
        "revenue by country": "revenue_by_country",
        "revenue per country": "revenue_by_country",
        "money by country": "revenue_by_country",
        "active subscriptions": "active_subscriptions",
        "users by country": "users_by_country",
        "dau": "latest_dau",
        "daily active": "latest_dau",
        "top users by session": "top_users_by_session_duration",
        "longest session": "top_users_by_session_duration",
    }
    if hit is None:
        for phrase, metric in aliases.items():
            if phrase in text and metric in metric_names:
                hit = metric
                break

    # Natural language: money/revenue + country → revenue_by_country
    moneyish = any(
        w in text
        for w in ("revenue", "money", "bring in", "brought in", "sales", "earned", "earn")
    )
    usersish = any(
        w in text for w in ("user", "users", "customers", "signups", "subscribers")
    )
    sessionish = any(
        w in text for w in ("session", "sessions", "session time", "session times")
    )
    topish = any(w in text for w in ("top", "longest", "highest", "rank"))
    if (
        hit is None
        and sessionish
        and (topish or usersish)
        and "top_users_by_session_duration" in metric_names
    ):
        hit = "top_users_by_session_duration"
    if hit is None and moneyish and "revenue_by_country" in metric_names:
        if country or "country" in text or "by country" in text:
            hit = "revenue_by_country"
    if hit is None and moneyish and country and "revenue_by_country" in metric_names:
        hit = "revenue_by_country"
    if hit is None and usersish and country and "users_by_country" in metric_names:
        hit = "users_by_country"
    if hit is None and moneyish and "total_revenue_usd" in metric_names and not country:
        # global revenue only when no country mentioned
        if any(w in text for w in ("total revenue", "all revenue", "overall revenue")):
            hit = "total_revenue_usd"
        elif "revenue" in text or "money" in text:
            # Prefer breaking down by country when vague "how much money"
            if "revenue_by_country" in metric_names:
                hit = "revenue_by_country"
            else:
                hit = "total_revenue_usd"

    if hit is None:
        options = sorted(metric_names)
        return ParsedAsk(
            status="ambiguous",
            wants_analysis=wants_analysis,
            country_filter=country,
            valid_options=options,
            decline_message=(
                "I'm not sure which catalog metric you mean. "
                f"I can answer: {', '.join(options)}."
            ),
        )

    return ParsedAsk(
        status="ok",
        intent=raw_text.strip(),
        metric_name=hit,
        country_filter=country,
        wants_analysis=wants_analysis,
    )


METRIC_SQL: dict[str, DraftPlan] = {
    "total_revenue_usd": DraftPlan(
        sql="SELECT SUM(amount_usd) AS total_revenue_usd FROM public.payments",
        plain_language="Sum of all payment amounts in USD from public.payments.",
        definitions=["total_revenue_usd: Sum of all payment amounts in USD."],
        columns=["total_revenue_usd"],
    ),
    "revenue_by_country": DraftPlan(
        sql=(
            "SELECT country, SUM(total_revenue_usd) AS total_revenue_usd "
            "FROM marts.user_revenue_summary "
            "GROUP BY country ORDER BY total_revenue_usd DESC"
        ),
        plain_language="Total user revenue in USD grouped by country.",
        definitions=[
            "revenue_by_country: Sum of total_revenue_usd from user_revenue_summary by country."
        ],
        columns=["country", "total_revenue_usd"],
    ),
    "active_subscriptions": DraftPlan(
        sql=(
            "SELECT COUNT(*) AS active_subscriptions "
            "FROM public.subscriptions WHERE status = 'active'"
        ),
        plain_language="Count of subscriptions with status active.",
        definitions=[
            "active_subscriptions: Count of subscriptions currently marked active."
        ],
        columns=["active_subscriptions"],
    ),
    "users_by_country": DraftPlan(
        sql=(
            "SELECT country, COUNT(*) AS user_count "
            "FROM public.users GROUP BY country ORDER BY user_count DESC"
        ),
        plain_language="Number of users grouped by country.",
        definitions=["users_by_country: Number of users grouped by country."],
        columns=["country", "user_count"],
    ),
    "latest_dau": DraftPlan(
        sql=(
            "SELECT date, dau FROM marts.user_activity_metrics "
            "ORDER BY date DESC LIMIT 1"
        ),
        plain_language="Most recent daily active user count from activity marts.",
        definitions=["latest_dau: Most recent DAU from user_activity_metrics."],
        columns=["date", "dau"],
    ),
    "top_users_by_session_duration": DraftPlan(
        sql=(
            "SELECT user_id, MAX(duration_minutes) AS max_duration_minutes "
            "FROM public.sessions "
            "GROUP BY user_id "
            "ORDER BY max_duration_minutes DESC "
            "LIMIT 10"
        ),
        plain_language=(
            "Top 10 users by their longest single session length in minutes."
        ),
        definitions=[
            "top_users_by_session_duration: Rank users by MAX(duration_minutes)."
        ],
        columns=["user_id", "max_duration_minutes"],
    ),
}


def catalog_sql_drafter(parsed: ParsedAsk) -> DraftPlan:
    if not parsed.metric_name or parsed.metric_name not in METRIC_SQL:
        raise ValueError(f"no SQL template for metric {parsed.metric_name!r}")
    base = METRIC_SQL[parsed.metric_name]
    country = parsed.country_filter
    if not country:
        return base

    if parsed.metric_name == "revenue_by_country":
        sql = (
            "SELECT country, SUM(total_revenue_usd) AS total_revenue_usd "
            "FROM marts.user_revenue_summary "
            f"WHERE country = '{country}' "
            "GROUP BY country"
        )
        return DraftPlan(
            sql=sql,
            plain_language=f"Total user revenue in USD for country {country}.",
            definitions=base.definitions,
            columns=["country", "total_revenue_usd"],
        )
    if parsed.metric_name == "users_by_country":
        sql = (
            "SELECT country, COUNT(*) AS user_count "
            f"FROM public.users WHERE country = '{country}' "
            "GROUP BY country"
        )
        return DraftPlan(
            sql=sql,
            plain_language=f"Number of users in country {country}.",
            definitions=base.definitions,
            columns=["country", "user_count"],
        )
    return base


@dataclass
class Proposers:
    parse_ask: Callable[..., ParsedAsk] = catalog_keyword_parser
    draft_sql: Callable[..., DraftPlan] = catalog_sql_drafter


def default_proposers(*, settings, gov) -> Proposers:
    """Live Slack: LLM scope + LLM SQL (templates only as fallback / tests)."""
    from data_request_agent.scope_parser import build_live_parse_ask
    from data_request_agent.sql_drafter import build_live_sql_drafter

    return Proposers(
        parse_ask=build_live_parse_ask(settings=settings, gov=gov),
        draft_sql=build_live_sql_drafter(settings=settings, gov=gov),
    )
