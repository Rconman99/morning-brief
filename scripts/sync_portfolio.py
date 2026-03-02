"""Sync portfolio holdings from E*TRADE before pipeline run.

Three-tier fallback:
  1. E*TRADE API (if tokens exist and were obtained today)
  2. CSV export (manual E*TRADE download or --csv flag)
  3. Existing portfolio.json (stale but functional)

Usage:
  .venv/bin/python scripts/sync_portfolio.py              # API -> CSV -> stale
  .venv/bin/python scripts/sync_portfolio.py --csv path    # force CSV source
  .venv/bin/python scripts/sync_portfolio.py --dry-run     # print, don't write
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import csv
import json
import logging
import shutil
from datetime import datetime, date

from dotenv import load_dotenv
import os

logger = logging.getLogger(__name__)

PORTFOLIO_FILE = PROJECT_ROOT / "config" / "portfolio.json"
TOKEN_FILE = PROJECT_ROOT / "config" / "etrade_tokens.json"
DEFAULT_CSV = PROJECT_ROOT / "data" / "raw" / "etrade_positions.csv"


def setup_logging():
    log_dir = PROJECT_ROOT / "data" / "outputs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(log_dir / "run.log", mode="a"),
            ],
        )


# ---------------------------------------------------------------------------
# Load existing portfolio for field preservation
# ---------------------------------------------------------------------------

def load_existing_portfolio() -> dict:
    """Load current portfolio.json, return empty structure if missing."""
    if PORTFOLIO_FILE.exists():
        try:
            return json.loads(PORTFOLIO_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Could not read existing portfolio.json: {e}")
    return {"holdings": [], "cash": 0, "options_positions": []}


def build_trading_window_map(portfolio: dict) -> dict:
    """Extract {ticker: trading_window} from existing portfolio."""
    windows = {}
    for h in portfolio.get("holdings", []):
        if "trading_window" in h:
            windows[h["ticker"]] = h["trading_window"]
    return windows


# ---------------------------------------------------------------------------
# Tier 1: E*TRADE API sync
# ---------------------------------------------------------------------------

def tokens_are_fresh() -> bool:
    """Check if saved tokens were obtained today (they expire at midnight ET)."""
    if not TOKEN_FILE.exists():
        return False
    try:
        data = json.loads(TOKEN_FILE.read_text())
        obtained = datetime.fromisoformat(data["obtained_at"])
        return obtained.date() == date.today()
    except (json.JSONDecodeError, KeyError, ValueError):
        return False


def sync_from_api() -> tuple[list[dict], str]:
    """Fetch positions from E*TRADE API. Raises on any failure."""
    import pyetrade

    load_dotenv(PROJECT_ROOT / ".env")
    key = os.getenv("ETRADE_CONSUMER_KEY", "")
    secret = os.getenv("ETRADE_CONSUMER_SECRET", "")

    if not key or key == "your_consumer_key":
        raise ValueError("ETRADE_CONSUMER_KEY not configured")

    if not tokens_are_fresh():
        raise ValueError("Tokens missing or expired (obtained before today)")

    tokens = json.loads(TOKEN_FILE.read_text())

    accounts = pyetrade.ETradeAccounts(
        key, secret,
        tokens["access_token"],
        tokens["access_token_secret"],
    )

    # List accounts and use the first one
    account_list = accounts.list_accounts(resp_format="json")
    acct_data = account_list["AccountListResponse"]["Accounts"]["Account"]
    if isinstance(acct_data, dict):
        acct_data = [acct_data]

    account_key = acct_data[0]["accountIdKey"]
    logger.info(f"Using E*TRADE account: {acct_data[0].get('accountId', account_key)}")

    # Get positions
    positions_resp = accounts.get_account_portfolio(account_key, resp_format="json")

    holdings = []
    positions = (
        positions_resp
        .get("PortfolioResponse", {})
        .get("AccountPortfolio", [{}])[0]
        .get("Position", [])
    )

    for pos in positions:
        product = pos.get("Product", {})
        ticker = product.get("symbol", "")
        if not ticker:
            continue

        shares = pos.get("quantity", 0)
        cost_basis_total = pos.get("costBasisTotal", 0)
        cost_basis_per_share = cost_basis_total / shares if shares else 0

        holdings.append({
            "ticker": ticker,
            "shares": int(shares) if float(shares) == int(shares) else float(shares),
            "cost_basis": round(cost_basis_per_share, 4),
        })

    if not holdings:
        raise ValueError("API returned no positions")

    return holdings, "api"


# ---------------------------------------------------------------------------
# Tier 2: CSV sync
# ---------------------------------------------------------------------------

def sync_from_csv(csv_path: Path) -> tuple[list[dict], str]:
    """Parse an E*TRADE CSV export into holdings list.

    Expected columns: Symbol, Description, Quantity, Last Price, Value, Cost Basis Total
    The 'Cost Basis Total' is divided by Quantity to get per-share cost basis.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    holdings = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        for row in reader:
            ticker = row.get("Symbol", "").strip()
            if not ticker:
                continue

            try:
                shares_raw = float(row.get("Quantity", "0").replace(",", ""))
                shares = int(shares_raw) if shares_raw == int(shares_raw) else shares_raw
            except (ValueError, TypeError):
                logger.warning(f"Skipping {ticker}: invalid quantity")
                continue

            try:
                cost_total = float(row.get("Cost Basis Total", "0").replace(",", "").replace("$", ""))
                cost_basis = round(cost_total / shares, 4) if shares else 0
            except (ValueError, TypeError, ZeroDivisionError):
                logger.warning(f"Skipping {ticker}: invalid cost basis")
                continue

            holdings.append({
                "ticker": ticker,
                "shares": shares,
                "cost_basis": cost_basis,
            })

    if not holdings:
        raise ValueError(f"CSV parsed but contained no valid positions: {csv_path}")

    return holdings, "csv"


# ---------------------------------------------------------------------------
# Write portfolio
# ---------------------------------------------------------------------------

def write_portfolio(holdings: list[dict], source: str, dry_run: bool = False) -> None:
    """Write updated portfolio.json, preserving cash, options, and trading_windows."""
    existing = load_existing_portfolio()
    window_map = build_trading_window_map(existing)

    # Re-attach trading_window to matching tickers
    for h in holdings:
        if h["ticker"] in window_map:
            h["trading_window"] = window_map[h["ticker"]]

    new_portfolio = {
        "holdings": holdings,
        "cash": existing.get("cash", 0),
        "options_positions": existing.get("options_positions", []),
    }

    if dry_run:
        print("\n=== DRY RUN — would write this portfolio ===")
        print(json.dumps(new_portfolio, indent=2))
        print(f"\nSource: {source}")
        print(f"Holdings: {len(holdings)}")
        return

    # Backup existing file
    if PORTFOLIO_FILE.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = PORTFOLIO_FILE.parent / f"portfolio.json.bak.{timestamp}"
        shutil.copy2(PORTFOLIO_FILE, backup_path)
        logger.info(f"Backed up portfolio to {backup_path.name}")

    PORTFOLIO_FILE.write_text(json.dumps(new_portfolio, indent=2) + "\n")
    logger.info(f"Portfolio updated from {source}: {len(holdings)} holdings")


# ---------------------------------------------------------------------------
# Diff reporting
# ---------------------------------------------------------------------------

def report_changes(old_portfolio: dict, new_holdings: list[dict]) -> None:
    """Print a summary of what changed."""
    old_tickers = {h["ticker"] for h in old_portfolio.get("holdings", [])}
    new_tickers = {h["ticker"] for h in new_holdings}

    added = new_tickers - old_tickers
    removed = old_tickers - new_tickers
    kept = old_tickers & new_tickers

    if added:
        print(f"  + Added: {', '.join(sorted(added))}")
    if removed:
        print(f"  - Removed: {', '.join(sorted(removed))}")
    if not added and not removed:
        print(f"  No ticker changes ({len(kept)} holdings)")

    # Check for share count changes
    old_map = {h["ticker"]: h for h in old_portfolio.get("holdings", [])}
    for h in new_holdings:
        if h["ticker"] in old_map:
            old_shares = old_map[h["ticker"]]["shares"]
            if h["shares"] != old_shares:
                print(f"  ~ {h['ticker']}: {old_shares} -> {h['shares']} shares")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    setup_logging()

    parser = argparse.ArgumentParser(description="Sync portfolio from E*TRADE")
    parser.add_argument("--csv", type=Path, help="Path to E*TRADE CSV export")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    args = parser.parse_args()

    existing = load_existing_portfolio()
    holdings = None
    source = None

    # If --csv flag provided, go straight to CSV
    if args.csv:
        try:
            holdings, source = sync_from_csv(args.csv)
            print(f"Synced from CSV: {args.csv}")
        except (FileNotFoundError, ValueError) as e:
            logger.error(f"CSV sync failed: {e}")
            sys.exit(1)
    else:
        # Tier 1: Try API
        try:
            holdings, source = sync_from_api()
            print("Synced from E*TRADE API")
        except Exception as e:
            logger.info(f"API sync unavailable: {e}")

            # Tier 2: Try default CSV location
            if DEFAULT_CSV.exists():
                try:
                    holdings, source = sync_from_csv(DEFAULT_CSV)
                    print(f"Synced from CSV: {DEFAULT_CSV}")
                except (FileNotFoundError, ValueError) as e2:
                    logger.warning(f"CSV sync failed: {e2}")

        # Tier 3: Use existing portfolio.json
        if holdings is None:
            print("No sync source available. Using existing portfolio.json.")
            return

    # Report and write
    report_changes(existing, holdings)
    write_portfolio(holdings, source, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
