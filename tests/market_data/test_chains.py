"""Unit tests for src/market_data/chains.py.

Import convention: tests import from src.* (no pythonpath override in
pyproject.toml), matching the pattern established in tests/auth/test_client.py.

Fixtures (captured 2026-07-10 via live Schwab API calls):
  - fixtures/chain_valid_small.json  — AAPL PUT, strikeCount=4, 45 days out.
    Used by: success-path get_chain tests.
  - fixtures/chain_empty.json        — VTSAX PUT, 45 days out (mutual fund,
    non-optionable). numberOfContracts=0.
    Used by: empty-chain / NoOptionsDataError tests.
  - fixtures/chain_error_400.json    — ZZZZZ PUT (nonexistent symbol), 400
    error body.
    Used by: 400-status tests.

429 handling note: There is no captured fixture for a 429 response. Deliberately
triggering rate limiting just to capture an unused body is not worth spamming the
API. The 429 retry logic branches on status_code only and never inspects the
response body, so a bare MagicMock(status_code=429) is safe and sufficient.
"""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from src.market_data.chains import (
    ChainFetchError,
    NoOptionsDataError,
    get_chain,
    get_chains,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name):
    return json.loads((FIXTURES / name).read_text())


def _mock_response(status_code, json_data=None):
    """Build a MagicMock that behaves like requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


# ---------------------------------------------------------------------------
# get_chain tests
# ---------------------------------------------------------------------------


@patch("src.market_data.chains.time.sleep")
def test_get_chain_success_returns_parsed_dict(mock_sleep):
    fixture = _load_fixture("chain_valid_small.json")
    client = MagicMock()
    client.option_chains.return_value = _mock_response(200, fixture)

    result = get_chain(client, "AAPL")

    assert isinstance(result, dict)
    assert result["symbol"] == "AAPL"
    assert result["numberOfContracts"] > 0
    assert "putExpDateMap" in result


@patch("src.market_data.chains.time.sleep")
def test_get_chain_passes_correct_default_params(mock_sleep):
    fixture = _load_fixture("chain_valid_small.json")
    client = MagicMock()
    client.option_chains.return_value = _mock_response(200, fixture)

    get_chain(client, "AAPL")

    client.option_chains.assert_called_once()
    kwargs = client.option_chains.call_args.kwargs
    assert kwargs["symbol"] == "AAPL"
    assert kwargs["contractType"] == "PUT"
    # toDate should be roughly 45 days from now (check it's a datetime)
    from datetime import datetime, timedelta

    expected_min = datetime.now() + timedelta(days=44)
    expected_max = datetime.now() + timedelta(days=46)
    assert expected_min <= kwargs["toDate"] <= expected_max


@patch("src.market_data.chains.time.sleep")
def test_get_chain_respects_custom_contract_type_and_days_out(mock_sleep):
    fixture = _load_fixture("chain_valid_small.json")
    client = MagicMock()
    client.option_chains.return_value = _mock_response(200, fixture)

    get_chain(client, "AAPL", contract_type="CALL", days_out=90)

    kwargs = client.option_chains.call_args.kwargs
    assert kwargs["contractType"] == "CALL"
    from datetime import datetime, timedelta

    expected_min = datetime.now() + timedelta(days=89)
    expected_max = datetime.now() + timedelta(days=91)
    assert expected_min <= kwargs["toDate"] <= expected_max


@patch("src.market_data.chains.time.sleep")
def test_get_chain_raises_no_options_data_error_on_empty_chain(mock_sleep):
    fixture = _load_fixture("chain_empty.json")
    client = MagicMock()
    client.option_chains.return_value = _mock_response(200, fixture)

    with pytest.raises(NoOptionsDataError, match="numberOfContracts=0"):
        get_chain(client, "VTSAX")


@patch("src.market_data.chains.time.sleep")
def test_get_chain_raises_no_options_data_error_on_400(mock_sleep):
    fixture = _load_fixture("chain_error_400.json")
    client = MagicMock()
    client.option_chains.return_value = _mock_response(400, fixture)

    with pytest.raises(NoOptionsDataError, match="400 response"):
        get_chain(client, "ZZZZZ")


@patch("src.market_data.chains.time.sleep")
def test_get_chain_raises_chain_fetch_error_on_unexpected_status(mock_sleep):
    client = MagicMock()
    client.option_chains.return_value = _mock_response(500)

    with pytest.raises(ChainFetchError, match="unexpected status 500"):
        get_chain(client, "SPY")


@patch("src.market_data.chains.time.sleep")
def test_get_chain_retries_once_on_429_then_succeeds(mock_sleep):
    fixture = _load_fixture("chain_valid_small.json")
    client = MagicMock()
    client.option_chains.side_effect = [
        _mock_response(429),
        _mock_response(200, fixture),
    ]

    result = get_chain(client, "AAPL")

    assert result["symbol"] == "AAPL"
    assert client.option_chains.call_count == 2
    mock_sleep.assert_called_once_with(60)


@patch("src.market_data.chains.time.sleep")
def test_get_chain_raises_chain_fetch_error_after_second_429(mock_sleep):
    client = MagicMock()
    client.option_chains.side_effect = [
        _mock_response(429),
        _mock_response(429),
    ]

    with pytest.raises(ChainFetchError, match="rate limited after retry"):
        get_chain(client, "SPY")

    # Sleep called exactly once (between attempt 1 and 2), not after the raise
    mock_sleep.assert_called_once_with(60)


@patch("src.market_data.chains.time.sleep")
def test_get_chain_logs_warning_when_chain_truncated(mock_sleep, caplog):
    fixture = _load_fixture("chain_valid_small.json")
    # Patch the fixture to have isChainTruncated=True
    fixture = {**fixture, "isChainTruncated": True}
    client = MagicMock()
    client.option_chains.return_value = _mock_response(200, fixture)

    with caplog.at_level(logging.WARNING, logger="src.market_data.chains"):
        result = get_chain(client, "AAPL")

    # Should still return data
    assert result["symbol"] == "AAPL"
    assert result["numberOfContracts"] > 0
    # Should log a truncation warning
    assert any("truncated" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# get_chains tests
# ---------------------------------------------------------------------------


@patch("src.market_data.chains.time.sleep")
@patch("src.market_data.chains.get_chain")
def test_get_chains_returns_dict_excluding_skipped_tickers(mock_get_chain, mock_sleep):
    spy_data = {"symbol": "SPY", "numberOfContracts": 100}
    mock_get_chain.side_effect = [
        spy_data,
        NoOptionsDataError("VTSAX: no options"),
    ]

    result = get_chains(MagicMock(), ["SPY", "VTSAX"])

    assert list(result.keys()) == ["SPY"]
    assert result["SPY"] is spy_data


@patch("src.market_data.chains.time.sleep")
@patch("src.market_data.chains.get_chain")
def test_get_chains_raises_chain_fetch_error_when_all_tickers_fail(
    mock_get_chain, mock_sleep
):
    mock_get_chain.side_effect = NoOptionsDataError("bad ticker")

    with pytest.raises(ChainFetchError, match="All 2 tickers failed"):
        get_chains(MagicMock(), ["VTSAX", "ZZZZZ"])


@patch("src.market_data.chains.time.sleep")
@patch("src.market_data.chains.get_chain")
def test_get_chains_empty_ticker_list_returns_empty_dict_no_calls_made(
    mock_get_chain, mock_sleep
):
    result = get_chains(MagicMock(), [])

    assert result == {}
    mock_get_chain.assert_not_called()
    mock_sleep.assert_not_called()


@patch("src.market_data.chains.time.sleep")
@patch("src.market_data.chains.get_chain")
def test_get_chains_propagates_systemic_error_and_halts_remaining_tickers(
    mock_get_chain, mock_sleep
):
    spy_data = {"symbol": "SPY", "numberOfContracts": 100}
    mock_get_chain.side_effect = [
        spy_data,
        ChainFetchError("rate limited after retry"),
    ]

    with pytest.raises(ChainFetchError, match="rate limited"):
        get_chains(MagicMock(), ["SPY", "AAPL", "QQQ"])

    # Only SPY and AAPL were attempted; QQQ was never reached
    assert mock_get_chain.call_count == 2


@patch("src.market_data.chains.time.sleep")
@patch("src.market_data.chains.get_chain")
def test_get_chains_sleeps_delay_seconds_between_every_call_including_last(
    mock_get_chain, mock_sleep
):
    mock_get_chain.return_value = {"symbol": "X", "numberOfContracts": 1}

    get_chains(MagicMock(), ["SPY", "AAPL", "QQQ"])

    # Default delay is 0.5, sleep called once per ticker (including last)
    assert mock_sleep.call_count == 3
    mock_sleep.assert_has_calls([call(0.5), call(0.5), call(0.5)])


@patch("src.market_data.chains.time.sleep")
@patch("src.market_data.chains.get_chain")
def test_get_chains_uses_custom_delay_seconds(mock_get_chain, mock_sleep):
    mock_get_chain.return_value = {"symbol": "X", "numberOfContracts": 1}

    get_chains(MagicMock(), ["SPY", "AAPL"], delay_seconds=1.5)

    assert mock_sleep.call_count == 2
    mock_sleep.assert_has_calls([call(1.5), call(1.5)])
