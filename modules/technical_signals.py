"""Technical signals: RSI, MACD, Bollinger Bands, SMA, volume analysis with composite scoring."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging

import pandas as pd

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


def analyze_ticker(ticker: str, history: pd.DataFrame) -> dict | None:
    """Compute technical indicators for a single ticker.

    Returns None if insufficient data.
    """
    if history.empty or len(history) < 200:
        logger.warning("%s: insufficient history (%d rows, need 200+)", ticker, len(history))
        return None

    try:
        import pandas_ta as ta
    except ImportError:
        logger.error("pandas-ta not installed, cannot compute technical signals")
        return None

    close = history["Close"]
    high = history.get("High", close)
    low = history.get("Low", close)
    volume = history.get("Volume")

    # RSI(14)
    rsi_series = ta.rsi(close, length=14)
    rsi_14 = round(float(rsi_series.iloc[-1]), 2) if rsi_series is not None and not rsi_series.empty else None

    # MACD(12, 26, 9)
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    if macd_df is not None and not macd_df.empty:
        macd_hist = float(macd_df.iloc[-1, 2])  # MACDh column
        macd_hist_prev = float(macd_df.iloc[-2, 2]) if len(macd_df) >= 2 else 0
        macd_line = float(macd_df.iloc[-1, 0])
        macd_signal_line = float(macd_df.iloc[-1, 1])
        if macd_line > macd_signal_line and macd_hist > macd_hist_prev:
            macd_signal = "bullish_crossover"
        elif macd_line < macd_signal_line and macd_hist < macd_hist_prev:
            macd_signal = "bearish_crossover"
        else:
            macd_signal = "neutral"
        macd_histogram = round(macd_hist, 4)
    else:
        macd_signal = "neutral"
        macd_histogram = 0.0
        macd_hist = 0.0
        macd_hist_prev = 0.0

    # Bollinger Bands(20, 2)
    bb = ta.bbands(close, length=20, std=2)
    if bb is not None and not bb.empty:
        upper = float(bb.iloc[-1, 2])  # BBU
        lower = float(bb.iloc[-1, 0])  # BBL
        current = float(close.iloc[-1])
        if current > upper:
            bb_position = "above_upper"
        elif current < lower:
            bb_position = "below_lower"
        else:
            # Check for squeeze (bandwidth)
            mid = float(bb.iloc[-1, 1])
            bandwidth = (upper - lower) / mid if mid > 0 else 0
            bb_position = "squeeze" if bandwidth < 0.04 else "middle"
    else:
        bb_position = "unknown"

    # SMA 20/50/200
    sma_20 = float(ta.sma(close, length=20).iloc[-1]) if len(close) >= 20 else None
    sma_50 = float(ta.sma(close, length=50).iloc[-1]) if len(close) >= 50 else None
    sma_200 = float(ta.sma(close, length=200).iloc[-1]) if len(close) >= 200 else None

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

    # Composite scoring
    indicators = []

    # RSI score
    rsi_score = 0.0
    if rsi_14 is not None:
        if rsi_14 < 30:
            rsi_score = 1.0
            rsi_signal = "oversold"
        elif rsi_14 > 70:
            rsi_score = -1.0
            rsi_signal = "overbought"
        else:
            rsi_signal = "neutral"
        indicators.append({"name": "RSI", "value": rsi_14, "signal": rsi_signal, "score": rsi_score})

    # MACD score
    macd_score = 0.0
    if macd_hist > 0 and macd_hist > macd_hist_prev:
        macd_score = 1.0
    elif macd_hist < 0 and macd_hist < macd_hist_prev:
        macd_score = -1.0
    indicators.append({"name": "MACD", "value": macd_histogram, "signal": macd_signal, "score": macd_score})

    # Bollinger Bands score
    bb_score = 0.0
    if bb_position == "below_lower":
        bb_score = 1.0
    elif bb_position == "above_upper":
        bb_score = -1.0
    indicators.append({"name": "Bollinger Bands", "value": bb_position, "signal": bb_position, "score": bb_score})

    # SMA score
    sma_score = 0.0
    if sma_200 and current_price > sma_200:
        sma_score += 0.5
    if sma_trend == "golden_cross":
        sma_score += 0.5
    elif sma_trend == "death_cross":
        sma_score = -1.0
    indicators.append({"name": "SMA", "value": sma_trend, "signal": sma_trend, "score": sma_score})

    # Volume score
    vol_score = 0.0
    if volume_ratio and volume_ratio > 1.5:
        vol_score = 1.0 if price_change > 0 else -1.0
    vol_signal = "surge_up" if vol_score > 0 else "surge_down" if vol_score < 0 else "normal"
    indicators.append({"name": "Volume", "value": volume_ratio, "signal": vol_signal, "score": vol_score})

    # Composite: sum clamped to [-5, +5]
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
        "indicators": indicators,
    }


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
