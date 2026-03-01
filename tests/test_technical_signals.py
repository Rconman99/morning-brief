"""Tests for technical_signals module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch

from modules.technical_signals import (
    analyze_ticker, analyze_technical_signals, main,
    calculate_vwap, calculate_volume_profile,
)


def make_mock_history(days: int = 260, base_price: float = 500.0,
                      trend: float = 0.001) -> pd.DataFrame:
    """Generate mock price history with enough data for 200-day SMA."""
    dates = pd.date_range(end="2026-02-25", periods=days, freq="B")
    np.random.seed(42)
    returns = np.random.normal(trend, 0.015, days)
    prices = base_price * np.cumprod(1 + returns)
    high = prices * (1 + np.abs(np.random.normal(0, 0.008, days)))
    low = prices * (1 - np.abs(np.random.normal(0, 0.008, days)))
    return pd.DataFrame({
        "Open": prices * 0.999,
        "High": high,
        "Low": low,
        "Close": prices,
        "Volume": np.random.randint(5000000, 50000000, days),
    }, index=dates)


def test_analyze_ticker_returns_all_fields():
    """All indicator fields should be present in result."""
    history = make_mock_history(260)
    result = analyze_ticker("NVDA", history)
    assert result is not None
    assert result["ticker"] == "NVDA"
    assert "composite_score" in result
    assert "rsi_14" in result
    assert "macd_signal" in result
    assert "macd_histogram" in result
    assert "bb_position" in result
    assert "sma_trend" in result
    assert "volume_ratio" in result
    assert "indicators" in result
    assert len(result["indicators"]) >= 5  # 5 base + VWAP if calculable
    assert "vwap" in result
    assert "vwap_signal" in result
    assert "volume_profile" in result


def test_composite_score_in_range():
    """Composite score must be clamped to [-5, +5]."""
    history = make_mock_history(260)
    result = analyze_ticker("NVDA", history)
    assert -5.0 <= result["composite_score"] <= 5.0


def test_insufficient_data_returns_none():
    """Less than 200 rows should return None."""
    short_history = make_mock_history(50)
    result = analyze_ticker("NVDA", short_history)
    assert result is None


def test_empty_history_returns_none():
    result = analyze_ticker("NVDA", pd.DataFrame())
    assert result is None


def test_rsi_value_in_range():
    """RSI should be between 0 and 100."""
    history = make_mock_history(260)
    result = analyze_ticker("NVDA", history)
    assert 0 <= result["rsi_14"] <= 100


def test_macd_signal_valid():
    """MACD signal should be one of the expected values."""
    history = make_mock_history(260)
    result = analyze_ticker("NVDA", history)
    assert result["macd_signal"] in ("bullish_crossover", "bearish_crossover", "neutral")


def test_bb_position_valid():
    """BB position should be a known value."""
    history = make_mock_history(260)
    result = analyze_ticker("NVDA", history)
    assert result["bb_position"] in ("above_upper", "below_lower", "middle", "squeeze", "unknown")


def test_sma_trend_valid():
    """SMA trend should be a known value."""
    history = make_mock_history(260)
    result = analyze_ticker("NVDA", history)
    assert result["sma_trend"] in ("golden_cross", "death_cross", "above_200", "below_200", "unknown")


@patch("modules.technical_signals.yahoo_finance_price_history")
@patch("modules.technical_signals.get_all_tickers")
def test_analyze_technical_signals(mock_tickers, mock_yf):
    """Full analysis should return results for each ticker."""
    mock_tickers.return_value = ["NVDA", "AAPL"]
    mock_yf.side_effect = lambda t, **kw: make_mock_history(260, 500 if t == "NVDA" else 180)

    data = analyze_technical_signals()
    assert len(data["results"]) == 2
    tickers = {r["ticker"] for r in data["results"]}
    assert tickers == {"NVDA", "AAPL"}


@patch("modules.technical_signals.yahoo_finance_price_history")
@patch("modules.technical_signals.get_all_tickers")
def test_main_creates_output(mock_tickers, mock_yf, tmp_path):
    mock_tickers.return_value = ["NVDA"]
    mock_yf.return_value = make_mock_history(260)

    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    outputs = tmp_path / "data" / "outputs"
    outputs.mkdir(parents=True)

    with patch("modules.technical_signals.PROJECT_ROOT", tmp_path), \
         patch("lib.data_envelope.PROCESSED_DIR", processed):
        main()

    output_file = processed / "technical_signals.json"
    assert output_file.exists()
    envelope = json.loads(output_file.read_text())
    assert envelope["module"] == "technical_signals"
    assert envelope["status"] in ("success", "partial")
    assert len(envelope["data"]["results"]) == 1


# ── VWAP tests ──

def test_calculate_vwap_basic():
    """VWAP should be a weighted average of typical price by volume."""
    np.random.seed(99)
    history = pd.DataFrame({
        "High": [110, 120, 115],
        "Low": [90, 100, 95],
        "Close": [100, 110, 105],
        "Volume": [1000, 2000, 1000],
    })
    # typical_price = (H+L+C)/3 = [100, 110, 105]
    # cum_tp_vol = [100*1000, 100*1000+110*2000, 100*1000+110*2000+105*1000]
    #            = [100000, 320000, 425000]
    # cum_vol   = [1000, 3000, 4000]
    # vwap[-1]  = 425000 / 4000 = 106.25
    vwap = calculate_vwap(history)
    assert vwap == 106.25


def test_calculate_vwap_empty():
    """Empty history should return None."""
    assert calculate_vwap(pd.DataFrame()) is None


def test_calculate_vwap_zero_volume():
    """All-zero volume should return None."""
    history = pd.DataFrame({
        "High": [110], "Low": [90], "Close": [100], "Volume": [0],
    })
    assert calculate_vwap(history) is None


def test_calculate_vwap_with_full_history():
    """VWAP from full mock history should be a reasonable price."""
    history = make_mock_history(260, base_price=500.0)
    vwap = calculate_vwap(history)
    assert vwap is not None
    # VWAP should be in the ballpark of the price range
    assert 300 < vwap < 800


# ── Volume Profile tests ──

def test_calculate_volume_profile_basic():
    """POC should be the price level with most volume."""
    np.random.seed(99)
    # Cluster most volume around $100, less at extremes
    closes = np.array([100]*15 + [120]*3 + [80]*2)
    volumes = np.array([10000]*15 + [1000]*3 + [500]*2)
    history = pd.DataFrame({
        "Close": closes,
        "Volume": volumes,
        "High": closes * 1.01,
        "Low": closes * 0.99,
    })
    result = calculate_volume_profile(history, bins=10)
    assert result is not None
    assert "poc" in result
    assert "poc_volume" in result
    assert "high_volume_nodes" in result
    # POC should be near $100 where most volume is
    assert 95 <= result["poc"] <= 105


def test_calculate_volume_profile_too_short():
    """Less than 20 rows should return None."""
    history = pd.DataFrame({
        "Close": [100]*10, "Volume": [1000]*10,
        "High": [101]*10, "Low": [99]*10,
    })
    assert calculate_volume_profile(history) is None


def test_calculate_volume_profile_empty():
    """Empty history should return None."""
    assert calculate_volume_profile(pd.DataFrame()) is None


def test_calculate_volume_profile_hvn_count():
    """High volume nodes should have at most 3 entries."""
    history = make_mock_history(260)
    result = calculate_volume_profile(history)
    assert result is not None
    assert len(result["high_volume_nodes"]) <= 3


# ── VWAP signal in analyze_ticker ──

def test_vwap_signal_present():
    """analyze_ticker should include VWAP signal in its result."""
    history = make_mock_history(260)
    result = analyze_ticker("NVDA", history)
    assert result is not None
    assert result["vwap"] is not None
    assert result["vwap_signal"] in ("above", "below", "at_vwap")


def test_volume_profile_present():
    """analyze_ticker should include volume profile in its result."""
    history = make_mock_history(260)
    result = analyze_ticker("NVDA", history)
    assert result is not None
    assert result["volume_profile"] is not None
    assert "poc" in result["volume_profile"]
