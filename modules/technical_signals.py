"""Technical signals: RSI, MACD, Bollinger Bands, SMA, volume analysis with composite scoring.

Pure pandas/numpy implementations. No pandas-ta dependency.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging

import pandas as pd
import numpy as np

from lib.data_envelope import create_envelope, save_envelope
from lib.api import yahoo_finance_price_history

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


def calculate_vwap(history: pd.DataFrame) -> float | None:
    """VWAP = cumulative(typical_price * volume) / cumulative(volume).
    Uses full history. Returns latest VWAP value."""
    if history.empty:
        return None
    try:
        tp = (history["High"] + history["Low"] + history["Close"]) / 3
        vol = history["Volume"]
        if vol.sum() == 0:
            return None
        vwap = (tp * vol).cumsum() / vol.cumsum()
        return round(float(vwap.iloc[-1]), 2)
    except Exception:
        return None


def calculate_volume_profile(history: pd.DataFrame, bins: int = 20) -> dict | None:
    """Identify price levels with highest traded volume.
    Returns Point of Control (POC) and nearby support/resistance.
    """
    if history.empty or len(history) < 20:
        return None
    try:
        import numpy as np
        closes = history["Close"].values
        volumes = history["Volume"].values
        price_range = np.linspace(closes.min(), closes.max(), bins + 1)

        profile = []
        for i in range(len(price_range) - 1):
            mask = (closes >= price_range[i]) & (closes < price_range[i + 1])
            vol = float(volumes[mask].sum())
            mid = round((price_range[i] + price_range[i + 1]) / 2, 2)
            profile.append({"price_level": mid, "volume": vol})

        if not profile:
            return None

        # Point of Control = price level with highest volume
        poc = max(profile, key=lambda x: x["volume"])

        # High Volume Nodes = top 3 levels (support/resistance)
        sorted_profile = sorted(profile, key=lambda x: x["volume"], reverse=True)
        hvn = [p["price_level"] for p in sorted_profile[:3]]

        return {
            "poc": poc["price_level"],
            "poc_volume": poc["volume"],
            "high_volume_nodes": hvn,
        }
    except Exception:
        return None


def calculate_rsi(close: pd.Series, length: int = 14) -> float | None:
    """Calculate RSI(length) using EWM method.

    Args:
        close: Close price series
        length: Period (default 14)

    Returns:
        RSI value or None if insufficient data
    """
    if len(close) < length:
        return None

    try:
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)

        # EWM with alpha = 1/length
        avg_gain = gain.ewm(alpha=1/length, min_periods=length).mean()
        avg_loss = loss.ewm(alpha=1/length, min_periods=length).mean()

        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))

        return round(float(rsi.iloc[-1]), 2)
    except Exception as e:
        logger.debug("RSI calculation error: %s", e)
        return None


def calculate_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[float, float, str]:
    """Calculate MACD histogram, line, signal line, and crossover signal.

    Args:
        close: Close price series
        fast: Fast EMA period (default 12)
        slow: Slow EMA period (default 26)
        signal: Signal line period (default 9)

    Returns:
        (macd_histogram_value, macd_line, signal_direction)
    """
    if len(close) < slow:
        return 0.0, 0.0, "neutral"

    try:
        ema_12 = close.ewm(span=fast, adjust=False).mean()
        ema_26 = close.ewm(span=slow, adjust=False).mean()
        macd_line = ema_12 - ema_26
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line

        hist_curr = float(histogram.iloc[-1])
        hist_prev = float(histogram.iloc[-2]) if len(histogram) >= 2 else 0.0
        line_curr = float(macd_line.iloc[-1])
        line_signal = float(signal_line.iloc[-1])

        # Determine crossover
        if line_curr > line_signal and hist_curr > hist_prev:
            macd_signal = "bullish_crossover"
        elif line_curr < line_signal and hist_curr < hist_prev:
            macd_signal = "bearish_crossover"
        else:
            macd_signal = "neutral"

        return round(hist_curr, 4), line_curr, macd_signal
    except Exception as e:
        logger.debug("MACD calculation error: %s", e)
        return 0.0, 0.0, "neutral"


def calculate_bollinger_bands(close: pd.Series, window: int = 20, std_dev: int = 2) -> tuple[float, float, float, str]:
    """Calculate Bollinger Bands and determine position.

    Args:
        close: Close price series
        window: SMA window (default 20)
        std_dev: Standard deviation multiplier (default 2)

    Returns:
        (upper_band, middle_band, lower_band, position_signal)
    """
    if len(close) < window:
        return None, None, None, "unknown"

    try:
        sma = close.rolling(window=window).mean()
        std = close.rolling(window=window).std()

        upper = sma + (std_dev * std)
        lower = sma - (std_dev * std)

        upper_val = float(upper.iloc[-1])
        middle_val = float(sma.iloc[-1])
        lower_val = float(lower.iloc[-1])
        current = float(close.iloc[-1])

        if current > upper_val:
            position = "above_upper"
        elif current < lower_val:
            position = "below_lower"
        else:
            # Check for squeeze
            bandwidth = (upper_val - lower_val) / middle_val if middle_val > 0 else 0
            position = "squeeze" if bandwidth < 0.04 else "middle"

        return upper_val, middle_val, lower_val, position
    except Exception as e:
        logger.debug("Bollinger Bands calculation error: %s", e)
        return None, None, None, "unknown"


def calculate_sma(close: pd.Series, window: int) -> float | None:
    """Calculate simple moving average.

    Args:
        close: Close price series
        window: SMA period

    Returns:
        SMA value or None if insufficient data
    """
    if len(close) < window:
        return None

    try:
        sma = close.rolling(window=window).mean()
        return round(float(sma.iloc[-1]), 2)
    except Exception:
        return None


def analyze_ticker(ticker: str, history: pd.DataFrame) -> dict | None:
    """Compute technical indicators for a single ticker.

    Returns None if insufficient data.
    """
    if history.empty or len(history) < 200:
        logger.warning("%s: insufficient history (%d rows, need 200+)", ticker, len(history))
        return None

    try:
        close = history["Close"]
        high = history.get("High", close)
        low = history.get("Low", close)
        volume = history.get("Volume")

        # RSI(14)
        rsi_14 = calculate_rsi(close, length=14)

        # MACD(12, 26, 9)
        macd_histogram, macd_line, macd_signal = calculate_macd(close, fast=12, slow=26, signal=9)
        macd_hist_prev = 0.0  # Simplified; used for trend detection

        # Bollinger Bands(20, 2)
        upper_bb, middle_bb, lower_bb, bb_position = calculate_bollinger_bands(close, window=20, std_dev=2)

        # SMA 20/50/200
        sma_20 = calculate_sma(close, window=20)
        sma_50 = calculate_sma(close, window=50)
        sma_200 = calculate_sma(close, window=200)

        current_price = float(close.iloc[-1])

        if sma_50 and sma_200:
            if sma_50 > sma_200:
                sma_trend = "golden_cross"
            else:
                sma_trend = "death_cross"
        elif sma_200:
            sma_trend = "above_200" if current_price > sma_200 else "below_200"
        else:
            sma_trend = "unknown"

        # Volume ratio
        volume_ratio = None
        if volume is not None and len(volume) >= 20:
            avg_vol = float(volume.iloc[-20:].mean())
            current_vol = float(volume.iloc[-1])
            volume_ratio = round(current_vol / avg_vol, 2) if avg_vol > 0 else None

        # Price change today
        price_change = float(close.iloc[-1] - close.iloc[-2]) if len(close) >= 2 else 0

        # Composite scoring — CONTINUOUS scores for better differentiation
        indicators = []

        # RSI score: continuous mapping from 0-100 to [-1, +1]
        # 30 → +1 (oversold=bullish), 50 → 0, 70 → -1 (overbought=bearish)
        rsi_score = 0.0
        if rsi_14 is not None:
            if rsi_14 < 30:
                rsi_score = 1.0
                rsi_signal = "oversold"
            elif rsi_14 > 70:
                rsi_score = -1.0
                rsi_signal = "overbought"
            elif rsi_14 < 50:
                # 30-50 maps to +1 to 0 (linear)
                rsi_score = round((50 - rsi_14) / 20, 2)
                rsi_signal = "bullish_lean"
            else:
                # 50-70 maps to 0 to -1 (linear)
                rsi_score = round(-(rsi_14 - 50) / 20, 2)
                rsi_signal = "bearish_lean"
            indicators.append({"name": "RSI", "value": rsi_14, "signal": rsi_signal, "score": rsi_score})

        # MACD score: use histogram magnitude relative to price for continuous scoring
        macd_score = 0.0
        if current_price > 0 and macd_histogram != 0:
            # Normalize histogram by price, scale to [-1, +1]
            normalized = macd_histogram / current_price * 100  # percent of price
            macd_score = round(max(-1.0, min(1.0, normalized * 10)), 2)
        indicators.append({"name": "MACD", "value": macd_histogram, "signal": macd_signal, "score": macd_score})

        # Bollinger Bands score: continuous based on position within bands
        bb_score = 0.0
        if upper_bb and lower_bb and middle_bb:
            bb_range = upper_bb - lower_bb
            if bb_range > 0:
                # Position: -1 at upper band, +1 at lower band (mean-reversion logic)
                bb_position_pct = (current_price - middle_bb) / (bb_range / 2)
                bb_score = round(max(-1.0, min(1.0, -bb_position_pct)), 2)
        indicators.append({"name": "Bollinger Bands", "value": bb_position, "signal": bb_position, "score": bb_score})

        # SMA score: continuous based on price distance from moving averages
        sma_score = 0.0
        if sma_200 and sma_200 > 0:
            # Distance from 200 SMA as % of price, scaled
            sma_dist_200 = (current_price - sma_200) / sma_200
            sma_score += round(max(-0.5, min(0.5, sma_dist_200 * 5)), 2)
        if sma_trend == "golden_cross":
            sma_score = round(min(1.0, sma_score + 0.5), 2)
        elif sma_trend == "death_cross":
            sma_score = round(max(-1.0, sma_score - 0.5), 2)
        indicators.append({"name": "SMA", "value": sma_trend, "signal": sma_trend, "score": sma_score})

        # Volume score: continuous based on volume ratio magnitude
        vol_score = 0.0
        if volume_ratio is not None and volume_ratio > 0:
            # Directional volume: high volume amplifies the price direction
            vol_magnitude = min(1.0, (volume_ratio - 1.0) / 1.5) if volume_ratio > 1.0 else 0.0
            if price_change > 0:
                vol_score = round(vol_magnitude, 2)
            elif price_change < 0:
                vol_score = round(-vol_magnitude, 2)
        vol_signal = "surge_up" if vol_score > 0.3 else "surge_down" if vol_score < -0.3 else "normal"
        indicators.append({"name": "Volume", "value": volume_ratio, "signal": vol_signal, "score": vol_score})

        # VWAP: continuous based on distance from VWAP
        vwap = calculate_vwap(history)
        vwap_signal = None
        vwap_score = 0.0
        if vwap is not None and vwap > 0:
            vwap_dist = (current_price - vwap) / vwap
            vwap_score = round(max(-0.5, min(0.5, vwap_dist * 20)), 2)
            if vwap_score > 0.1:
                vwap_signal = "above"
            elif vwap_score < -0.1:
                vwap_signal = "below"
            else:
                vwap_signal = "at_vwap"
            indicators.append({"name": "VWAP", "value": vwap, "signal": vwap_signal, "score": vwap_score})

        # Volume Profile
        vol_profile = calculate_volume_profile(history)

        # Composite: sum clamped to [-5, +5] (includes VWAP score)
        raw_composite = sum(ind["score"] for ind in indicators)
        composite_score = round(max(-5.0, min(5.0, raw_composite)), 2)

        return {
            "ticker": ticker,
            "composite_score": composite_score,
            "rsi_14": rsi_14,
            "macd_signal": macd_signal,
            "macd_histogram": macd_histogram,
            "bb_position": bb_position,
            "sma_trend": sma_trend,
            "volume_ratio": volume_ratio,
            "vwap": vwap,
            "vwap_signal": vwap_signal,
            "volume_profile": vol_profile,
            "indicators": indicators,
        }
    except Exception as e:
        logger.error("%s: analysis error: %s", ticker, e)
        return None


def analyze_technical_signals(tickers: list[str] = None) -> dict:
    """Run technical analysis on all tickers."""
    if tickers is None:
        tickers = get_all_tickers()

    results = []
    for ticker in tickers:
        history = yahoo_finance_price_history(ticker, period="1y")
        result = analyze_ticker(ticker, history)
        if result:
            results.append(result)
            logger.info("%s: composite=%.1f rsi=%s macd=%s",
                        ticker, result["composite_score"],
                        result["rsi_14"], result["macd_signal"])
        else:
            logger.warning("%s: skipped (insufficient data)", ticker)

    return {"results": results}


def main():
    setup_logging()
    logger.info("=== Technical Signals Module ===")

    data = analyze_technical_signals()
    status = "success" if data["results"] else "error"
    error = None if data["results"] else "No tickers had sufficient data"
    if data["results"] and len(data["results"]) < len(get_all_tickers()):
        status = "partial"

    envelope = create_envelope("technical_signals", data, status=status, error=error)
    save_envelope(envelope, "technical_signals.json")
    logger.info("Technical signals complete: %d tickers analyzed", len(data["results"]))


if __name__ == "__main__":
    main()
