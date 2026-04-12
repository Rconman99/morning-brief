"""Options trading configuration and hard risk limits.

Tuned for the wheel strategy (CSP → covered calls).
Based on tastytrade research: 30-delta, 45 DTE, close at 50% profit.
"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- HARD LIMITS (agent cannot modify these) ---

MAX_CONTRACTS_PER_POSITION = 2          # Max 2 contracts per underlying
MAX_OPTIONS_BUYING_POWER_PCT = 0.40     # 40% of buying power for options total
MAX_SINGLE_POSITION_PCT = 0.20          # 20% of account per single options position
MIN_IV_RANK = 30                        # Don't sell when IV is cheap
OPTIMAL_IV_RANK = 50                    # Best entry zone for selling premium
MIN_OPEN_INTEREST = 200                 # Liquidity floor per contract
MIN_BID_ASK_RATIO = 0.80               # Bid must be >= 80% of ask (spread check)
CLOSE_AT_PROFIT_PCT = 0.50             # Close at 50% of max credit received
ROLL_AT_DTE = 21                        # Roll or close at 21 DTE
MAX_ROLLS = 2                           # Max 2 rolls before taking assignment
NO_SELL_BEFORE_EARNINGS_DAYS = 7        # Don't sell options 7 days before earnings
CLOSE_BY_EXPIRY_DTE = 1                # Close positions day before expiry (pin risk)
MIN_PREMIUM = 0.50                      # Minimum $0.50 premium ($50/contract) to bother

# Wheel universe — liquid stocks with affordable 100-share lots
# At $100K account: $200/share * 100 shares = $20K collateral = 20% of account
OPTIONS_WHEEL_UNIVERSE = ["AAPL", "NVDA", "AMD", "MSFT", "AMZN"]

# --- STRATEGY PARAMS (Darwinian loop can adjust within bounds) ---

DEFAULT_OPTIONS_STRATEGY_PARAMS = {
    "wheel_csp": {
        "weight": 1.0,
        "target_delta": 0.30,           # 30-delta (~70% chance OTM)
        "delta_range": (0.20, 0.40),    # Acceptable delta range
        "target_dte": 45,               # Open at 45 DTE
        "dte_range": (30, 60),          # Acceptable DTE range
        "min_iv_rank": 30,
        "require_uptrend": True,        # Stock must be above 50-day SMA
    },
    "wheel_cc": {
        "weight": 1.0,
        "target_delta": 0.30,
        "delta_range": (0.15, 0.35),
        "target_dte": 35,               # Slightly shorter for CCs
        "dte_range": (21, 50),
        "sell_above_cost_basis": True,   # Strike must be above avg cost
    },
}


def load_options_config() -> dict:
    """Load options strategy config, merging defaults with saved overrides."""
    config_path = PROJECT_ROOT / "agent" / "options_strategy_params.json"
    params = {}
    for k, v in DEFAULT_OPTIONS_STRATEGY_PARAMS.items():
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
