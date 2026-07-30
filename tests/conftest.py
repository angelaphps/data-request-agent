"""Shared fixtures for Stage 1 scenario tests."""

from __future__ import annotations

import uuid

import pytest
from langgraph.types import Command

from data_request_agent.config import Settings
from data_request_agent.destinations import MemoryDestination
from data_request_agent.governance import Governance
from data_request_agent.graph import AgentRuntime, build_graph
from data_request_agent.proposers import DraftPlan, ParsedAsk, Proposers
from data_request_agent.stores import PostgresStore


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module")
def gov(settings: Settings) -> Governance:
    return Governance(settings.database_url)


@pytest.fixture
def memory_dest() -> MemoryDestination:
    return MemoryDestination()


@pytest.fixture
def runtime(settings: Settings, gov: Governance, memory_dest: MemoryDestination) -> AgentRuntime:
    return AgentRuntime(
        settings=settings,
        gov=gov,
        store=PostgresStore(settings.query_database_url),
        destination=memory_dest,
        proposers=Proposers(),
    )


def new_thread_id(prefix: str = "sc") -> str:
    return f"{prefix}-{uuid.uuid4()}"


def invoke_until_interrupt(graph, payload: dict, config: dict):
    result = graph.invoke(payload, config)
    assert result.get("__interrupt__"), f"expected interrupt, got phase={result.get('phase')} {result}"
    return result


def resume(graph, config: dict, action: str, actor: str):
    return graph.invoke(
        Command(resume={"action": action, "actor_slack_id": actor}),
        config,
    )


def personal_device_proposers() -> Proposers:
    """Draft selects a personal column (device_type) for guard tests."""

    def parse_ask(raw_text: str, *, metric_names: list[str]) -> ParsedAsk:
        wants = "analy" in raw_text.lower()
        return ParsedAsk(
            status="ok",
            intent=raw_text,
            metric_name="users_device_sample",
            dataset_name="users",
            wants_analysis=wants,
        )

    def draft_sql(parsed: ParsedAsk) -> DraftPlan:
        return DraftPlan(
            sql=(
                "SELECT user_id, country, device_type "
                "FROM public.users ORDER BY user_id LIMIT 20"
            ),
            plain_language="Sample of users including device_type (personal).",
            definitions=["users sample with device_type"],
            columns=["user_id", "country", "device_type"],
        )

    return Proposers(parse_ask=parse_ask, draft_sql=draft_sql)
