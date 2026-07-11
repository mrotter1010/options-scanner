"""Live integration tests for src/auth/client.py.

These tests make real API calls against the Schwab API and require valid
credentials set in the following environment variables:

    SCHWAB_CLIENT_ID
    SCHWAB_CLIENT_SECRET
    SCHWAB_CALLBACK_URL

They must be run deliberately::

    pytest -m live -v

They are excluded from normal runs via the ``-m 'not live'`` default in
pyproject.toml.

On the first run, schwabdev will open a browser for the OAuth authorization
flow. The callback is captured automatically on port 8182 (matching the
registered callback URL https://127.0.0.1:8182). Your browser may show a
security warning about a self-signed certificate — proceed past it.
"""

from datetime import datetime, timezone

import pytest
from dotenv import load_dotenv

load_dotenv()

import schwabdev

from src.auth.client import get_client


@pytest.mark.live
def test_live_auth_get_client_returns_valid_session():
    client = get_client()
    assert isinstance(client, schwabdev.Client)

    response = client.account_linked()
    assert response.status_code == 200

    # _refresh_token_issued is a private attribute of schwabdev.tokens.Tokens.
    # This access may break if schwabdev changes its internals.
    issued = client.tokens._refresh_token_issued
    if issued is not None:
        age_hours = (datetime.now(timezone.utc) - issued).total_seconds() / 3600
        print(f"\nRefresh token age: {age_hours:.1f} hours")
