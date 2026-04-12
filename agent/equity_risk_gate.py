"""Equity risk gate — approves or rejects trade proposals.

Every proposal from equity_strategies.py passes through here before execution.
Enforces hard limits from equity_config.py that cannot be overridden.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import datetime

from agent import equity_config as config
from agent.equity_db import (
    get_todays_trades, get_open_positions_from_trades,
    check_wash_sale, get_current_drawdown, get_drawdown_pause_until,
)
from agent.equity_executor import get_positions, get_account

logger = logging.getLogger(__name__)


def _is_market_hours() -> bool:
    """Check if US market is currently open (9:30-16:00 ET)."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from datetime import timezone, timedelta
        ZoneInfo = None

    now = datetime.now()
    if ZoneInfo:
        now = datetime.now(ZoneInfo("America/New_York"))

    # Weekday check (0=Mon, 6=Sun)
    if now.weekday() >= 5:
        return False

    hour, minute = now.hour, now.minute
    market_open = (hour == 9 and minute >= 30) or hour >= 10
    market_close = hour < 16
    return market_open and market_close


def _get_sector_exposure(positions: list) -> dict:
    """Calculate current $ exposure by sector."""
    exposure = {}
    for pos in positions:
        ticker = pos.get("ticker", "")
        sector = config.TICKER_SECTOR.get(ticker, "Unknown")
        value = pos.get("market_value") or (pos.get("qty", 0) * pos.get("avg_cost", 0))
        exposure[sector] = exposure.get(sector, 0) + value
    return exposure


def _get_portfolio_correlation(ticker: str) -> float | None:
    """Check correlation of ticker against existing positions.

    Reads from the portfolio module's correlation data.
    """
    from lib.data_envelope import load_envelope
    portfolio = load_envelope("portfolio.json")
    if portfolio.get("status") != "success":
        return None

    correlations = portfolio.get("data", {}).get("correlations", {})
    if not correlations:
        return None

    # Check correlation with any existing position
    max_corr = 0
    for pair_key, corr_val in correlations.items():
        if ticker in pair_key:
            # corr_val can be a float or a dict with {"correlation": float}
            if isinstance(corr_val, dict):
                c = corr_val.get("correlation")
            else:
                c = corr_val
            if c is not None:
                max_corr = max(max_corr, abs(c))

    return max_corr if max_corr > 0 else None


def check_proposal(proposal: dict, portfolio_value: float, force: bool = False) -> dict:
    """Check a trade proposal against all risk limits.

    Returns: {"approved": bool, "reason": str, "adjusted_quantity": float}
    """
    ticker = proposal.get("ticker", "")
    side = proposal.get("side", "BUY")
    price = proposal.get("limit_price", 0)
    conviction = proposal.get("conviction", 0)
    sector = proposal.get("sector", "Unknown")
    quantity = proposal.get("quantity_override") or proposal.get("quantity", 0)

    # If no quantity set yet, calculate from position sizing
    if quantity <= 0 and price > 0:
        max_position_usd = portfolio_value * config.MAX_SINGLE_POSITION_PCT
        quantity = max_position_usd / price

    cost = price * quantity if price and quantity else 0

    # --- CHECK 1: Market hours ---
    if not force and not _is_market_hours():
        return {
            "approved": False,
            "reason": "Market closed — equity orders only during 9:30-16:00 ET",
        }

    # --- CHECK 2: Minimum conviction ---
    if conviction < config.MIN_CONVICTION_SCORE:
        return {
            "approved": False,
            "reason": f"Conviction {conviction} below minimum {config.MIN_CONVICTION_SCORE}",
        }

    # --- CHECK 3: Drawdown circuit breaker ---
    pause_until = get_drawdown_pause_until()
    if pause_until and side == "BUY":
        return {
            "approved": False,
            "reason": f"Drawdown pause active until {pause_until} — no new buys",
        }

    # --- CHECK 4: Daily loss limit ---
    acct = get_account()
    # Use Alpaca's equity if available, otherwise fallback
    equity = acct.get("equity") or portfolio_value

    # --- CHECK 5: Single position size ---
    max_single = portfolio_value * config.MAX_SINGLE_POSITION_PCT
    if cost > max_single:
        quantity = max_single / price if price > 0 else 0
        cost = max_single
        logger.info("Position sized down to $%.0f (%.0f%% cap)", max_single,
                     config.MAX_SINGLE_POSITION_PCT * 100)

    # --- CHECK 6: Total exposure ---
    positions = get_positions()
    total_invested = sum(
        p.get("market_value") or (p.get("qty", 0) * p.get("avg_cost", 0))
        for p in positions
    )
    if side == "BUY":
        total_after = total_invested + cost
        max_total = portfolio_value * config.MAX_TOTAL_EXPOSURE_PCT
        if total_after > max_total:
            remaining = max_total - total_invested
            if remaining <= 0:
                return {
                    "approved": False,
                    "reason": f"Total exposure ${total_invested:,.0f} at "
                              f"{config.MAX_TOTAL_EXPOSURE_PCT*100:.0f}% cap — no room",
                }
            quantity = remaining / price if price > 0 else 0
            cost = remaining

    # --- CHECK 7: Max concurrent positions ---
    if side == "BUY" and len(positions) >= config.MAX_CONCURRENT_POSITIONS:
        # Check if we already hold this ticker (adding to position is OK)
        held_tickers = {p["ticker"] for p in positions}
        if ticker not in held_tickers:
            return {
                "approved": False,
                "reason": f"{len(positions)} positions open — max {config.MAX_CONCURRENT_POSITIONS}",
            }

    # --- CHECK 8: Sector concentration ---
    if side == "BUY":
        sector_exposure = _get_sector_exposure(positions)
        sector_after = sector_exposure.get(sector, 0) + cost
        max_sector = portfolio_value * config.MAX_SECTOR_EXPOSURE_PCT
        if sector_after > max_sector:
            remaining = max_sector - sector_exposure.get(sector, 0)
            if remaining <= 0:
                return {
                    "approved": False,
                    "reason": f"Sector '{sector}' at ${sector_exposure.get(sector, 0):,.0f} — "
                              f"{config.MAX_SECTOR_EXPOSURE_PCT*100:.0f}% cap hit",
                }
            quantity = remaining / price if price > 0 else 0
            cost = remaining

    # --- CHECK 9: Correlation check ---
    if side == "BUY":
        max_corr = _get_portfolio_correlation(ticker)
        if max_corr and max_corr > config.MAX_CORRELATION:
            return {
                "approved": False,
                "reason": f"{ticker} correlation {max_corr:.2f} exceeds {config.MAX_CORRELATION} limit",
            }

    # --- CHECK 10: Wash sale warning ---
    if side == "BUY":
        wash = check_wash_sale(ticker)
        if wash:
            logger.warning(
                "WASH SALE: %s was sold at loss on %s — rebuy blocked until %s",
                ticker, wash["sell_date"], wash["rebuy_window_end"],
            )
            return {
                "approved": False,
                "reason": f"Wash sale: {ticker} sold at loss on {wash['sell_date']}, "
                          f"blocked until {wash['rebuy_window_end']}",
            }

    # --- CHECK 11: Duplicate check ---
    todays = get_todays_trades()
    for t in todays:
        if t.get("ticker") == ticker and t.get("side") == side:
            return {
                "approved": False,
                "reason": f"Already {side.lower()} {ticker} today — skipping duplicate",
            }

    # --- CHECK 12: Minimum trade size ---
    if cost < 10:
        return {
            "approved": False,
            "reason": f"Position size ${cost:.2f} too small — minimum $10",
        }

    return {
        "approved": True,
        "reason": f"Approved: {side} {ticker} {quantity:.2f} shares @ ${price:.2f} "
                  f"(${cost:,.0f}, conviction {conviction})",
        "adjusted_quantity": round(quantity, 4),
    }


def filter_proposals(proposals: list, portfolio_value: float, force: bool = False) -> list:
    """Run all proposals through the risk gate. Returns approved proposals."""
    approved = []

    for p in proposals:
        result = check_proposal(p, portfolio_value, force=force)
        p["risk_check"] = result

        if result["approved"]:
            # Apply adjusted quantity from risk gate
            if "adjusted_quantity" in result:
                p["quantity"] = result["adjusted_quantity"]
            elif "quantity_override" in p:
                p["quantity"] = p["quantity_override"]
            elif p.get("limit_price", 0) > 0:
                max_usd = portfolio_value * config.MAX_SINGLE_POSITION_PCT
                p["quantity"] = round(max_usd / p["limit_price"], 4)

            approved.append(p)
            logger.info("APPROVED: %s %s — %s", p["side"], p["ticker"], result["reason"])
        else:
            logger.info("REJECTED: %s %s — %s", p.get("side", "?"), p.get("ticker", "?"),
                        result["reason"])

    return approved
