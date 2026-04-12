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


def _get_atr_stop(ticker: str, current_price: float, side: str) -> float | None:
    """Get ATR-based stop loss price from portfolio module data."""
    from lib.data_envelope import load_envelope
    portfolio = load_envelope("portfolio.json")
    if portfolio.get("status") != "success":
        return None

    for h in portfolio.get("data", {}).get("holdings", []):
        if h.get("ticker") == ticker and h.get("atr"):
            atr = h["atr"]
            # 2x ATR stop for buys, 2x ATR for sells (inverse)
            if side.upper() == "BUY":
                return round(current_price - (2.0 * atr), 2)
            else:
                return round(current_price + (2.0 * atr), 2)

    # Default: 5% stop if no ATR data
    if side.upper() == "BUY":
        return round(current_price * 0.95, 2)
    return round(current_price * 1.05, 2)


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
    stop_price: float = None,
    take_profit: float = None,
) -> dict:
    """Place an order via Alpaca with optional bracket (stop + take-profit).

    For BUY orders, automatically attaches an ATR-based stoploss unless
    stop_price is explicitly provided or set to 0 to disable.
    """
    mode = get_mode()
    now = datetime.now().astimezone().isoformat()

    # Sanity checks
    if quantity <= 0:
        return {"status": "error", "error": f"Invalid quantity {quantity}", "timestamp": now}
    if limit_price is not None and limit_price <= 0:
        return {"status": "error", "error": f"Invalid price {limit_price}", "timestamp": now}

    # Alpaca doesn't allow fractional short sells — round sells to whole shares
    # and block sells for stocks we don't hold (no naked shorting)
    if side.upper() == "SELL" and client is None:
        pass  # offline mode, allow anything
    elif side.upper() == "SELL":
        positions = get_positions()
        held = {p["ticker"]: p["qty"] for p in positions}
        if ticker not in held or held[ticker] <= 0:
            return {"status": "skipped", "error": f"Don't hold {ticker} — skipping sell (no short selling)",
                    "timestamp": now}
        # Can only sell what we own
        quantity = min(quantity, held[ticker])
        # Round to whole shares for sells to avoid fractional short sell error
        quantity = int(quantity)

    # Auto-calculate ATR stoploss for BUY orders if not provided
    if side.upper() == "BUY" and stop_price is None and limit_price:
        stop_price = _get_atr_stop(ticker, limit_price, side)

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
        "stop_price": stop_price,
        "take_profit": take_profit,
    }

    client = get_client()

    # Offline paper mode — no Alpaca keys, just log it
    if not client:
        order_record["status"] = "paper_filled"
        order_record["fill_price"] = limit_price
        order_record["slippage"] = 0
        order_record["alpaca_order_id"] = f"offline_{int(datetime.now().timestamp())}"

        row_id = record_trade(order_record)
        stop_info = f" stop=${stop_price:.2f}" if stop_price else ""
        logger.info(
            "[PAPER-OFFLINE] %s %s %.2f shares @ $%.2f ($%.2f)%s — %s | %s",
            side.upper(), ticker, quantity, limit_price or 0,
            (limit_price or 0) * quantity, stop_info, strategy, reason[:60],
        )
        # Send notification
        try:
            from lib.notify import alert_trade_placed
            alert_trade_placed(order_record)
        except Exception:
            pass
        return order_record

    # Alpaca API execution (paper or live)
    try:
        from alpaca.trading.requests import (
            LimitOrderRequest, MarketOrderRequest, OrderRequest,
        )
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

        order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL

        # Use bracket order if we have stop and/or take-profit
        has_bracket = side.upper() == "BUY" and (stop_price or take_profit)

        if has_bracket and limit_price:
            # Bracket order: entry limit + stoploss + optional take-profit
            order_kwargs = {
                "symbol": ticker,
                "qty": round(quantity, 4),
                "side": order_side,
                "time_in_force": TimeInForce.DAY,
                "order_class": OrderClass.BRACKET,
                "limit_price": round(limit_price, 2),
            }
            if stop_price:
                order_kwargs["stop_loss"] = {"stop_price": round(stop_price, 2)}
            if take_profit:
                order_kwargs["take_profit"] = {"limit_price": round(take_profit, 2)}

            req = OrderRequest(**order_kwargs)
        elif limit_price:
            req = LimitOrderRequest(
                symbol=ticker,
                qty=round(quantity, 4),
                side=order_side,
                time_in_force=TimeInForce.DAY,
                limit_price=round(limit_price, 2),
            )
        else:
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

        stop_info = f" stop=${stop_price:.2f}" if stop_price else ""
        tp_info = f" tp=${take_profit:.2f}" if take_profit else ""
        logger.info(
            "[%s] %s %s %.2f shares @ $%.2f%s%s — %s (order %s)",
            mode.upper(), side.upper(), ticker, quantity,
            limit_price or 0, stop_info, tp_info, strategy, order.id,
        )

    except Exception as e:
        order_record["status"] = "error"
        order_record["error"] = str(e)
        logger.error("Order failed for %s: %s", ticker, e)

    # Record every order attempt to SQLite
    record_trade(order_record)

    # Send notification
    try:
        from lib.notify import alert_trade_placed
        alert_trade_placed(order_record)
    except Exception:
        pass

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
