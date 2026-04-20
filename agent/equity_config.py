"""Equity trading configuration and hard risk limits.

These limits CANNOT be modified by the agent's self-improvement loop.
They are the non-negotiable guardrails that prevent catastrophic loss.

Mirrors agent/config.py (Polymarket) but tuned for stock trading.
"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- HARD LIMITS (agent cannot modify these) ---

MAX_SINGLE_POSITION_PCT = 0.05      # 5% of portfolio on any one stock (hard cap)
RISK_PER_TRADE_PCT = 0.01           # 1% of equity at risk per trade (ATR-normalized sizing)
ATR_STOP_MULTIPLIER = 2.0           # Stop distance = 2x ATR(14). Canonical default.
MAX_TOTAL_EXPOSURE_PCT = 0.80       # 80% max invested (20% always cash)
MAX_SECTOR_EXPOSURE_PCT = 0.30      # 30% max in any one GICS sector
MAX_DAILY_LOSS_PCT = 0.015          # 1.5% daily loss → pause trading
MAX_DRAWDOWN_PCT = 0.05             # 5% peak-to-trough → pause 3 days
MAX_DRAWDOWN_PAUSE_DAYS = 3         # Days to pause after drawdown breach
MAX_CONCURRENT_POSITIONS = 5        # Max open positions at once
MAX_CORRELATION = 0.80              # Reject trades >0.8 correlated with existing
MIN_CONVICTION_SCORE = 3            # Minimum combined signal score to trade
ORDER_TYPE = "limit"                # Limit orders only — never market
TIME_IN_FORCE = "day"               # Orders expire at close

# Sector mapping for GICS sector concentration checks
TICKER_SECTOR = {
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "AMD": "Technology", "GOOGL": "Communication Services",
    "META": "Communication Services", "AMZN": "Consumer Discretionary",
    "TSLA": "Consumer Discretionary",
    "XLK": "Technology", "XLF": "Financials", "XLE": "Energy",
    "XLV": "Health Care", "XLI": "Industrials",
    "XLY": "Consumer Discretionary", "XLP": "Consumer Staples",
    "XLU": "Utilities", "XLB": "Materials", "XLRE": "Real Estate",
    "XLC": "Communication Services", "VOO": "Broad Market",
}

# --- STRATEGY WEIGHTS (Darwinian loop can modify these within bounds) ---

STRATEGY_WEIGHT_MIN = 0.3
STRATEGY_WEIGHT_MAX = 2.5
STRATEGY_WEIGHT_ADJUST = 0.05
EVAL_CYCLE_DAYS = 7                 # Evaluate weekly (equities move slower than prediction markets)

# --- DEFAULT STRATEGY PARAMS (agent CAN modify via self-improvement) ---

DEFAULT_EQUITY_STRATEGY_PARAMS = {
    "congressional_conviction": {
        "weight": 1.0,
        "min_cluster_size": 2,          # At least 2 congress members buying
        "min_amount": 100000,           # $100K+ trades only
        "lookback_days": 45,            # Match the 45-day disclosure window
        "tone_boost_threshold": 1.0,    # Earnings tone > +1 adds conviction
        "tone_penalty_threshold": -2.0, # Earnings tone < -2 blocks the trade
    },
    "technical_reversion": {
        # Connors RSI(2) mean reversion — April 2026 research
        "weight": 1.0,
        "rsi2_buy_threshold": 10,        # RSI(2) < 10 for buys
        "rsi2_sell_threshold": 90,       # RSI(2) > 90 for sells
        "vix_percentile_floor": 50,      # Only trade when vol regime allows
        "tickers": ["AAPL", "NVDA", "MSFT", "AMD", "GOOGL", "AMZN", "TSLA",
                     "META", "SPY", "QQQ"],
    },
    "sector_rotation": {
        "weight": 1.0,
        "rebalance_days": 21,           # Rebalance every ~1 month
        "min_relative_strength": 1.0,   # Minimum RS score to be a leader
        "top_n_sectors": 3,             # Overweight top 3 sectors
        "position_per_sector_usd": 500, # $ per sector ETF position
    },
    "overnight_drift": {
        # Elm Wealth / arxiv 2025 — close-to-open captures SPY's +47% 5y drift
        "weight": 1.0,
        "tickers": ["SPY", "QQQ"],
        "vix_spike_pct": 0.15,          # Skip if VIX spikes >15% intraday
        "allow_any_time": False,        # True = fires any time of day (backtest/testing)
    },
}


def load_equity_config() -> dict:
    """Load equity strategy config, merging defaults with saved overrides."""
    config_path = PROJECT_ROOT / "agent" / "equity_strategy_params.json"
    params = {}
    for k, v in DEFAULT_EQUITY_STRATEGY_PARAMS.items():
        params[k] = dict(v)

    if config_path.exists():
        try:
            saved = json.loads(config_path.read_text())
            for strategy, overrides in saved.items():
                if strategy in params:
                    params[strategy].update(overrides)
        except (json.JSONDecodeError, OSError):
            pass

    return params


def save_equity_params(params: dict) -> None:
    """Save equity strategy params (used by Darwinian loop)."""
    config_path = PROJECT_ROOT / "agent" / "equity_strategy_params.json"
    config_path.write_text(json.dumps(params, indent=2))
