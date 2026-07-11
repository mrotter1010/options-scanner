"""Options chain retrieval from Schwab API"""

import logging
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class ChainFetchError(Exception):
    """Base exception for chain fetch failures. Raised for systemic issues:
    repeated 429, unexpected status codes, or when every ticker in a batch
    fails. Not caught by get_chains — halts the run."""


class NoOptionsDataError(ChainFetchError):
    """Raised for a single ticker with no usable options data — invalid
    symbol or non-optionable symbol. Caught and skipped by get_chains."""


def get_chain(client, ticker, contract_type="PUT", days_out=45):
    """Fetch an options chain for a single ticker.

    Returns the parsed JSON dict on success. Raises NoOptionsDataError for
    invalid/non-optionable symbols, ChainFetchError for systemic failures.
    """
    to_date = datetime.now() + timedelta(days=days_out)

    attempt = 0
    while True:
        attempt += 1
        resp = client.option_chains(
            symbol=ticker,
            contractType=contract_type,
            toDate=to_date,
        )
        if resp.status_code == 429:
            if attempt >= 2:
                raise ChainFetchError(f"{ticker}: rate limited after retry")
            logger.warning(f"{ticker}: rate limited (429), backing off 60s before retry")
            time.sleep(60)
            continue
        break

    if resp.status_code == 400:
        raise NoOptionsDataError(f"{ticker}: invalid or non-optionable symbol (400 response)")

    if resp.status_code != 200:
        raise ChainFetchError(f"{ticker}: unexpected status {resp.status_code}")

    data = resp.json()

    if data.get("numberOfContracts", 0) == 0:
        raise NoOptionsDataError(f"{ticker}: no options contracts available (numberOfContracts=0)")

    if data.get("isChainTruncated") is True:
        logger.warning(f"{ticker}: chain response is truncated (isChainTruncated=True)")

    return data


def get_chains(client, tickers, delay_seconds=0.5):
    """Fetch options chains for multiple tickers, skipping non-optionable ones.

    Returns a dict mapping ticker -> chain data. Tickers that raise
    NoOptionsDataError are logged and skipped. If every ticker fails,
    raises ChainFetchError (likely a systemic issue).
    """
    results = {}
    for ticker in tickers:
        try:
            results[ticker] = get_chain(client, ticker)
        except NoOptionsDataError as e:
            logger.warning(str(e))
        time.sleep(delay_seconds)

    if tickers and not results:
        raise ChainFetchError(
            f"All {len(tickers)} tickers failed to return chain data — this is "
            f"very likely a systemic issue (auth, network, or malformed request), "
            f"not {len(tickers)} coincidentally bad tickers. Check logs above for "
            f"the underlying errors."
        )

    return results
