"""Position sizing calculator: risk-based position recommendations."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging

from lib.data_envelope import create_envelope, save_envelope, load_envelope

logger = logging.getLogger(__name__)


def setup_logging():
    """Call this ONLY inside main(). NEVER at module level."""
    log_dir = PROJECT_ROOT / "data" / "outputs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(log_dir / "run.log", mode="a"),
            ],
        )


def get_all_tickers() -> list[str]:
    """Get union of watchlist tickers and portfolio holdings, deduped."""
    tickers = set()
    watchlist_path = PROJECT_ROOT / "config" / "watchlist.json"
    portfolio_path = PROJECT_ROOT / "config" / "portfolio.json"

    if watchlist_path.exists():
        try:
            wl = json.loads(watchlist_path.read_text())
            tickers.update(wl.get("tickers", []))
        except (json.JSONDecodeError, OSError):
            pass

    if portfolio_path.exists():
        try:
            pf = json.loads(portfolio_path.read_text())
            for h in pf.get("holdings", []):
                tickers.add(h["ticker"])
        except (json.JSONDecodeError, OSError):
            pass

    return sorted(tickers)


def load_settings() -> dict:
    path = PROJECT_ROOT / "config" / "settings.json"
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def load_portfolio_config() -> dict:
    path = PROJECT_ROOT / "config" / "portfolio.json"
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def calculate_portfolio_value(portfolio_data: dict, cash: float) -> float:
    """Calculate total portfolio value from processed portfolio data."""
    holdings_value = sum(
        (h.get("current_price", 0) or 0) * (h.get("shares", 0) or 0)
        for h in portfolio_data.get("holdings", [])
    )
    return holdings_value + cash


def check_correlation_overlap(ticker: str, portfolio_data: dict, threshold: float = 0.80) -> bool:
    """Check if ticker is highly correlated with any existing holding."""
    correlations = portfolio_data.get("correlations", {})
    for pair, data in correlations.items():
        corr_val = data.get("correlation") if isinstance(data, dict) else data
        if corr_val and abs(corr_val) >= threshold:
            tickers_in_pair = pair.split("_")
            if ticker in tickers_in_pair:
                return True
    return False


def _calculate_entry_levels(current_price: float, high_volume_nodes: list) -> dict:
    """Calculate 3-tier entry levels using volume profile support or % fallbacks.

    - Aggressive: current price (enter now)
    - Moderate: nearest HVN below price, or 3% pullback
    - Conservative: next HVN below moderate, or 6% pullback
    """
    fallback_moderate = round(current_price * 0.97, 2)
    fallback_conservative = round(current_price * 0.94, 2)

    # Filter HVNs below current price, sorted descending (closest first)
    support_nodes = sorted(
        [n for n in high_volume_nodes if n < current_price],
        reverse=True,
    )

    aggressive = {
        "price": round(current_price, 2),
        "note": "Current price",
    }

    if len(support_nodes) >= 1:
        mod_price = round(support_nodes[0], 2)
        moderate = {"price": mod_price, "note": f"Volume support at ${mod_price}"}
    else:
        moderate = {"price": fallback_moderate, "note": "3% pullback"}

    if len(support_nodes) >= 2:
        cons_price = round(support_nodes[1], 2)
        conservative = {"price": cons_price, "note": f"Deep support at ${cons_price}"}
    else:
        conservative = {"price": fallback_conservative, "note": "6% pullback"}

    return {
        "aggressive": aggressive,
        "moderate": moderate,
        "conservative": conservative,
    }


def _calculate_exit_targets(current_price: float, atr: float) -> dict:
    """Calculate 3-tier exit targets using ATR-based Fibonacci extension equivalents.

    - Target 1: +2.0x ATR (~127.2% Fib)
    - Target 2: +3.5x ATR (~161.8% Fib)
    - Target 3: +6.0x ATR (~261.8% Fib)
    """
    targets = {}
    for label, multiplier in [("target_1", 2.0), ("target_2", 3.5), ("target_3", 6.0)]:
        target_price = round(current_price + (atr * multiplier), 2)
        pct_gain = round(((target_price - current_price) / current_price) * 100, 1)
        targets[label] = {
            "price": target_price,
            "note": f"{multiplier}x ATR (+{pct_gain}%)",
        }
    return targets


def size_position(ticker: str, current_price: float, atr: float,
                  portfolio_value: float, settings: dict,
                  is_correlated: bool = False,
                  volume_profile: dict = None) -> dict:
    """Calculate position size for a single ticker with 3-tier entry/exit levels.

    Uses fixed risk model: risk X% of portfolio per trade.
    Stop loss = entry - (ATR * multiplier)
    Shares = (portfolio * risk%) / (entry - stop)

    Entry levels use volume profile HVNs when available, otherwise % pullbacks.
    Exit targets use ATR-based Fibonacci extension equivalents.
    """
    risk_pct = settings.get("risk_per_trade_pct", 0.02)
    max_position_pct = settings.get("max_position_pct", 0.15)
    atr_mult = settings.get("atr_multiplier_default", 2.0)

    if not current_price or current_price <= 0 or not atr or atr <= 0:
        return {
            "ticker": ticker,
            "recommended_shares": 0,
            "recommended_value": 0,
            "stop_loss": None,
            "risk_per_share": None,
            "portfolio_pct": 0,
            "entry_levels": {},
            "exit_targets": {},
            "note": "Insufficient price/ATR data",
        }

    stop_loss = round(current_price - (atr * atr_mult), 2)
    risk_per_share = current_price - stop_loss

    if risk_per_share <= 0:
        return {
            "ticker": ticker,
            "recommended_shares": 0,
            "recommended_value": 0,
            "stop_loss": stop_loss,
            "risk_per_share": 0,
            "portfolio_pct": 0,
            "entry_levels": {},
            "exit_targets": {},
            "note": "ATR stop is at or above current price — skip",
        }

    # Fixed risk model
    risk_amount = portfolio_value * risk_pct
    shares = int(risk_amount / risk_per_share)

    # Cap at max position percentage
    max_value = portfolio_value * max_position_pct
    max_shares = int(max_value / current_price)
    shares = min(shares, max_shares)

    # Halve if correlated with existing holding
    if is_correlated:
        shares = max(1, shares // 2)

    value = round(shares * current_price, 2)
    pct = round(value / portfolio_value, 4) if portfolio_value > 0 else 0

    note_parts = []
    note_parts.append(f"Based on {risk_pct:.0%} risk with ${atr * atr_mult:.2f} ATR stop")
    if is_correlated:
        note_parts.append("halved due to correlation with existing holding")
    if shares == max_shares:
        note_parts.append(f"capped at {max_position_pct:.0%} max position")

    # 3-tier entry/exit calculations
    hvn_list = []
    if volume_profile and isinstance(volume_profile, dict):
        hvn_list = volume_profile.get("high_volume_nodes", [])
    entry_levels = _calculate_entry_levels(current_price, hvn_list)
    exit_targets = _calculate_exit_targets(current_price, atr)

    return {
        "ticker": ticker,
        "recommended_shares": shares,
        "recommended_value": value,
        "stop_loss": stop_loss,
        "risk_per_share": round(risk_per_share, 2),
        "portfolio_pct": pct,
        "entry_levels": entry_levels,
        "exit_targets": exit_targets,
        "note": " | ".join(note_parts),
    }


def analyze_position_sizes() -> dict:
    """Calculate position sizes for all tickers with price/ATR data."""
    settings = load_settings()
    portfolio_config = load_portfolio_config()
    cash = portfolio_config.get("cash", 0)

    portfolio_env = load_envelope("portfolio.json")
    portfolio_data = portfolio_env.get("data", {})

    # Load technical signals for volume profile data (entry level support)
    tech_env = load_envelope("technical_signals.json")
    tech_data = tech_env.get("data", {})
    volume_profile_by_ticker = {}
    for result in tech_data.get("results", []):
        t = result.get("ticker")
        if t and result.get("volume_profile"):
            volume_profile_by_ticker[t] = result["volume_profile"]

    portfolio_value = calculate_portfolio_value(portfolio_data, cash)

    atr_by_ticker = {}
    price_by_ticker = {}
    for h in portfolio_data.get("holdings", []):
        t = h.get("ticker")
        if t:
            atr_by_ticker[t] = h.get("atr")
            price_by_ticker[t] = h.get("current_price")

    results = []
    all_tickers = get_all_tickers()
    for ticker in all_tickers:
        price = price_by_ticker.get(ticker)
        atr = atr_by_ticker.get(ticker)

        if not price or not atr:
            continue

        is_correlated = check_correlation_overlap(
            ticker, portfolio_data,
            settings.get("risk_correlation_alert", 0.80),
        )

        vol_profile = volume_profile_by_ticker.get(ticker)
        sizing = size_position(ticker, price, atr, portfolio_value, settings,
                               is_correlated, volume_profile=vol_profile)
        results.append(sizing)

        if sizing["recommended_shares"] > 0:
            logger.info("%s: %d shares ($%.0f) @ $%.2f with $%.2f stop — %s",
                        ticker, sizing["recommended_shares"], sizing["recommended_value"],
                        price, sizing["stop_loss"], sizing["note"])

    return {
        "portfolio_value": round(portfolio_value, 2),
        "risk_per_trade_pct": settings.get("risk_per_trade_pct", 0.02),
        "max_position_pct": settings.get("max_position_pct", 0.15),
        "positions": results,
    }


def main():
    setup_logging()
    logger.info("=== Position Sizer Module ===")

    data = analyze_position_sizes()
    status = "success"
    error = None

    envelope = create_envelope("position_sizer", data, status=status, error=error)
    save_envelope(envelope, "position_sizer.json")
    logger.info("Position sizing complete: %d tickers, portfolio $%.0f",
                len(data["positions"]), data["portfolio_value"])


if __name__ == "__main__":
    main()
