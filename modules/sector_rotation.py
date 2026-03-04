"""Sector rotation tracker using Select Sector SPDR ETFs.

Analyzes the 11 sector ETFs (XLK, XLF, XLE, etc.) for rotation signals,
relative strength, and breadth indicators.

Data source: yfinance (6-month history)
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import datetime

try:
    import pandas as pd
    import numpy as np
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False

from lib.data_envelope import create_envelope, save_envelope
from lib.cache import make_cache_key, get_cached, set_cached

logger = logging.getLogger(__name__)

SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}

# Rotation types
DEFENSIVE_SECTORS = {"XLU", "XLP", "XLV"}
GROWTH_SECTORS = {"XLK", "XLY", "XLC"}
ENERGY_SECTOR = {"XLE"}
MATERIALS_SECTOR = {"XLB"}


def setup_logging():
    """Configure logging (call only in main)."""
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


def fetch_price_history(ticker: str, period: str = "6mo") -> pd.DataFrame | None:
    """Fetch price history from yfinance with caching.

    Handles MultiIndex columns from yfinance 0.2.x+ by flattening them.
    Also validates price data for NaN and negative values.
    """
    if not HAS_YFINANCE or not HAS_PANDAS:
        logger.error("yfinance or pandas not available")
        return None

    # Try cache first
    cache_key = make_cache_key("yfinance", "history", {"ticker": ticker, "period": period})
    cached = get_cached(cache_key, max_age_hours=24)
    if cached:
        try:
            df = pd.DataFrame(cached)
            logger.info("Loaded %s from cache", ticker)
            return df
        except Exception as e:
            logger.warning("Cache parse error for %s: %s", ticker, e)

    # Fetch from yfinance
    try:
        df = yf.download(ticker, period=period, auto_adjust=True, progress=False)
        if df.empty:
            logger.warning("No data for %s", ticker)
            return None

        # Flatten MultiIndex columns (yfinance 0.2.x+ returns MultiIndex for single tickers)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            logger.debug("Flattened MultiIndex columns for %s", ticker)

        # Validate price data
        required_cols = ["Close", "High", "Low", "Volume"]
        for col in required_cols:
            if col in df.columns:
                if df[col].isna().any():
                    logger.warning("%s has NaN values in %s column", ticker, col)
                if (df[col] < 0).any():
                    logger.warning("%s has negative values in %s column", ticker, col)

        # Cache the data
        cache_data = df.reset_index().to_dict(orient="records")
        set_cached(cache_key, cache_data)

        logger.info("Downloaded %s: %d rows", ticker, len(df))
        return df
    except Exception as e:
        logger.error("yfinance error for %s: %s", ticker, e)
        return None


def calculate_returns(df: pd.DataFrame) -> dict | None:
    """Calculate returns over various periods.

    Handles MultiIndex columns and validates price data.
    """
    if df is None or df.empty:
        return None

    try:
        # Ensure columns are flat strings (handle MultiIndex)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close = df["Close"]
        current = close.iloc[-1]

        # Validate current price
        if pd.isna(current) or current <= 0:
            logger.warning("Invalid current price: %s", current)
            return None

        # Calculate returns for each period
        returns = {}
        periods = {
            "return_1d": 1,
            "return_5d": 5,
            "return_21d": 21,
            "return_63d": 63,
        }

        for name, days in periods.items():
            if len(close) > days:
                past_price = close.iloc[-days - 1]
                if pd.isna(past_price) or past_price <= 0:
                    logger.warning("Invalid past price for %s: %s", name, past_price)
                    returns[name] = None
                else:
                    ret = ((current - past_price) / past_price) * 100
                    returns[name] = round(ret, 2)
            else:
                returns[name] = None

        return returns
    except Exception as e:
        logger.error("Return calculation error: %s", e)
        return None


def calculate_trend(df: pd.DataFrame) -> str:
    """Determine trend using 20-day moving average.

    Handles MultiIndex columns and validates data.
    """
    if df is None or df.empty or len(df) < 20:
        return "unknown"

    try:
        # Ensure columns are flat strings (handle MultiIndex)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close = df["Close"]
        sma_20 = close.rolling(window=20).mean()
        current = close.iloc[-1]
        sma_val = sma_20.iloc[-1]

        # Validate values
        if pd.isna(sma_val) or pd.isna(current):
            return "unknown"

        if current <= 0 or sma_val <= 0:
            logger.warning("Invalid prices for trend calculation: current=%s, sma=%s", current, sma_val)
            return "unknown"

        return "uptrend" if current > sma_val else "downtrend"
    except Exception as e:
        logger.error("Trend calculation error: %s", e)
        return "unknown"


def calculate_volume_ratio(df: pd.DataFrame) -> float | None:
    """Calculate current volume / 20-day average volume.

    Handles MultiIndex columns and validates volume data.
    """
    if df is None or df.empty or len(df) < 20:
        return None

    try:
        # Ensure columns are flat strings (handle MultiIndex)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        volume = df["Volume"]
        current_vol = volume.iloc[-1]
        avg_vol = volume.iloc[-20:].mean()

        # Validate volume data
        if pd.isna(current_vol) or pd.isna(avg_vol):
            logger.warning("Invalid volume data: current=%s, avg=%s", current_vol, avg_vol)
            return None

        if avg_vol == 0 or current_vol < 0:
            logger.warning("Invalid volume values: current=%s, avg=%s", current_vol, avg_vol)
            return None

        ratio = current_vol / avg_vol
        return round(ratio, 2)
    except Exception as e:
        logger.error("Volume ratio error: %s", e)
        return None


def analyze_sectors() -> dict | None:
    """Analyze all sectors and SPY for rotation signals."""
    if not HAS_PANDAS or not HAS_YFINANCE:
        logger.error("pandas or yfinance not available for sector analysis")
        return None

    all_sectors = {}
    spy_data = None
    spy_returns = None

    # Fetch SPY for relative strength calculation
    spy_df = fetch_price_history("SPY", period="6mo")
    if spy_df is not None:
        spy_returns = calculate_returns(spy_df)
        logger.info("SPY returns: %s", spy_returns)

    # Fetch each sector ETF
    for etf, sector in SECTOR_ETFS.items():
        df = fetch_price_history(etf, period="6mo")
        if df is None or df.empty:
            logger.warning("Failed to fetch %s", etf)
            continue

        try:
            # Ensure columns are flat strings (handle MultiIndex)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            close = df["Close"]
            current_price = round(float(close.iloc[-1]), 2)

            # Get returns
            returns = calculate_returns(df)
            if returns is None:
                logger.warning("No returns for %s", etf)
                continue

            # Calculate relative strength vs SPY
            rel_strength = {}
            if spy_returns:
                for period in ["return_1d", "return_5d", "return_21d", "return_63d"]:
                    if returns[period] is not None and spy_returns.get(period) is not None:
                        rel_strength[period] = round(returns[period] - spy_returns[period], 2)
                    else:
                        rel_strength[period] = None

            # Get trend
            trend = calculate_trend(df)

            # Get volume ratio
            vol_ratio = calculate_volume_ratio(df)

            all_sectors[etf] = {
                "sector": sector,
                "price": current_price,
                "return_1d": returns.get("return_1d"),
                "return_5d": returns.get("return_5d"),
                "return_21d": returns.get("return_21d"),
                "return_63d": returns.get("return_63d"),
                "relative_strength_1d": rel_strength.get("return_1d"),
                "relative_strength_5d": rel_strength.get("return_5d"),
                "relative_strength_21d": rel_strength.get("return_21d"),
                "relative_strength_63d": rel_strength.get("return_63d"),
                "trend": trend,
                "volume_ratio": vol_ratio,
            }
            logger.info("%s (%s): price=%.2f, ret_21d=%.2f, rel_str_21d=%.2f, trend=%s",
                       etf, sector, current_price, returns.get("return_21d") or 0,
                       rel_strength.get("return_21d") or 0, trend)
        except Exception as e:
            logger.error("Analysis error for %s: %s", etf, e)
            continue

    if not all_sectors:
        logger.error("No sector data available")
        return None

    return {
        "all_sectors": all_sectors,
        "spy_returns": spy_returns,
    }


def get_leaders_laggards(all_sectors: dict, timeframe: str = "21d") -> tuple[list, list]:
    """Get top 3 and bottom 3 sectors by relative strength."""
    rel_strength_key = f"relative_strength_{timeframe}"

    # Filter sectors with valid data
    valid = [
        (etf, data)
        for etf, data in all_sectors.items()
        if data[rel_strength_key] is not None
    ]

    if not valid:
        return [], []

    # Sort by relative strength
    valid.sort(key=lambda x: x[1][rel_strength_key], reverse=True)

    leaders = []
    laggards = []

    # Top 3
    for etf, data in valid[:3]:
        leaders.append({
            "etf": etf,
            "sector": data["sector"],
            f"return_{timeframe}": data[f"return_{timeframe}"],
            f"relative_strength": data[rel_strength_key],
            "trend": data["trend"],
        })

    # Bottom 3
    for etf, data in valid[-3:]:
        laggards.append({
            "etf": etf,
            "sector": data["sector"],
            f"return_{timeframe}": data[f"return_{timeframe}"],
            f"relative_strength": data[rel_strength_key],
            "trend": data["trend"],
        })

    return leaders, laggards


def detect_rotation_type(leaders_5d: list, leaders_21d: list) -> str:
    """Detect rotation type based on leader composition."""
    if not leaders_5d or not leaders_21d:
        return "stable"

    # Get ETFs in each leadership group
    leaders_5d_etfs = {item["etf"] for item in leaders_5d}
    leaders_21d_etfs = {item["etf"] for item in leaders_21d}

    # Check if they're the same
    if leaders_5d_etfs == leaders_21d_etfs:
        return "stable"

    # Check defensive rotation (utilities, staples, health care becoming leaders)
    defensive_in_5d = len(leaders_5d_etfs & DEFENSIVE_SECTORS)
    defensive_in_21d = len(leaders_21d_etfs & DEFENSIVE_SECTORS)

    if defensive_in_5d > defensive_in_21d:
        return "defensive_rotation"

    # Check growth rotation (tech, discretionary, comms becoming leaders)
    growth_in_5d = len(leaders_5d_etfs & GROWTH_SECTORS)
    growth_in_21d = len(leaders_21d_etfs & GROWTH_SECTORS)

    if growth_in_5d > growth_in_21d:
        return "growth_rotation"

    # Check energy rotation
    energy_in_5d = len(leaders_5d_etfs & ENERGY_SECTOR)
    energy_in_21d = len(leaders_21d_etfs & ENERGY_SECTOR)

    if energy_in_5d > energy_in_21d:
        return "energy_rotation"

    return "stable"


def calculate_breadth(all_sectors: dict) -> dict:
    """Calculate breadth: number of positive vs negative sectors."""
    positive = 0
    negative = 0

    for etf, data in all_sectors.items():
        ret_5d = data.get("return_5d")
        if ret_5d is not None:
            if ret_5d > 0:
                positive += 1
            else:
                negative += 1

    total = positive + negative

    # Determine breadth label
    if positive >= 8:
        label = "broad_rally"
    elif positive <= 3:
        label = "broad_selloff"
    else:
        label = "mixed"

    return {
        "positive_sectors": positive,
        "negative_sectors": negative,
        "total_sectors": total,
        "label": label,
    }


def determine_signal(all_sectors: dict, rotation_type: str, breadth: dict) -> tuple[str, str]:
    """Determine overall signal and signal detail."""
    signal = "stable"
    detail = "No significant rotation detected"

    if rotation_type == "defensive_rotation":
        signal = "rotation_alert"
        detail = "Money rotating from growth to defensives — risk-off shift detected"
    elif rotation_type == "growth_rotation":
        signal = "growth_momentum"
        detail = "Growth and discretionary sectors gaining leadership — risk-on environment"
    elif rotation_type == "energy_rotation":
        signal = "energy_strength"
        detail = "Energy leading — commodity/inflation cycle in effect"

    # Breadth-based signals can override or refine
    if breadth["label"] == "broad_rally":
        if signal == "stable":
            signal = "broad_rally"
            detail = f"Broad-based rally: {breadth['positive_sectors']}/{breadth['total_sectors']} sectors positive"
    elif breadth["label"] == "broad_selloff":
        if signal != "rotation_alert":
            signal = "broad_selloff"
            detail = f"Broad-based selloff: only {breadth['positive_sectors']}/{breadth['total_sectors']} sectors positive"

    return signal, detail


def main():
    """Main entry point."""
    setup_logging()
    logger.info("=== Sector Rotation Tracker ===")

    # Check dependencies
    if not HAS_PANDAS or not HAS_YFINANCE:
        error_msg = "pandas or yfinance not available"
        logger.error(error_msg)
        envelope = create_envelope("sector_rotation", {}, status="error", error=error_msg)
        save_envelope(envelope, "sector_rotation.json")
        return

    # Analyze sectors
    result = analyze_sectors()
    if result is None:
        error_msg = "Failed to analyze sectors"
        logger.error(error_msg)
        envelope = create_envelope("sector_rotation", {}, status="error", error=error_msg)
        save_envelope(envelope, "sector_rotation.json")
        return

    all_sectors = result["all_sectors"]
    spy_returns = result["spy_returns"]

    # Get leaders and laggards
    leaders_21d, laggards_21d = get_leaders_laggards(all_sectors, "21d")
    leaders_5d, laggards_5d = get_leaders_laggards(all_sectors, "5d")

    if not leaders_21d or not leaders_5d:
        error_msg = "Insufficient sector data to determine leaders"
        logger.error(error_msg)
        envelope = create_envelope("sector_rotation", {}, status="error", error=error_msg)
        save_envelope(envelope, "sector_rotation.json")
        return

    # Detect rotation type
    rotation_type = detect_rotation_type(leaders_5d, leaders_21d)
    logger.info("Rotation type: %s", rotation_type)

    # Calculate breadth
    breadth = calculate_breadth(all_sectors)
    logger.info("Breadth: %s positive, %s negative (%s)",
               breadth["positive_sectors"], breadth["negative_sectors"], breadth["label"])

    # Determine signal
    signal, signal_detail = determine_signal(all_sectors, rotation_type, breadth)
    logger.info("Signal: %s — %s", signal, signal_detail)

    # Build output envelope
    data = {
        "signal": signal,
        "signal_detail": signal_detail,
        "leaders_21d": leaders_21d,
        "laggards_21d": laggards_21d,
        "leaders_5d": leaders_5d,
        "laggards_5d": laggards_5d,
        "rotation_type": rotation_type,
        "breadth": breadth,
        "all_sectors": all_sectors,
        "spy_returns": spy_returns,
        "data_source": "yfinance",
        "analyzed_at": datetime.now().astimezone().isoformat(),
    }

    envelope = create_envelope("sector_rotation", data, status="success")
    save_envelope(envelope, "sector_rotation.json")
    logger.info("Sector rotation analysis complete")


if __name__ == "__main__":
    main()
