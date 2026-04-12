"""Agent configuration and hard risk limits.

These limits CANNOT be modified by the agent's self-improvement loop.
They are the non-negotiable guardrails that prevent catastrophic loss.
"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- HARD LIMITS (agent cannot modify these) ---

MAX_SINGLE_POSITION_PCT = 0.10      # 10% of bankroll on any one market
MAX_TOTAL_EXPOSURE_PCT = 0.80       # 80% max deployed (20% always cash)
MAX_CATEGORY_EXPOSURE_PCT = 0.50    # 50% max in any one category (e.g., BTC)
MAX_DAILY_LOSS_PCT = 0.05           # 5% daily loss → pause 24h
MAX_WEEKLY_LOSS_PCT = 0.10          # 10% weekly loss → pause + alert
MIN_EDGE_TO_TRADE = 0.10            # 10% minimum expected value edge
MIN_VOLUME_24H = 5000               # $5K minimum 24h volume
MIN_LIQUIDITY = 2000                # $2K minimum liquidity
ORDER_TYPE = "limit"                # Limit orders only — never market orders
MAX_SLIPPAGE = 0.03                 # 3% max slippage from target price

# --- STRATEGY WEIGHTS (Darwinian loop can modify these within bounds) ---

STRATEGY_WEIGHT_MIN = 0.3
STRATEGY_WEIGHT_MAX = 2.5
STRATEGY_WEIGHT_ADJUST = 0.05       # ±5% per evaluation cycle
EVAL_CYCLE_DAYS = 5                 # Evaluate and adjust every 5 days

# --- DEFAULT STRATEGY PARAMS (agent CAN modify these via self-improvement) ---

DEFAULT_STRATEGY_PARAMS = {
    "weather_edge": {
        "weight": 1.0,
        "min_edge_pct": 0.15,           # 15% edge minimum for weather
        "max_position_usd": 200,        # Conservative start
        "cities": ["nyc", "london", "chicago", "miami", "denver", "seattle"],
        "forecast_confidence_floor": 0.65,
    },
    "gimme_bets": {
        "weight": 1.0,
        "min_price": 0.92,              # 92%+ probability
        "max_days_to_expiry": 30,
        "max_position_usd": 500,
        "min_volume_24h": 10000,
    },
    "btc_sentiment": {
        "weight": 1.0,
        "conviction_threshold": 3,       # Minimum conviction score to trade
        "max_position_usd": 1000,
        "fear_greed_extreme_low": 20,    # Contrarian buy zone
        "fear_greed_extreme_high": 80,   # Contrarian sell zone
        "funding_rate_extreme": 0.01,    # 1% = overleveraged
    },
    "probability_arb": {
        "weight": 1.0,
        "min_gap_pct": 0.10,            # 10% minimum gap to trade
        "max_position_usd": 300,
        "max_markets_per_run": 15,       # Limit Claude API calls
        "require_confidence": "medium",  # Minimum: low, medium, or high
    },
}


def load_agent_config() -> dict:
    """Load agent config, merging defaults with any saved overrides."""
    config_path = PROJECT_ROOT / "agent" / "strategy_params.json"
    params = dict(DEFAULT_STRATEGY_PARAMS)

    if config_path.exists():
        try:
            saved = json.loads(config_path.read_text())
            for strategy, overrides in saved.items():
                if strategy in params:
                    params[strategy].update(overrides)
        except (json.JSONDecodeError, OSError):
            pass

    return params


def save_strategy_params(params: dict) -> None:
    """Save strategy params (used by Darwinian loop to persist improvements)."""
    config_path = PROJECT_ROOT / "agent" / "strategy_params.json"
    config_path.write_text(json.dumps(params, indent=2))
