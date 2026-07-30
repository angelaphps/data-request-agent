"""Compose the one LangGraph — Stage 1 spine + Stage 2 resilience."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from data_request_agent.approval import ensure_permission, wait_for_run_cancel
from data_request_agent.config import Settings
from data_request_agent.delivery import guard_and_deliver
from data_request_agent.destinations import Destination, MemoryDestination
from data_request_agent.execution import recheck_and_run
from data_request_agent.governance import Governance
from data_request_agent.intake import clarify_once, identify, parse
from data_request_agent.planner import plan_query
from data_request_agent.proposers import Proposers
from data_request_agent.results_check import check_results
from data_request_agent.state import AgentState
from data_request_agent.stores import PostgresStore, TabularStore


@dataclass
class AgentRuntime:
    settings: Settings
    gov: Governance
    store: TabularStore
    destination: Destination
    proposers: Proposers
    plan_analysis: Any | None = None


def build_graph(
    settings: Settings,
    checkpointer: BaseCheckpointSaver,
    *,
    runtime: AgentRuntime | None = None,
) -> Any:
    rt = runtime or _default_runtime(settings)

    def node_identify(state: AgentState) -> AgentState:
        return identify(state, gov=rt.gov)

    def node_parse(state: AgentState) -> AgentState:
        return parse(
            state,
            gov=rt.gov,
            parse_ask=rt.proposers.parse_ask,
            max_clarify=rt.settings.max_clarify_questions,
        )

    def node_clarify(state: AgentState) -> AgentState:
        return clarify_once(state, gov=rt.gov)

    def node_plan(state: AgentState) -> AgentState:
        return plan_query(
            state,
            gov=rt.gov,
            store=rt.store,
            proposers=rt.proposers,
            max_retries=rt.settings.max_planner_retries,
        )

    def node_preview(state: AgentState) -> AgentState:
        return wait_for_run_cancel(state)

    def node_permission(state: AgentState) -> AgentState:
        return ensure_permission(state, gov=rt.gov, settings=rt.settings)

    def node_execute(state: AgentState) -> AgentState:
        return recheck_and_run(
            state, gov=rt.gov, store=rt.store, settings=rt.settings
        )

    def node_results(state: AgentState) -> AgentState:
        return check_results(state, gov=rt.gov)

    def node_deliver(state: AgentState) -> AgentState:
        return guard_and_deliver(
            state,
            gov=rt.gov,
            destination=rt.destination,
            settings=rt.settings,
            plan_analysis=rt.plan_analysis,
        )

    graph = StateGraph(AgentState)
    graph.add_node("identify", node_identify)
    graph.add_node("parse", node_parse)
    graph.add_node("clarify", node_clarify)
    graph.add_node("plan", node_plan)
    graph.add_node("preview", node_preview)
    graph.add_node("permission", node_permission)
    graph.add_node("execute", node_execute)
    graph.add_node("results", node_results)
    graph.add_node("deliver", node_deliver)

    graph.add_edge(START, "identify")
    graph.add_edge("identify", "parse")
    graph.add_conditional_edges(
        "parse",
        _after_parse,
        {"clarify": "clarify", "plan": "plan", "end": END},
    )
    graph.add_edge("clarify", "parse")
    graph.add_conditional_edges("plan", _after_plan, {"preview": "preview", "end": END})
    graph.add_conditional_edges(
        "preview", _after_preview, {"permission": "permission", "end": END}
    )
    graph.add_conditional_edges(
        "permission", _after_permission, {"execute": "execute", "end": END}
    )
    graph.add_conditional_edges(
        "execute", _after_execute, {"results": "results", "end": END}
    )
    graph.add_conditional_edges(
        "results",
        _after_results,
        {"deliver": "deliver", "execute": "execute", "end": END},
    )
    graph.add_edge("deliver", END)
    return graph.compile(checkpointer=checkpointer)


def build_skeleton_graph(checkpointer: BaseCheckpointSaver) -> Any:
    from data_request_agent.graph_skeleton import build_skeleton_graph as _skel

    return _skel(checkpointer)


def _default_runtime(settings: Settings) -> AgentRuntime:
    gov = Governance(settings.database_url)
    store = PostgresStore(settings.query_database_url)
    dest: Destination
    if settings.slack_bot_token:
        from data_request_agent.destinations import SlackThreadDestination

        dest = SlackThreadDestination(bot_token=settings.slack_bot_token)
    else:
        dest = MemoryDestination()
    from data_request_agent.proposers import default_proposers

    return AgentRuntime(
        settings=settings,
        gov=gov,
        store=store,
        destination=dest,
        proposers=default_proposers(settings=settings, gov=gov),
    )


def _after_parse(state: AgentState) -> Literal["clarify", "plan", "end"]:
    phase = state.get("phase")
    if phase == "needs_clarify":
        return "clarify"
    if phase == "declined":
        return "end"
    return "plan"


def _after_plan(state: AgentState) -> Literal["preview", "end"]:
    return "preview" if state.get("phase") == "planned" else "end"


def _after_preview(state: AgentState) -> Literal["permission", "end"]:
    return "permission" if state.get("phase") == "run_confirmed" else "end"


def _after_permission(state: AgentState) -> Literal["execute", "end"]:
    return "execute" if state.get("phase") == "approved" else "end"


def _after_execute(state: AgentState) -> Literal["results", "end"]:
    return "results" if state.get("phase") == "executed" else "end"


def _after_results(state: AgentState) -> Literal["deliver", "execute", "end"]:
    phase = state.get("phase")
    if phase == "results_ok":
        return "deliver"
    if phase == "results_retry":
        return "execute"
    return "end"
