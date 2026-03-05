"""Detect unusual options activity (smart money signals) across watchlist and portfolio tickers."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import datetime

import pandas as pd

from lib.data_envelope import create_envelope, save_envelope
from lib.api import yahoo_finance_price_history, yahoo_finance_info

logger = logging.getLogger(__name__)


def setup_logging():
    """Configure logging for this module."""
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


def detect_unusual_activity(ticker: str, current_price: float) -> dict | None:
    """Detect unusual options activity for a single ticker.
    
    Returns dict with unusual strikes, sentiments, and aggregated signals.
    Returns None if no options data available.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not available")
        return None

    try:
        t = yf.Ticker(ticker)
        expiries = list(t.options) if t.options else []
    except Exception as e:
        logger.warning("Could not get options expiries for %s: %s", ticker, e)
        return None

    if not expiries:
        logger.info("No options data for %s (may be ETF), skipping", ticker)
        return None

    # Limit to next 3 expiries to keep it fast
    expiries = expiries[:3]

    unusual_strikes = []
    call_sweep_count = 0
    put_sweep_count = 0

    for expiry in expiries:
        try:
            chain = t.option_chain(expiry)
        except Exception as e:
            logger.warning("Failed to get option chain for %s at %s: %s", ticker, expiry, e)
            continue

        # Process calls
        if not chain.calls.empty:
            for _, row in chain.calls.iterrows():
                try:
                    volume = float(row.get("volume", 0)) or 0
                    oi = float(row.get("openInterest", 0)) or 0
                    vol_oi_ratio = volume / max(1, oi)

                    # Flag UNUSUAL if vol_oi_ratio >= 3.0 AND volume >= 500
                    if vol_oi_ratio >= 3.0 and volume >= 500:
                        strike = float(row.get("strike", 0))
                        last_price = float(row.get("lastPrice", 0)) or 0
                        iv = float(row.get("impliedVolatility", 0)) or None

                        unusual_strikes.append({
                            "expiry": expiry,
                            "strike": strike,
                            "type": "call",
                            "volume": int(volume),
                            "oi": int(oi),
                            "vol_oi_ratio": round(vol_oi_ratio, 2),
                            "last_price": round(last_price, 2),
                            "iv": round(iv, 4) if iv else None,
                        })
                        call_sweep_count += 1
                except Exception as e:
                    logger.debug("Error processing call row for %s: %s", ticker, e)
                    continue

        # Process puts
        if not chain.puts.empty:
            for _, row in chain.puts.iterrows():
                try:
                    volume = float(row.get("volume", 0)) or 0
                    oi = float(row.get("openInterest", 0)) or 0
                    vol_oi_ratio = volume / max(1, oi)

                    # Flag UNUSUAL if vol_oi_ratio >= 3.0 AND volume >= 500
                    if vol_oi_ratio >= 3.0 and volume >= 500:
                        strike = float(row.get("strike", 0))
                        last_price = float(row.get("lastPrice", 0)) or 0
                        iv = float(row.get("impliedVolatility", 0)) or None

                        unusual_strikes.append({
                            "expiry": expiry,
                            "strike": strike,
                            "type": "put",
                            "volume": int(volume),
                            "oi": int(oi),
                            "vol_oi_ratio": round(vol_oi_ratio, 2),
                            "last_price": round(last_price, 2),
                            "iv": round(iv, 4) if iv else None,
                        })
                        put_sweep_count += 1
                except Exception as e:
                    logger.debug("Error processing put row for %s: %s", ticker, e)
                    continue

    if not unusual_strikes:
        return None

    # Determine net sentiment
    if call_sweep_count >= put_sweep_count + 2:
        net_sentiment = "bullish"
    elif put_sweep_count >= call_sweep_count + 2:
        net_sentiment = "bearish"
    else:
        net_sentiment = "mixed"

    # Find largest trade by notional value (volume * lastPrice)
    largest_trade = None
    max_notional = 0
    for strike_data in unusual_strikes:
        notional = strike_data["volume"] * strike_data["last_price"]
        if notional > max_notional:
            max_notional = notional
            largest_trade = {
                "strike": strike_data["strike"],
                "type": strike_data["type"],
                "volume": strike_data["volume"],
                "notional": int(notional),
            }

    # Sort by vol_oi_ratio descending, keep top 10
    unusual_strikes_sorted = sorted(unusual_strikes, key=lambda x: x["vol_oi_ratio"], reverse=True)[:10]

    return {
        "ticker": ticker,
        "call_sweep_count": call_sweep_count,
        "put_sweep_count": put_sweep_count,
        "net_sentiment": net_sentiment,
        "largest_trade": largest_trade,
        "unusual_strikes": unusual_strikes_sorted,
    }


def main():
    """Main entry point: scan watchlist + portfolio for unusual options activity."""
    setup_logging()
    logger.info("=== Unusual Options Module ===")

    # Load watchlist and portfolio
    watchlist_path = PROJECT_ROOT / "config" / "watchlist.json"
    portfolio_path = PROJECT_ROOT / "config" / "portfolio.json"

    tickers_set = set()

    if watchlist_path.exists():
        try:
            watchlist = json.loads(watchlist_path.read_text())
            tickers_set.update(watchlist.get("tickers", []))
        except Exception as e:
            logger.warning("Failed to load watchlist: %s", e)

    if portfolio_path.exists():
        try:
            portfolio = json.loads(portfolio_path.read_text())
            for holding in portfolio.get("holdings", []):
                tickers_set.add(holding["ticker"])
        except Exception as e:
            logger.warning("Failed to load portfolio: %s", e)

    if not tickers_set:
        envelope = create_envelope("unusual_options", {}, status="error", error="No tickers found")
        save_envelope(envelope, "unusual_options.json")
        return

    results = []
    tickers_with_unusual = 0

    for ticker in sorted(tickers_set):
        logger.info("Scanning unusual options for %s", ticker)

        # Get current price
        hist = yahoo_finance_price_history(ticker, period="5d")
        if hist.empty:
            info = yahoo_finance_info(ticker)
            current_price = info.get("regularMarketPrice") or info.get("previousClose")
            if not current_price:
                logger.warning("Cannot get price for %s, skipping", ticker)
                continue
            current_price = float(current_price)
        else:
            current_price = float(hist["Close"].iloc[-1])

        # Detect unusual activity
        activity = detect_unusual_activity(ticker, current_price)
        if activity:
            results.append(activity)
            tickers_with_unusual += 1

    total_unusual = sum(len(r.get("unusual_strikes", [])) for r in results)

    scan_summary = {
        "tickers_scanned": len(tickers_set),
        "tickers_with_unusual": tickers_with_unusual,
        "total_unusual_strikes": total_unusual,
    }

    has_data = len(results) > 0
    status = "success" if has_data else "partial"
    error = None if results else "No unusual options activity detected"

    envelope = create_envelope(
        "unusual_options",
        {
            "results": results,
            "scan_summary": scan_summary,
        },
        status=status,
        error=error,
    )
    save_envelope(envelope, "unusual_options.json")
    logger.info("Unusual options scan complete: %d tickers with unusual activity", tickers_with_unusual)


if __name__ == "__main__":
    main()
