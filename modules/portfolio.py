"""Portfolio analysis: P&L, correlations, ATR, trailing stops."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging

import numpy as np
import pandas as pd

from lib.data_envelope import create_envelope, save_envelope
from lib.api import yahoo_finance_price_history

logger = logging.getLogger(__name__)


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


def compute_atr(history: pd.DataFrame, current_price: float, period: int = 14) -> float | None:
    """Compute 14-day ATR from price history."""
    if history.empty or len(history) < period + 1:
        return None
    try:
        if "High" in history.columns and "Low" in history.columns:
            high = history["High"]
            low = history["Low"]
            close = history["Close"]
            prev_close = close.shift(1)
            tr = pd.concat([
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ], axis=1).max(axis=1)
            atr = tr.rolling(period).mean().iloc[-1]
            if pd.notna(atr):
                return round(float(atr), 4)
        # Fallback: volatility-based estimate
        returns = history["Close"].pct_change().dropna()
        if len(returns) > 0:
            return round(float(returns.std() * current_price * (period ** 0.5)), 4)
    except Exception as e:
        logger.warning("ATR calculation error: %s", e)
    return None


def analyze_portfolio(holdings: list[dict], settings: dict) -> dict:
    """Analyze portfolio holdings with price data."""
    atr_mult = settings.get("atr_multiplier_default", 2.0)
    min_corr_days = settings.get("min_correlation_days", 60)

    results = []
    daily_returns = {}

    for h in holdings:
        ticker = h["ticker"]
        shares = h["shares"]
        cost_basis = h["cost_basis"]

        history = yahoo_finance_price_history(ticker, period="2y")

        if history.empty:
            results.append({
                "ticker": ticker,
                "shares": shares,
                "cost_basis": cost_basis,
                "current_price": None,
                "pnl": None,
                "atr": None,
                "trailing_stop": None,
                "locked_profit": None,
                "error": "No price data available",
            })
            continue

        current_price = round(float(history["Close"].iloc[-1]), 2)
        pnl = round((current_price - cost_basis) * shares, 2)

        atr = compute_atr(history, current_price)
        trailing_stop = round(current_price - (atr_mult * atr), 2) if atr else None
        locked_profit = round((trailing_stop - cost_basis) * shares, 2) if trailing_stop else None

        # Collect daily returns for correlation
        rets = history["Close"].pct_change().dropna()
        daily_returns[ticker] = rets

        results.append({
            "ticker": ticker,
            "shares": shares,
            "cost_basis": cost_basis,
            "current_price": current_price,
            "pnl": pnl,
            "atr": atr,
            "trailing_stop": trailing_stop,
            "locked_profit": locked_profit,
        })
        logger.info("%s: price=%.2f pnl=%.2f atr=%s stop=%s",
                    ticker, current_price, pnl, atr, trailing_stop)

    # Compute pairwise correlations
    correlations = {}
    tickers_with_data = list(daily_returns.keys())
    for i in range(len(tickers_with_data)):
        for j in range(i + 1, len(tickers_with_data)):
            t1, t2 = tickers_with_data[i], tickers_with_data[j]
            r1, r2 = daily_returns[t1], daily_returns[t2]
            # Align on common dates
            combined = pd.concat([r1, r2], axis=1, join="inner")
            combined.columns = [t1, t2]
            overlap = len(combined)
            pair_key = f"{t1}_{t2}"

            if overlap >= min_corr_days:
                corr = round(float(combined[t1].corr(combined[t2])), 4)
                correlations[pair_key] = {"correlation": corr, "overlap_days": overlap}
            else:
                correlations[pair_key] = {
                    "correlation": None,
                    "overlap_days": overlap,
                    "flag": "insufficient data",
                }

    total_pnl = sum(r["pnl"] for r in results if r.get("pnl") is not None)

    return {
        "holdings": results,
        "correlations": correlations,
        "total_pnl": round(total_pnl, 2),
    }


def main():
    setup_logging()
    logger.info("=== Portfolio Module ===")

    portfolio_path = PROJECT_ROOT / "config" / "portfolio.json"
    settings_path = PROJECT_ROOT / "config" / "settings.json"

    if not portfolio_path.exists():
        envelope = create_envelope("portfolio", {}, status="error", error="portfolio.json not found")
        save_envelope(envelope, "portfolio.json")
        return

    portfolio = json.loads(portfolio_path.read_text())
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    holdings = portfolio.get("holdings", [])

    result = analyze_portfolio(holdings, settings)

    has_data = any(h.get("current_price") is not None for h in result["holdings"])
    status = "success" if has_data else "error"
    error = None if has_data else "Could not fetch price data for any holding"
    if has_data and any(h.get("current_price") is None for h in result["holdings"]):
        status = "partial"

    envelope = create_envelope("portfolio", result, status=status, error=error)
    save_envelope(envelope, "portfolio.json")
    logger.info("Portfolio analysis complete: total P&L = $%.2f", result["total_pnl"])


if __name__ == "__main__":
    main()
