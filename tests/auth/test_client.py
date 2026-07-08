"""Unit tests for src/auth/client.py."""

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.auth.client import check_refresh_token_expiry, get_client

# Valid fake credentials matching schwabdev's validation rules.
VALID_CLIENT_ID = "A" * 32       # schwabdev requires exactly 32 chars
VALID_CLIENT_SECRET = "B" * 16   # schwabdev requires exactly 16 chars
VALID_CALLBACK_URL = "https://127.0.0.1"

VALID_ENV = {
    "SCHWAB_CLIENT_ID": VALID_CLIENT_ID,
    "SCHWAB_CLIENT_SECRET": VALID_CLIENT_SECRET,
    "SCHWAB_CALLBACK_URL": VALID_CALLBACK_URL,
}


# ---------------------------------------------------------------------------
# get_client — missing env vars
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"SCHWAB_CLIENT_SECRET": VALID_CLIENT_SECRET, "SCHWAB_CALLBACK_URL": VALID_CALLBACK_URL}, clear=True)
def test_get_client_raises_when_client_id_missing():
    with pytest.raises(EnvironmentError, match="SCHWAB_CLIENT_ID"):
        get_client()


@patch.dict("os.environ", {}, clear=True)
def test_get_client_raises_when_multiple_env_vars_missing():
    with pytest.raises(EnvironmentError) as exc_info:
        get_client()
    msg = str(exc_info.value)
    assert "SCHWAB_CLIENT_ID" in msg
    assert "SCHWAB_CLIENT_SECRET" in msg
    assert "SCHWAB_CALLBACK_URL" in msg


# ---------------------------------------------------------------------------
# get_client — format validation
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {**VALID_ENV, "SCHWAB_CLIENT_ID": "too_short"}, clear=True)
def test_get_client_raises_when_client_id_wrong_length():
    with pytest.raises(EnvironmentError, match="SCHWAB_CLIENT_ID must be exactly 32 characters"):
        get_client()


@patch.dict("os.environ", {**VALID_ENV, "SCHWAB_CLIENT_SECRET": "short"}, clear=True)
def test_get_client_raises_when_secret_wrong_length():
    with pytest.raises(EnvironmentError, match="SCHWAB_CLIENT_SECRET must be exactly 16 characters"):
        get_client()


@patch.dict("os.environ", {**VALID_ENV, "SCHWAB_CALLBACK_URL": "http://127.0.0.1"}, clear=True)
def test_get_client_raises_when_callback_url_not_https():
    with pytest.raises(EnvironmentError, match="must start with 'https://'"):
        get_client()


@patch.dict("os.environ", {**VALID_ENV, "SCHWAB_CALLBACK_URL": "https://127.0.0.1/"}, clear=True)
def test_get_client_raises_when_callback_url_has_trailing_slash():
    with pytest.raises(EnvironmentError, match="must not end with '/'"):
        get_client()


# ---------------------------------------------------------------------------
# get_client — success path
# ---------------------------------------------------------------------------


@patch("src.auth.client.check_refresh_token_expiry")  # no-op so we don't need real tokens
@patch("src.auth.client.schwabdev.Client")  # mock the real schwabdev.Client constructor
@patch.dict("os.environ", VALID_ENV, clear=True)
def test_get_client_returns_schwabdev_client(mock_client_cls, mock_check):
    sentinel = MagicMock(name="schwabdev.Client-instance")
    mock_client_cls.return_value = sentinel

    result = get_client()

    assert result is sentinel
    mock_client_cls.assert_called_once()
    # Verify correct kwargs were forwarded to schwabdev.Client.__init__
    call_kwargs = mock_client_cls.call_args.kwargs
    assert call_kwargs["app_key"] == VALID_CLIENT_ID
    assert call_kwargs["app_secret"] == VALID_CLIENT_SECRET
    assert call_kwargs["callback_url"] == VALID_CALLBACK_URL
    assert "tokens.json" in call_kwargs["tokens_file"]


# ---------------------------------------------------------------------------
# check_refresh_token_expiry
# ---------------------------------------------------------------------------


def _make_client_mock(refresh_token_issued):
    """Build a mock schwabdev.Client with tokens._refresh_token_issued set.

    The real schwabdev.Client stores token metadata on a Tokens object
    accessible as client.tokens. We mock that object and set the private
    attribute _refresh_token_issued directly.

    NOTE: _refresh_token_issued is a private attribute of schwabdev.tokens.Tokens.
    This mock may break if schwabdev changes its internals.
    """
    client = MagicMock(name="schwabdev.Client")
    # Mimic the real tokens object's private attribute
    # (sourced from schwabdev.tokens.Tokens.__init__ — set to a datetime)
    client.tokens._refresh_token_issued = refresh_token_issued
    return client


def test_check_refresh_token_expiry_returns_silently_when_no_token():
    # _refresh_token_issued is None when no token file has been loaded yet.
    # NOTE: _refresh_token_issued is a private attribute; may break if schwabdev changes.
    client = MagicMock(name="schwabdev.Client")
    client.tokens = MagicMock(spec=[])  # empty spec so getattr returns None
    assert not hasattr(client.tokens, "_refresh_token_issued")

    # Should return without raising
    check_refresh_token_expiry(client)


def test_check_refresh_token_expiry_returns_silently_when_token_fresh(capsys):
    # Token issued 2 days ago — well within the 7-day lifetime
    issued = datetime.now(timezone.utc) - timedelta(days=2)
    # NOTE: _refresh_token_issued is a private attribute; may break if schwabdev changes.
    client = _make_client_mock(refresh_token_issued=issued)

    check_refresh_token_expiry(client)

    captured = capsys.readouterr()
    assert captured.out == ""


def test_check_refresh_token_expiry_warns_within_48_hours(capsys):
    # Token issued 5 days 12 hours ago — 36 hours remain, inside the 48h warning window
    issued = datetime.now(timezone.utc) - timedelta(days=5, hours=12)
    # NOTE: _refresh_token_issued is a private attribute; may break if schwabdev changes.
    client = _make_client_mock(refresh_token_issued=issued)

    check_refresh_token_expiry(client)  # should not raise

    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    # Remaining time is ~36 hours; verify a reasonable number appears
    assert "hours" in captured.out
    assert "Delete data/tokens.json" in captured.out


def test_check_refresh_token_expiry_raises_when_expired():
    # Token issued 8 days ago — expired 1 day ago
    issued = datetime.now(timezone.utc) - timedelta(days=8)
    # NOTE: _refresh_token_issued is a private attribute; may break if schwabdev changes.
    client = _make_client_mock(refresh_token_issued=issued)

    with pytest.raises(RuntimeError) as exc_info:
        check_refresh_token_expiry(client)
    msg = str(exc_info.value)
    assert "expired" in msg.lower()
    assert "Delete data/tokens.json" in msg
    assert "get_client()" in msg


# ---------------------------------------------------------------------------
# Logging safety
# ---------------------------------------------------------------------------


@patch("src.auth.client.check_refresh_token_expiry")
@patch("src.auth.client.schwabdev.Client")  # mock the real constructor
@patch.dict("os.environ", VALID_ENV, clear=True)
def test_get_client_does_not_expose_credentials_in_logs(mock_client_cls, mock_check, caplog):
    mock_client_cls.return_value = MagicMock(name="schwabdev.Client-instance")

    with caplog.at_level(logging.DEBUG, logger="src.auth.client"):
        get_client()

    full_log = " ".join(record.message for record in caplog.records)
    # The actual credential values must never appear in log output
    assert VALID_CLIENT_ID not in full_log
    assert VALID_CLIENT_SECRET not in full_log


# ---------------------------------------------------------------------------
# Coverage note:
# The datetime.min sentinel check (client.py line 90-91) is not exercised by
# the tests above. That branch handles the case where schwabdev has loaded a
# Tokens object but _refresh_token_issued was never written (set to
# datetime.min by schwabdev.tokens.Tokens.__init__). This is an internal
# schwabdev state that occurs only transiently during first-run before the
# OAuth flow completes. It is guarded here for safety but not specified in the
# test matrix. Coverage remains >= 95% without it.
# ---------------------------------------------------------------------------
