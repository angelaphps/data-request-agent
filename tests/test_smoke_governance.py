"""Stage 0 gate: governance catalog + admin lookup + business DB probe."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from data_request_agent.config import Settings
from data_request_agent.governance import Governance

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module")
def gov(settings: Settings) -> Governance:
    return Governance(settings.database_url)


def test_admin_lookup_from_seed(gov: Governance) -> None:
    assert gov.is_admin("U_ADMIN") is True
    assert gov.is_admin("U_REQ") is False


def test_real_admin_seeded_from_yaml(gov: Governance) -> None:
    assert gov.is_admin("U0B9N7SB1CH") is True


def test_catalog_users_dataset(gov: Governance) -> None:
    dataset = gov.get_dataset("users")
    assert dataset is not None
    assert dataset["table_schema"] == "public"
    assert dataset["table_name"] == "users"
    cols = {c["name"]: c["sensitivity"] for c in dataset["columns"]}
    assert cols["user_id"] == "none"
    assert cols["country"] == "internal"


def test_catalog_metric_total_revenue(gov: Governance) -> None:
    metric = gov.get_metric("total_revenue_usd")
    assert metric is not None
    assert metric["dataset_name"] == "payments"


def test_audit_writer_roundtrip(gov: Governance) -> None:
    event_id = gov.audit(
        "smoke_test",
        {"ok": True},
        actor_slack_id="U_ADMIN",
    )
    assert event_id > 0
    recent = gov.recent_audit(limit=5)
    assert any(r["id"] == event_id and r["event"] == "smoke_test" for r in recent)


def test_business_database_reachable(settings: Settings) -> None:
    assert settings.business_database_url, "BUSINESS_DATABASE_URL must be set"
    with psycopg.connect(
        settings.query_database_url,
        row_factory=dict_row,
        connect_timeout=15,
    ) as conn:
        users = conn.execute("SELECT COUNT(*) AS n FROM public.users").fetchone()
        payments = conn.execute(
            "SELECT COUNT(*) AS n FROM public.payments"
        ).fetchone()
    assert users["n"] > 0
    assert payments["n"] > 0
