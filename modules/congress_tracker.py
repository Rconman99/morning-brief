"""Congressional Trading Tracker: monitors STOCK Act disclosures from House and Senate members.

Data sources (tried in order):
1. House Stock Watcher API (free, no key needed)
2. Senate Stock Watcher GitHub data (free, no key needed)
3. Capitol Trades BFF API (free, no key needed)
4. Sample data fallback (data/sample/congress_trades.json)

NOTE: Congressional trades are reported with up to 45-day delay under the STOCK Act.
This module tracks disclosed trades and flags overlaps with your watchlist/portfolio.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import datetime, date, timedelta
from collections import defaultdict

from lib.data_envelope import create_envelope, save_envelope
from lib.cache import get_cached, set_cached

logger = logging.getLogger(__name__)

# Try importing requests
try:
    import requests
except ImportError:
    requests = None
    logger.warning("requests not installed — API fetching disabled")

# Amount range midpoints for sorting/analysis
AMOUNT_MIDPOINTS = {
    "$1,001 - $15,000": 8000,
    "$15,001 - $50,000": 32500,
    "$50,001 - $100,000": 75000,
    "$100,001 - $250,000": 175000,
    "$250,001 - $500,000": 375000,
    "$500,001 - $1,000,000": 750000,
    "$1,000,001 - $5,000,000": 3000000,
    "$5,000,001 - $25,000,000": 15000000,
    "$25,000,001 - $50,000,000": 37500000,
    "$50,000,001+": 75000000,
    "Over $50,000,000": 75000000,
}

# Notable politicians to highlight (historically active traders)
NOTABLE_TRADERS = [
    "nancy pelosi", "tommy tuberville", "dan crenshaw", "josh gottheimer",
    "mark green", "michael mccaul", "markwayne mullin", "ro khanna",
    "katie porter", "dianne feinstein", "pat fallon", "david perdue"
]


def setup_logging():
    """Call this ONLY inside main(). NEVER at module level."""
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


def get_all_tickers() -> list[str]:
    """Get union of watchlist tickers and portfolio holdings, deduped."""
    tickers = set()
    watchlist_path = PROJECT_ROOT / "config" / "watchlist.json"
    portfolio_path = PROJECT_ROOT / "config" / "portfolio.json"

    if watchlist_path.exists():
        try:
            wl = json.loads(watchlist_path.read_text())
            tickers.update(wl.get("tickers", []))
        except (json.JSONDecodeError, OSError):
            pass

    if portfolio_path.exists():
        try:
            pf = json.loads(portfolio_path.read_text())
            for h in pf.get("holdings", []):
                tickers.add(h["ticker"])
        except (json.JSONDecodeError, OSError):
            pass

    return sorted(tickers)


def _parse_amount(amount_str: str) -> int:
    """Convert amount range string to estimated midpoint value."""
    if not amount_str:
        return 0
    # Try exact match first
    for key, val in AMOUNT_MIDPOINTS.items():
        if key.lower() in amount_str.lower():
            return val
    # Try to extract any number
    import re
    nums = re.findall(r'[\d,]+', amount_str.replace(',', ''))
    if nums:
        try:
            return int(nums[0])
        except ValueError:
            pass
    return 0


def fetch_house_trades(lookback_days: int = 90) -> list[dict]:
    """Fetch recent House trades from House Stock Watcher API."""
    if requests is None:
        return []

    cache_key = f"congress_house_{date.today().isoformat()}"
    cached = get_cached(cache_key, max_age_hours=12)
    if cached:
        return cached.get("trades", [])

    try:
        url = "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"
        resp = requests.get(url, timeout=30, headers={"User-Agent": "TradingSystem/1.0"})
        resp.raise_for_status()
        raw = resp.json()

        cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
        trades = []
        for t in raw:
            tx_date = t.get("transaction_date", "")
            if tx_date and tx_date >= cutoff:
                trades.append({
                    "politician": t.get("representative", "Unknown"),
                    "chamber": "House",
                    "party": t.get("party", ""),
                    "state": t.get("state", ""),
                    "ticker": (t.get("ticker", "") or "").upper().strip(),
                    "asset_description": t.get("asset_description", ""),
                    "transaction_type": t.get("type", ""),
                    "amount_range": t.get("amount", ""),
                    "transaction_date": tx_date,
                    "disclosure_date": t.get("disclosure_date", ""),
                    "owner": t.get("owner", "Self"),
                })

        set_cached(cache_key, {"trades": trades})
        logger.info("Fetched %d House trades (last %d days)", len(trades), lookback_days)
        return trades

    except Exception as e:
        logger.warning("Failed to fetch House trades: %s", e)
        return []


def fetch_senate_trades(lookback_days: int = 90) -> list[dict]:
    """Fetch recent Senate trades from Senate Stock Watcher."""
    if requests is None:
        return []

    cache_key = f"congress_senate_{date.today().isoformat()}"
    cached = get_cached(cache_key, max_age_hours=12)
    if cached:
        return cached.get("trades", [])

    try:
        url = "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json"
        resp = requests.get(url, timeout=30, headers={"User-Agent": "TradingSystem/1.0"})
        resp.raise_for_status()
        raw = resp.json()

        cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
        trades = []
        for t in raw:
            tx_date = t.get("transaction_date", "")
            if tx_date and tx_date >= cutoff:
                trades.append({
                    "politician": f"{t.get('first_name', '')} {t.get('last_name', '')}".strip() or "Unknown",
                    "chamber": "Senate",
                    "party": t.get("party", ""),
                    "state": t.get("state", ""),
                    "ticker": (t.get("ticker", "") or "").upper().strip(),
                    "asset_description": t.get("asset_description", ""),
                    "transaction_type": t.get("type", ""),
                    "amount_range": t.get("amount", ""),
                    "transaction_date": tx_date,
                    "disclosure_date": t.get("disclosure_date", ""),
                    "owner": t.get("owner", "Self"),
                })

        set_cached(cache_key, {"trades": trades})
        logger.info("Fetched %d Senate trades (last %d days)", len(trades), lookback_days)
        return trades

    except Exception as e:
        logger.warning("Failed to fetch Senate trades: %s", e)
        return []


def load_sample_trades() -> list[dict]:
    """Load sample congressional trades as fallback."""
    sample_path = PROJECT_ROOT / "data" / "sample" / "congress_trades.json"
    if sample_path.exists():
        try:
            data = json.loads(sample_path.read_text())
            logger.info("Loaded %d sample congressional trades", len(data.get("trades", [])))
            return data.get("trades", [])
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load sample trades: %s", e)
    return []


def fetch_all_trades(lookback_days: int = 90) -> tuple[list[dict], str]:
    """Fetch trades from all sources, return (trades, source_used)."""
    # Try House first
    house = fetch_house_trades(lookback_days)
    senate = fetch_senate_trades(lookback_days)

    if house or senate:
        all_trades = house + senate
        source = "api"
        if house and senate:
            source = "house_api+senate_api"
        elif house:
            source = "house_api"
        else:
            source = "senate_api"
        return all_trades, source

    # Fallback to sample data
    sample = load_sample_trades()
    if sample:
        return sample, "sample_data"

    return [], "none"


def analyze_trades(trades: list[dict], watchlist_tickers: list[str]) -> dict:
    """Analyze congressional trades for signals relevant to our watchlist.

    Returns analysis dict with:
    - watchlist_trades: trades matching our watchlist tickers
    - notable_trades: trades by historically notable traders
    - cluster_signals: multiple politicians buying/selling same ticker
    - big_trades: individual trades > $500k estimated
    - summary_stats: buy/sell counts, top tickers, partisan breakdown
    """
    if not trades:
        return {
            "watchlist_trades": [],
            "notable_trades": [],
            "cluster_signals": [],
            "big_trades": [],
            "summary_stats": {},
            "signal": "no_data",
            "signal_detail": "No congressional trading data available",
        }

    watchlist_set = {t.upper() for t in watchlist_tickers}

    # Filter to clean trades with valid tickers
    valid_trades = [t for t in trades if t.get("ticker") and len(t["ticker"]) <= 5
                    and t["ticker"].isalpha()]

    # 1. Watchlist matches
    watchlist_trades = [t for t in valid_trades if t["ticker"] in watchlist_set]
    watchlist_trades.sort(key=lambda x: x.get("transaction_date", ""), reverse=True)

    # 2. Notable trader activity
    notable_trades = []
    for t in valid_trades:
        name = t.get("politician", "").lower()
        if any(notable in name for notable in NOTABLE_TRADERS):
            notable_trades.append(t)
    notable_trades.sort(key=lambda x: _parse_amount(x.get("amount_range", "")), reverse=True)

    # 3. Cluster signals: 3+ politicians trading same ticker same direction in 30 days
    cluster_signals = []
    ticker_direction = defaultdict(list)
    for t in valid_trades:
        direction = "buy" if "purchase" in t.get("transaction_type", "").lower() else "sell"
        ticker_direction[(t["ticker"], direction)].append(t)

    for (ticker, direction), group in ticker_direction.items():
        unique_politicians = set(t["politician"] for t in group)
        if len(unique_politicians) >= 3:
            total_est = sum(_parse_amount(t.get("amount_range", "")) for t in group)
            cluster_signals.append({
                "ticker": ticker,
                "direction": direction,
                "politician_count": len(unique_politicians),
                "politicians": sorted(unique_politicians),
                "trade_count": len(group),
                "estimated_total": total_est,
                "in_watchlist": ticker in watchlist_set,
                "signal": "strong_bullish" if direction == "buy" else "strong_bearish",
            })
        elif len(unique_politicians) >= 2 and ticker in watchlist_set:
            total_est = sum(_parse_amount(t.get("amount_range", "")) for t in group)
            cluster_signals.append({
                "ticker": ticker,
                "direction": direction,
                "politician_count": len(unique_politicians),
                "politicians": sorted(unique_politicians),
                "trade_count": len(group),
                "estimated_total": total_est,
                "in_watchlist": True,
                "signal": "moderate_bullish" if direction == "buy" else "moderate_bearish",
            })

    cluster_signals.sort(key=lambda x: x["politician_count"], reverse=True)

    # 4. Big trades (>$500k estimated)
    big_trades = [t for t in valid_trades if _parse_amount(t.get("amount_range", "")) >= 500000]
    big_trades.sort(key=lambda x: _parse_amount(x.get("amount_range", "")), reverse=True)

    # 5. Summary stats
    buys = [t for t in valid_trades if "purchase" in t.get("transaction_type", "").lower()]
    sells = [t for t in valid_trades if "sale" in t.get("transaction_type", "").lower()]

    # Top tickers by trade count
    ticker_counts = defaultdict(int)
    for t in valid_trades:
        ticker_counts[t["ticker"]] += 1
    top_tickers = sorted(ticker_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    # Partisan breakdown
    dem_buys = len([t for t in buys if t.get("party") == "D"])
    rep_buys = len([t for t in buys if t.get("party") == "R"])
    dem_sells = len([t for t in sells if t.get("party") == "D"])
    rep_sells = len([t for t in sells if t.get("party") == "R"])

    summary_stats = {
        "total_trades": len(valid_trades),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "unique_politicians": len(set(t["politician"] for t in valid_trades)),
        "unique_tickers": len(set(t["ticker"] for t in valid_trades)),
        "watchlist_matches": len(watchlist_trades),
        "top_tickers": [{"ticker": t, "count": c} for t, c in top_tickers],
        "partisan_breakdown": {
            "democrat": {"buys": dem_buys, "sells": dem_sells},
            "republican": {"buys": rep_buys, "sells": rep_sells},
        },
    }

    # Determine overall signal
    watchlist_buys = [t for t in watchlist_trades if "purchase" in t.get("transaction_type", "").lower()]
    watchlist_sells = [t for t in watchlist_trades if "sale" in t.get("transaction_type", "").lower()]
    big_watchlist = [t for t in watchlist_trades if _parse_amount(t.get("amount_range", "")) >= 500000]

    if len(cluster_signals) > 0 and any(c["in_watchlist"] for c in cluster_signals):
        signal = "cluster_detected"
        signal_detail = f"{len(cluster_signals)} cluster signal(s) detected, {sum(1 for c in cluster_signals if c['in_watchlist'])} in your watchlist"
    elif len(big_watchlist) >= 2:
        signal = "significant_activity"
        signal_detail = f"{len(big_watchlist)} large trades ($500k+) in your watchlist tickers"
    elif len(watchlist_trades) >= 3:
        signal = "active"
        signal_detail = f"{len(watchlist_trades)} congressional trades in your watchlist ({len(watchlist_buys)} buys, {len(watchlist_sells)} sells)"
    elif len(watchlist_trades) > 0:
        signal = "normal"
        signal_detail = f"{len(watchlist_trades)} congressional trade(s) in your watchlist"
    else:
        signal = "quiet"
        signal_detail = "No congressional activity in your watchlist tickers"

    return {
        "watchlist_trades": watchlist_trades[:20],
        "notable_trades": notable_trades[:15],
        "cluster_signals": cluster_signals[:10],
        "big_trades": big_trades[:15],
        "summary_stats": summary_stats,
        "signal": signal,
        "signal_detail": signal_detail,
    }


def run_congress_tracker(lookback_days: int = 90) -> dict:
    """Main entry point: fetch trades and analyze."""
    tickers = get_all_tickers()
    trades, source = fetch_all_trades(lookback_days)

    logger.info("Congressional trades: %d total from %s, checking against %d watchlist tickers",
                len(trades), source, len(tickers))

    analysis = analyze_trades(trades, tickers)
    analysis["data_source"] = source
    analysis["lookback_days"] = lookback_days
    analysis["analyzed_at"] = datetime.now().astimezone().isoformat()

    return analysis


def main():
    setup_logging()
    logger.info("=== Congressional Trading Tracker ===")

    data = run_congress_tracker()

    if data["signal"] == "no_data" and data["data_source"] == "none":
        status = "error"
        error = "No congressional trading data available from any source"
    elif data["data_source"] == "sample_data":
        status = "partial"
        error = "Using sample data — live API fetch failed"
    else:
        status = "success"
        error = None

    envelope = create_envelope("congress_tracker", data, status=status, error=error)
    save_envelope(envelope, "congress_tracker.json")

    stats = data.get("summary_stats", {})
    logger.info("Congress tracker complete: %d trades, %d watchlist matches, signal=%s",
                stats.get("total_trades", 0), stats.get("watchlist_matches", 0), data["signal"])


if __name__ == "__main__":
    main()
