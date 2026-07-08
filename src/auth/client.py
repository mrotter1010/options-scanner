"""Schwab API authentication client wrapper."""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import schwabdev

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TOKENS_FILE = PROJECT_ROOT / "data" / "tokens.json"

_REQUIRED_ENV_VARS = (
    "SCHWAB_CLIENT_ID",
    "SCHWAB_CLIENT_SECRET",
    "SCHWAB_CALLBACK_URL",
)


def get_client() -> schwabdev.Client:
    """Build and return an authenticated schwabdev.Client.

    Reads credentials from environment variables, validates them,
    and instantiates the client with a project-local tokens file.

    Raises:
        EnvironmentError: If required env vars are missing or invalid.
    """
    # --- Check for missing env vars ---
    missing = [name for name in _REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variable(s): {', '.join(missing)}"
        )

    client_id = os.environ["SCHWAB_CLIENT_ID"]
    client_secret = os.environ["SCHWAB_CLIENT_SECRET"]
    callback_url = os.environ["SCHWAB_CALLBACK_URL"]

    # --- Validate format before passing to schwabdev ---
    errors = []
    if len(client_id) != 32:
        errors.append(
            f"SCHWAB_CLIENT_ID must be exactly 32 characters (got {len(client_id)})"
        )
    if len(client_secret) != 16:
        errors.append(
            f"SCHWAB_CLIENT_SECRET must be exactly 16 characters (got {len(client_secret)})"
        )
    if not callback_url.startswith("https://"):
        errors.append("SCHWAB_CALLBACK_URL must start with 'https://'")
    if callback_url.endswith("/"):
        errors.append("SCHWAB_CALLBACK_URL must not end with '/'")
    if errors:
        raise EnvironmentError(
            "Invalid Schwab credential format:\n  - " + "\n  - ".join(errors)
        )

    # --- Ensure data directory exists ---
    TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)

    client = schwabdev.Client(
        app_key=client_id,
        app_secret=client_secret,
        callback_url=callback_url,
        tokens_file=str(TOKENS_FILE),
    )
    logger.info("Auth client initialized")

    check_refresh_token_expiry(client)
    return client


def check_refresh_token_expiry(client: schwabdev.Client) -> None:
    """Warn or raise if the refresh token is near or past expiry.

    Args:
        client: An initialised schwabdev.Client instance.

    Raises:
        RuntimeError: If the refresh token is older than 7 days.
    """
    issued = getattr(client.tokens, "_refresh_token_issued", None)
    if issued is None:
        return

    # datetime.min sentinel means no token file was loaded
    if issued == datetime.min.replace(tzinfo=timezone.utc):
        return

    age_seconds = (datetime.now(timezone.utc) - issued).total_seconds()
    refresh_token_lifetime = 7 * 24 * 60 * 60  # 7 days in seconds
    remaining_seconds = refresh_token_lifetime - age_seconds

    if remaining_seconds <= 0:
        expiry_time = issued.isoformat()
        raise RuntimeError(
            f"Refresh token expired (issued {expiry_time}). "
            "Delete data/tokens.json and call get_client() to re-run the OAuth login flow."
        )

    warning_threshold = 2 * 24 * 60 * 60  # 48 hours
    if remaining_seconds < warning_threshold:
        hours_left = remaining_seconds / 3600
        logger.warning("Refresh token expires in %.1f hours", hours_left)
        print(
            f"WARNING: Schwab refresh token expires in {hours_left:.1f} hours. "
            "Delete data/tokens.json and call get_client() to re-run the OAuth login flow."
        )
