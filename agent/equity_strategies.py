"""Three strategy families for equity trading.

Each strategy reads from existing morning-brief signal modules,
scores opportunities by conviction, and returns trade proposals
for the equity risk gate to approve/reject.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import datetime, date

from lib.data_envelope import load_envelope
from agent.equity_config import TICKER_SECTOR

logger = logging.getLogger(__name__)


def _load_signal(filename: str) -> dict:
    """Load a processed signal file, returning empty data on failure."""
    envelope = load_envelope(filename)
    if envelope.get("status") in ("success", "partial"):
        return envelope.get("data", {})
    return {}


def _get_current_price(ticker: str) -> float | None:
    """Get current price from technical signals or portfolio data."""
    tech = _load_signal("technical_signals.json")
    for r in tech.get("results", []):
        if r.get("ticker") == ticker:
            return r.get("vwap") or r.get("current_price")

    portfolio = _load_signal("portfolio.json")
    for h in portfolio.get("holdings", []):
        if h.get("ticker") == ticker:
            return h.get("current_price")
    return None


# ============================================================
# STRATEGY 1: Congressional + Earnings Conviction
# ============================================================

def scan_congressional_conviction(params: dict) -> list:
    """Find stocks where congressional buying clusters align with positive earnings.

    Combines insider/congressional signals with earnings tone and valuation
    to generate high-conviction trade proposals.
    """
    congress = _load_signal("congress_tracker.json")
    earnings = _load_signal("earnings_tone.json")
    valuation = _load_signal("valuation.json")

    if not congress:
        logger.info("Congressional conviction: no congress data available")
        return []

    min_cluster = params.get("min_cluster_size", 2)
    min_amount = params.get("min_amount", 100000)
    tone_boost = params.get("tone_boost_threshold", 1.0)
    tone_penalty = params.get("tone_penalty_threshold", -2.0)

    # Build earnings tone lookup
    tone_by_ticker = {}
    for r in earnings.get("results", []):
        ticker = r.get("ticker", "")
        if ticker and not ticker.startswith("SAMPLE_"):
            tone_by_ticker[ticker] = r.get("tone_score", 0)

    # Build valuation lookup
    valuation_bonus = {}
    for comp in valuation.get("comparisons", []):
        cheaper = comp.get("cheaper")
        if cheaper:
            valuation_bonus[cheaper] = 1  # Cheaper stock gets +1 conviction
        expensive = comp.get("more_expensive")
        if expensive:
            valuation_bonus[expensive] = -1

    proposals = []

    # Cluster signals — multiple congress members buying/selling the same stock
    for cluster in congress.get("cluster_signals", []):
        ticker = cluster.get("ticker")
        if not ticker:
            continue

        direction = cluster.get("direction", "")
        signal = cluster.get("signal", "")
        count = cluster.get("politician_count", 0)
        est_total = cluster.get("estimated_total", 0)

        if count < min_cluster or est_total < min_amount:
            continue

        # Build conviction score
        conviction = 0
        reasons = []

        # Congressional signal strength
        if signal == "strong_bullish":
            conviction += count * 2
            reasons.append(f"{count} congress members buying (${est_total:,.0f})")
        elif signal == "strong_bearish":
            conviction -= count * 2
            reasons.append(f"{count} congress members selling (${est_total:,.0f})")
        elif direction == "buy":
            conviction += count
            reasons.append(f"{count} congress members buying")
        elif direction == "sell":
            conviction -= count
            reasons.append(f"{count} congress members selling")

        # Earnings tone modifier
        tone = tone_by_ticker.get(ticker, 0)
        if tone > tone_boost:
            conviction += 2
            reasons.append(f"earnings tone +{tone:.1f} (bullish)")
        elif tone < tone_penalty:
            conviction -= 2
            reasons.append(f"earnings tone {tone:.1f} (bearish)")
        elif tone != 0:
            conviction += 1 if tone > 0 else -1
            reasons.append(f"earnings tone {tone:.1f}")

        # Valuation modifier
        val_mod = valuation_bonus.get(ticker, 0)
        if val_mod != 0:
            conviction += val_mod
            reasons.append("undervalued vs peer" if val_mod > 0 else "overvalued vs peer")

        # Determine trade direction
        if conviction > 0:
            side = "BUY"
        elif conviction < 0:
            side = "SELL"
        else:
            continue  # No conviction, skip

        current_price = _get_current_price(ticker)
        if not current_price:
            continue

        proposals.append({
            "strategy": "congressional_conviction",
            "ticker": ticker,
            "side": side,
            "limit_price": round(current_price, 2),
            "conviction": abs(conviction),
            "sector": TICKER_SECTOR.get(ticker, "Unknown"),
            "reason": f"Congressional {direction} + " + "; ".join(reasons[:3]),
            "politicians": cluster.get("politicians", []),
        })

    proposals.sort(key=lambda x: x["conviction"], reverse=True)
    return proposals


# ============================================================
# STRATEGY 2: Technical Mean Reversion
# ============================================================

def scan_technical_reversion(params: dict) -> list:
    """Find oversold stocks to buy and overbought stocks to sell.

    Uses RSI, composite score, MACD, Bollinger Bands, and Fibonacci levels
    from the existing technical_signals module.
    """
    tech = _load_signal("technical_signals.json")
    if not tech:
        logger.info("Technical reversion: no technical signals available")
        return []

    rsi_oversold = params.get("rsi_oversold", 30)
    rsi_overbought = params.get("rsi_overbought", 70)
    min_composite_buy = params.get("min_composite_buy", 0.40)
    max_composite_sell = params.get("max_composite_sell", 0.30)
    bb_confirm = params.get("bb_confirmation", True)

    proposals = []

    for r in tech.get("results", []):
        ticker = r.get("ticker")
        rsi = r.get("rsi_14")
        composite = r.get("composite_score")
        macd_signal = r.get("macd_signal", "")
        bb_pos = r.get("bb_position", "")
        vwap = r.get("vwap")
        fib = r.get("fibonacci", {})
        stoch_signal = r.get("stochastic_signal", "")

        if not ticker or rsi is None or composite is None:
            continue

        conviction = 0
        reasons = []
        side = None
        limit_price = vwap

        # --- OVERSOLD BUY ---
        if rsi < rsi_oversold:
            conviction += 2
            reasons.append(f"RSI {rsi:.0f} (oversold)")
            side = "BUY"

            if composite > min_composite_buy:
                conviction += 1
                reasons.append(f"composite {composite:.2f}")

            if "bullish" in macd_signal:
                conviction += 1
                reasons.append("MACD bullish")

            if bb_confirm and bb_pos == "below_lower":
                conviction += 1
                reasons.append("below lower BB")

            if stoch_signal == "oversold":
                conviction += 1
                reasons.append("stochastic oversold")

            # Use Fibonacci support for limit price
            fib_levels = fib.get("levels", {})
            if fib_levels.get("0.618"):
                limit_price = fib_levels["0.618"]
                reasons.append(f"limit at fib 0.618 (${limit_price:.2f})")

        # --- OVERBOUGHT SELL ---
        elif rsi > rsi_overbought:
            conviction += 2
            reasons.append(f"RSI {rsi:.0f} (overbought)")
            side = "SELL"

            if composite < max_composite_sell:
                conviction += 1
                reasons.append(f"composite {composite:.2f}")

            if "bearish" in macd_signal:
                conviction += 1
                reasons.append("MACD bearish")

            if bb_confirm and bb_pos == "above_upper":
                conviction += 1
                reasons.append("above upper BB")

            if stoch_signal == "overbought":
                conviction += 1
                reasons.append("stochastic overbought")

            # Use Fibonacci resistance for limit price
            fib_ext = fib.get("extensions", {})
            if fib_ext.get("1.272"):
                limit_price = fib_ext["1.272"]

        if not side or conviction < 2:
            continue

        if not limit_price or limit_price <= 0:
            continue

        proposals.append({
            "strategy": "technical_reversion",
            "ticker": ticker,
            "side": side,
            "limit_price": round(limit_price, 2),
            "conviction": conviction,
            "sector": TICKER_SECTOR.get(ticker, "Unknown"),
            "reason": "; ".join(reasons[:4]),
            "rsi": rsi,
            "composite": composite,
        })

    proposals.sort(key=lambda x: x["conviction"], reverse=True)
    return proposals


# ============================================================
# STRATEGY 3: Sector Rotation (Monthly Rebalance)
# ============================================================

def scan_sector_rotation(params: dict) -> list:
    """Generate sector ETF rotation trades based on relative strength.

    Overweights top-performing sectors, underweights laggards.
    Only triggers on signal changes or monthly rebalance.
    """
    rotation = _load_signal("sector_rotation.json")
    if not rotation:
        logger.info("Sector rotation: no rotation data available")
        return []

    top_n = params.get("top_n_sectors", 3)
    min_rs = params.get("min_relative_strength", 1.0)
    position_usd = params.get("position_per_sector_usd", 500)

    signal = rotation.get("signal", "")
    leaders = rotation.get("leaders_21d", [])
    laggards = rotation.get("laggards_21d", [])

    proposals = []

    # BUY leaders (top N by relative strength)
    for leader in leaders[:top_n]:
        etf = leader.get("etf")
        rs = leader.get("relative_strength", 0)
        ret_21d = leader.get("return_21d", 0)
        trend = leader.get("trend", "")

        if not etf or rs < min_rs:
            continue

        # Conviction based on relative strength magnitude
        conviction = 3 if rs > 3 else 2 if rs > 2 else 1
        if trend == "uptrend":
            conviction += 1
        if signal in ("growth_momentum", "risk_on"):
            conviction += 1

        current_price = _get_current_price(etf)
        if not current_price or current_price <= 0:
            continue

        quantity = position_usd / current_price

        proposals.append({
            "strategy": "sector_rotation",
            "ticker": etf,
            "side": "BUY",
            "limit_price": round(current_price, 2),
            "quantity_override": round(quantity, 2),
            "conviction": conviction,
            "sector": leader.get("sector", TICKER_SECTOR.get(etf, "Unknown")),
            "reason": f"Sector leader: {leader.get('sector', etf)} RS={rs:.1f}, "
                      f"21d return {ret_21d:.1f}%, signal={signal}",
            "relative_strength": rs,
        })

    # SELL laggards (if we hold any)
    for laggard in laggards[:top_n]:
        etf = laggard.get("etf")
        rs = laggard.get("relative_strength", 0)
        trend = laggard.get("trend", "")

        if not etf:
            continue

        # Only sell if actually underperforming
        if rs > -1.0:
            continue

        conviction = 3 if rs < -3 else 2 if rs < -2 else 1
        if trend == "downtrend":
            conviction += 1
        if signal in ("defensive_shift", "risk_off"):
            conviction += 1

        current_price = _get_current_price(etf)
        if not current_price:
            continue

        proposals.append({
            "strategy": "sector_rotation",
            "ticker": etf,
            "side": "SELL",
            "limit_price": round(current_price, 2),
            "conviction": conviction,
            "sector": laggard.get("sector", TICKER_SECTOR.get(etf, "Unknown")),
            "reason": f"Sector laggard: {laggard.get('sector', etf)} RS={rs:.1f}, "
                      f"signal={signal}",
            "relative_strength": rs,
        })

    proposals.sort(key=lambda x: x["conviction"], reverse=True)
    return proposals


# ============================================================
# MAIN: Run all equity strategies
# ============================================================

def run_all_equity_strategies(params: dict) -> list:
    """Run all three strategy families and return combined proposals."""
    all_proposals = []

    # Congressional + Earnings Conviction
    congress_params = params.get("congressional_conviction", {})
    congress = scan_congressional_conviction(congress_params)
    weight = congress_params.get("weight", 1.0)
    for p in congress:
        p["strategy_weight"] = weight
    all_proposals.extend(congress)
    logger.info("Congressional Conviction: %d proposals", len(congress))

    # Technical Mean Reversion
    tech_params = params.get("technical_reversion", {})
    tech = scan_technical_reversion(tech_params)
    weight = tech_params.get("weight", 1.0)
    for p in tech:
        p["strategy_weight"] = weight
    all_proposals.extend(tech)
    logger.info("Technical Reversion: %d proposals", len(tech))

    # Sector Rotation
    sector_params = params.get("sector_rotation", {})
    sector = scan_sector_rotation(sector_params)
    weight = sector_params.get("weight", 1.0)
    for p in sector:
        p["strategy_weight"] = weight
    all_proposals.extend(sector)
    logger.info("Sector Rotation: %d proposals", len(sector))

    # Deduplicate by ticker — keep highest conviction per ticker
    seen = {}
    for p in sorted(all_proposals, key=lambda x: x["conviction"], reverse=True):
        key = (p["ticker"], p["side"])
        if key not in seen:
            seen[key] = p

    deduped = sorted(seen.values(), key=lambda x: x["conviction"], reverse=True)
    logger.info("Total: %d proposals (%d after dedup)", len(all_proposals), len(deduped))
    return deduped
