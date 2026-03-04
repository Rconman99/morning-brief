"""Tests for sector rotation module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd

from modules.sector_rotation import (
    fetch_price_history,
    calculate_returns,
    calculate_trend,
    calculate_volume_ratio,
    analyze_sectors,
    get_leaders_laggards,
    detect_rotation_type,
    calculate_breadth,
    determine_signal,
    main,
    SECTOR_ETFS,
    DEFENSIVE_SECTORS,
    GROWTH_SECTORS,
)


@pytest.fixture
def sample_price_data():
    """Create sample OHLCV DataFrame."""
    dates = pd.date_range("2025-09-04", periods=120, freq="D")
    data = {
        "Open": [100 + i * 0.5 for i in range(120)],
        "High": [102 + i * 0.5 for i in range(120)],
        "Low": [98 + i * 0.5 for i in range(120)],
        "Close": [101 + i * 0.5 for i in range(120)],
        "Volume": [1000000 + i * 1000 for i in range(120)],
        "Adj Close": [101 + i * 0.5 for i in range(120)],
    }
    df = pd.DataFrame(data, index=dates)
    return df


@pytest.fixture
def mock_yfinance_download(sample_price_data):
    """Mock yfinance.download to return sample data."""
    def download_mock(ticker, period=None, auto_adjust=None, progress=None):
        # Add slight variation per ticker for realistic returns
        data = sample_price_data.copy()
        if ticker == "SPY":
            # SPY returns: 2.5% over period
            data["Close"] = data["Close"] * 1.025
            data["Adj Close"] = data["Adj Close"] * 1.025
        elif ticker == "XLK":
            # Tech leading: +5% over period
            data["Close"] = data["Close"] * 1.05
            data["Adj Close"] = data["Adj Close"] * 1.05
        elif ticker == "XLE":
            # Energy lagging: -2% over period
            data["Close"] = data["Close"] * 0.98
            data["Adj Close"] = data["Adj Close"] * 0.98
        return data

    return download_mock


# --- Tests for individual functions ---

def test_calculate_returns(sample_price_data):
    """Test return calculation over various periods."""
    result = calculate_returns(sample_price_data)
    assert result is not None
    assert "return_1d" in result
    assert "return_5d" in result
    assert "return_21d" in result
    assert "return_63d" in result
    assert all(isinstance(v, (int, float, type(None))) for v in result.values())


def test_calculate_returns_empty():
    """Test return calculation with empty DataFrame."""
    result = calculate_returns(None)
    assert result is None

    result = calculate_returns(pd.DataFrame())
    assert result is None


def test_calculate_trend_uptrend(sample_price_data):
    """Test trend calculation identifies uptrend."""
    trend = calculate_trend(sample_price_data)
    assert trend == "uptrend"  # Close values increase monotonically


def test_calculate_trend_downtrend():
    """Test trend calculation identifies downtrend."""
    dates = pd.date_range("2025-09-04", periods=100, freq="D")
    # Decreasing prices for downtrend
    data = {
        "Close": [100 - i * 0.5 for i in range(100)],
        "Volume": [1000000 for _ in range(100)],
    }
    df = pd.DataFrame(data, index=dates)
    trend = calculate_trend(df)
    assert trend == "downtrend"


def test_calculate_trend_insufficient_data():
    """Test trend with insufficient data."""
    dates = pd.date_range("2025-09-04", periods=5, freq="D")
    data = {"Close": [100, 101, 102, 103, 104], "Volume": [1000000] * 5}
    df = pd.DataFrame(data, index=dates)
    trend = calculate_trend(df)
    assert trend == "unknown"


def test_calculate_volume_ratio(sample_price_data):
    """Test volume ratio calculation."""
    ratio = calculate_volume_ratio(sample_price_data)
    assert ratio is not None
    assert isinstance(ratio, float)
    assert ratio > 0


def test_calculate_volume_ratio_insufficient_data():
    """Test volume ratio with insufficient data."""
    dates = pd.date_range("2025-09-04", periods=5, freq="D")
    data = {"Volume": [1000000] * 5}
    df = pd.DataFrame(data, index=dates)
    ratio = calculate_volume_ratio(df)
    assert ratio is None


def test_get_leaders_laggards():
    """Test leader/laggard detection."""
    all_sectors = {
        "XLK": {
            "sector": "Technology",
            "relative_strength_21d": 3.0,
            "return_21d": 5.0,
            "trend": "uptrend",
        },
        "XLF": {
            "sector": "Financials",
            "relative_strength_21d": 2.0,
            "return_21d": 3.0,
            "trend": "uptrend",
        },
        "XLE": {
            "sector": "Energy",
            "relative_strength_21d": 1.0,
            "return_21d": 2.0,
            "trend": "uptrend",
        },
        "XLV": {
            "sector": "Health Care",
            "relative_strength_21d": 0.5,
            "return_21d": 1.0,
            "trend": "downtrend",
        },
        "XLI": {
            "sector": "Industrials",
            "relative_strength_21d": -0.5,
            "return_21d": 0.0,
            "trend": "downtrend",
        },
        "XLY": {
            "sector": "Consumer Discretionary",
            "relative_strength_21d": -1.0,
            "return_21d": -2.0,
            "trend": "downtrend",
        },
    }

    leaders, laggards = get_leaders_laggards(all_sectors, "21d")
    assert len(leaders) == 3
    assert len(laggards) == 3
    assert leaders[0]["etf"] == "XLK"  # Highest relative strength
    # Laggards are sorted ascending (lowest are last), so we check the last one
    assert laggards[-1]["etf"] == "XLY"  # Lowest relative strength


def test_get_leaders_laggards_no_data():
    """Test leader/laggard with missing data."""
    all_sectors = {
        "XLK": {"relative_strength_21d": None},
        "XLF": {"relative_strength_21d": None},
    }
    leaders, laggards = get_leaders_laggards(all_sectors, "21d")
    assert leaders == []
    assert laggards == []


def test_detect_rotation_type_growth():
    """Test growth rotation detection."""
    leaders_5d = [
        {"etf": "XLK", "sector": "Technology"},
        {"etf": "XLY", "sector": "Consumer Discretionary"},
        {"etf": "XLC", "sector": "Communication Services"},
    ]
    leaders_21d = [
        {"etf": "XLU", "sector": "Utilities"},
        {"etf": "XLP", "sector": "Consumer Staples"},
        {"etf": "XLV", "sector": "Health Care"},
    ]
    rotation = detect_rotation_type(leaders_5d, leaders_21d)
    assert rotation == "growth_rotation"


def test_detect_rotation_type_defensive():
    """Test defensive rotation detection."""
    leaders_5d = [
        {"etf": "XLU", "sector": "Utilities"},
        {"etf": "XLP", "sector": "Consumer Staples"},
        {"etf": "XLV", "sector": "Health Care"},
    ]
    leaders_21d = [
        {"etf": "XLK", "sector": "Technology"},
        {"etf": "XLY", "sector": "Consumer Discretionary"},
        {"etf": "XLC", "sector": "Communication Services"},
    ]
    rotation = detect_rotation_type(leaders_5d, leaders_21d)
    assert rotation == "defensive_rotation"


def test_detect_rotation_type_stable():
    """Test stable rotation (no change)."""
    leaders_5d = [
        {"etf": "XLK", "sector": "Technology"},
        {"etf": "XLF", "sector": "Financials"},
        {"etf": "XLI", "sector": "Industrials"},
    ]
    leaders_21d = [
        {"etf": "XLK", "sector": "Technology"},
        {"etf": "XLF", "sector": "Financials"},
        {"etf": "XLI", "sector": "Industrials"},
    ]
    rotation = detect_rotation_type(leaders_5d, leaders_21d)
    assert rotation == "stable"


def test_calculate_breadth():
    """Test breadth calculation."""
    all_sectors = {
        "XLK": {"return_5d": 2.0},  # positive
        "XLF": {"return_5d": 1.0},  # positive
        "XLE": {"return_5d": -1.0},  # negative
        "XLV": {"return_5d": 0.5},  # positive
        "XLI": {"return_5d": 1.5},  # positive
        "XLY": {"return_5d": 2.5},  # positive
        "XLP": {"return_5d": -0.5},  # negative
        "XLU": {"return_5d": -1.5},  # negative
        "XLB": {"return_5d": 0.1},  # positive
        "XLRE": {"return_5d": 0.2},  # positive
        "XLC": {"return_5d": 1.8},  # positive
    }
    breadth = calculate_breadth(all_sectors)
    assert breadth["positive_sectors"] == 8
    assert breadth["negative_sectors"] == 3
    assert breadth["label"] == "broad_rally"


def test_calculate_breadth_selloff():
    """Test breadth shows selloff."""
    all_sectors = {
        "XLK": {"return_5d": -2.0},
        "XLF": {"return_5d": -1.0},
        "XLE": {"return_5d": -1.5},
        "XLV": {"return_5d": 0.5},
        "XLI": {"return_5d": -0.5},
        "XLY": {"return_5d": -2.5},
        "XLP": {"return_5d": -1.2},
        "XLU": {"return_5d": 0.1},
        "XLB": {"return_5d": -1.8},
        "XLRE": {"return_5d": -0.8},
        "XLC": {"return_5d": 0.2},
    }
    breadth = calculate_breadth(all_sectors)
    assert breadth["positive_sectors"] <= 3
    assert breadth["label"] == "broad_selloff"


def test_determine_signal_growth_rotation():
    """Test signal determination for growth rotation."""
    all_sectors = {}
    signal, detail = determine_signal(all_sectors, "growth_rotation", {"label": "mixed"})
    assert signal == "growth_momentum"
    assert "growth" in detail.lower() or "risk-on" in detail.lower()


def test_determine_signal_defensive_rotation():
    """Test signal determination for defensive rotation."""
    all_sectors = {}
    signal, detail = determine_signal(all_sectors, "defensive_rotation", {"label": "mixed"})
    assert signal == "rotation_alert"
    assert "defensive" in detail.lower() or "risk-off" in detail.lower()


def test_determine_signal_broad_rally():
    """Test signal determination for broad rally."""
    all_sectors = {}
    breadth = {"positive_sectors": 9, "total_sectors": 11, "label": "broad_rally"}
    signal, detail = determine_signal(all_sectors, "stable", breadth)
    assert signal == "broad_rally"
    assert "9" in detail


def test_determine_signal_broad_selloff():
    """Test signal determination for broad selloff."""
    all_sectors = {}
    breadth = {"positive_sectors": 2, "total_sectors": 11, "label": "broad_selloff"}
    signal, detail = determine_signal(all_sectors, "stable", breadth)
    assert signal == "broad_selloff"
    assert "2" in detail


# --- Integration tests ---

@patch("modules.sector_rotation.get_cached", return_value=None)
@patch("modules.sector_rotation.yf.download")
def test_fetch_price_history(mock_download, mock_cache, sample_price_data):
    """Test price history fetch."""
    mock_download.return_value = sample_price_data
    result = fetch_price_history("XLK", period="6mo")
    assert result is not None
    assert len(result) > 0
    mock_download.assert_called_once()


@patch("modules.sector_rotation.get_cached", return_value=None)
@patch("modules.sector_rotation.yf.download", side_effect=Exception("API error"))
def test_fetch_price_history_error(mock_download, mock_cache):
    """Test price history fetch with error."""
    result = fetch_price_history("XLK", period="6mo")
    assert result is None


@patch("modules.sector_rotation.fetch_price_history")
def test_analyze_sectors(mock_fetch, sample_price_data):
    """Test full sector analysis."""
    mock_fetch.return_value = sample_price_data

    result = analyze_sectors()
    assert result is not None
    assert "all_sectors" in result
    assert "spy_returns" in result
    assert len(result["all_sectors"]) <= len(SECTOR_ETFS)


@patch("modules.sector_rotation.fetch_price_history")
def test_analyze_sectors_partial_failure(mock_fetch, sample_price_data):
    """Test sector analysis handles some failures gracefully."""
    call_count = [0]

    def fetch_with_failure(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] % 3 == 0:  # Fail every 3rd call
            return None
        return sample_price_data

    mock_fetch.side_effect = fetch_with_failure
    result = analyze_sectors()
    assert result is not None
    assert "all_sectors" in result
    # Should have fewer sectors due to some failures
    assert len(result["all_sectors"]) < len(SECTOR_ETFS)


def test_main_creates_output(tmp_path):
    """Test main() creates output file."""
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    outputs = tmp_path / "data" / "outputs"
    outputs.mkdir(parents=True)

    # Mock yfinance and analyze_sectors
    sample_data = {
        "all_sectors": {
            "XLK": {
                "sector": "Technology",
                "price": 220.5,
                "return_1d": 0.3,
                "return_5d": 2.1,
                "return_21d": 5.2,
                "return_63d": 12.8,
                "relative_strength_1d": 0.2,
                "relative_strength_5d": 2.3,
                "relative_strength_21d": 3.1,
                "relative_strength_63d": 7.5,
                "trend": "uptrend",
                "volume_ratio": 1.15,
            },
            "XLE": {
                "sector": "Energy",
                "price": 65.2,
                "return_1d": -0.4,
                "return_5d": -1.2,
                "return_21d": -2.3,
                "return_63d": -5.1,
                "relative_strength_1d": -0.5,
                "relative_strength_5d": -1.4,
                "relative_strength_21d": -4.4,
                "relative_strength_63d": -10.8,
                "trend": "downtrend",
                "volume_ratio": 1.08,
            },
        },
        "spy_returns": {
            "return_1d": 0.1,
            "return_5d": -0.2,
            "return_21d": 2.1,
            "return_63d": 5.3,
        },
    }

    with patch("modules.sector_rotation.PROJECT_ROOT", tmp_path), \
         patch("modules.sector_rotation.analyze_sectors", return_value=sample_data), \
         patch("lib.data_envelope.PROCESSED_DIR", processed):
        main()

    output_file = processed / "sector_rotation.json"
    assert output_file.exists()
    envelope = json.loads(output_file.read_text())
    assert envelope["module"] == "sector_rotation"
    assert envelope["status"] == "success"
    assert "signal" in envelope["data"]
    assert "leaders_21d" in envelope["data"]
    assert "breadth" in envelope["data"]


def test_main_no_dependencies(tmp_path):
    """Test main() when dependencies are missing."""
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    outputs = tmp_path / "data" / "outputs"
    outputs.mkdir(parents=True)

    with patch("modules.sector_rotation.PROJECT_ROOT", tmp_path), \
         patch("modules.sector_rotation.HAS_PANDAS", False), \
         patch("lib.data_envelope.PROCESSED_DIR", processed):
        main()

    output_file = processed / "sector_rotation.json"
    assert output_file.exists()
    envelope = json.loads(output_file.read_text())
    assert envelope["status"] == "error"
    assert envelope["error_message"] is not None


def test_main_analyze_fails(tmp_path):
    """Test main() when analysis fails."""
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    outputs = tmp_path / "data" / "outputs"
    outputs.mkdir(parents=True)

    with patch("modules.sector_rotation.PROJECT_ROOT", tmp_path), \
         patch("modules.sector_rotation.analyze_sectors", return_value=None), \
         patch("lib.data_envelope.PROCESSED_DIR", processed):
        main()

    output_file = processed / "sector_rotation.json"
    assert output_file.exists()
    envelope = json.loads(output_file.read_text())
    assert envelope["status"] == "error"
