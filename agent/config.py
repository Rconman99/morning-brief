"""Agent configuration and hard risk limits.

These limits CANNOT be modified by the agent's self-improvement loop.
They are the non-negotiable guardrails that prevent catastrophic loss.
"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- HARD LIMITS (agent cannot modify these) ---

MAX_SINGLE_POSITION_PCT = 0.10      # 10% of bankroll on any one market
MAX_TOTAL_EXPOSURE_PCT = 0.95       # 95% max deployed (5% cash buffer)
MAX_CATEGORY_EXPOSURE_PCT = 0.50    # 50% max in any one category (e.g., BTC)
MAX_DAILY_LOSS_PCT = 0.05           # 5% daily loss → pause 24h
MAX_WEEKLY_LOSS_PCT = 0.10          # 10% weekly loss → pause + alert
MIN_EDGE_TO_TRADE = 0.10            # 10% minimum expected value edge
MIN_VOLUME_24H = 5000               # $5K minimum 24h volume
MIN_LIQUIDITY = 2000                # $2K minimum liquidity
ORDER_TYPE = "limit"                # Limit orders only — never market orders
MAX_SLIPPAGE = 0.03                 # 3% max slippage from target price

# --- AUTO-EXIT (capital recycling) ---
# Take profit on any open position once price reaches this threshold.
# Applied every agent cycle BEFORE scanning new opportunities.
AUTO_EXIT_PRICE = 0.985             # Sell when bid >= 98.5% (capture ~75% of edge, free capital)

# --- COPY TRADING (opt-in; disabled by default for safety) ---
COPY_TRADER_ENABLED = False         # Flip to True to emit copy-trade proposals
STAKE_WHALE_PCT = 0.01              # 1% of bankroll per copy (fractional Kelly)
COPY_MIN_WHALE_USD = 10_000         # Only mirror whales ≥ $10K
COPY_MIN_CONFIDENCE = "high"        # "high" or "medium" — floor for flow signals
COPY_MAX_SLIPPAGE = 0.02            # Reserved for future mid→fill guard
COPY_MAX_PER_RUN = 3                # Cap new copies per agent run

# --- DAILY LOSS KILL-SWITCH (account-level; realized P&L) ---
# If set (negative USD, e.g. -50.0), pauses trading when today's realized pnl_usd
# from settled/resolved/closed trades goes below this threshold. If None, derives
# from MAX_DAILY_LOSS_PCT. Only closed trades count — unfilled limit orders don't.
MAX_DAILY_LOSS_USD = None

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
        "min_price": 0.94,              # 94%+ probability (tighter floor for fast cycling)
        "max_days_to_expiry": 7,        # Only short-cycle markets (was 30 — locked capital too long)
        "max_position_pct": 0.10,       # 10% of bankroll — risk gate enforces same ceiling
        "max_position_usd": 10,         # Absolute floor cap (used if bankroll not injected)
        "min_volume_24h": 50000,        # Need depth to exit at target (was 10K)
        "min_gross_edge": 0.03,         # Skip cycles below 3% gross — fees eat smaller edges
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

# --- MICROSTRUCTURE SETTINGS (from Becker research) ---
# These inform the market_microstructure.py filter

LONGSHOT_BLOCK_THRESHOLD = 0.15     # Block YES buys below 15 cents
LONGSHOT_PENALTY_THRESHOLD = 0.35   # Penalize YES buys below 35 cents
PREFER_NO_SIDE = True               # Prefer NO side in longshot zone (exploits bias)
MAKER_ONLY = True                   # Always use limit orders (makers win, takers lose)
CATEGORY_EDGE_ENABLED = True        # Adjust conviction by category
TIME_OF_DAY_ENABLED = True          # Adjust for informed vs retail flow hours


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
