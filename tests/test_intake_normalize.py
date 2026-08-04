"""Intake helpers — Slack text normalization."""

from data_request_agent.intake import normalize_slack_text


def test_normalize_slack_italic_wrappers():
    assert (
        normalize_slack_text("_session duration trend by device_")
        == "session duration trend by device"
    )
    assert (
        normalize_slack_text("_list of users with country and device most recent 20_")
        == "list of users with country and device most recent 20"
    )
    assert normalize_slack_text("*bold ask*") == "bold ask"
    assert normalize_slack_text("plain ask") == "plain ask"
    assert normalize_slack_text("_partial") == "_partial"
