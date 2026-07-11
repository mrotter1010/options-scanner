"""Live integration tests for src/market_data/chains.py.

These tests make real API calls against the Schwab API and require valid
credentials set in the following environment variables:

    SCHWAB_CLIENT_ID
    SCHWAB_CLIENT_SECRET
    SCHWAB_CALLBACK_URL

They must be run deliberately::

    pytest -m live -v

They are excluded from normal runs via the ``-m 'not live'`` default in
pyproject.toml.
"""

import pytest
from dotenv import load_dotenv

load_dotenv()

from src.auth.client import get_client
from src.market_data.chains import NoOptionsDataError, get_chain


@pytest.fixture(scope="module")
def client():
    return get_client()


@pytest.mark.live
def test_get_chain_live_returns_real_data_with_greeks(client):
    data = get_chain(client, "SPY")

    assert data["numberOfContracts"] > 0
    assert data["putExpDateMap"]

    # Drill into first expiration, first strike, first contract
    first_exp = next(iter(data["putExpDateMap"]))
    strikes = data["putExpDateMap"][first_exp]
    first_strike = next(iter(strikes))
    contract = strikes[first_strike][0]

    # Greeks must be present as keys
    for greek in ("delta", "gamma", "theta", "vega", "rho"):
        assert greek in contract, f"Missing Greek: {greek}"

    # Standard contract fields
    for field in ("bid", "ask", "strikePrice", "expirationDate"):
        assert field in contract, f"Missing field: {field}"


@pytest.mark.live
def test_get_chain_live_raises_no_options_data_error_for_non_optionable_symbol(client):
    with pytest.raises(NoOptionsDataError):
        get_chain(client, "VTSAX")
