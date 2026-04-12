"""Alpaca options order execution and contract search.

Uses the same TradingClient as equity_executor (cached).
Options use OCC symbols: AAPL260417C00150000
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import logging
import math
from datetime import datetime, date, timedelta

from agent.equity_executor import get_client, get_mode
from agent.equity_db import record_trade

logger = logging.getLogger(__name__)


def get_occ_symbol(ticker: str, expiry: date, contract_type: str, strike: float) -> str:
    """Format an OCC option symbol.

    Example: AAPL260417C00150000 = AAPL, 2026-04-17, Call, $150.00
    """
    t = contract_type[0].upper()  # C or P
    exp = expiry.strftime("%y%m%d")
    strike_int = int(strike * 1000)
    return f"{ticker}{exp}{t}{strike_int:08d}"


def search_contracts(
    ticker: str,
    contract_type: str = "put",
    min_dte: int = 30,
    max_dte: int = 60,
    delta_target: float = 0.30,
    min_oi: int = 200,
) -> list[dict]:
    """Search Alpaca for option contracts matching criteria.

    Returns contracts sorted by closest-to-target delta.
    """
    client = get_client()
    if not client:
        logger.warning("No Alpaca client — cannot search contracts")
        return []

    try:
        from alpaca.trading.requests import GetOptionContractsRequest
        from alpaca.trading.enums import AssetStatus

        today = date.today()
        min_expiry = today + timedelta(days=min_dte)
        max_expiry = today + timedelta(days=max_dte)

        # Get current price for strike range calculation
        from lib.api import yahoo_finance_info
        info = yahoo_finance_info(ticker)
        current_price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
        if not current_price:
            logger.warning("Cannot get price for %s — skipping contract search", ticker)
            return []

        # Strike range: for puts, look 15-30% below current price
        # For calls, look 5-20% above current price
        if contract_type.lower() == "put":
            strike_min = current_price * 0.70
            strike_max = current_price * 0.95
        else:
            strike_min = current_price * 1.02
            strike_max = current_price * 1.25

        req = GetOptionContractsRequest(
            underlying_symbols=[ticker],
            status=AssetStatus.ACTIVE,
            expiration_date_gte=min_expiry.isoformat(),
            expiration_date_lte=max_expiry.isoformat(),
            type=contract_type.lower(),
            strike_price_gte=str(round(strike_min, 2)),
            strike_price_lte=str(round(strike_max, 2)),
            limit=100,
        )

        result = client.get_option_contracts(req)
        contracts = result.option_contracts if result else []

        # Enrich with delta calculation
        from modules.options import compute_delta

        enriched = []
        for c in contracts:
            strike = float(c.strike_price)
            expiry = c.expiration_date
            if isinstance(expiry, str):
                expiry = date.fromisoformat(expiry)
            dte = (expiry - today).days

            # Compute delta
            delta = compute_delta(current_price, strike, dte)
            if delta is None:
                # Estimate delta from moneyness
                moneyness = current_price / strike if contract_type.lower() == "put" else strike / current_price
                delta = max(0.05, min(0.95, 1.0 - moneyness))

            # For puts, delta is negative — we want abs value for comparison
            abs_delta = abs(delta) if contract_type.lower() == "call" else 1.0 - delta

            enriched.append({
                "occ_symbol": c.symbol,
                "ticker": ticker,
                "contract_type": contract_type.lower(),
                "strike": strike,
                "expiry": expiry.isoformat() if isinstance(expiry, date) else expiry,
                "dte": dte,
                "delta": round(abs_delta, 4),
                "open_interest": getattr(c, "open_interest", None),
                "current_price": current_price,
                "break_even": strike - 0 if contract_type.lower() == "put" else strike + 0,
            })

        # Sort by closest to target delta
        enriched.sort(key=lambda x: abs(x["delta"] - delta_target))

        # Filter by minimum OI if available
        # (Alpaca contract search doesn't always return OI — we'll check at order time)

        return enriched[:10]

    except Exception as e:
        logger.error("Contract search failed for %s: %s", ticker, e)
        return []


def get_option_quote(occ_symbol: str) -> dict:
    """Get the latest quote (bid/ask) for an option contract."""
    try:
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.requests import OptionLatestQuoteRequest
        import os

        data_client = OptionHistoricalDataClient(
            api_key=os.environ.get("ALPACA_API_KEY", ""),
            secret_key=os.environ.get("ALPACA_SECRET_KEY", ""),
        )
        quotes = data_client.get_option_latest_quote(
            OptionLatestQuoteRequest(symbol_or_symbols=occ_symbol)
        )
        if occ_symbol in quotes:
            q = quotes[occ_symbol]
            return {
                "bid": float(q.bid_price),
                "ask": float(q.ask_price),
                "mid": round((float(q.bid_price) + float(q.ask_price)) / 2, 2),
                "bid_size": q.bid_size,
                "ask_size": q.ask_size,
            }
    except Exception as e:
        logger.debug("Quote fetch failed for %s: %s", occ_symbol, e)

    return {"bid": 0, "ask": 0, "mid": 0, "bid_size": 0, "ask_size": 0}


def place_options_order(
    occ_symbol: str,
    side: str,
    qty: int,
    limit_price: float,
    strategy: str = "",
    reason: str = "",
    conviction: int = 0,
    run_id: str = "",
) -> dict:
    """Place a single-leg options order via Alpaca."""
    mode = get_mode()
    now = datetime.now().astimezone().isoformat()

    if qty <= 0 or limit_price <= 0:
        return {"status": "error", "error": f"Invalid qty={qty} or price={limit_price}"}

    order_record = {
        "timestamp": now,
        "ticker": occ_symbol,
        "side": side.upper(),
        "quantity": qty,
        "price": round(limit_price, 2),
        "cost_usd": round(limit_price * 100 * qty, 2),  # Options are 100 shares/contract
        "strategy": strategy,
        "reason": reason,
        "conviction": conviction,
        "sector": "options",
        "mode": mode,
        "status": "pending",
        "run_id": run_id,
    }

    client = get_client()
    if not client:
        order_record["status"] = "paper_filled"
        order_record["fill_price"] = limit_price
        order_record["alpaca_order_id"] = f"offline_opt_{int(datetime.now().timestamp())}"
        record_trade(order_record)
        logger.info("[PAPER-OFFLINE] %s %s %d contracts @ $%.2f — %s",
                     side.upper(), occ_symbol, qty, limit_price, reason[:60])
        return order_record

    try:
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL
        req = LimitOrderRequest(
            symbol=occ_symbol,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
            limit_price=round(limit_price, 2),
        )
        order = client.submit_order(req)

        order_record["status"] = "submitted"
        order_record["alpaca_order_id"] = str(order.id)
        if order.filled_avg_price:
            order_record["fill_price"] = float(order.filled_avg_price)

        logger.info("[%s] %s %s %d contracts @ $%.2f — %s (order %s)",
                     mode.upper(), side.upper(), occ_symbol, qty,
                     limit_price, strategy, order.id)

    except Exception as e:
        order_record["status"] = "error"
        order_record["error"] = str(e)
        logger.error("Options order failed for %s: %s", occ_symbol, e)

    record_trade(order_record)
    return order_record


def get_options_positions() -> list[dict]:
    """Get current option positions from Alpaca."""
    client = get_client()
    if not client:
        return []

    try:
        positions = client.get_all_positions()
        opts = []
        for p in positions:
            # Options symbols are longer than stock tickers (OCC format)
            if len(p.symbol) > 10:
                opts.append({
                    "occ_symbol": p.symbol,
                    "qty": int(float(p.qty)),
                    "avg_cost": float(p.avg_entry_price),
                    "market_value": float(p.market_value),
                    "current_price": float(p.current_price),
                    "unrealized_pnl": float(p.unrealized_pl),
                    "unrealized_pnl_pct": float(p.unrealized_plpc),
                    "side": p.side,
                })
        return opts
    except Exception as e:
        logger.error("Failed to get options positions: %s", e)
        return []


def calculate_iv_rank(ticker: str, period_days: int = 365) -> float | None:
    """Calculate IV Rank: where current IV sits in its 1-year range.

    Returns 0-100 float, or None if data insufficient.
    Uses historical volatility as a proxy since Alpaca doesn't provide IV.
    """
    try:
        import numpy as np
        from lib.api import yahoo_finance_price_history

        df = yahoo_finance_price_history(ticker, period="1y")
        if df is None or df.empty or len(df) < 60:
            return None

        close = df["Close"] if "Close" in df.columns else df.iloc[:, 0]
        log_returns = np.log(close / close.shift(1)).dropna()

        # Rolling 30-day historical vol (annualized)
        rolling_vol = log_returns.rolling(30).std() * np.sqrt(252)
        rolling_vol = rolling_vol.dropna()

        if len(rolling_vol) < 30:
            return None

        current_vol = rolling_vol.iloc[-1]
        vol_min = rolling_vol.min()
        vol_max = rolling_vol.max()

        if vol_max == vol_min:
            return 50.0

        iv_rank = ((current_vol - vol_min) / (vol_max - vol_min)) * 100
        return round(max(0, min(100, iv_rank)), 1)

    except Exception as e:
        logger.debug("IV rank calculation failed for %s: %s", ticker, e)
        return None
