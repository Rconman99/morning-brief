"""Minimal backtesting engine for equity strategies.

Replays historical OHLCV data through strategy functions to measure
performance without live API dependency. No lookahead bias — signals
from day N are filled at day N+1 open.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from agent.data_cache import get_cached_ohlcv

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    ticker: str
    side: str           # "BUY" or "SELL"
    entry_date: str
    entry_price: float
    exit_date: str = ""
    exit_price: float = 0.0
    shares: int = 0
    pnl: float = 0.0
    hold_days: int = 0


@dataclass
class BacktestResult:
    equity_curve: list[float] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    total_return: float = 0.0
    avg_trade_duration: float = 0.0
    total_trades: int = 0

    def summary(self) -> str:
        return (
            f"Total Return: {self.total_return:+.2%}\n"
            f"Sharpe Ratio: {self.sharpe:.2f}\n"
            f"Max Drawdown: {self.max_drawdown:.2%}\n"
            f"Win Rate:     {self.win_rate:.1%}\n"
            f"Total Trades: {self.total_trades}\n"
            f"Avg Duration: {self.avg_trade_duration:.1f} days"
        )


# ============================================================
# Technical Indicators (computed from OHLCV)
# ============================================================

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Standard Wilder smoothing RSI."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    # Wilder smoothing: first value is SMA, rest use exponential
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()

    # Apply Wilder smoothing after the initial SMA
    for i in range(period, len(gain)):
        avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD line, signal line, and histogram."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return pd.DataFrame({
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram,
    }, index=close.index)


def build_signals(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Build technical signals from OHLCV data. Returns DataFrame with indicators."""
    signals = ohlcv.copy()
    signals["rsi"] = compute_rsi(signals["Close"])
    macd_df = compute_macd(signals["Close"])
    signals = pd.concat([signals, macd_df], axis=1)
    # 20-day Bollinger Bands
    signals["sma_20"] = signals["Close"].rolling(20).mean()
    signals["bb_upper"] = signals["sma_20"] + 2 * signals["Close"].rolling(20).std()
    signals["bb_lower"] = signals["sma_20"] - 2 * signals["Close"].rolling(20).std()
    return signals


# ============================================================
# Built-in Strategy: Technical Mean Reversion
# ============================================================

def technical_reversion(signals: pd.DataFrame, ticker: str, day_idx: int) -> str | None:
    """RSI < 30 buy, RSI > 70 sell. Returns 'BUY', 'SELL', or None."""
    if day_idx < 14:
        return None  # Not enough data for RSI

    row = signals.iloc[day_idx]
    rsi = row.get("rsi")

    if pd.isna(rsi):
        return None

    if rsi < 30:
        return "BUY"
    elif rsi > 70:
        return "SELL"
    return None


# ============================================================
# Backtesting Engine
# ============================================================

SLIPPAGE_PCT = 0.001   # 0.1% slippage per trade
COMMISSION = 0.0       # Alpaca is commission-free
POSITION_SIZE_PCT = 0.05  # 5% of equity per trade
MAX_POSITIONS = 5


def backtest(
    strategy_fn,
    tickers: list[str],
    start_date: str,
    end_date: str,
    initial_capital: float = 100_000,
) -> BacktestResult:
    """Replay historical data through a strategy function.

    Args:
        strategy_fn: Callable(signals_df, ticker, day_idx) -> 'BUY' | 'SELL' | None
        tickers: List of stock symbols to trade
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD
        initial_capital: Starting cash

    Returns:
        BacktestResult with equity curve, trades, and performance metrics.
    """
    # Load and build signals for all tickers
    ticker_signals: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        ohlcv = get_cached_ohlcv(ticker, start_date, end_date)
        if ohlcv.empty:
            logger.warning("No cached data for %s between %s and %s", ticker, start_date, end_date)
            continue
        ticker_signals[ticker] = build_signals(ohlcv)

    if not ticker_signals:
        logger.error("No data for any tickers — cannot backtest")
        return BacktestResult()

    # Build a unified trading calendar from the union of all dates
    all_dates = sorted(set().union(*(df.index for df in ticker_signals.values())))

    cash = initial_capital
    positions: dict[str, dict] = {}  # ticker -> {shares, entry_price, entry_date}
    equity_curve = []
    completed_trades: list[Trade] = []
    pending_signals: list[dict] = []  # signals generated today, to fill tomorrow

    for i, current_date in enumerate(all_dates):
        date_str = current_date.strftime("%Y-%m-%d")

        # --- Step 1: Fill yesterday's signals at today's open (no lookahead) ---
        for sig in pending_signals:
            t = sig["ticker"]
            action = sig["action"]
            sigs_df = ticker_signals.get(t)
            if sigs_df is None or current_date not in sigs_df.index:
                continue

            fill_price = sigs_df.loc[current_date, "Open"]
            if pd.isna(fill_price) or fill_price <= 0:
                continue

            if action == "BUY" and t not in positions and len(positions) < MAX_POSITIONS:
                # Apply slippage (buying at slightly higher price)
                fill_price *= (1 + SLIPPAGE_PCT)
                position_value = cash * POSITION_SIZE_PCT
                shares = int(position_value / fill_price)
                if shares <= 0:
                    continue
                cost = shares * fill_price + COMMISSION
                if cost > cash:
                    continue
                cash -= cost
                positions[t] = {
                    "shares": shares,
                    "entry_price": fill_price,
                    "entry_date": date_str,
                }

            elif action == "SELL" and t in positions:
                # Apply slippage (selling at slightly lower price)
                fill_price *= (1 - SLIPPAGE_PCT)
                pos = positions.pop(t)
                proceeds = pos["shares"] * fill_price - COMMISSION
                cash += proceeds
                pnl = proceeds - (pos["shares"] * pos["entry_price"])
                entry_dt = datetime.strptime(pos["entry_date"], "%Y-%m-%d")
                exit_dt = datetime.strptime(date_str, "%Y-%m-%d")
                hold_days = (exit_dt - entry_dt).days
                completed_trades.append(Trade(
                    ticker=t,
                    side="BUY",
                    entry_date=pos["entry_date"],
                    entry_price=round(pos["entry_price"], 2),
                    exit_date=date_str,
                    exit_price=round(fill_price, 2),
                    shares=pos["shares"],
                    pnl=round(pnl, 2),
                    hold_days=hold_days,
                ))

        pending_signals.clear()

        # --- Step 2: Generate signals for today (filled tomorrow) ---
        for ticker, sigs_df in ticker_signals.items():
            if current_date not in sigs_df.index:
                continue
            day_idx = sigs_df.index.get_loc(current_date)
            action = strategy_fn(sigs_df, ticker, day_idx)
            if action:
                pending_signals.append({"ticker": ticker, "action": action})

        # --- Step 3: Mark-to-market portfolio value ---
        portfolio_value = cash
        for t, pos in positions.items():
            sigs_df = ticker_signals.get(t)
            if sigs_df is not None and current_date in sigs_df.index:
                price = sigs_df.loc[current_date, "Close"]
                if not pd.isna(price):
                    portfolio_value += pos["shares"] * price
            else:
                # Use entry price if no current data
                portfolio_value += pos["shares"] * pos["entry_price"]

        equity_curve.append(portfolio_value)

    # --- Force-close any open positions at last available price ---
    if positions and all_dates:
        last_date = all_dates[-1]
        last_date_str = last_date.strftime("%Y-%m-%d")
        for t, pos in list(positions.items()):
            sigs_df = ticker_signals.get(t)
            if sigs_df is not None and last_date in sigs_df.index:
                close_price = sigs_df.loc[last_date, "Close"] * (1 - SLIPPAGE_PCT)
            else:
                close_price = pos["entry_price"]
            proceeds = pos["shares"] * close_price
            pnl = proceeds - (pos["shares"] * pos["entry_price"])
            entry_dt = datetime.strptime(pos["entry_date"], "%Y-%m-%d")
            hold_days = (last_date - entry_dt).days
            completed_trades.append(Trade(
                ticker=t,
                side="BUY",
                entry_date=pos["entry_date"],
                entry_price=round(pos["entry_price"], 2),
                exit_date=last_date_str,
                exit_price=round(close_price, 2),
                shares=pos["shares"],
                pnl=round(pnl, 2),
                hold_days=hold_days,
            ))

    # --- Calculate Performance Metrics ---
    result = BacktestResult(
        equity_curve=equity_curve,
        trades=completed_trades,
        total_trades=len(completed_trades),
    )

    if len(equity_curve) >= 2:
        result.total_return = (equity_curve[-1] / equity_curve[0]) - 1

        # Sharpe ratio (annualized, assuming 252 trading days)
        daily_returns = pd.Series(equity_curve).pct_change().dropna()
        if len(daily_returns) > 1 and daily_returns.std() > 0:
            result.sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)

        # Max drawdown
        equity_series = pd.Series(equity_curve)
        running_max = equity_series.cummax()
        drawdown = (equity_series - running_max) / running_max
        result.max_drawdown = abs(drawdown.min())

    if completed_trades:
        wins = sum(1 for t in completed_trades if t.pnl > 0)
        result.win_rate = wins / len(completed_trades)
        result.avg_trade_duration = sum(t.hold_days for t in completed_trades) / len(completed_trades)

    return result


# ============================================================
# CLI: Run a quick backtest
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    tickers = ["AAPL", "NVDA", "AMD", "MSFT", "AMZN"]

    # 1 year backtest ending today
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

    print(f"\n=== Backtesting technical_reversion ===")
    print(f"Tickers: {', '.join(tickers)}")
    print(f"Period:  {start} to {end}")
    print(f"Capital: $100,000\n")

    result = backtest(
        strategy_fn=technical_reversion,
        tickers=tickers,
        start_date=start,
        end_date=end,
        initial_capital=100_000,
    )

    print(result.summary())

    if result.trades:
        print(f"\n--- Trade Log ({len(result.trades)} trades) ---")
        for t in result.trades[:20]:  # Show first 20
            print(f"  {t.ticker:5s} {t.entry_date} -> {t.exit_date}  "
                  f"${t.entry_price:8.2f} -> ${t.exit_price:8.2f}  "
                  f"P&L: ${t.pnl:+8.2f}  ({t.hold_days}d)")
