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

from modules.technical_signals import analyze_ticker, analyze_technical_signals, main


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
    assert len(result["indicators"]) == 5


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
