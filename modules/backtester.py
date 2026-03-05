"""Backtest historical verdicts against actual price data. Simulates P&L and evaluates strategy effectiveness."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import datetime, timedelta
import statistics

import pandas as pd
import numpy as np

from lib.data_envelope import create_envelope, save_envelope
from lib.api import yahoo_finance_price_history

logger = logging.getLogger(__name__)

VERDICT_LOG_PATH = PROJECT_ROOT / "data" / "scorecard" / "verdict_log.json"


def setup_logging():
    """Initialize logging. Call only once in main()."""
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


def load_verdict_log(path: Path = None) -> dict:
    """Load verdict_log.json. Return empty if missing or corrupt."""
    p = path or VERDICT_LOG_PATH
    if not p.exists():
        logger.warning("verdict_log.json not found at %s", p)
        return {"entries": []}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to parse verdict_log.json: %s", e)
        return {"entries": []}


def get_price_on_date(history: pd.DataFrame, target_date: str) -> float | None:
    """Get close price on or nearest to target_date from history DataFrame."""
    if history.empty:
        return None
    try:
        # Convert index to datetime if not already
        if not isinstance(history.index, pd.DatetimeIndex):
            history.index = pd.to_datetime(history.index)
        
        # Convert target_date string to datetime
        target_dt = pd.to_datetime(target_date)
        
        # Try exact match first
        if target_dt in history.index:
            return float(history.loc[target_dt, "Close"])
        
        # Find nearest date within 5 days using absolute timedelta
        date_diffs = np.abs((history.index - target_dt).days.astype(float))
        nearest_idx = date_diffs.argmin()
        days_diff = date_diffs[nearest_idx]
        if days_diff <= 5:
            return float(history.iloc[nearest_idx]["Close"])
        return None
    except Exception as e:
        logger.warning("get_price_on_date error for %s: %s", target_date, e)
        return None


def get_returns_window(history: pd.DataFrame, start_date: str, days: int) -> float | None:
    """Calculate return from start_date to start_date + days trading days."""
    if history.empty:
        return None
    try:
        if not isinstance(history.index, pd.DatetimeIndex):
            history.index = pd.to_datetime(history.index)
        
        start_dt = pd.to_datetime(start_date)
        
        # Get price at or near start date
        if start_dt in history.index:
            start_price = float(history.loc[start_dt, "Close"])
            start_idx = history.index.get_loc(start_dt)
        else:
            # Find nearest date within 5 days
            date_diffs = np.abs((history.index - start_dt).days.astype(float))
            nearest_idx = date_diffs.argmin()
            days_diff = date_diffs[nearest_idx]
            if days_diff > 5:
                return None
            start_price = float(history.iloc[nearest_idx]["Close"])
            start_idx = nearest_idx
        
        # Get price at or after (start_date + days trading days)
        target_idx = min(start_idx + days, len(history) - 1)
        end_price = float(history.iloc[target_idx]["Close"])
        
        return (end_price - start_price) / start_price if start_price > 0 else None
    except Exception as e:
        logger.warning("get_returns_window error: %s", e)
        return None


def score_verdict(verdict: str, ticker_return_5d: float, spy_return_5d: float) -> bool:
    """Score a verdict based on 5-day return window.
    
    BUY: win if ticker_return > 0
    SELL: win if ticker_return < 0
    AVOID: win if ticker_return < spy_return
    HOLD/REVIEW: always False (not scored)
    """
    if verdict == "BUY":
        return ticker_return_5d > 0 if ticker_return_5d is not None else False
    elif verdict == "SELL":
        return ticker_return_5d < 0 if ticker_return_5d is not None else False
    elif verdict == "AVOID":
        if ticker_return_5d is None or spy_return_5d is None:
            return False
        return ticker_return_5d < spy_return_5d
    else:  # HOLD, REVIEW
        return False


def backtest_verdicts(entries: list[dict], trade_size: float = 10000) -> dict:
    """Backtest all verdict entries. Return analysis dict."""
    results = {
        "total_verdicts_tested": 0,
        "trade_size": trade_size,
        "summary": {
            "total_pnl": 0.0,
            "win_rate": 0.0,
            "avg_return_5d": 0.0,
            "alpha_vs_spy": 0.0,
            "sharpe_5d": 0.0,
            "best_trade": None,
            "worst_trade": None,
        },
        "by_verdict": {
            "BUY": {"count": 0, "wins": 0, "avg_return_5d": 0.0, "total_pnl": 0.0},
            "SELL": {"count": 0, "wins": 0, "avg_return_5d": 0.0, "total_pnl": 0.0},
            "AVOID": {"count": 0, "wins": 0, "avg_return_5d": 0.0, "total_pnl": 0.0},
            "HOLD": {"count": 0, "wins": 0, "avg_return_5d": 0.0, "total_pnl": 0.0},
        },
        "equity_curve": [],
        "trades": [],
    }

    trades_tested = []
    cumulative_pnl = 0.0
    last_date = None

    for entry in entries:
        try:
            date = entry.get("date")
            ticker = entry.get("ticker")
            verdict = entry.get("verdict")
            price_at_verdict = entry.get("price_at_verdict")
            spy_price_at_verdict = entry.get("spy_price_at_verdict")

            if not all([date, ticker, verdict, price_at_verdict]):
                logger.debug("Skipping incomplete entry: %s %s", ticker, verdict)
                continue

            # Get price history for ticker and SPY
            hist = yahoo_finance_price_history(ticker, period="6mo")
            spy_hist = yahoo_finance_price_history("SPY", period="6mo")

            if hist.empty or spy_hist.empty:
                logger.warning("No history for %s on %s", ticker, date)
                continue

            # Calculate returns at multiple windows
            returns = {}
            for window_days in [1, 5, 10, 20]:
                ret = get_returns_window(hist, date, window_days)
                returns[f"return_{window_days}d"] = ret

            # Get SPY return for 5-day window
            spy_return_5d = get_returns_window(spy_hist, date, 5)

            # Skip if we don't have 5-day return
            if returns["return_5d"] is None:
                logger.debug("No 5d return for %s on %s", ticker, date)
                continue

            # Score the verdict
            is_win = score_verdict(verdict, returns["return_5d"], spy_return_5d)

            # Calculate P&L
            pnl_5d = returns["return_5d"] * trade_size

            # Only score BUY, SELL, AVOID for counting
            if verdict in ["BUY", "SELL", "AVOID"]:
                results["by_verdict"][verdict]["count"] += 1
                results["by_verdict"][verdict]["wins"] += 1 if is_win else 0
                results["by_verdict"][verdict]["total_pnl"] += pnl_5d

            # For HOLD, just record count (don't score)
            if verdict == "HOLD":
                results["by_verdict"]["HOLD"]["count"] += 1

            trade_record = {
                "date": date,
                "ticker": ticker,
                "verdict": verdict,
                "entry_price": round(float(price_at_verdict), 2),
                "return_1d": round(returns["return_1d"], 4) if returns["return_1d"] else None,
                "return_5d": round(returns["return_5d"], 4),
                "return_10d": round(returns["return_10d"], 4) if returns["return_10d"] else None,
                "return_20d": round(returns["return_20d"], 4) if returns["return_20d"] else None,
                "spy_return_5d": round(float(spy_return_5d), 4) if spy_return_5d else None,
                "pnl_5d": round(pnl_5d, 2),
                "win": is_win,
            }
            trades_tested.append(trade_record)
            results["trades"].append(trade_record)

            # Track equity curve
            if verdict in ["BUY", "SELL"]:  # Only actionable verdicts
                cumulative_pnl += pnl_5d
                if last_date != date:
                    results["equity_curve"].append({
                        "date": date,
                        "cumulative_pnl": round(cumulative_pnl, 2),
                    })
                    last_date = date

        except Exception as e:
            logger.warning("Error processing verdict for %s: %s", entry.get("ticker"), e)
            continue

    # Aggregate statistics
    results["total_verdicts_tested"] = len(trades_tested)

    if not trades_tested:
        return results

    # Calculate summary metrics
    scored_trades = [t for t in trades_tested if t["verdict"] in ["BUY", "SELL", "AVOID"]]
    
    if scored_trades:
        wins = sum(1 for t in scored_trades if t["win"])
        results["summary"]["win_rate"] = round(wins / len(scored_trades), 3)
        results["summary"]["total_pnl"] = round(sum(t["pnl_5d"] for t in scored_trades), 2)

        returns_5d = [t["return_5d"] for t in scored_trades if t["return_5d"] is not None]
        if returns_5d:
            results["summary"]["avg_return_5d"] = round(statistics.mean(returns_5d), 4)
            
            if len(returns_5d) > 1:
                std_return = statistics.stdev(returns_5d)
                if std_return > 0:
                    results["summary"]["sharpe_5d"] = round(
                        results["summary"]["avg_return_5d"] / std_return, 3
                    )

        # Alpha vs SPY
        spy_returns_5d = [t["spy_return_5d"] for t in scored_trades if t["spy_return_5d"] is not None]
        if spy_returns_5d and returns_5d:
            avg_spy = statistics.mean(spy_returns_5d)
            results["summary"]["alpha_vs_spy"] = round(
                results["summary"]["avg_return_5d"] - avg_spy, 4
            )

        # Best and worst trades
        if scored_trades:
            best = max(scored_trades, key=lambda t: t["return_5d"])
            worst = min(scored_trades, key=lambda t: t["return_5d"])
            results["summary"]["best_trade"] = {
                "ticker": best["ticker"],
                "verdict": best["verdict"],
                "date": best["date"],
                "return_5d": best["return_5d"],
            }
            results["summary"]["worst_trade"] = {
                "ticker": worst["ticker"],
                "verdict": worst["verdict"],
                "date": worst["date"],
                "return_5d": worst["return_5d"],
            }

    # Calculate average returns per verdict type
    for verdict in ["BUY", "SELL", "AVOID"]:
        verdict_trades = [t for t in scored_trades if t["verdict"] == verdict]
        if verdict_trades:
            returns_list = [t["return_5d"] for t in verdict_trades]
            results["by_verdict"][verdict]["avg_return_5d"] = round(
                statistics.mean(returns_list), 4
            )

    return results


def main():
    """Load verdicts, backtest, save envelope."""
    setup_logging()
    logger.info("=== Backtester Module ===")

    verdict_log = load_verdict_log()
    entries = verdict_log.get("entries", [])

    if not entries:
        logger.error("No verdict entries found")
        envelope = create_envelope(
            "backtester",
            {},
            status="error",
            error="verdict_log.json is empty or missing"
        )
        save_envelope(envelope, "backtester.json")
        return

    logger.info("Backtesting %d verdict entries", len(entries))
    analysis = backtest_verdicts(entries)

    logger.info(
        "Backtest complete: %d verdicts tested, %.1f%% win rate, $%.2f total P&L",
        analysis["total_verdicts_tested"],
        analysis["summary"]["win_rate"] * 100,
        analysis["summary"]["total_pnl"],
    )

    envelope = create_envelope("backtester", analysis, status="success")
    save_envelope(envelope, "backtester.json")
    logger.info("Backtester module complete")


if __name__ == "__main__":
    main()
