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


def _get_atr_value(ticker: str) -> float | None:
    """Fetch 14-day ATR for a ticker. Portfolio cache first, yfinance fallback."""
    from lib.data_envelope import load_envelope
    portfolio = load_envelope("portfolio.json")
    if portfolio.get("status") == "success":
        for h in portfolio.get("data", {}).get("holdings", []):
            if h.get("ticker") == ticker and h.get("atr"):
                return float(h["atr"])

    try:
        from lib.api import yahoo_finance_price_history
        df = yahoo_finance_price_history(ticker, period="2mo")
        if df is None or df.empty or len(df) < 15:
            return None
        high, low, close = df["High"], df["Low"], df["Close"]
        prev_close = close.shift(1)
        tr = (high - low).combine((high - prev_close).abs(), max).combine(
            (low - prev_close).abs(), max)
        atr = tr.rolling(14).mean().iloc[-1]
        return float(atr) if atr and atr > 0 else None
    except Exception:
        return None


def _get_atr_stop(ticker: str, current_price: float, side: str) -> float | None:
    """Stop price at 2x ATR from entry. Falls back to 5% if no ATR."""
    atr = _get_atr_value(ticker)
    if atr:
        if side.upper() == "BUY":
            return round(current_price - (2.0 * atr), 2)
        return round(current_price + (2.0 * atr), 2)
    # Fallback: 5% stop
    if side.upper() == "BUY":
        return round(current_price * 0.95, 2)
    return round(current_price * 1.05, 2)


def _get_atr_take_profit(ticker: str, current_price: float, side: str) -> float | None:
    """Take-profit at 3x ATR from entry (1.5:1 R:R vs 2x ATR stop).

    Research basis: LuxAlgo, de Prado recommend minimum 1.5:1 R:R for
    positive expectancy. 3x ATR target + 2x ATR stop = 1.5:1 natural R:R.
    """
    atr = _get_atr_value(ticker)
    if atr:
        if side.upper() == "BUY":
            return round(current_price + (3.0 * atr), 2)
        return round(current_price - (3.0 * atr), 2)
    # Fallback: 7.5% target (matches 5% stop at 1.5:1)
    if side.upper() == "BUY":
        return round(current_price * 1.075, 2)
    return round(current_price * 0.925, 2)


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
    client = get_client()

    # Sanity checks
    if quantity <= 0:
        return {"status": "error", "error": f"Invalid quantity {quantity}", "timestamp": now}
    if limit_price is not None and limit_price <= 0:
        return {"status": "error", "error": f"Invalid price {limit_price}", "timestamp": now}

    # Dedupe: skip if an open order already exists for this symbol+side this run.
    # Reason: twice on April 13 the agent submitted two identical NVDA buys in the
    # same run because a retry loop didn't check existing state.
    if client is not None:
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            open_req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[ticker], limit=50)
            open_orders = client.get_orders(open_req)
            for o in open_orders:
                if o.symbol == ticker and o.side.value.lower() == side.lower():
                    logger.info("SKIP duplicate: existing open %s order %s for %s", side, o.id, ticker)
                    return {"status": "skipped", "error": f"open {side} order already exists ({o.id})",
                            "ticker": ticker, "timestamp": now}
        except Exception as e:
            logger.debug("Dedupe check failed (non-fatal): %s", e)

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

    # Auto-attach 2x ATR stop + 3x ATR take-profit to every BUY entry.
    # Positions get protection even if launchd misses a fire (the prior bug where
    # orders could live for days with no exit plan). Caller can opt out by
    # passing stop_price=0 and take_profit=0 explicitly.
    if side.upper() == "BUY" and limit_price:
        if stop_price is None:
            stop_price = _get_atr_stop(ticker, limit_price, side)
        if take_profit is None:
            take_profit = _get_atr_take_profit(ticker, limit_price, side)

    # Alpaca rejects bracket orders with fractional qty:
    # "fractional orders must be simple orders". Round down to whole
    # shares; if that zeros the qty, drop the bracket and let a simple
    # limit order through instead.
    if side.upper() == "BUY" and (stop_price or take_profit) and quantity != int(quantity):
        int_qty = int(quantity)
        if int_qty >= 1:
            logger.info(
                "Bracket requires whole shares — rounding %s qty %.4f → %d",
                ticker, quantity, int_qty,
            )
            quantity = float(int_qty)
        else:
            logger.info(
                "Bracket requires whole shares but %s qty=%.4f rounds to 0 — using simple limit",
                ticker, quantity,
            )
            stop_price = None
            take_profit = None

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
            LimitOrderRequest, MarketOrderRequest,
            StopLossRequest, TakeProfitRequest,
        )
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

        order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL

        # Bracket order if BUY entry has stop and/or take-profit attached.
        has_bracket = side.upper() == "BUY" and (stop_price or take_profit)

        if has_bracket and limit_price:
            # Proper typed bracket request — LimitOrderRequest + OTO/BRACKET
            # class + StopLossRequest + TakeProfitRequest. Prior impl used
            # generic OrderRequest(**kwargs) which alpaca-py 0.43 rejects.
            bracket_kwargs = {
                "symbol": ticker,
                "qty": round(quantity, 4),
                "side": order_side,
                "time_in_force": TimeInForce.DAY,
                "limit_price": round(limit_price, 2),
                "order_class": OrderClass.BRACKET,
            }
            if stop_price:
                bracket_kwargs["stop_loss"] = StopLossRequest(stop_price=round(stop_price, 2))
            if take_profit:
                bracket_kwargs["take_profit"] = TakeProfitRequest(limit_price=round(take_profit, 2))

            req = LimitOrderRequest(**bracket_kwargs)
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
        order_record["alpaca_order_id"] = str(order.id)

        # Poll briefly for fast fills (up to 3s). Many limit orders fill immediately
        # when priced aggressively; otherwise we record the transient state.
        import time
        final_order = order
        for _ in range(6):
            if final_order.status.value in ("filled", "rejected", "canceled", "expired"):
                break
            if final_order.status.value == "partially_filled":
                break
            time.sleep(0.5)
            try:
                final_order = client.get_order_by_id(order.id)
            except Exception:
                break

        # Map Alpaca order.status → our DB status so reconciliation is meaningful
        alpaca_status = final_order.status.value  # filled/new/accepted/rejected/canceled/expired/partially_filled/pending_new
        status_map = {
            "filled": "filled",
            "partially_filled": "partial_fill",
            "new": "open",
            "accepted": "open",
            "accepted_for_bidding": "open",
            "pending_new": "open",
            "pending_cancel": "open",
            "rejected": "rejected",
            "canceled": "canceled",
            "expired": "expired",
            "suspended": "open",
            "replaced": "open",
            "done_for_day": "expired",
        }
        order_record["status"] = status_map.get(alpaca_status, "submitted")
        order_record["alpaca_status"] = alpaca_status
        order_record["fill_price"] = float(final_order.filled_avg_price) if final_order.filled_avg_price else None
        order_record["filled_qty"] = float(final_order.filled_qty) if final_order.filled_qty else 0.0

        if final_order.filled_avg_price and limit_price:
            order_record["slippage"] = round(
                abs(float(final_order.filled_avg_price) - limit_price), 4
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


def reconcile_with_alpaca(days: int = 30) -> dict:
    """Sync DB with Alpaca's authoritative order state.

    Fetches recent Alpaca orders and updates DB rows by alpaca_order_id so that
    phantom 'submitted' rows get corrected to filled/expired/canceled.
    """
    client = get_client()
    if not client:
        return {"status": "error", "error": "No client — offline mode"}

    import sqlite3
    from datetime import timedelta
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    from agent.equity_db import DB_PATH

    after = (datetime.now() - timedelta(days=days)).isoformat()
    req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500, after=after)
    orders = client.get_orders(req)

    status_map = {
        "filled": "filled", "partially_filled": "partial_fill",
        "new": "open", "accepted": "open", "pending_new": "open",
        "rejected": "rejected", "canceled": "canceled", "expired": "expired",
        "done_for_day": "expired", "replaced": "open", "suspended": "open",
    }

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    updated, unknown = 0, 0
    for o in orders:
        oid = str(o.id)
        new_status = status_map.get(o.status.value, "submitted")
        fill_price = float(o.filled_avg_price) if o.filled_avg_price else None
        filled_qty = float(o.filled_qty) if o.filled_qty else 0.0
        row = c.execute("SELECT id, status FROM trades WHERE alpaca_order_id = ?", (oid,)).fetchone()
        if row:
            c.execute(
                "UPDATE trades SET status = ?, fill_price = ?, filled_qty = ? WHERE id = ?",
                (new_status, fill_price, filled_qty, row[0]),
            )
            updated += 1
        else:
            unknown += 1
    conn.commit()
    conn.close()

    logger.info("Reconciled: %d updated, %d Alpaca orders not in DB", updated, unknown)
    return {"status": "ok", "updated": updated, "alpaca_only": unknown, "total_alpaca": len(orders)}
