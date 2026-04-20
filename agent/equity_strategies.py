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

        # Aggressive limit: 0.3% slippage buffer so the order actually fills.
        # Reason: April 13-16 runs placed 3 NVDA buys at exact current price and
        # all expired unfilled because price drifted away before close.
        SLIPPAGE = 0.003
        if side == "BUY":
            limit_price = round(current_price * (1 + SLIPPAGE), 2)
        else:
            limit_price = round(current_price * (1 - SLIPPAGE), 2)

        proposals.append({
            "strategy": "congressional_conviction",
            "ticker": ticker,
            "side": side,
            "limit_price": limit_price,
            "conviction": abs(conviction),
            "sector": TICKER_SECTOR.get(ticker, "Unknown"),
            "reason": f"Congressional {direction} + " + "; ".join(reasons[:3]),
            "politicians": cluster.get("politicians", []),
        })

    proposals.sort(key=lambda x: x["conviction"], reverse=True)
    return proposals


# ============================================================
# STRATEGY 2: Technical Mean Reversion — Connors RSI(2) variant
# ============================================================
#
# Research (April 2026): QuantifiedStrategies shows RSI(2) mean reversion on
# QQQ with Connors regime filter getting Sharpe 2.85 / 75% win rate / 12.75%
# CAGR — vs classic RSI(14)+BB which barely beats buy-hold. The three rules:
#
#   1. RSI(2) < 10 for buys, > 90 for sells (not RSI(14) @ 30/70)
#   2. Only take longs when close > SMA(200) — Connors trend filter
#   3. Only trade when VIX 365-day percentile rank > 50 — elevated vol regime
#
# The Fibonacci limit-price logic was removed (overfit pseudo-science and was
# a contributor to the April 13-16 unfilled-orders problem).


def _compute_rsi(close, period: int) -> float | None:
    """Wilder's RSI for a pandas close series. Returns latest value or None."""
    try:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-9)
        rsi = 100 - (100 / (1 + rs))
        val = float(rsi.iloc[-1])
        return val if 0 <= val <= 100 else None
    except Exception:
        return None


def _get_vix_percentile(lookback_days: int = 365) -> float | None:
    """Return VIX current percentile rank over the trailing lookback."""
    try:
        from lib.api import yahoo_finance_price_history
        df = yahoo_finance_price_history("^VIX", period="2y")
        if df is None or df.empty or len(df) < lookback_days:
            return None
        tail = df["Close"].tail(lookback_days)
        current = float(df["Close"].iloc[-1])
        rank = (tail < current).sum() / len(tail) * 100
        return float(rank)
    except Exception:
        return None


def scan_technical_reversion(params: dict) -> list:
    """Connors RSI(2) mean reversion with trend + vol-regime gates."""
    tickers = params.get("tickers") or ["AAPL", "NVDA", "MSFT", "AMD", "GOOGL",
                                         "AMZN", "TSLA", "META", "SPY", "QQQ"]
    rsi_buy = params.get("rsi2_buy_threshold", 10)
    rsi_sell = params.get("rsi2_sell_threshold", 90)
    vix_pct_floor = params.get("vix_percentile_floor", 50)

    # Regime gate: skip strategy entirely if vol is too low to mean-revert
    vix_pct = _get_vix_percentile()
    if vix_pct is None:
        logger.info("RSI(2): VIX percentile unavailable — proceeding without regime filter")
    elif vix_pct < vix_pct_floor:
        logger.info("RSI(2): VIX %.0fth pct < floor %d — skipping all signals", vix_pct, vix_pct_floor)
        return []

    from lib.api import yahoo_finance_price_history
    proposals = []

    for ticker in tickers:
        df = yahoo_finance_price_history(ticker, period="1y")
        if df is None or df.empty or len(df) < 210:
            continue

        close = df["Close"]
        rsi2 = _compute_rsi(close, 2)
        sma200 = float(close.tail(200).mean())
        current_price = float(close.iloc[-1])

        if rsi2 is None or current_price <= 0:
            continue

        side = None
        reasons = []

        # BUY: oversold AND price above 200-day SMA (Connors trend filter)
        if rsi2 < rsi_buy and current_price > sma200:
            side = "BUY"
            reasons.append(f"RSI(2)={rsi2:.0f} oversold")
            reasons.append(f"close ${current_price:.2f} > SMA200 ${sma200:.2f}")

        # SELL: overbought AND price below 200-day SMA (downtrend confirmation)
        elif rsi2 > rsi_sell and current_price < sma200:
            side = "SELL"
            reasons.append(f"RSI(2)={rsi2:.0f} overbought")
            reasons.append(f"close ${current_price:.2f} < SMA200 ${sma200:.2f}")

        if not side:
            continue

        if vix_pct is not None:
            reasons.append(f"VIX {vix_pct:.0f}th pct")

        # Conviction scales with how extreme RSI(2) is
        if side == "BUY":
            conviction = 5 if rsi2 < 5 else 4 if rsi2 < 8 else 3
        else:
            conviction = 5 if rsi2 > 95 else 4 if rsi2 > 92 else 3

        # Marketable limit — 0.3% buffer aligns with congressional strategy
        slippage = 0.003
        limit_price = round(current_price * (1 + slippage if side == "BUY" else 1 - slippage), 2)

        proposals.append({
            "strategy": "technical_reversion",
            "ticker": ticker,
            "side": side,
            "limit_price": limit_price,
            "conviction": conviction,
            "sector": TICKER_SECTOR.get(ticker, "Unknown"),
            "reason": "; ".join(reasons),
            "rsi2": rsi2,
            "sma200": sma200,
            "vix_percentile": vix_pct,
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
            "limit_price": round(current_price * 1.003, 2),  # 0.3% slippage buffer
            "quantity_override": round(quantity, 2),
            "conviction": conviction,
            "sector": leader.get("sector", TICKER_SECTOR.get(etf, "Unknown")),
            "reason": f"Sector leader: {leader.get('sector', etf)} RS={rs:.1f}, "
                      f"21d return {ret_21d:.1f}%, signal={signal}",
            "relative_strength": rs,
        })

    # NOTE: The "SELL laggards" loop was removed in April 2026 research.
    # Alvarez Quant + QuantPedia data show bottom-3 sector mean-reversion has
    # NEGATIVE Sharpe historically — laggards keep lagging for months.
    # We only BUY top momentum now; exits happen when a holding rolls out of
    # the top-3 (handled on next rebalance, not via active SELLs).

    proposals.sort(key=lambda x: x["conviction"], reverse=True)
    return proposals


# ============================================================
# STRATEGY 4: Overnight Drift on SPY/QQQ
# ============================================================
#
# Research (April 2026): Elm Wealth + arxiv 2025 + Bespoke analysis confirm
# Q3 2020–Q3 2025 SPY close-to-open returned +47% vs open-to-close +30%.
# Simple close-to-open capture is the best retail alpha addition: low
# complexity, Sharpe 0.8–1.2 standalone.
#
# Rules:
#   - Fires only in the pre-close window (15:45–16:00 ET) to buy into close
#   - Only SPY / QQQ (where the effect is cleanest and liquidity is infinite)
#   - Skip if VIX spiked >15% intraday (crisis kills the overnight effect)
#   - Partner SELL happens next morning at open, via tracker close-out logic
#     (not auto-scheduled here — add later when we have a morning fire tier)
#
# This function only surfaces BUYs; the SELL half is managed by holding
# period logic in the tracker or a future overnight_exit strategy.


def scan_overnight_drift(params: dict) -> list:
    """Pre-close BUYs on SPY/QQQ to capture overnight drift."""
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except ImportError:
        now_et = datetime.now()

    # Only fire in the 15:45–15:59 ET window
    if not (now_et.hour == 15 and now_et.minute >= 45):
        if params.get("allow_any_time", False):
            pass  # backtest / dry-run flag
        else:
            return []

    tickers = params.get("tickers") or ["SPY", "QQQ"]
    vix_spike_floor = params.get("vix_spike_pct", 0.15)

    # Intraday VIX check — skip if crisis-mode spike
    try:
        from lib.api import yahoo_finance_price_history
        vix_df = yahoo_finance_price_history("^VIX", period="5d")
        if vix_df is not None and not vix_df.empty and len(vix_df) >= 2:
            vix_today = float(vix_df["Close"].iloc[-1])
            vix_prev = float(vix_df["Close"].iloc[-2])
            if vix_today / vix_prev - 1 > vix_spike_floor:
                logger.info("Overnight drift: VIX +%.1f%% today, > %.1f%% crisis floor — skip",
                            (vix_today / vix_prev - 1) * 100, vix_spike_floor * 100)
                return []
    except Exception:
        pass  # non-fatal; continue without filter

    proposals = []
    for ticker in tickers:
        try:
            df = yahoo_finance_price_history(ticker, period="5d")
            if df is None or df.empty:
                continue
            current_price = float(df["Close"].iloc[-1])
        except Exception:
            continue

        if current_price <= 0:
            continue

        proposals.append({
            "strategy": "overnight_drift",
            "ticker": ticker,
            "side": "BUY",
            "limit_price": round(current_price * 1.001, 2),  # tight buffer, deep liquidity
            "conviction": 4,
            "sector": TICKER_SECTOR.get(ticker, "Broad Market"),
            "reason": f"Overnight drift entry, target exit at tomorrow open",
            "hold_hours": 17,  # ~close to next open
        })

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

    # Overnight Drift (SPY/QQQ, pre-close window only)
    overnight_params = params.get("overnight_drift", {})
    overnight = scan_overnight_drift(overnight_params)
    weight = overnight_params.get("weight", 1.0)
    for p in overnight:
        p["strategy_weight"] = weight
    all_proposals.extend(overnight)
    logger.info("Overnight Drift: %d proposals", len(overnight))

    # Deduplicate by ticker — keep highest conviction per ticker
    seen = {}
    for p in sorted(all_proposals, key=lambda x: x["conviction"], reverse=True):
        key = (p["ticker"], p["side"])
        if key not in seen:
            seen[key] = p

    deduped = sorted(seen.values(), key=lambda x: x["conviction"], reverse=True)
    logger.info("Total: %d proposals (%d after dedup)", len(all_proposals), len(deduped))
    return deduped
