"""Stage 1.0 walking skeleton (interrupt/resume) — used by unit tests."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from data_request_agent.state import AgentState


def wait_for_human(state: AgentState) -> AgentState:
    decision = interrupt(
        {
            "kind": "skeleton_confirm",
            "prompt": "Stage 1.0 walking skeleton — press Continue to resume.",
            "requester_slack_id": state.get("requester_slack_id"),
            "channel_id": state.get("channel_id"),
            "thread_ts": state.get("thread_ts"),
        }
    )
    if isinstance(decision, dict):
        value = str(decision.get("action") or decision.get("value") or decision)
    else:
        value = str(decision)
    return {**state, "human_decision": value, "phase": "resumed"}


def finish_skeleton(state: AgentState) -> AgentState:
    return {
        **state,
        "phase": "done",
        "delivery_message": f"Skeleton resumed with decision={state.get('human_decision')!r}",
    }


def build_skeleton_graph(checkpointer: BaseCheckpointSaver) -> Any:
    graph = StateGraph(AgentState)
    graph.add_node("wait_for_human", wait_for_human)
    graph.add_node("finish_skeleton", finish_skeleton)
    graph.add_edge(START, "wait_for_human")
    graph.add_edge("wait_for_human", "finish_skeleton")
    graph.add_edge("finish_skeleton", END)
    return graph.compile(checkpointer=checkpointer)
