"""Expire stale approvals and optionally notify requesters."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_request_agent.config import Settings
from data_request_agent.governance import Governance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Post a Slack DM when an approval expires (requires bot token)",
    )
    args = parser.parse_args()
    settings = Settings()
    gov = Governance(settings.database_url)
    expired = gov.expire_stale_approvals()
    print(f"expired={len(expired)} ids={expired}")
    if args.notify and expired and settings.slack_bot_token:
        from slack_sdk import WebClient

        client = WebClient(token=settings.slack_bot_token)
        for approval_id in expired:
            row = gov.get_approval(approval_id)
            if not row:
                continue
            try:
                client.chat_postMessage(
                    channel=row["channel_id"],
                    thread_ts=row["thread_ts"],
                    text=(
                        f"This data-request approval expired after "
                        f"{settings.approval_expiry_hours} hours. "
                        "Message me again if you still need the data."
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                print(f"notify_failed id={approval_id} err={exc}")


if __name__ == "__main__":
    main()
