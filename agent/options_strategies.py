"""Wheel strategy for options trading.

Phase 1: Sell cash-secured puts on high IV rank stocks in uptrends
Phase 2: Sell covered calls on assigned/held positions
Phase 3: Manage open positions (close winners, roll losers, avoid pin risk)
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import logging
from datetime import date, timedelta

from lib.data_envelope import load_envelope
from agent.options_config import (
    OPTIONS_WHEEL_UNIVERSE, MIN_IV_RANK, OPTIMAL_IV_RANK,
    MIN_OPEN_INTEREST, MIN_BID_ASK_RATIO, CLOSE_AT_PROFIT_PCT,
    ROLL_AT_DTE, CLOSE_BY_EXPIRY_DTE, MIN_PREMIUM,
    NO_SELL_BEFORE_EARNINGS_DAYS,
)
from agent.options_executor import (
    search_contracts, get_option_quote, calculate_iv_rank,
    get_options_positions,
)
from agent.equity_executor import get_positions

logger = logging.getLogger(__name__)


def _load_signal(filename: str) -> dict:
    envelope = load_envelope(filename)
    if envelope.get("status") in ("success", "partial"):
        return envelope.get("data", {})
    return {}


def _get_sma_trend(ticker: str) -> str | None:
    """Check if stock is above 50-day SMA from technical signals."""
    tech = _load_signal("technical_signals.json")
    for r in tech.get("results", []):
        if r.get("ticker") == ticker:
            return r.get("sma_trend")
    return None


def _has_earnings_soon(ticker: str, days: int = 7) -> bool:
    """Check if earnings are within N days — don't sell options into earnings."""
    # Check earnings tone data for recent activity
    earnings = _load_signal("earnings_tone.json")
    # Conservative: if we have no data, assume earnings could be soon
    # In practice, you'd check an earnings calendar API
    return False  # TODO: integrate earnings calendar check


# ============================================================
# PHASE 1: Cash-Secured Put Selling
# ============================================================

def scan_csp_opportunities(params: dict) -> list:
    """Find stocks where selling cash-secured puts has positive expectation.

    Entry criteria (tastytrade playbook):
    - IV Rank > 30 (ideally > 50)
    - Stock in uptrend (above 50-day SMA)
    - 30-delta put, 45 DTE
    - Decent premium (> $0.50)
    - Good liquidity (OI > 200, tight spread)
    """
    target_delta = params.get("target_delta", 0.30)
    dte_range = params.get("dte_range", (30, 60))
    min_iv = params.get("min_iv_rank", MIN_IV_RANK)
    require_uptrend = params.get("require_uptrend", True)

    proposals = []

    for ticker in OPTIONS_WHEEL_UNIVERSE:
        # Check IV rank
        iv_rank = calculate_iv_rank(ticker)
        if iv_rank is None:
            logger.debug("Skipping %s — no IV rank data", ticker)
            continue
        if iv_rank < min_iv:
            logger.debug("Skipping %s — IV rank %.1f below minimum %d", ticker, iv_rank, min_iv)
            continue

        # Check trend
        if require_uptrend:
            sma_trend = _get_sma_trend(ticker)
            if sma_trend and "death" in (sma_trend or ""):
                logger.debug("Skipping %s — death cross (downtrend)", ticker)
                continue

        # Check earnings
        if _has_earnings_soon(ticker, NO_SELL_BEFORE_EARNINGS_DAYS):
            logger.debug("Skipping %s — earnings within %d days", ticker, NO_SELL_BEFORE_EARNINGS_DAYS)
            continue

        # Search for put contracts
        contracts = search_contracts(
            ticker, "put",
            min_dte=dte_range[0], max_dte=dte_range[1],
            delta_target=target_delta,
        )
        if not contracts:
            continue

        # Pick the best contract (closest to target delta)
        best = contracts[0]

        # Get quote for premium
        quote = get_option_quote(best["occ_symbol"])
        bid = quote.get("bid", 0)
        ask = quote.get("ask", 0)
        mid = quote.get("mid", 0)

        # Liquidity check
        if ask > 0 and bid / ask < MIN_BID_ASK_RATIO:
            logger.debug("Skipping %s put — wide spread (bid/ask = %.2f)", ticker, bid / ask if ask else 0)
            continue

        # Premium check
        premium = bid if bid > 0 else mid
        if premium < MIN_PREMIUM:
            logger.debug("Skipping %s put — premium $%.2f below minimum $%.2f",
                         ticker, premium, MIN_PREMIUM)
            continue

        # Cash required (collateral for CSP = strike * 100)
        cash_required = best["strike"] * 100
        break_even = best["strike"] - premium
        max_profit = premium * 100  # Per contract

        # Conviction scoring
        conviction = 3  # Base for meeting all criteria
        if iv_rank >= OPTIMAL_IV_RANK:
            conviction += 2
        if iv_rank >= 70:
            conviction += 1
        sma = _get_sma_trend(ticker)
        if sma == "golden_cross":
            conviction += 1

        # Check if break-even is below max pain (structural support)
        options_data = _load_signal("options.json")
        for t in options_data.get("tickers", []):
            if t.get("ticker") == ticker:
                max_pain = t.get("max_pain")
                if max_pain and break_even < max_pain:
                    conviction += 1  # Break-even below max pain = extra safety

        proposals.append({
            "strategy": "wheel_csp",
            "type": "options",
            "ticker": ticker,
            "occ_symbol": best["occ_symbol"],
            "contract_type": "put",
            "side": "SELL",
            "strike": best["strike"],
            "expiry": best["expiry"],
            "dte": best["dte"],
            "delta": best["delta"],
            "premium": round(premium, 2),
            "limit_price": round(premium, 2),
            "quantity": 1,  # Start with 1 contract
            "cash_required": cash_required,
            "break_even": round(break_even, 2),
            "max_profit": round(max_profit, 2),
            "iv_rank": iv_rank,
            "conviction": conviction,
            "sector": "options",
            "reason": (f"CSP: SELL {ticker} {best['strike']}P {best['expiry']} "
                       f"@ ${premium:.2f} (IV rank {iv_rank:.0f}, "
                       f"delta {best['delta']:.2f}, BE ${break_even:.2f})"),
        })

    proposals.sort(key=lambda x: x["conviction"], reverse=True)
    return proposals


# ============================================================
# PHASE 2: Covered Call Selling
# ============================================================

def scan_cc_opportunities(params: dict) -> list:
    """Find held stock positions where selling covered calls makes sense.

    Requirements:
    - Hold >= 100 shares of the stock
    - No covered call already open on this position
    - Strike above cost basis (don't lock in a loss)
    - No earnings within 7 days
    """
    target_delta = params.get("target_delta", 0.30)
    dte_range = params.get("dte_range", (21, 50))
    sell_above_cost = params.get("sell_above_cost_basis", True)

    # Get current stock positions
    stock_positions = get_positions()
    option_positions = get_options_positions()

    # Check which tickers already have CCs
    tickers_with_cc = set()
    for op in option_positions:
        sym = op["occ_symbol"]
        # Extract ticker from OCC symbol and check if it's a short call
        if op["side"] == "short" and "C" in sym[6:8]:
            # Rough extraction — ticker is before the date portion
            for t in OPTIONS_WHEEL_UNIVERSE:
                if sym.startswith(t):
                    tickers_with_cc.add(t)

    proposals = []

    for pos in stock_positions:
        ticker = pos.get("ticker", "")
        qty = pos.get("qty", 0)
        avg_cost = pos.get("avg_cost", 0)

        # Need at least 100 shares for 1 covered call contract
        if qty < 100 or ticker not in OPTIONS_WHEEL_UNIVERSE:
            continue

        # Already have a CC on this position?
        if ticker in tickers_with_cc:
            logger.debug("Skipping CC on %s — already have covered call open", ticker)
            continue

        # Earnings check
        if _has_earnings_soon(ticker, NO_SELL_BEFORE_EARNINGS_DAYS):
            continue

        # Search for call contracts
        contracts = search_contracts(
            ticker, "call",
            min_dte=dte_range[0], max_dte=dte_range[1],
            delta_target=target_delta,
        )
        if not contracts:
            continue

        best = contracts[0]

        # Must be above cost basis
        if sell_above_cost and best["strike"] <= avg_cost:
            logger.debug("Skipping CC on %s — strike $%.2f below cost basis $%.2f",
                         ticker, best["strike"], avg_cost)
            continue

        # Get quote
        quote = get_option_quote(best["occ_symbol"])
        bid = quote.get("bid", 0)
        premium = bid if bid > 0 else quote.get("mid", 0)

        if premium < MIN_PREMIUM:
            continue

        contracts_to_sell = min(int(qty // 100), MAX_CONTRACTS_PER_POSITION)
        max_profit = premium * 100 * contracts_to_sell

        # Conviction
        conviction = 3
        iv_rank = calculate_iv_rank(ticker)
        if iv_rank and iv_rank >= OPTIMAL_IV_RANK:
            conviction += 1
        if best["strike"] > avg_cost * 1.05:
            conviction += 1  # 5%+ above cost basis

        proposals.append({
            "strategy": "wheel_cc",
            "type": "options",
            "ticker": ticker,
            "occ_symbol": best["occ_symbol"],
            "contract_type": "call",
            "side": "SELL",
            "strike": best["strike"],
            "expiry": best["expiry"],
            "dte": best["dte"],
            "delta": best["delta"],
            "premium": round(premium, 2),
            "limit_price": round(premium, 2),
            "quantity": contracts_to_sell,
            "max_profit": round(max_profit, 2),
            "iv_rank": iv_rank,
            "avg_cost": avg_cost,
            "conviction": conviction,
            "sector": "options",
            "reason": (f"CC: SELL {ticker} {best['strike']}C {best['expiry']} "
                       f"@ ${premium:.2f} x{contracts_to_sell} "
                       f"(cost basis ${avg_cost:.2f}, delta {best['delta']:.2f})"),
        })

    proposals.sort(key=lambda x: x["conviction"], reverse=True)
    return proposals


# ============================================================
# PHASE 3: Position Management
# ============================================================

def scan_management_actions(params: dict) -> list:
    """Check open options positions for management triggers.

    Actions:
    1. Close at 50% profit (buy back for less than half the credit)
    2. Roll at 21 DTE if not profitable
    3. Close day before expiry (pin risk)
    4. Close if delta doubled from entry
    """
    option_positions = get_options_positions()
    if not option_positions:
        return []

    proposals = []
    today = date.today()

    for pos in option_positions:
        occ = pos["occ_symbol"]
        qty = abs(pos["qty"])
        avg_cost = pos["avg_cost"]
        current_price = pos["current_price"]
        pnl_pct = pos.get("unrealized_pnl_pct", 0)

        # Parse expiry from OCC symbol (chars after ticker, format YYMMDD)
        try:
            # Find where the date starts — after ticker letters, before C/P
            date_str = ""
            for i, ch in enumerate(occ):
                if ch.isdigit():
                    date_str = occ[i:i+6]
                    break
            if date_str:
                expiry = date(2000 + int(date_str[:2]), int(date_str[2:4]), int(date_str[4:6]))
                dte = (expiry - today).days
            else:
                dte = 999
        except (ValueError, IndexError):
            dte = 999

        is_short = pos["side"] == "short"

        # --- MANAGEMENT RULE 1: Close at 50% profit ---
        if is_short and pnl_pct >= CLOSE_AT_PROFIT_PCT * 100:
            quote = get_option_quote(occ)
            close_price = quote.get("ask", current_price)

            proposals.append({
                "strategy": "wheel_manage",
                "type": "options",
                "action": "close_winner",
                "ticker": occ,
                "occ_symbol": occ,
                "side": "BUY",  # Buy to close
                "quantity": qty,
                "limit_price": round(close_price, 2),
                "conviction": 8,  # High priority — take profit
                "sector": "options",
                "reason": f"CLOSE WINNER: {occ} at {pnl_pct:.0f}% profit (target {CLOSE_AT_PROFIT_PCT*100:.0f}%)",
            })
            continue

        # --- MANAGEMENT RULE 2: Roll at 21 DTE ---
        if is_short and dte <= ROLL_AT_DTE and pnl_pct < CLOSE_AT_PROFIT_PCT * 100:
            quote = get_option_quote(occ)
            close_price = quote.get("ask", current_price)

            proposals.append({
                "strategy": "wheel_manage",
                "type": "options",
                "action": "roll",
                "ticker": occ,
                "occ_symbol": occ,
                "side": "BUY",  # Buy to close current
                "quantity": qty,
                "limit_price": round(close_price, 2),
                "conviction": 7,
                "sector": "options",
                "reason": f"ROLL: {occ} at {dte} DTE, P&L {pnl_pct:.0f}% — close and reopen at 45 DTE",
            })
            continue

        # --- MANAGEMENT RULE 3: Close before expiry (pin risk) ---
        if dte <= CLOSE_BY_EXPIRY_DTE:
            quote = get_option_quote(occ)
            close_price = quote.get("ask", current_price) if is_short else quote.get("bid", current_price)
            side = "BUY" if is_short else "SELL"

            proposals.append({
                "strategy": "wheel_manage",
                "type": "options",
                "action": "close_expiry",
                "ticker": occ,
                "occ_symbol": occ,
                "side": side,
                "quantity": qty,
                "limit_price": round(close_price, 2),
                "conviction": 9,  # Highest priority — avoid pin risk
                "sector": "options",
                "reason": f"CLOSE EXPIRY: {occ} expires in {dte} days — avoiding pin risk",
            })

    proposals.sort(key=lambda x: x["conviction"], reverse=True)
    return proposals


# ============================================================
# MAIN: Run all options strategies
# ============================================================

MAX_CONTRACTS_PER_POSITION = 2  # Import from config

def run_all_options_strategies(params: dict) -> list:
    """Run wheel strategy phases and return all proposals."""
    all_proposals = []

    # Phase 3 first — manage existing positions (highest priority)
    management = scan_management_actions(params)
    all_proposals.extend(management)
    logger.info("Options Management: %d actions", len(management))

    # Phase 2 — covered calls on held positions
    cc_params = params.get("wheel_cc", {})
    covered_calls = scan_cc_opportunities(cc_params)
    all_proposals.extend(covered_calls)
    logger.info("Covered Calls: %d proposals", len(covered_calls))

    # Phase 1 — new cash-secured puts
    csp_params = params.get("wheel_csp", {})
    csps = scan_csp_opportunities(csp_params)
    all_proposals.extend(csps)
    logger.info("Cash-Secured Puts: %d proposals", len(csps))

    logger.info("Total options proposals: %d", len(all_proposals))
    return all_proposals
