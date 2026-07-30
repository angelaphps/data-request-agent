"""Stage 1.0: LangGraph interrupt/resume with Postgres checkpointer."""

from __future__ import annotations

import uuid

from langgraph.types import Command

from data_request_agent.config import Settings
from data_request_agent.governance import Governance
from data_request_agent.graph_skeleton import build_skeleton_graph


def test_interrupt_then_resume() -> None:
    settings = Settings()
    gov = Governance(settings.database_url)
    thread_id = f"test-pause-{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}

    with gov.open_checkpointer() as handle:
        graph = build_skeleton_graph(handle.checkpointer)
        first = graph.invoke(
            {
                "requester_slack_id": "U_REQ",
                "channel_id": "D_TEST",
                "thread_ts": "1.0",
                "raw_text": "ping",
                "phase": "waiting",
            },
            config,
        )
        assert first.get("__interrupt__"), first
        interrupt_payload = first["__interrupt__"][0].value
        assert interrupt_payload["kind"] == "skeleton_confirm"

        second = graph.invoke(
            Command(resume={"action": "continue", "actor_slack_id": "U_REQ"}),
            config,
        )
        assert second.get("phase") == "done"
        assert second.get("human_decision") == "continue"
        assert not second.get("__interrupt__")


def test_resume_survives_new_checkpointer_handle() -> None:
    """Same thread_id resumes after 'process restart' (new pool/handle)."""
    settings = Settings()
    gov = Governance(settings.database_url)
    thread_id = f"test-restart-{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}

    with gov.open_checkpointer() as handle:
        graph = build_skeleton_graph(handle.checkpointer)
        first = graph.invoke(
            {
                "requester_slack_id": "U_ADMIN",
                "channel_id": "D_TEST",
                "thread_ts": "2.0",
                "raw_text": "restart me",
                "phase": "waiting",
            },
            config,
        )
        assert first.get("__interrupt__")

    # New handle ≈ process restart; Postgres still holds the checkpoint.
    with gov.open_checkpointer() as handle2:
        graph2 = build_skeleton_graph(handle2.checkpointer)
        second = graph2.invoke(
            Command(resume={"action": "continue"}),
            config,
        )
        assert second.get("phase") == "done"
        assert second.get("human_decision") == "continue"
