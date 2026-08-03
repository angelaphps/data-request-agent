"""Scenario tests = definition of done (PLAN.md A1). Stage 1 gates implemented."""

from __future__ import annotations

import pytest
from langgraph.types import Command

from data_request_agent.graph import AgentRuntime, build_graph
from data_request_agent.proposers import Proposers
from tests.conftest import (
    invoke_until_interrupt,
    new_thread_id,
    personal_device_proposers,
    resume,
)


def test_01_clarification_loop_then_plan(settings, gov, runtime, memory_dest):
    """Ambiguous ask → clarify interrupt → answer with metric → plan preview."""
    thread_id = new_thread_id("clarify")
    config = {"configurable": {"thread_id": thread_id}}
    with gov.open_checkpointer() as handle:
        graph = build_graph(settings, handle.checkpointer, runtime=runtime)
        first = invoke_until_interrupt(
            graph,
            {
                "requester_slack_id": "U_ADMIN",
                "channel_id": "D_ADMIN",
                "thread_ts": "10.1",
                "raw_text": "show me the numbers",
            },
            config,
        )
        assert first["__interrupt__"][0].value["kind"] == "clarify"
        second = graph.invoke(
            Command(
                resume={"text": "total_revenue_usd", "actor_slack_id": "U_ADMIN"}
            ),
            config,
        )
        assert second.get("__interrupt__")
        assert second["__interrupt__"][0].value["kind"] == "plan_preview"
        final = resume(graph, config, "run", "U_ADMIN")
        assert final.get("phase") == "delivered"
        events = [e["event"] for e in gov.recent_audit(limit=20)]
        assert "clarify_asked" in events
        assert "clarify_answered" in events


def test_02_admin_straight_through_file(settings, gov, runtime, memory_dest):
    """Admin DM → preview Run → file in private thread (no approval card)."""
    thread_id = new_thread_id("admin")
    config = {"configurable": {"thread_id": thread_id}}
    with gov.open_checkpointer() as handle:
        graph = build_graph(settings, handle.checkpointer, runtime=runtime)
        first = invoke_until_interrupt(
            graph,
            {
                "requester_slack_id": "U_ADMIN",
                "channel_id": "D_ADMIN",
                "thread_ts": "100.1",
                "raw_text": "total_revenue_usd please",
            },
            config,
        )
        assert first["__interrupt__"][0].value["kind"] == "plan_preview"
        final = resume(graph, config, "run", "U_ADMIN")
        assert final.get("phase") == "delivered"
        assert final.get("is_admin") is True
        assert memory_dest.deliveries
        assert memory_dest.deliveries[-1].get("csv") is not None
        events = [e["event"] for e in gov.recent_audit(limit=8)]
        assert "approval_auto_admin" in events


def test_03_requester_approval_file(settings, gov, runtime, memory_dest):
    """Requester → Run → admin Approve → file."""
    thread_id = new_thread_id("req")
    config = {"configurable": {"thread_id": thread_id}}
    with gov.open_checkpointer() as handle:
        graph = build_graph(settings, handle.checkpointer, runtime=runtime)
        first = invoke_until_interrupt(
            graph,
            {
                "requester_slack_id": "U_REQ",
                "channel_id": "D_REQ",
                "thread_ts": "200.1",
                "raw_text": "total_revenue_usd",
            },
            config,
        )
        assert first["__interrupt__"][0].value["kind"] == "plan_preview"
        second = resume(graph, config, "run", "U_REQ")
        assert second.get("__interrupt__")
        assert second["__interrupt__"][0].value["kind"] == "admin_approval"
        final = resume(graph, config, "approve", "U_ADMIN")
        assert final.get("phase") == "delivered"
        assert memory_dest.deliveries


def test_03b_approve_without_personal_redacts(
    settings, gov, memory_dest
):
    """Admin 'approve without personal' strips personal cols from the CSV."""
    from data_request_agent.approval import APPROVE_WITHOUT_PERSONAL

    runtime = AgentRuntime(
        settings=settings,
        gov=gov,
        store=__import__(
            "data_request_agent.stores", fromlist=["PostgresStore"]
        ).PostgresStore(settings.query_database_url),
        destination=memory_dest,
        proposers=personal_device_proposers(),
    )
    thread_id = new_thread_id("nopii")
    config = {"configurable": {"thread_id": thread_id}}
    with gov.open_checkpointer() as handle:
        graph = build_graph(settings, handle.checkpointer, runtime=runtime)
        invoke_until_interrupt(
            graph,
            {
                "requester_slack_id": "U_REQ",
                "channel_id": "D_REQ",
                "thread_ts": "301.1",
                "raw_text": "users sample with device type",
            },
            config,
        )
        second = resume(graph, config, "run", "U_REQ")
        assert second.get("__interrupt__")
        assert second["__interrupt__"][0].value.get("touches_personal_data") is True
        final = resume(graph, config, APPROVE_WITHOUT_PERSONAL, "U_ADMIN")
        assert final.get("phase") == "delivered"
        payload = memory_dest.deliveries[-1]
        rows = payload.get("rows") or []
        assert rows
        assert "device_type" not in rows[0]
        assert "user_id" in rows[0]
        assert "hidden personal" in (final.get("delivery_message") or "").lower()
        events = [e["event"] for e in gov.recent_audit(limit=20)]
        assert "approval_approved" in events


def test_03c_approve_keeps_personal_on_csv(settings, gov, memory_dest):
    """Full Approve keeps personal columns on the CSV (file path only)."""
    runtime = AgentRuntime(
        settings=settings,
        gov=gov,
        store=__import__(
            "data_request_agent.stores", fromlist=["PostgresStore"]
        ).PostgresStore(settings.query_database_url),
        destination=memory_dest,
        proposers=personal_device_proposers(),
    )
    thread_id = new_thread_id("keeppii")
    config = {"configurable": {"thread_id": thread_id}}
    with gov.open_checkpointer() as handle:
        graph = build_graph(settings, handle.checkpointer, runtime=runtime)
        invoke_until_interrupt(
            graph,
            {
                "requester_slack_id": "U_REQ",
                "channel_id": "D_REQ",
                "thread_ts": "302.1",
                "raw_text": "users sample with device type",
            },
            config,
        )
        resume(graph, config, "run", "U_REQ")
        final = resume(graph, config, "approve", "U_ADMIN")
        assert final.get("phase") == "delivered"
        payload = memory_dest.deliveries[-1]
        rows = payload.get("rows") or []
        assert rows
        assert "device_type" in rows[0]
        assert "user_id" in rows[0]
        assert payload.get("csv") is not None


def test_04_admin_reject_no_delivery(settings, gov, runtime, memory_dest):
    """Admin Reject → notify requester; no file delivery."""
    thread_id = new_thread_id("rej")
    config = {"configurable": {"thread_id": thread_id}}
    before = len(memory_dest.deliveries)
    with gov.open_checkpointer() as handle:
        graph = build_graph(settings, handle.checkpointer, runtime=runtime)
        invoke_until_interrupt(
            graph,
            {
                "requester_slack_id": "U_REQ",
                "channel_id": "D_REQ",
                "thread_ts": "300.1",
                "raw_text": "active_subscriptions",
            },
            config,
        )
        resume(graph, config, "run", "U_REQ")
        final = resume(graph, config, "reject", "U_ADMIN")
        assert final.get("phase") == "rejected"
        assert "declined" in (final.get("delivery_message") or "").lower()
        assert len(memory_dest.deliveries) == before
        assert final.get("result_ref") is None
        events = [e["event"] for e in gov.recent_audit(limit=15)]
        assert "approval_rejected" in events


def test_04b_preview_cancel_no_run(settings, gov, runtime, memory_dest):
    """Cancel on plan preview → cancelled; nothing delivered."""
    thread_id = new_thread_id("cancel")
    config = {"configurable": {"thread_id": thread_id}}
    before = len(memory_dest.deliveries)
    with gov.open_checkpointer() as handle:
        graph = build_graph(settings, handle.checkpointer, runtime=runtime)
        first = invoke_until_interrupt(
            graph,
            {
                "requester_slack_id": "U_ADMIN",
                "channel_id": "D_ADMIN",
                "thread_ts": "110.1",
                "raw_text": "total_revenue_usd please",
            },
            config,
        )
        assert first["__interrupt__"][0].value["kind"] == "plan_preview"
        final = resume(graph, config, "cancel", "U_ADMIN")
        assert final.get("phase") == "cancelled"
        assert "cancel" in (final.get("delivery_message") or "").lower()
        assert len(memory_dest.deliveries) == before
        assert final.get("result_ref") is None


def test_05_permission_recheck_blocks_stale_approval(settings, gov, runtime, memory_dest):
    """Invalidate approval between approve and run → re-check blocks."""
    thread_id = new_thread_id("recheck")
    config = {"configurable": {"thread_id": thread_id}}
    with gov.open_checkpointer() as handle:
        graph = build_graph(settings, handle.checkpointer, runtime=runtime)
        invoke_until_interrupt(
            graph,
            {
                "requester_slack_id": "U_REQ",
                "channel_id": "D_REQ",
                "thread_ts": "400.1",
                "raw_text": "latest_dau",
            },
            config,
        )
        second = resume(graph, config, "run", "U_REQ")
        approval_id = second["__interrupt__"][0].value["approval_id"]
        # Approve then corrupt permission before execution continues —
        # we approve, then invalidate, then the graph would need to be mid-flight.
        # Instead: approve, invalidate, then force recheck by re-invoking execute path
        # via a fresh resume isn't possible. Invalidate AFTER approve resume starts
        # execute — race. Cleaner: approve, then call invalidate, then manually
        # invoke a second run of recheck by building state.
        #
        # Practical approach: after full approve, invalidate and call recheck_and_run
        # on the resulting approved state by replaying with a custom graph step.
        from data_request_agent.execution import recheck_and_run

        # Intercept: approve normally but invalidate before execute by patching
        # record — use two-step: get to approval interrupt, approve with a wrapper.

    # Redo with hook: invalidate immediately after approval is recorded using
    # a custom permission node — simplest reliable test:
    with gov.open_checkpointer() as handle:
        graph = build_graph(settings, handle.checkpointer, runtime=runtime)
        invoke_until_interrupt(
            graph,
            {
                "requester_slack_id": "U_REQ",
                "channel_id": "D_REQ",
                "thread_ts": "400.2",
                "raw_text": "latest_dau",
            },
            config={"configurable": {"thread_id": thread_id + "-b"}},
        )
        cfg = {"configurable": {"thread_id": thread_id + "-b"}}
        second = resume(graph, cfg, "run", "U_REQ")
        approval_id = second["__interrupt__"][0].value["approval_id"]

        # Resume approve but first set up: we'll invalidate in the same tick by
        # approving then immediately invalidating before deliver — actually
        # execute runs in same invoke. So invalidate approval_id WHILE status
        # is still pending, then approve — no that's wrong.
        #
        # Correct approach: complete approve→execute normally is too late.
        # After delivered we'd recheck again. Better: unit-style call:

        from data_request_agent.execution import recheck_and_run
        from data_request_agent.stores import PostgresStore

        # Create an approved permission then invalidate it, then recheck_and_run
        plan = {
            "draft_sql": "SELECT date, dau FROM marts.user_activity_metrics ORDER BY date DESC LIMIT 1",
            "plain_language_plan": "latest dau",
            "trial_columns": ["date", "dau"],
            "approved_columns": ["date", "dau"],
            "touches_personal_data": False,
        }
        aid = approval_id + "-stale"
        gov.create_approval(
            approval_id=aid,
            request_id="stale-test",
            requester_slack_id="U_REQ",
            channel_id="D_REQ",
            thread_ts="400.3",
            plan=plan,
            touches_personal_data=False,
            status="approved",
            decided_by=["U_ADMIN"],
        )
        gov.invalidate_approval(aid)
        state = recheck_and_run(
            {
                "requester_slack_id": "U_REQ",
                "approved": {"approval_id": aid, "plan": plan, "status": "approved"},
                "plan": plan,
            },
            gov=gov,
            store=PostgresStore(settings.query_database_url),
            settings=settings,
        )
        assert state.get("phase") == "recheck_failed"
        assert state.get("error") == "permission_recheck_failed"


def test_06_analysis_path(settings, gov, memory_dest):
    """Analysis ask → text answer + summary table (+ optional chart), not mock CSV."""
    from data_request_agent.analysis import heuristic_analysis_plan
    from data_request_agent.proposers import DraftPlan, ParsedAsk, Proposers

    def parse_ask(raw_text: str, *, metric_names: list[str]) -> ParsedAsk:
        return ParsedAsk(
            status="ok",
            intent=raw_text,
            metric_name="session_duration_by_country",
            wants_analysis=True,
        )

    def draft_sql(parsed: ParsedAsk) -> DraftPlan:
        return DraftPlan(
            sql=(
                "SELECT u.country, AVG(s.duration_minutes) AS avg_duration_minutes "
                "FROM public.sessions s "
                "JOIN public.users u ON s.user_id = u.user_id "
                "GROUP BY u.country "
                "ORDER BY avg_duration_minutes DESC"
            ),
            plain_language="Average session duration by country.",
            definitions=["avg session duration by country"],
            columns=["country", "avg_duration_minutes"],
        )

    runtime = AgentRuntime(
        settings=settings,
        gov=gov,
        store=__import__(
            "data_request_agent.stores", fromlist=["PostgresStore"]
        ).PostgresStore(settings.query_database_url),
        destination=memory_dest,
        proposers=Proposers(parse_ask=parse_ask, draft_sql=draft_sql),
        plan_analysis=heuristic_analysis_plan,
    )
    thread_id = new_thread_id("analysis")
    config = {"configurable": {"thread_id": thread_id}}
    with gov.open_checkpointer() as handle:
        graph = build_graph(settings, handle.checkpointer, runtime=runtime)
        invoke_until_interrupt(
            graph,
            {
                "requester_slack_id": "U_ADMIN",
                "channel_id": "D_ADMIN",
                "thread_ts": "606.1",
                "raw_text": "analyze average session duration by country",
            },
            config,
        )
        final = resume(graph, config, "run", "U_ADMIN")
        assert final.get("phase") == "delivered"
        assert final.get("analysis_mock") is False
        assert final.get("analysis_answer")
        assert "|" in (final.get("analysis_table_markdown") or "")
        payload = memory_dest.deliveries[-1]
        assert payload.get("wants_analysis") is True
        assert payload.get("csv") is None
        assert payload.get("text")
        # Analysis frame must not carry personal columns (device_type).
        guarded = final.get("guarded_rows") or []
        if guarded:
            assert "device_type" not in guarded[0]
        assert "Data as of:" in (final.get("delivery_message") or "")
        # Stage 4: thread context saved for follow-ups.
        key = gov.thread_key("D_ADMIN", "606.1")
        row = gov.get_thread_context(key)
        assert row is not None
        ctx = row["context"]
        assert ctx.get("answer")
        assert "device_type" not in (ctx.get("column_names") or [])
        events = [e["event"] for e in gov.recent_audit(limit=20)]
        assert "thread_context_saved" in events
        from data_request_agent.followups import try_answer_followup

        quiet = settings.model_copy(update={"openai_api_key": ""})
        follow = try_answer_followup(
            "why is that country higher?",
            gov=gov,
            settings=quiet,
            channel_id="D_ADMIN",
            thread_ts="606.1",
            requester_slack_id="U_ADMIN",
        )
        assert follow is not None
        assert follow.needs_new_request is False
        assert follow.reply
        assert (
            try_answer_followup(
                "top 10 users by session times",
                gov=gov,
                settings=quiet,
                channel_id="D_ADMIN",
                thread_ts="606.1",
                requester_slack_id="U_ADMIN",
            )
            is None
        )


def test_06d_requester_analysis_after_approval(settings, gov, memory_dest):
    """Non-admin analysis ask → approval → analysis delivery (not CSV)."""
    from data_request_agent.analysis import heuristic_analysis_plan
    from data_request_agent.proposers import DraftPlan, ParsedAsk, Proposers

    def parse_ask(raw_text: str, *, metric_names: list[str]) -> ParsedAsk:
        return ParsedAsk(
            status="ok",
            intent=raw_text,
            metric_name="session_duration_by_country",
            wants_analysis=True,
        )

    def draft_sql(parsed: ParsedAsk) -> DraftPlan:
        return DraftPlan(
            sql=(
                "SELECT u.country, AVG(s.duration_minutes) AS avg_duration_minutes "
                "FROM public.sessions s "
                "JOIN public.users u ON s.user_id = u.user_id "
                "GROUP BY u.country "
                "ORDER BY avg_duration_minutes DESC"
            ),
            plain_language="Average session duration by country.",
            definitions=["avg session duration by country"],
            columns=["country", "avg_duration_minutes"],
        )

    runtime = AgentRuntime(
        settings=settings,
        gov=gov,
        store=__import__(
            "data_request_agent.stores", fromlist=["PostgresStore"]
        ).PostgresStore(settings.query_database_url),
        destination=memory_dest,
        proposers=Proposers(parse_ask=parse_ask, draft_sql=draft_sql),
        plan_analysis=heuristic_analysis_plan,
    )
    thread_id = new_thread_id("req-analysis")
    config = {"configurable": {"thread_id": thread_id}}
    with gov.open_checkpointer() as handle:
        graph = build_graph(settings, handle.checkpointer, runtime=runtime)
        invoke_until_interrupt(
            graph,
            {
                "requester_slack_id": "U_REQ",
                "channel_id": "D_REQ",
                "thread_ts": "606.4",
                "raw_text": "analyze average session duration by country",
            },
            config,
        )
        resume(graph, config, "run", "U_REQ")
        final = resume(graph, config, "approve", "U_ADMIN")
        assert final.get("phase") == "delivered"
        assert final.get("analysis_mock") is False
        assert final.get("analysis_answer")
        payload = memory_dest.deliveries[-1]
        assert payload.get("wants_analysis") is True
        assert payload.get("csv") is None
        key = gov.thread_key("D_REQ", "606.4")
        assert gov.get_thread_context(key) is not None


def test_06e_requester_analysis_approve_without_personal(
    settings, gov, memory_dest
):
    """Approve without personal → analysis frame has no device_type."""
    from data_request_agent.analysis import heuristic_analysis_plan
    from data_request_agent.approval import APPROVE_WITHOUT_PERSONAL

    runtime = AgentRuntime(
        settings=settings,
        gov=gov,
        store=__import__(
            "data_request_agent.stores", fromlist=["PostgresStore"]
        ).PostgresStore(settings.query_database_url),
        destination=memory_dest,
        proposers=personal_device_proposers(),
        plan_analysis=heuristic_analysis_plan,
    )
    thread_id = new_thread_id("req-analy-nopii")
    config = {"configurable": {"thread_id": thread_id}}
    with gov.open_checkpointer() as handle:
        graph = build_graph(settings, handle.checkpointer, runtime=runtime)
        invoke_until_interrupt(
            graph,
            {
                "requester_slack_id": "U_REQ",
                "channel_id": "D_REQ",
                "thread_ts": "606.5",
                "raw_text": "analyze users sample with device type",
            },
            config,
        )
        resume(graph, config, "run", "U_REQ")
        final = resume(graph, config, APPROVE_WITHOUT_PERSONAL, "U_ADMIN")
        assert final.get("phase") == "delivered"
        guarded = final.get("guarded_rows") or []
        assert guarded
        assert "device_type" not in guarded[0]
        assert "country" in guarded[0]
        assert "hidden personal" in (final.get("delivery_message") or "").lower()
        assert "device_type" in (final.get("delivery_message") or "").lower()


def test_03d_admin_csv_keeps_approved_personal(settings, gov, memory_dest):
    """Admin CSV path keeps personal columns when the plan envelope covers them."""
    from data_request_agent.delivery import guard_and_deliver

    state = guard_and_deliver(
        {
            "requester_slack_id": "U_ADMIN",
            "channel_id": "D_ADMIN",
            "thread_ts": "303.1",
            "result_rows": [
                {"user_id": 1, "country": "US", "device_type": "iOS"},
            ],
            "plan": {
                "plain_language_plan": "users sample",
                "wants_analysis": False,
                "approved_columns": ["user_id", "country", "device_type"],
            },
            "approved": {
                "status": "approved",
                "plan": {
                    "plain_language_plan": "users sample",
                    "wants_analysis": False,
                    "approved_columns": ["user_id", "country", "device_type"],
                },
            },
        },
        gov=gov,
        destination=memory_dest,
        settings=settings,
    )
    assert state.get("phase") == "delivered"
    payload = memory_dest.deliveries[-1]
    assert payload.get("csv") is not None
    assert "device_type" in (payload.get("rows") or [{}])[0]

    """Low-cardinality personal dims (e.g. 3 devices) stay so charts have an axis."""
    from data_request_agent.analysis import heuristic_analysis_plan
    from data_request_agent.delivery import guard_and_deliver

    state = guard_and_deliver(
        {
            "requester_slack_id": "U_ADMIN",
            "channel_id": "D_ADMIN",
            "thread_ts": "606.2",
            "result_rows": [
                {"device_type": "iOS", "avg_duration_minutes": 12.5},
                {"device_type": "Android", "avg_duration_minutes": 10.0},
                {"device_type": "Web", "avg_duration_minutes": 8.0},
            ],
            "plan": {
                "plain_language_plan": "session duration by device",
                "wants_analysis": True,
                "approved_columns": ["device_type", "avg_duration_minutes"],
                "trial_columns": ["device_type", "avg_duration_minutes"],
            },
            "approved": {
                "status": "approved",
                "plan": {
                    "plain_language_plan": "session duration by device",
                    "wants_analysis": True,
                    "approved_columns": ["device_type", "avg_duration_minutes"],
                },
            },
        },
        gov=gov,
        destination=memory_dest,
        settings=settings,
        plan_analysis=heuristic_analysis_plan,
    )
    assert state.get("phase") == "delivered"
    guarded = state.get("guarded_rows") or []
    assert guarded
    assert "device_type" in guarded[0]
    assert not state.get("hidden_personal")
    payload = memory_dest.deliveries[-1]
    assert payload.get("chart_png") or state.get("analysis_answer")


def test_06c_analysis_strips_high_cardinality_personal_extract(
    settings, gov, memory_dest
):
    """Many distinct personal values (contact-list style) are stripped before analysis."""
    from data_request_agent.analysis import heuristic_analysis_plan
    from data_request_agent.delivery import guard_and_deliver

    rows = [
        {
            "user_id": i,
            "device_type": f"device_{i}",
            "country": "US",
            "avg_duration_minutes": float(i),
        }
        for i in range(1, 15)
    ]
    state = guard_and_deliver(
        {
            "requester_slack_id": "U_ADMIN",
            "channel_id": "D_ADMIN",
            "thread_ts": "606.3",
            "result_rows": rows,
            "plan": {
                "plain_language_plan": "sample with many devices",
                "wants_analysis": True,
                "approved_columns": [
                    "user_id",
                    "country",
                    "device_type",
                    "avg_duration_minutes",
                ],
                "trial_columns": [
                    "user_id",
                    "country",
                    "device_type",
                    "avg_duration_minutes",
                ],
            },
            "approved": {
                "status": "approved",
                "plan": {
                    "plain_language_plan": "sample with many devices",
                    "wants_analysis": True,
                    "approved_columns": [
                        "user_id",
                        "country",
                        "device_type",
                        "avg_duration_minutes",
                    ],
                },
            },
        },
        gov=gov,
        destination=memory_dest,
        settings=settings,
        plan_analysis=heuristic_analysis_plan,
    )
    assert state.get("phase") == "delivered"
    guarded = state.get("guarded_rows") or []
    assert guarded
    assert "device_type" not in guarded[0]
    assert "country" in guarded[0]
    assert "device_type" in (state.get("hidden_personal") or [])


def test_07_results_check_retry_then_honest(settings, gov, runtime, memory_dest):
    """Forced results mismatch → one retry → honest failure (no delivery)."""
    thread_id = new_thread_id("results")
    config = {"configurable": {"thread_id": thread_id}}
    before = len(memory_dest.deliveries)
    with gov.open_checkpointer() as handle:
        graph = build_graph(settings, handle.checkpointer, runtime=runtime)
        invoke_until_interrupt(
            graph,
            {
                "requester_slack_id": "U_ADMIN",
                "channel_id": "D_ADMIN",
                "thread_ts": "70.1",
                "raw_text": "latest_dau",
                "force_results_mismatch": True,
            },
            config,
        )
        final = resume(graph, config, "run", "U_ADMIN")
        assert final.get("phase") == "results_failed"
        assert "tried twice" in (final.get("delivery_message") or "").lower()
        assert len(memory_dest.deliveries) == before
        events = [e["event"] for e in gov.recent_audit(limit=25)]
        assert "results_check_failed" in events


def test_08_expiry_redirect_data_as_of(settings, gov, runtime, memory_dest):
    """Expiry sweep + data-as-of stamp (public redirect unit-tested separately)."""
    # Happy path includes data-as-of
    thread_id = new_thread_id("asof")
    config = {"configurable": {"thread_id": thread_id}}
    with gov.open_checkpointer() as handle:
        graph = build_graph(settings, handle.checkpointer, runtime=runtime)
        invoke_until_interrupt(
            graph,
            {
                "requester_slack_id": "U_ADMIN",
                "channel_id": "D_ADMIN",
                "thread_ts": "80.1",
                "raw_text": "total_revenue_usd",
            },
            config,
        )
        final = resume(graph, config, "run", "U_ADMIN")
        assert final.get("phase") == "delivered"
        assert "Data as of:" in (final.get("delivery_message") or "")

    # Expiry: approve, backdate expires_at, sweep, recheck fails
    from data_request_agent.execution import recheck_and_run
    from data_request_agent.stores import PostgresStore
    from datetime import datetime, timedelta, timezone
    import uuid

    plan = {
        "draft_sql": (
            "SELECT date, dau FROM marts.user_activity_metrics "
            "ORDER BY date DESC LIMIT 1"
        ),
        "plain_language_plan": "latest dau",
        "trial_columns": ["date", "dau"],
        "approved_columns": ["date", "dau"],
        "touches_personal_data": False,
    }
    aid = str(uuid.uuid4())
    gov.create_approval(
        approval_id=aid,
        request_id="expiry-test",
        requester_slack_id="U_REQ",
        channel_id="D_REQ",
        thread_ts="80.2",
        plan=plan,
        touches_personal_data=False,
        expires_at=datetime.now(timezone.utc) + timedelta(
            hours=settings.approval_expiry_hours
        ),
        status="approved",
        decided_by=["U_ADMIN"],
    )
    gov.backdate_approval_expiry(
        aid, hours_ago=settings.approval_expiry_hours + 1
    )
    expired = gov.expire_stale_approvals()
    assert aid in expired
    row = gov.get_approval(aid)
    assert row is not None and row["status"] == "expired"
    state = recheck_and_run(
        {
            "requester_slack_id": "U_REQ",
            "approved": {"approval_id": aid, "plan": plan, "status": "approved"},
            "plan": plan,
        },
        gov=gov,
        store=PostgresStore(settings.query_database_url),
        settings=settings,
    )
    assert state.get("error") == "approval_expired"
    events = [e["event"] for e in gov.recent_audit(limit=15)]
    assert "approval_expired" in events


def test_09_audit_trail_lifecycle(settings, gov, runtime):
    """Full requester lifecycle writes expected audit events."""
    thread_id = new_thread_id("audit")
    config = {"configurable": {"thread_id": thread_id}}
    with gov.open_checkpointer() as handle:
        graph = build_graph(settings, handle.checkpointer, runtime=runtime)
        invoke_until_interrupt(
            graph,
            {
                "requester_slack_id": "U_REQ",
                "channel_id": "D_REQ",
                "thread_ts": "500.1",
                "raw_text": "users_by_country",
            },
            config,
        )
        resume(graph, config, "run", "U_REQ")
        resume(graph, config, "approve", "U_ADMIN")
    events = [e["event"] for e in gov.recent_audit(limit=50)]
    for required in (
        "request_received",
        "plan_ready",
        "approval_requested",
        "approval_approved",
        "executed",
        "delivered",
    ):
        assert required in events, f"missing {required} in {events[:20]}"


def test_10_personal_data_guard_and_analysis_mock(settings, gov, memory_dest):
    """Guard hides unapproved personal cols; analysis branch is real (Stage 3)."""
    from data_request_agent.analysis import heuristic_analysis_plan

    runtime = AgentRuntime(
        settings=settings,
        gov=gov,
        store=__import__(
            "data_request_agent.stores", fromlist=["PostgresStore"]
        ).PostgresStore(settings.query_database_url),
        destination=memory_dest,
        proposers=personal_device_proposers(),
        plan_analysis=heuristic_analysis_plan,
    )
    thread_id = new_thread_id("guard")
    config = {"configurable": {"thread_id": thread_id}}
    with gov.open_checkpointer() as handle:
        graph = build_graph(settings, handle.checkpointer, runtime=runtime)
        first = invoke_until_interrupt(
            graph,
            {
                "requester_slack_id": "U_ADMIN",
                "channel_id": "D_ADMIN",
                "thread_ts": "600.1",
                "raw_text": "users sample with analysis please",
            },
            config,
        )
        # Shrink approved envelope before run: only user_id + country
        # Resume run, then patch plan in checkpoint is hard — instead mutate
        # after plan interrupt by approving with narrowed columns via custom resume
        # Simplest: after plan_preview, the plan is in state; we resume run which
        # auto-approves for admin with full columns. Then test guard by delivering
        # with narrowed approved_columns using delivery function directly PLUS
        # full path asserting analysis mock.

        # Path A — real analysis path (no Stage 1–2 mock):
        final = resume(graph, config, "run", "U_ADMIN")
        assert final.get("phase") == "delivered"
        assert final.get("analysis_mock") is False
        assert final.get("analysis_answer") or "Ran:" in (
            final.get("delivery_message") or ""
        )
        assert memory_dest.deliveries[-1].get("analysis_mock") is False

    # Path B — guard hides personal device_type when not approved
    from data_request_agent.delivery import guard_and_deliver
    from data_request_agent.stores import PostgresStore

    rows = PostgresStore(settings.query_database_url).execute(
        "SELECT user_id, country, device_type FROM public.users ORDER BY user_id LIMIT 5"
    )
    state = guard_and_deliver(
        {
            "requester_slack_id": "U_ADMIN",
            "channel_id": "D_ADMIN",
            "thread_ts": "600.2",
            "result_rows": rows,
            "plan": {
                "plain_language_plan": "sample",
                "approved_columns": ["user_id", "country"],
                "wants_analysis": False,
            },
            "approved": {
                "plan": {
                    "plain_language_plan": "sample",
                    "approved_columns": ["user_id", "country"],
                    "wants_analysis": False,
                }
            },
        },
        gov=gov,
        destination=memory_dest,
    )
    guarded = state.get("guarded_rows") or []
    assert guarded
    assert "device_type" not in guarded[0]
    assert "user_id" in guarded[0]


def test_11_non_admin_approve_refused(settings, gov, runtime, memory_dest):
    """Non-admin Approve is ignored; request stays open for a real admin."""
    thread_id = new_thread_id("noauth")
    config = {"configurable": {"thread_id": thread_id}}
    with gov.open_checkpointer() as handle:
        graph = build_graph(settings, handle.checkpointer, runtime=runtime)
        invoke_until_interrupt(
            graph,
            {
                "requester_slack_id": "U_REQ",
                "channel_id": "D_REQ",
                "thread_ts": "700.1",
                "raw_text": "total_revenue_usd",
            },
            config,
        )
        resume(graph, config, "run", "U_REQ")
        refused = resume(graph, config, "approve", "U_REQ")
        assert refused.get("__interrupt__"), "non-admin approve must keep waiting"
        events = [e["event"] for e in gov.recent_audit(limit=30)]
        assert "approval_refused_non_admin" in events
        assert len(memory_dest.deliveries) == 0

        final = resume(graph, config, "approve", "U_ADMIN")
        assert final.get("phase") == "delivered"
        assert len(memory_dest.deliveries) == 1
        assert "approval_approved" in [e["event"] for e in gov.recent_audit(limit=30)]
