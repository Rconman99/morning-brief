"""Alpaca order executor — handles authentication and order placement.

Supports two modes:
- PAPER: Alpaca paper trading (separate base URL, same API)
- LIVE: Alpaca live trading

Set ALPACA_API_KEY + ALPACA_SECRET_KEY in .env.
Set ALPACA_PAPER=true (default) for paper trading.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import logging
import os
from datetime import datetime

from agent.equity_db import record_trade, get_open_positions_from_trades

logger = logging.getLogger(__name__)

_cached_client = None


def get_mode() -> str:
    """Returns 'live' if keys are set and ALPACA_PAPER is explicitly false."""
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    paper = os.environ.get("ALPACA_PAPER", "true").lower()

    if not key or not secret:
        return "paper"
    if paper in ("false", "0", "no"):
        return "live"
    return "paper"


def get_client():
    """Create an Alpaca TradingClient. Cached after first call.

    Returns None if alpaca-py not installed or keys missing.
    """
    global _cached_client
    if _cached_client is not None:
        return _cached_client

    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")

    if not key or not secret:
        logger.warning("No Alpaca API keys — running in offline paper mode")
        return None

    try:
        from alpaca.trading.client import TradingClient
        paper = get_mode() == "paper"
        client = TradingClient(key, secret, paper=paper)
        _cached_client = client
        logger.info("Alpaca client initialized [%s mode]", "paper" if paper else "LIVE")
        return client
    except Exception as e:
        logger.error("Failed to create Alpaca client: %s", e)
        return None


def get_account() -> dict:
    """Get Alpaca account info (equity, buying power, etc.)."""
    client = get_client()
    if not client:
        return {"equity": 0, "buying_power": 0, "cash": 0, "status": "offline"}

    try:
        acct = client.get_account()
        return {
            "equity": float(acct.equity),
            "buying_power": float(acct.buying_power),
            "cash": float(acct.cash),
            "portfolio_value": float(acct.portfolio_value),
            "status": acct.status,
            "pattern_day_trader": acct.pattern_day_trader,
            "day_trade_count": acct.daytrade_count,
        }
    except Exception as e:
        logger.error("Failed to get account: %s", e)
        return {"equity": 0, "buying_power": 0, "cash": 0, "status": "error", "error": str(e)}


def get_balance() -> float:
    """Get available buying power."""
    acct = get_account()
    return acct.get("buying_power", 0)


def get_positions() -> list[dict]:
    """Get current Alpaca positions."""
    client = get_client()
    if not client:
        # Fallback to trade-history-derived positions
        db_positions = get_open_positions_from_trades()
        return [
            {"ticker": t, "qty": p["qty"], "avg_cost": p["avg_cost"], "sector": p["sector"]}
            for t, p in db_positions.items()
        ]

    try:
        positions = client.get_all_positions()
        return [
            {
                "ticker": p.symbol,
                "qty": float(p.qty),
                "avg_cost": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pnl": float(p.unrealized_pl),
                "unrealized_pnl_pct": float(p.unrealized_plpc),
                "current_price": float(p.current_price),
                "side": p.side,
            }
            for p in positions
        ]
    except Exception as e:
        logger.error("Failed to get positions: %s", e)
        return []


def place_order(
    ticker: str,
    side: str,
    quantity: float,
    limit_price: float = None,
    strategy: str = "",
    reason: str = "",
    conviction: int = 0,
    sector: str = "",
    run_id: str = "",
) -> dict:
    """Place a limit order via Alpaca. Returns order result dict.

    In offline mode (no keys), records as paper trade without hitting Alpaca.
    """
    mode = get_mode()
    now = datetime.now().astimezone().isoformat()

    # Sanity checks
    if quantity <= 0:
        return {"status": "error", "error": f"Invalid quantity {quantity}", "timestamp": now}
    if limit_price is not None and limit_price <= 0:
        return {"status": "error", "error": f"Invalid price {limit_price}", "timestamp": now}

    order_record = {
        "timestamp": now,
        "ticker": ticker,
        "side": side.upper(),
        "quantity": round(quantity, 4),
        "price": round(limit_price, 2) if limit_price else 0,
        "cost_usd": round((limit_price or 0) * quantity, 2),
        "strategy": strategy,
        "reason": reason,
        "conviction": conviction,
        "sector": sector,
        "mode": mode,
        "status": "pending",
        "run_id": run_id,
    }

    client = get_client()

    # Offline paper mode — no Alpaca keys, just log it
    if not client:
        order_record["status"] = "paper_filled"
        order_record["fill_price"] = limit_price
        order_record["slippage"] = 0
        order_record["alpaca_order_id"] = f"offline_{int(datetime.now().timestamp())}"

        row_id = record_trade(order_record)
        logger.info(
            "[PAPER-OFFLINE] %s %s %.2f shares @ $%.2f ($%.2f) — %s | %s",
            side.upper(), ticker, quantity, limit_price or 0,
            (limit_price or 0) * quantity, strategy, reason[:60],
        )
        return order_record

    # Alpaca API execution (paper or live)
    try:
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL

        if limit_price:
            req = LimitOrderRequest(
                symbol=ticker,
                qty=round(quantity, 4),
                side=order_side,
                time_in_force=TimeInForce.DAY,
                limit_price=round(limit_price, 2),
            )
        else:
            # Fallback to market order only if no price given (should be rare)
            req = MarketOrderRequest(
                symbol=ticker,
                qty=round(quantity, 4),
                side=order_side,
                time_in_force=TimeInForce.DAY,
            )

        order = client.submit_order(req)

        order_record["status"] = "submitted"
        order_record["alpaca_order_id"] = str(order.id)
        order_record["fill_price"] = float(order.filled_avg_price) if order.filled_avg_price else None

        if order.filled_avg_price and limit_price:
            order_record["slippage"] = round(
                abs(float(order.filled_avg_price) - limit_price), 4
            )

        logger.info(
            "[%s] %s %s %.2f shares @ $%.2f — %s (order %s)",
            mode.upper(), side.upper(), ticker, quantity,
            limit_price or 0, strategy, order.id,
        )

    except Exception as e:
        order_record["status"] = "error"
        order_record["error"] = str(e)
        logger.error("Order failed for %s: %s", ticker, e)

    # Record every order attempt to SQLite
    record_trade(order_record)
    return order_record


def close_position(ticker: str) -> dict:
    """Close an entire position. Emergency use."""
    client = get_client()
    if not client:
        return {"status": "error", "error": "No client — offline mode"}

    try:
        client.close_position(ticker)
        logger.warning("CLOSED position: %s", ticker)
        return {"status": "closed", "ticker": ticker}
    except Exception as e:
        logger.error("Failed to close %s: %s", ticker, e)
        return {"status": "error", "ticker": ticker, "error": str(e)}


def close_all_positions() -> list:
    """Kill switch — flatten everything."""
    client = get_client()
    if not client:
        return [{"status": "error", "error": "No client — offline mode"}]

    try:
        client.close_all_positions(cancel_orders=True)
        logger.warning("KILL SWITCH: closed all positions and cancelled all orders")
        return [{"status": "all_closed"}]
    except Exception as e:
        logger.error("Kill switch failed: %s", e)
        return [{"status": "error", "error": str(e)}]
