"""Tests for unusual_options module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from modules.unusual_options import detect_unusual_activity, main


def make_mock_calls_df():
    """Create mock calls DataFrame with some high vol/oi ratios."""
    return pd.DataFrame({
        "strike": [100, 105, 110, 115, 120],
        "volume": [15000, 1000, 800, 500, 200],
        "openInterest": [3000, 2000, 5000, 100, 1500],
        "lastPrice": [30.0, 25.0, 20.0, 15.0, 10.0],
        "impliedVolatility": [0.45, 0.42, 0.40, 0.38, 0.35],
    })


def make_mock_puts_df():
    """Create mock puts DataFrame with some high vol/oi ratios."""
    return pd.DataFrame({
        "strike": [100, 95, 90, 85, 80],
        "volume": [2000, 3500, 1000, 600, 300],
        "openInterest": [5000, 1000, 2000, 500, 1200],
        "lastPrice": [2.0, 5.0, 8.0, 12.0, 15.0],
        "impliedVolatility": [0.42, 0.45, 0.48, 0.50, 0.52],
    })


def test_detect_unusual_activity_with_signals():
    """Test detecting unusual options activity."""
    mock_ticker = MagicMock()
    mock_ticker.options = ("2026-03-20", "2026-04-17", "2026-06-19")

    chain_mock = MagicMock()
    chain_mock.calls = make_mock_calls_df()
    chain_mock.puts = make_mock_puts_df()
    mock_ticker.option_chain.return_value = chain_mock

    with patch("yfinance.Ticker", return_value=mock_ticker):
        result = detect_unusual_activity("TEST", 100.0)

    assert result is not None
    assert result["ticker"] == "TEST"
    assert result["call_sweep_count"] >= 1
    assert result["put_sweep_count"] >= 1
    assert result["net_sentiment"] in ["bullish", "bearish", "mixed"]
    assert result["largest_trade"] is not None
    assert result["largest_trade"]["notional"] > 0
    assert len(result["unusual_strikes"]) <= 10


def test_detect_unusual_activity_filters_low_volume():
    """Test that low volume strikes are filtered out."""
    # Create a DataFrame with mostly low vol/oi ratio and volume
    calls_df = pd.DataFrame({
        "strike": [100, 105],
        "volume": [100, 50],  # Both below 500 threshold
        "openInterest": [1000, 2000],
        "lastPrice": [10.0, 8.0],
        "impliedVolatility": [0.40, 0.38],
    })
    puts_df = pd.DataFrame({
        "strike": [100, 95],
        "volume": [100, 50],
        "openInterest": [1000, 2000],
        "lastPrice": [2.0, 5.0],
        "impliedVolatility": [0.40, 0.42],
    })

    mock_ticker = MagicMock()
    mock_ticker.options = ("2026-03-20",)

    chain_mock = MagicMock()
    chain_mock.calls = calls_df
    chain_mock.puts = puts_df
    mock_ticker.option_chain.return_value = chain_mock

    with patch("yfinance.Ticker", return_value=mock_ticker):
        result = detect_unusual_activity("TEST", 100.0)

    # Should return None since no strikes meet the unusual criteria
    assert result is None


def test_detect_unusual_activity_no_options():
    """Test handling when ticker has no options."""
    mock_ticker = MagicMock()
    mock_ticker.options = []

    with patch("yfinance.Ticker", return_value=mock_ticker):
        result = detect_unusual_activity("VOO", 400.0)

    assert result is None


def test_net_sentiment_bullish():
    """Test net sentiment calculation for bullish case."""
    calls_df = pd.DataFrame({
        "strike": [100, 105, 110],
        "volume": [10000, 8000, 6000],
        "openInterest": [1000, 1000, 1000],
        "lastPrice": [20.0, 15.0, 10.0],
        "impliedVolatility": [0.40, 0.40, 0.40],
    })
    puts_df = pd.DataFrame({
        "strike": [95, 90],
        "volume": [500, 200],  # Below volume threshold
        "openInterest": [5000, 5000],
        "lastPrice": [5.0, 10.0],
        "impliedVolatility": [0.40, 0.40],
    })

    mock_ticker = MagicMock()
    mock_ticker.options = ("2026-03-20",)

    chain_mock = MagicMock()
    chain_mock.calls = calls_df
    chain_mock.puts = puts_df
    mock_ticker.option_chain.return_value = chain_mock

    with patch("yfinance.Ticker", return_value=mock_ticker):
        result = detect_unusual_activity("TEST", 100.0)

    assert result is not None
    assert result["call_sweep_count"] >= 3  # All calls should be unusual
    assert result["put_sweep_count"] == 0  # Puts don't meet volume threshold
    assert result["net_sentiment"] == "bullish"


def test_net_sentiment_bearish():
    """Test net sentiment calculation for bearish case."""
    calls_df = pd.DataFrame({
        "strike": [100, 105],
        "volume": [500, 200],  # Below volume threshold
        "openInterest": [5000, 5000],
        "lastPrice": [20.0, 15.0],
        "impliedVolatility": [0.40, 0.40],
    })
    puts_df = pd.DataFrame({
        "strike": [95, 90, 85],
        "volume": [10000, 8000, 6000],
        "openInterest": [1000, 1000, 1000],
        "lastPrice": [5.0, 10.0, 15.0],
        "impliedVolatility": [0.40, 0.40, 0.40],
    })

    mock_ticker = MagicMock()
    mock_ticker.options = ("2026-03-20",)

    chain_mock = MagicMock()
    chain_mock.calls = calls_df
    chain_mock.puts = puts_df
    mock_ticker.option_chain.return_value = chain_mock

    with patch("yfinance.Ticker", return_value=mock_ticker):
        result = detect_unusual_activity("TEST", 100.0)

    assert result is not None
    assert result["call_sweep_count"] == 0  # Calls don't meet volume threshold
    assert result["put_sweep_count"] >= 3  # All puts should be unusual
    assert result["net_sentiment"] == "bearish"


def test_largest_trade_calculation():
    """Test that largest_trade is correctly identified by notional value."""
    calls_df = pd.DataFrame({
        "strike": [100, 105],
        "volume": [1000, 500],
        "openInterest": [100, 100],
        "lastPrice": [50.0, 10.0],  # First: 1000*50=50000, Second: 500*10=5000
        "impliedVolatility": [0.40, 0.40],
    })
    puts_df = pd.DataFrame(columns=["strike", "volume", "openInterest", "lastPrice", "impliedVolatility"])

    mock_ticker = MagicMock()
    mock_ticker.options = ("2026-03-20",)

    chain_mock = MagicMock()
    chain_mock.calls = calls_df
    chain_mock.puts = puts_df
    mock_ticker.option_chain.return_value = chain_mock

    with patch("yfinance.Ticker", return_value=mock_ticker):
        result = detect_unusual_activity("TEST", 100.0)

    assert result is not None
    assert result["largest_trade"] is not None
    assert result["largest_trade"]["strike"] == 100.0
    assert result["largest_trade"]["notional"] == 50000


@patch("modules.unusual_options.yahoo_finance_price_history")
@patch("modules.unusual_options.yahoo_finance_info")
def test_main_creates_output(mock_info, mock_hist, tmp_path):
    """Test that main() creates a valid output envelope."""
    # Mock price history
    dates = pd.date_range(end="2026-02-25", periods=5, freq="B")
    mock_hist.return_value = pd.DataFrame({
        "Close": [100.0, 101.0, 102.0, 103.0, 104.0],
    }, index=dates)

    # Create config files
    config = tmp_path / "config"
    config.mkdir()
    (config / "watchlist.json").write_text(json.dumps({
        "tickers": ["TEST1", "TEST2"],
        "comparison_pairs": [],
        "earnings_watch": [],
    }))
    (config / "portfolio.json").write_text(json.dumps({
        "holdings": [{"ticker": "TEST1", "shares": 50, "cost_basis": 90.0}],
        "cash": 10000,
        "options_positions": [],
    }))

    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    outputs = tmp_path / "data" / "outputs"
    outputs.mkdir(parents=True)

    # Mock yfinance
    mock_ticker = MagicMock()
    mock_ticker.options = ("2026-03-20",)

    chain_mock = MagicMock()
    chain_mock.calls = make_mock_calls_df()
    chain_mock.puts = make_mock_puts_df()
    mock_ticker.option_chain.return_value = chain_mock

    with patch("modules.unusual_options.PROJECT_ROOT", tmp_path), \
         patch("lib.data_envelope.PROJECT_ROOT", tmp_path), \
         patch("lib.data_envelope.PROCESSED_DIR", processed), \
         patch("yfinance.Ticker", return_value=mock_ticker):
        main()

    output_file = processed / "unusual_options.json"
    assert output_file.exists()

    envelope = json.loads(output_file.read_text())
    assert envelope["module"] == "unusual_options"
    assert envelope["status"] in ("success", "partial")
    assert "data" in envelope
    assert "results" in envelope["data"]
    assert "scan_summary" in envelope["data"]

    # Validate scan_summary structure
    summary = envelope["data"]["scan_summary"]
    assert "tickers_scanned" in summary
    assert "tickers_with_unusual" in summary
    assert "total_unusual_strikes" in summary


@patch("modules.unusual_options.yahoo_finance_price_history")
@patch("modules.unusual_options.yahoo_finance_info")
def test_main_handles_missing_configs(mock_info, mock_hist, tmp_path):
    """Test that main() handles missing config files gracefully."""
    config = tmp_path / "config"
    config.mkdir()

    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    outputs = tmp_path / "data" / "outputs"
    outputs.mkdir(parents=True)

    with patch("modules.unusual_options.PROJECT_ROOT", tmp_path), \
         patch("lib.data_envelope.PROJECT_ROOT", tmp_path), \
         patch("lib.data_envelope.PROCESSED_DIR", processed):
        main()

    output_file = processed / "unusual_options.json"
    assert output_file.exists()

    envelope = json.loads(output_file.read_text())
    assert envelope["module"] == "unusual_options"
    assert envelope["status"] == "error"
    assert envelope["error_message"] is not None
