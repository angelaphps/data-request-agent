"""Thin Bolt adapter — no business logic; forward events into the graph.

Identity comes only from the verified Slack event payload. Approve-authority
checks belong in graph/governance nodes (Stage 1.6), not here.

UX: plan preview uses Submit/Cancel; interactive messages are updated in place
so buttons disappear and the chosen status is visible.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import Command
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from data_request_agent.config import Settings
from data_request_agent.intake import PUBLIC_REDIRECT

logger = logging.getLogger(__name__)

# action_id values are stable Slack identifiers; button labels may change.
ACTION_RUN = "plan_run"  # labeled "Submit" in the UI
ACTION_CANCEL = "plan_cancel"
ACTION_APPROVE = "admin_approve"
ACTION_APPROVE_NO_PERSONAL = "admin_approve_no_personal"
ACTION_REJECT = "admin_reject"


class SlackApp:
    def __init__(self, settings: Settings, graph: Any) -> None:
        self.settings = settings
        self.graph = graph
        self.app = App(
            token=settings.slack_bot_token,
            signing_secret=settings.slack_signing_secret or None,
        )
        self._register_handlers()

    def _register_handlers(self) -> None:
        @self.app.event("app_mention")
        def on_mention(event: dict[str, Any], say: Any) -> None:
            say(PUBLIC_REDIRECT)

        @self.app.event("message")
        def on_message(event: dict[str, Any], say: Any, client: Any) -> None:
            if event.get("bot_id") or event.get("subtype"):
                return
            channel = event.get("channel") or ""
            if not channel.startswith("D"):
                say(PUBLIC_REDIRECT)
                return
            user = event.get("user")
            if not user:
                return
            text = event.get("text") or ""
            thread_ts = event.get("thread_ts") or event.get("ts")
            graph_thread_id = f"{channel}:{thread_ts}"
            config = {"configurable": {"thread_id": graph_thread_id}}

            snapshot = self.graph.get_state(config)
            if snapshot.next:
                result = self.graph.invoke(
                    Command(resume={"text": text, "actor_slack_id": user}),
                    config,
                )
            else:
                result = self.graph.invoke(
                    {
                        "requester_slack_id": user,
                        "channel_id": channel,
                        "thread_ts": thread_ts,
                        "raw_text": text,
                    },
                    config,
                )
            self._handle_result(
                result,
                say=say,
                client=client,
                thread_ts=thread_ts,
                graph_thread_id=graph_thread_id,
                source_channel=channel,
            )

        def _resume(
            ack: Any, body: dict[str, Any], say: Any, client: Any, action: str
        ) -> None:
            ack()
            user = (body.get("user") or {}).get("id") or ""
            actions = body.get("actions") or []
            graph_thread_id = (actions[0] or {}).get("value") if actions else None
            if not graph_thread_id:
                return

            msg_channel = (body.get("channel") or {}).get("id")
            message = body.get("message") or {}
            msg_ts = message.get("ts")
            body_blocks = message.get("blocks") or []

            # Immediate feedback: strip buttons, show in-progress status.
            if msg_channel and msg_ts:
                _update_interactive_message(
                    client,
                    channel=msg_channel,
                    ts=msg_ts,
                    original_blocks=body_blocks,
                    status=_pending_status_for_action(action, user),
                )

            config = {"configurable": {"thread_id": graph_thread_id}}
            result = self.graph.invoke(
                Command(resume={"action": action, "actor_slack_id": user}),
                config,
            )

            # Finalize the clicked message once the graph answers.
            if msg_channel and msg_ts:
                final_status = _final_status_for_result(
                    action=action, actor=user, result=result
                )
                _update_interactive_message(
                    client,
                    channel=msg_channel,
                    ts=msg_ts,
                    original_blocks=body_blocks,
                    status=final_status,
                )

            state_vals = (self.graph.get_state(config).values) or {}
            requester_channel = state_vals.get("channel_id")
            requester_thread = state_vals.get("thread_ts")

            self._handle_result(
                result,
                say=say,
                client=client,
                thread_ts=requester_thread or message.get("thread_ts") or msg_ts,
                graph_thread_id=graph_thread_id,
                source_channel=msg_channel,
                requester_channel=requester_channel,
                requester_thread=requester_thread,
                action=action,
                actor=user,
            )

        @self.app.action(ACTION_RUN)
        def on_run(ack, body, say, client):  # noqa: ANN001
            _resume(ack, body, say, client, "run")

        @self.app.action(ACTION_CANCEL)
        def on_cancel(ack, body, say, client):  # noqa: ANN001
            _resume(ack, body, say, client, "cancel")

        @self.app.action(ACTION_APPROVE)
        def on_approve(ack, body, say, client):  # noqa: ANN001
            _resume(ack, body, say, client, "approve")

        @self.app.action(ACTION_APPROVE_NO_PERSONAL)
        def on_approve_no_personal(ack, body, say, client):  # noqa: ANN001
            from data_request_agent.approval import APPROVE_WITHOUT_PERSONAL

            _resume(ack, body, say, client, APPROVE_WITHOUT_PERSONAL)

        @self.app.action(ACTION_REJECT)
        def on_reject(ack, body, say, client):  # noqa: ANN001
            _resume(ack, body, say, client, "reject")

    def _handle_result(
        self,
        result: dict[str, Any],
        *,
        say: Any,
        client: Any,
        thread_ts: str | None,
        graph_thread_id: str,
        source_channel: str | None = None,
        requester_channel: str | None = None,
        requester_thread: str | None = None,
        action: str | None = None,
        actor: str | None = None,
    ) -> None:
        interrupts = result.get("__interrupt__") or []
        if interrupts:
            payload = interrupts[0].value
            kind = payload.get("kind")
            if kind == "clarify":
                say(
                    text=payload.get("prompt") or "Please clarify.",
                    thread_ts=thread_ts,
                )
                return
            if kind == "plan_preview":
                say(
                    text="Plan ready — Submit or Cancel.",
                    thread_ts=thread_ts,
                    blocks=_preview_blocks(payload, graph_thread_id),
                )
                return
            if kind == "admin_approval":
                channel = self.settings.admin_channel_id
                if not channel:
                    _post(
                        client,
                        channel=requester_channel or source_channel,
                        thread_ts=requester_thread or thread_ts,
                        text="Admin channel not configured; cannot request approval.",
                    )
                    return
                notice = payload.get("notice")
                if notice:
                    client.chat_postMessage(channel=channel, text=notice)
                # Fresh card only when first requesting approval (or after a
                # refused non-admin click that re-opens the wait).
                client.chat_postMessage(
                    channel=channel,
                    text="Data request awaiting approval",
                    blocks=_approval_blocks(payload, graph_thread_id),
                )
                # First submit only — don't re-spam requester after a refused click.
                if not payload.get("notice"):
                    _post(
                        client,
                        channel=requester_channel or source_channel,
                        thread_ts=requester_thread or thread_ts,
                        text=(
                            "Submitted — waiting for an administrator to approve. "
                            "I'll update this thread when there's a decision."
                        ),
                    )
                return
            say(text=f"Paused: {payload}", thread_ts=thread_ts)
            return

        phase = result.get("phase")
        msg = result.get("delivery_message")

        # Admin decision: confirm in admin context; notify requester thread.
        if action in {"approve", "approve_without_personal", "reject"} and requester_channel:
            if phase == "delivered":
                without = action == "approve_without_personal"
                admin_note = (
                    "Approved without personal data — results sent to the requester "
                    "(personal columns stripped)."
                    if without
                    else "Approved — results were sent to the requester's thread."
                )
                req_note = (
                    "Your request was approved without personal data — "
                    "results are in this thread (personal columns were removed)."
                    if without
                    else "Your request was approved — results are in this thread."
                )
                _post(
                    client,
                    channel=source_channel,
                    thread_ts=None,
                    text=admin_note,
                )
                _post(
                    client,
                    channel=requester_channel,
                    thread_ts=requester_thread,
                    text=req_note,
                )
                return
            if phase == "rejected":
                _post(
                    client,
                    channel=requester_channel,
                    thread_ts=requester_thread,
                    text=msg or "Your request was declined by an administrator.",
                )
                return
            if phase == "cancelled":
                return

        # Requester Submit as admin (auto-approved) or Cancel / other completes.
        if phase == "delivered":
            # Destination already posted the file in the requester thread.
            return
        if msg:
            _post(
                client,
                channel=requester_channel or source_channel,
                thread_ts=requester_thread or thread_ts,
                text=msg,
            )
            return
        say(text=f"Done ({phase}).", thread_ts=thread_ts)

    def start(self) -> None:
        if not self.settings.slack_app_token:
            raise RuntimeError("SLACK_APP_TOKEN is required for Socket Mode")
        handler = SocketModeHandler(self.app, self.settings.slack_app_token)
        logger.info("Starting Slack Socket Mode")
        handler.start()


def _post(
    client: Any,
    *,
    channel: str | None,
    thread_ts: str | None,
    text: str,
) -> None:
    if not channel or not text:
        return
    kwargs: dict[str, Any] = {"channel": channel, "text": text}
    if thread_ts:
        kwargs["thread_ts"] = thread_ts
    try:
        client.chat_postMessage(**kwargs)
    except Exception:  # noqa: BLE001
        logger.exception("slack post failed channel=%s", channel)


def _pending_status_for_action(action: str, user: str) -> str:
    mention = f"<@{user}>" if user else "someone"
    if action == "run":
        return f"_Submitted by {mention} — working…_"
    if action == "cancel":
        return f"_Cancelled by {mention}…_"
    if action == "approve":
        return f"_Approve clicked by {mention} — checking…_"
    if action == "approve_without_personal":
        return f"_Approve without personal data clicked by {mention} — checking…_"
    if action == "reject":
        return f"_Reject clicked by {mention} — checking…_"
    return f"_Working ({action})…_"


def _final_status_for_result(*, action: str, actor: str, result: dict[str, Any]) -> str:
    mention = f"<@{actor}>" if actor else "someone"
    phase = result.get("phase")
    interrupts = result.get("__interrupt__") or []
    if interrupts:
        kind = (interrupts[0].value or {}).get("kind")
        if kind == "admin_approval":
            notice = (interrupts[0].value or {}).get("notice")
            if notice and action in {"approve", "approve_without_personal"}:
                return f"⚠️ {notice}"
            return f"✅ *Submitted* by {mention} — pending administrator approval"
        if kind == "plan_preview":
            return f"_Waiting for Submit/Cancel…_"

    if action == "run" and phase in {"approved", "executed", "results_ok", "delivered"}:
        return f"✅ *Submitted* by {mention} — running / complete"
    if action == "cancel" or phase == "cancelled":
        return f"🚫 *Cancelled* by {mention}"
    if action == "approve_without_personal" and phase in {
        "approved",
        "executed",
        "results_ok",
        "delivered",
    }:
        return f"✅ *Approved without personal data* by {mention}"
    if action == "approve" and phase in {
        "approved",
        "executed",
        "results_ok",
        "delivered",
    }:
        return f"✅ *Approved* by {mention}"
    if action == "reject" or phase == "rejected":
        return f"🚫 *Rejected* by {mention}"
    if phase == "approval_refused":
        return (
            f"⚠️ Only administrators can approve. "
            f"Click by {mention} was recorded and ignored — still waiting."
        )
    return f"_{phase or action} — {mention}_"


def _content_blocks_without_actions(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [b for b in blocks if b.get("type") != "actions"]


def _update_interactive_message(
    client: Any,
    *,
    channel: str,
    ts: str,
    original_blocks: list[dict[str, Any]],
    status: str,
) -> None:
    """Replace buttons with a status line; keep the plan/request body."""
    content = _content_blocks_without_actions(original_blocks)
    if not content:
        content = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "Data request"},
            }
        ]
    status_block = {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": status}],
    }
    try:
        client.chat_update(
            channel=channel,
            ts=ts,
            text=status,
            blocks=[*content, status_block],
        )
    except Exception:  # noqa: BLE001
        logger.exception("chat_update failed channel=%s ts=%s", channel, ts)


def _preview_blocks(payload: dict[str, Any], graph_thread_id: str) -> list[dict[str, Any]]:
    plan = payload.get("plain_language_plan") or ""
    cols = ", ".join(payload.get("trial_columns") or [])
    est = payload.get("trial_row_estimate")
    personal = payload.get("touches_personal_data")
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Plan*\n{plan}\n\n"
                    f"*Columns:* {cols or '(none)'}\n"
                    f"*Estimated rows:* {est}\n"
                    f"*Personal data:* {'yes' if personal else 'no'}"
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": ACTION_RUN,
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "Submit"},
                    "value": graph_thread_id,
                },
                {
                    "type": "button",
                    "action_id": ACTION_CANCEL,
                    "text": {"type": "plain_text", "text": "Cancel"},
                    "value": graph_thread_id,
                },
            ],
        },
    ]


def _approval_blocks(payload: dict[str, Any], graph_thread_id: str) -> list[dict[str, Any]]:
    ask = payload.get("verbatim_ask") or ""
    plan = payload.get("plain_language_plan") or ""
    cols = ", ".join(payload.get("trial_columns") or [])
    est = payload.get("trial_row_estimate")
    personal = payload.get("touches_personal_data")
    elements: list[dict[str, Any]] = [
        {
            "type": "button",
            "action_id": ACTION_APPROVE,
            "style": "primary",
            "text": {"type": "plain_text", "text": "Approve"},
            "value": graph_thread_id,
        },
    ]
    if personal:
        elements.append(
            {
                "type": "button",
                "action_id": ACTION_APPROVE_NO_PERSONAL,
                "text": {
                    "type": "plain_text",
                    "text": "Approve without personal data",
                },
                "value": graph_thread_id,
            }
        )
    elements.append(
        {
            "type": "button",
            "action_id": ACTION_REJECT,
            "style": "danger",
            "text": {"type": "plain_text", "text": "Reject"},
            "value": graph_thread_id,
        }
    )
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Request (verbatim)*\n>{ask}\n\n"
                    f"*Plan*\n{plan}\n\n"
                    f"*Columns:* {cols}\n"
                    f"*Estimated rows:* {est}\n"
                    f"*Personal data flagged:* {'yes' if personal else 'no'}\n"
                    "_No data rows are shown on this card._"
                    + (
                        "\n_Use “Approve without personal data” to release the "
                        "file with personal columns removed._"
                        if personal
                        else ""
                    )
                ),
            },
        },
        {"type": "actions", "elements": elements},
    ]


def create_slack_app(*, settings: Settings, graph: Any) -> SlackApp:
    return SlackApp(settings=settings, graph=graph)
