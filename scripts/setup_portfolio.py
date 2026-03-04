"""Setup script: extract portfolio holdings from a brokerage screenshot using Claude Vision."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import base64
import json
import os
import shutil
from datetime import date

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

SECTORS = {
    "tech": ["AAPL", "MSFT", "GOOGL", "GOOG", "META", "AMZN", "NVDA", "AMD", "TSLA", "CRM", "ORCL", "INTC", "AVGO", "QCOM", "ADBE"],
    "finance": ["JPM", "BAC", "GS", "MS", "V", "MA", "PYPL", "SQ", "COIN", "WFC", "C", "AXP", "BLK", "SCHW"],
    "healthcare": ["JNJ", "UNH", "PFE", "ABBV", "MRK", "LLY", "TMO", "ABT", "BMY", "AMGN"],
    "energy": ["XOM", "CVX", "COP", "SLB", "EOG", "OXY", "PSX", "VLO"],
    "consumer": ["WMT", "COST", "HD", "NKE", "SBUX", "MCD", "DIS", "TGT", "LOW", "PG", "KO", "PEP"],
    "industrial": ["CAT", "DE", "HON", "UPS", "BA", "LMT", "RTX", "GE"],
    "realestate": ["O", "AMT", "PLD", "SPG", "PSA"],
}

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _get_api_key() -> str:
    """Get Anthropic API key from environment."""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        print("Error: ANTHROPIC_API_KEY not found.")
        print("Set it in your .env file or shell environment:")
        print('  echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env')
        sys.exit(1)
    return key


def _read_image(image_path: str) -> tuple[str, str]:
    """Read image file and return (base64_data, media_type)."""
    path = Path(image_path)
    if not path.exists():
        print(f"Error: File not found: {image_path}")
        sys.exit(1)

    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        print(f"Error: Unsupported image format '{ext}'.")
        print(f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
        sys.exit(1)

    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_types[ext]
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return data, media_type


def extract_portfolio(image_path: str) -> list[dict]:
    """Send brokerage screenshot to Claude Vision API and extract holdings.

    Returns list of {"ticker", "shares", "cost_basis"}.
    """
    api_key = _get_api_key()
    image_data, media_type = _read_image(image_path)

    prompt = (
        "Analyze this brokerage portfolio screenshot. Extract every holding as JSON:\n"
        '[{"ticker": "AAPL", "shares": 100, "cost_basis": 178.50}]\n\n'
        "Rules:\n"
        "- ticker: uppercase stock symbol only (no company names)\n"
        "- shares: total shares held (handle fractional shares)\n"
        "- cost_basis: average cost per share (look for \"avg cost\", \"cost basis\", \"average price\")\n"
        "- If cost basis is not visible, use current price as estimate and note it\n"
        "- Ignore cash balances, options, crypto — stocks only\n"
        "- Return ONLY the JSON array, no other text"
    )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
    except Exception as e:
        print(f"Error calling Claude Vision API: {e}")
        sys.exit(1)

    raw = message.content[0].text.strip()

    # Extract JSON from response (handle markdown code blocks)
    if "```" in raw:
        start = raw.index("```") + 3
        if raw[start:].startswith("json"):
            start += 4
        end = raw.index("```", start)
        raw = raw[start:end].strip()

    try:
        holdings = json.loads(raw)
    except json.JSONDecodeError:
        print("Error: Could not parse Vision API response as JSON.")
        print(f"Raw response:\n{raw}")
        print("\nTry a clearer screenshot or enter holdings manually in config/portfolio.json")
        sys.exit(1)

    if not isinstance(holdings, list) or not holdings:
        print("Error: Vision API returned empty or invalid holdings.")
        print(f"Raw response:\n{raw}")
        sys.exit(1)

    # Normalize each holding
    cleaned = []
    for h in holdings:
        ticker = str(h.get("ticker", "")).upper().strip()
        if not ticker:
            continue
        shares = h.get("shares", 0)
        cost_basis = h.get("cost_basis", 0)
        try:
            shares = float(shares)
            cost_basis = float(cost_basis)
        except (ValueError, TypeError):
            print(f"Warning: skipping {ticker} — invalid shares/cost_basis")
            continue
        cleaned.append({
            "ticker": ticker,
            "shares": shares,
            "cost_basis": round(cost_basis, 2),
        })

    return cleaned


def generate_watchlist(holdings: list[dict]) -> dict:
    """Build watchlist.json content from extracted holdings.

    Groups tickers by sector to form comparison pairs.
    """
    tickers = [h["ticker"] for h in holdings]

    # Build reverse lookup: ticker -> sector
    ticker_to_sector = {}
    for sector, members in SECTORS.items():
        for t in members:
            ticker_to_sector[t] = sector

    # Group portfolio tickers by sector
    sector_groups: dict[str, list[str]] = {}
    for t in tickers:
        sector = ticker_to_sector.get(t)
        if sector:
            sector_groups.setdefault(sector, []).append(t)

    # Form pairs from tickers in the same sector
    pairs = []
    for group in sector_groups.values():
        if len(group) >= 2:
            for i in range(len(group) - 1):
                pairs.append([group[i], group[i + 1]])

    return {
        "tickers": tickers,
        "comparison_pairs": pairs,
        "earnings_watch": tickers,
    }


def display_holdings(holdings: list[dict]) -> None:
    """Pretty-print extracted holdings as a table."""
    print(f"\n{'Ticker':<10} {'Shares':>10} {'Cost Basis':>12}")
    print("-" * 34)
    for h in holdings:
        print(f"{h['ticker']:<10} {h['shares']:>10.2f} ${h['cost_basis']:>10.2f}")
    print("-" * 34)
    print(f"{'Total holdings:':<10} {len(holdings)}")


def write_configs(holdings: list[dict], cash: float = 0) -> None:
    """Write portfolio.json and watchlist.json, backing up originals."""
    config_dir = PROJECT_ROOT / "config"
    today = date.today().isoformat()

    # Build portfolio data
    portfolio_data = {
        "holdings": [
            {
                "ticker": h["ticker"],
                "shares": h["shares"],
                "cost_basis": h["cost_basis"],
                "date_acquired": today,
            }
            for h in holdings
        ],
        "cash": cash,
        "options_positions": [],
    }

    watchlist_data = generate_watchlist(holdings)

    # Backup and write each config
    for filename, data in [("portfolio.json", portfolio_data), ("watchlist.json", watchlist_data)]:
        filepath = config_dir / filename
        if filepath.exists():
            backup = filepath.with_suffix(".json.bak")
            shutil.copy2(filepath, backup)
            print(f"Backed up {filename} -> {backup.name}")

        filepath.write_text(json.dumps(data, indent=2) + "\n")
        print(f"Wrote {filename}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract portfolio holdings from a brokerage screenshot using Claude Vision."
    )
    parser.add_argument("image_path", help="Path to brokerage portfolio screenshot")
    parser.add_argument("--cash", type=float, default=0, help="Cash balance to include (default: 0)")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    print("Extracting holdings from screenshot...")
    holdings = extract_portfolio(args.image_path)

    display_holdings(holdings)

    if not args.yes:
        response = input("\nWrite these holdings to config files? [y/N] ").strip().lower()
        if response not in ("y", "yes"):
            print("Aborted.")
            sys.exit(0)

    write_configs(holdings, cash=args.cash)
    print("\nDone! Run your morning brief:")
    print("  .venv/bin/python scripts/run_all.py")


if __name__ == "__main__":
    main()
