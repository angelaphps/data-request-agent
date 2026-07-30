"""Destination seam: SlackThread now, Drive stub, Memory for tests."""

from __future__ import annotations

import io
from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class Destination(ABC):
    @abstractmethod
    def deliver(self, payload: dict[str, Any]) -> str:
        """Send payload; return a destination reference id."""
        ...


class MemoryDestination(Destination):
    """Test double — keeps last delivery in memory."""

    def __init__(self) -> None:
        self.deliveries: list[dict[str, Any]] = []

    def deliver(self, payload: dict[str, Any]) -> str:
        self.deliveries.append(payload)
        return f"memory:{len(self.deliveries)}"


class SlackThreadDestination(Destination):
    def __init__(self, *, bot_token: str) -> None:
        self.bot_token = bot_token

    def deliver(self, payload: dict[str, Any]) -> str:
        from slack_sdk import WebClient

        client = WebClient(token=self.bot_token)
        channel = payload["channel_id"]
        thread_ts = payload.get("thread_ts")
        text = payload.get("text") or "Here are your results."
        csv_text = payload.get("csv")
        filename = payload.get("filename") or "results.csv"
        chart_png = payload.get("chart_png")
        chart_filename = payload.get("chart_filename") or "analysis_chart.png"

        # Ensure we have a DM channel id (D…) for files_upload_v2.
        if not str(channel).startswith(("C", "G", "D")):
            opened = client.conversations_open(users=channel)
            channel = opened["channel"]["id"]

        if chart_png:
            client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)
            buf = io.BytesIO(chart_png)
            resp = client.files_upload_v2(
                channel=channel,
                file=buf,
                filename=chart_filename,
                initial_comment="Chart",
                thread_ts=thread_ts,
            )
            return str(resp.get("file", {}).get("id") or "chart_uploaded")

        if csv_text is not None:
            buf = io.BytesIO(csv_text.encode("utf-8"))
            resp = client.files_upload_v2(
                channel=channel,
                file=buf,
                filename=filename,
                initial_comment=text,
                thread_ts=thread_ts,
            )
            return str(resp.get("file", {}).get("id") or "uploaded")

        resp = client.chat_postMessage(
            channel=channel, text=text, thread_ts=thread_ts
        )
        return str(resp.get("ts") or "posted")


class DriveDestination(Destination):
    """Stub — not wired for the demo."""

    def deliver(self, payload: dict[str, Any]) -> str:
        raise NotImplementedError("Drive destination is a stub")


def rows_to_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    return pd.DataFrame(rows).to_csv(index=False)
