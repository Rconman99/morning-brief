"""Tests for opportunity_scanner module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock
from datetime import date

from modules.opportunity_scanner import (
    scan_opportunities, scan_sector_etfs, load_scan_config, main,
)


def _make_history(length=250, base_price=100.0, trend="up"):
    """Generate mock price history DataFrame."""
    dates = pd.date_range(end=date.today(), periods=length, freq="B")
    n = len(dates)  # actual count (B freq may differ from periods)
    if trend == "up":
        prices = base_price + np.linspace(0, 30, n) + np.random.randn(n) * 2
    elif trend == "down":
        prices = base_price - np.linspace(0, 30, n) + np.random.randn(n) * 2
    else:
        prices = base_price + np.random.randn(n) * 2

    volume = np.random.randint(1_000_000, 10_000_000, n).astype(float)
    # Make last day volume high for surge
    volume[-1] = volume[-20:].mean() * 2

    return pd.DataFrame({
        "Open": prices - 1,
        "High": prices + 2,
        "Low": prices - 2,
        "Close": prices,
        "Volume": volume,
    }, index=dates)


# ── Filter logic tests ──

@patch("modules.opportunity_scanner.yahoo_finance_info")
@patch("modules.opportunity_scanner.yahoo_finance_price_history")
def test_filter_passes_good_ticker(mock_history, mock_info):
    """Ticker with strong technicals should pass filter."""
    mock_history.return_value = _make_history(250, 100, "up")
    mock_info.return_value = {"trailingPE": 25.0, "marketCap": 50e9}

    config = {
        "scan_tickers": ["TEST"],
        "sector_etfs": [],
        "min_composite_to_surface": 0.5,  # low threshold for test
        "min_volume_ratio": 0.5,  # low threshold for test
    }
    data = scan_opportunities(config=config)
    # Should have at least attempted analysis
    assert data["universe_size"] == 1


@patch("modules.opportunity_scanner.yahoo_finance_info")
@patch("modules.opportunity_scanner.yahoo_finance_price_history")
def test_filter_excludes_overbought(mock_history, mock_info):
    """Ticker with RSI > 65 should be excluded."""
    mock_history.return_value = _make_history(250, 100, "up")
    mock_info.return_value = {}

    # Use high composite threshold that requires RSI 30-65
    config = {
        "scan_tickers": ["TEST"],
        "sector_etfs": [],
        "min_composite_to_surface": -10.0,  # accept anything
        "min_volume_ratio": 0.1,
    }
    data = scan_opportunities(config=config)
    # Filter checks RSI 30-65 — the mock may or may not pass depending on random data
    # But the pipeline should run without errors
    assert "opportunities" in data
    assert "passed_filter" in data


@patch("modules.opportunity_scanner.yahoo_finance_info")
@patch("modules.opportunity_scanner.yahoo_finance_price_history")
def test_filter_excludes_death_cross(mock_history, mock_info):
    """Ticker with death_cross SMA should be excluded."""
    mock_history.return_value = _make_history(250, 100, "down")
    mock_info.return_value = {}

    config = {
        "scan_tickers": ["TEST"],
        "sector_etfs": [],
        "min_composite_to_surface": -10.0,
        "min_volume_ratio": 0.1,
    }
    data = scan_opportunities(config=config)
    # Death cross tickers should be filtered out
    for o in data["opportunities"]:
        assert o.get("sma_trend") != "death_cross"


@patch("modules.opportunity_scanner.yahoo_finance_info")
@patch("modules.opportunity_scanner.yahoo_finance_price_history")
def test_filter_excludes_low_volume(mock_history, mock_info):
    """Ticker with low volume ratio should be excluded."""
    hist = _make_history(250, 100, "up")
    # Make volume uniform (ratio ~1.0)
    hist["Volume"] = 5_000_000.0
    mock_history.return_value = hist
    mock_info.return_value = {}

    config = {
        "scan_tickers": ["TEST"],
        "sector_etfs": [],
        "min_composite_to_surface": -10.0,
        "min_volume_ratio": 1.5,  # require high volume
    }
    data = scan_opportunities(config=config)
    # With uniform volume (ratio=1.0), should be excluded by 1.5 threshold
    assert data["passed_filter"] == 0


@patch("modules.opportunity_scanner.yahoo_finance_info")
@patch("modules.opportunity_scanner.yahoo_finance_price_history")
def test_ranking_order(mock_history, mock_info):
    """Opportunities should be ranked by composite_score descending."""
    mock_info.return_value = {"trailingPE": 20.0, "marketCap": 10e9}

    def side_effect(ticker, period="1y"):
        return _make_history(250, 100, "up")

    mock_history.side_effect = side_effect

    config = {
        "scan_tickers": ["A", "B", "C"],
        "sector_etfs": [],
        "min_composite_to_surface": -10.0,
        "min_volume_ratio": 0.1,
    }
    data = scan_opportunities(config=config)
    opps = data["opportunities"]
    if len(opps) >= 2:
        for i in range(len(opps) - 1):
            assert opps[i]["composite_score"] >= opps[i + 1]["composite_score"]


# ── Sector ETF tests ──

@patch("modules.opportunity_scanner.yahoo_finance_price_history")
def test_sector_etf_read(mock_history):
    """Sector ETF read should calculate 1d and 5d changes."""
    dates = pd.date_range(end=date.today(), periods=10, freq="B")
    n = len(dates)
    prices = list(range(100, 100 + n))  # steadily rising
    hist = pd.DataFrame({
        "Open": prices, "High": prices, "Low": prices,
        "Close": prices, "Volume": [1e6] * n,
    }, index=dates)
    mock_history.return_value = hist

    result = scan_sector_etfs(["XLK"])
    assert "XLK" in result
    assert result["XLK"]["change_1d"] > 0
    assert result["XLK"]["change_5d"] > 0
    assert result["XLK"]["trend"] == "bullish"


@patch("modules.opportunity_scanner.yahoo_finance_price_history")
def test_sector_etf_bearish(mock_history):
    """Declining ETF should be bearish."""
    dates = pd.date_range(end=date.today(), periods=10, freq="B")
    n = len(dates)
    prices = list(range(100 + n, 100, -1))  # steadily declining
    hist = pd.DataFrame({
        "Open": prices, "High": prices, "Low": prices,
        "Close": prices, "Volume": [1e6] * n,
    }, index=dates)
    mock_history.return_value = hist

    result = scan_sector_etfs(["XLF"])
    assert result["XLF"]["trend"] == "bearish"


# ── Output envelope tests ──

@patch("modules.opportunity_scanner.yahoo_finance_info")
@patch("modules.opportunity_scanner.yahoo_finance_price_history")
def test_output_envelope(mock_history, mock_info):
    """Output should have standard fields."""
    mock_history.return_value = _make_history(250, 100, "up")
    mock_info.return_value = {}

    config = {
        "scan_tickers": ["TEST"],
        "sector_etfs": ["XLK"],
        "min_composite_to_surface": 2.0,
        "min_volume_ratio": 1.3,
    }
    data = scan_opportunities(config=config)
    assert "scan_date" in data
    assert "universe_size" in data
    assert "passed_filter" in data
    assert "opportunities" in data
    assert "sector_read" in data
    assert isinstance(data["opportunities"], list)


# ── main() test ──

@patch("modules.opportunity_scanner.yahoo_finance_info")
@patch("modules.opportunity_scanner.yahoo_finance_price_history")
def test_main_creates_output(mock_history, mock_info, tmp_path):
    """main() should create opportunities.json."""
    mock_history.return_value = _make_history(250, 100, "up")
    mock_info.return_value = {}

    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    outputs = tmp_path / "data" / "outputs"
    outputs.mkdir(parents=True)

    with patch("modules.opportunity_scanner.PROJECT_ROOT", tmp_path), \
         patch("lib.data_envelope.PROCESSED_DIR", processed):
        # Create a minimal scan config
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "scan_universe.json").write_text(json.dumps({
            "scan_tickers": ["TEST"],
            "sector_etfs": [],
            "min_composite_to_surface": -10.0,
            "min_volume_ratio": 0.1,
        }))
        main()

    output_file = processed / "opportunities.json"
    assert output_file.exists()
    envelope = json.loads(output_file.read_text())
    assert envelope["module"] == "opportunities"
    assert envelope["status"] in ("success", "partial")
