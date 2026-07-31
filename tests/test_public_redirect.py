"""Public-channel redirect: DMs only; no query work in shared channels."""

from data_request_agent.intake import PUBLIC_REDIRECT, is_dm_channel


def test_public_redirect_copy_mentions_private():
    assert "privately" in PUBLIC_REDIRECT.lower()


def test_dm_channels_accepted():
    assert is_dm_channel("D0123ABC")
    assert is_dm_channel("D0B9N7SB1CH")


def test_public_and_private_channels_redirect():
    assert not is_dm_channel("C0123PUBLIC")
    assert not is_dm_channel("G0123GROUP")
    assert not is_dm_channel("")
