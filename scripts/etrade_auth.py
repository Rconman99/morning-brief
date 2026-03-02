"""One-time interactive OAuth 1.0a setup for E*TRADE API access.

E*TRADE access tokens expire at midnight ET daily. Run this script each
morning before the pipeline if you want API-based portfolio sync.

Usage: .venv/bin/python scripts/etrade_auth.py
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import datetime

from dotenv import load_dotenv
import os

logger = logging.getLogger(__name__)

TOKEN_FILE = PROJECT_ROOT / "config" / "etrade_tokens.json"


def setup_logging():
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[logging.StreamHandler()],
        )


def get_credentials() -> tuple[str, str]:
    """Load E*TRADE consumer key and secret from .env."""
    load_dotenv(PROJECT_ROOT / ".env")
    key = os.getenv("ETRADE_CONSUMER_KEY", "")
    secret = os.getenv("ETRADE_CONSUMER_SECRET", "")
    if not key or key == "your_consumer_key":
        raise ValueError(
            "ETRADE_CONSUMER_KEY not set in .env. "
            "Register an app at https://developer.etrade.com to get API keys."
        )
    if not secret or secret == "your_consumer_secret":
        raise ValueError("ETRADE_CONSUMER_SECRET not set in .env.")
    return key, secret


def is_sandbox() -> bool:
    """Check if sandbox mode is enabled."""
    load_dotenv(PROJECT_ROOT / ".env")
    return os.getenv("ETRADE_SANDBOX", "false").lower() == "true"


def run_oauth_flow():
    """Interactive OAuth 1.0a flow: get request token, authorize, exchange."""
    try:
        import pyetrade
    except ImportError:
        print("pyetrade not installed. Run: .venv/bin/pip install pyetrade")
        sys.exit(1)

    key, secret = get_credentials()
    sandbox = is_sandbox()
    dev_mode = sandbox  # pyetrade uses dev=True for sandbox

    print("=== E*TRADE OAuth Setup ===")
    print(f"Mode: {'Sandbox' if sandbox else 'Production'}")
    print()

    # Step 1: Get request token and authorize URL
    oauth = pyetrade.ETradeOAuth(key, secret)
    authorize_url = oauth.get_request_token()

    print("1. Open this URL in your browser:")
    print(f"   {authorize_url}")
    print()
    print("2. Log in to E*TRADE and authorize the app.")
    print("3. Copy the verification code shown on screen.")
    print()

    verifier = input("Enter verification code: ").strip()
    if not verifier:
        print("No code entered. Aborting.")
        sys.exit(1)

    # Step 2: Exchange for access token
    tokens = oauth.get_access_token(verifier)

    # Step 3: Save tokens
    token_data = {
        "access_token": tokens["oauth_token"],
        "access_token_secret": tokens["oauth_token_secret"],
        "obtained_at": datetime.now().astimezone().isoformat(),
    }

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(token_data, indent=2))

    print()
    print(f"Tokens saved to {TOKEN_FILE}")
    print("These expire at midnight ET. Re-run this script daily for API sync.")


def main():
    setup_logging()
    try:
        run_oauth_flow()
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"OAuth flow failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
