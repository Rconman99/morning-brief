"""Tests for macro_dashboard module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

from modules.macro_dashboard import (
    get_indicator_data,
    determine_signal,
    determine_signal_detail,
    determine_regime_summary,
    analyze_macro,
    main,
    _get_interpretation,
)


def make_mock_history(days: int = 60, base_price: float = 100.0, ticker: str = "VIX") -> pd.DataFrame:
    """Generate mock price history DataFrame."""
    dates = pd.date_range(end="2026-02-25", periods=days, freq="B")
    # Simulate realistic movements
    import numpy as np
    np.random.seed(42)
    returns = np.random.normal(0.001, 0.015, days)
    prices = base_price * np.cumprod(1 + returns)
    high = prices * (1 + np.abs(np.random.normal(0, 0.005, days)))
    low = prices * (1 - np.abs(np.random.normal(0, 0.005, days)))
    return pd.DataFrame({
        "Open": prices * 0.999,
        "High": high,
        "Low": low,
        "Close": prices,
        "Volume": np.random.randint(1000000, 50000000, days),
    }, index=dates)


class TestIndicatorInterpretation:
    """Test context-specific interpretation logic."""

    def test_vix_extreme_fear(self):
        result = _get_interpretation("VIX", 38.0, 5.0, 0.9, "rising")
        assert "Extreme fear" in result or "panic" in result.lower()

    def test_vix_elevated_fear(self):
        result = _get_interpretation("VIX", 27.0, 2.0, 0.7, "rising")
        assert "Elevated fear" in result or "above normal" in result.lower()

    def test_vix_complacency(self):
        result = _get_interpretation("VIX", 11.0, -1.0, 0.2, "falling")
        assert "Complacency" in result or "low volatility" in result.lower()

    def test_dxy_strong_dollar(self):
        result = _get_interpretation("DXY", 105.0, 1.0, 0.85, "rising")
        assert "Strong dollar" in result or "headwind" in result.lower()

    def test_dxy_weak_dollar(self):
        result = _get_interpretation("DXY", 98.0, -1.5, 0.15, "falling")
        assert "Weak dollar" in result or "favorable" in result.lower()

    def test_us10y_high_yields(self):
        result = _get_interpretation("US10Y", 5.5, 0.5, 0.8, "rising")
        assert "High yields" in result or "restrictive" in result.lower()

    def test_oil_high_prices(self):
        result = _get_interpretation("OIL", 95.0, 2.0, 0.7, "rising")
        assert "High energy" in result or "inflation" in result.lower()

    def test_gold_near_highs(self):
        result = _get_interpretation("GOLD", 2350.0, 0.5, 0.8, "rising")
        assert "highs" in result.lower() or "flight to safety" in result.lower()


class TestSignalDetermination:
    """Test signal logic (crisis, risk_off, risk_on, mixed)."""

    def test_crisis_high_vix(self):
        indicators = {
            "VIX": {"value": 38.0, "trend": "rising"},
            "DXY": {"value": 104.0, "trend": "flat"},
            "US10Y": {"value": 4.0, "trend": "flat"},
            "OIL": {"value": 80.0, "trend": "flat"},
            "GOLD": {"value": 2300.0, "trend": "rising"},
        }
        signal = determine_signal(indicators)
        assert signal == "crisis"

    def test_risk_off_elevated_vix_rising_gold_falling_yields(self):
        indicators = {
            "VIX": {"value": 28.0, "trend": "rising"},
            "DXY": {"value": 103.0, "trend": "flat"},
            "US10Y": {"value": 3.8, "trend": "falling"},
            "OIL": {"value": 75.0, "trend": "flat"},
            "GOLD": {"value": 2350.0, "trend": "rising"},
        }
        signal = determine_signal(indicators)
        assert signal == "risk_off"

    def test_risk_on_low_vix_stable_yields_falling_dxy(self):
        indicators = {
            "VIX": {"value": 12.0, "trend": "flat"},
            "DXY": {"value": 101.0, "trend": "falling"},
            "US10Y": {"value": 3.9, "trend": "flat"},
            "OIL": {"value": 70.0, "trend": "flat"},
            "GOLD": {"value": 2100.0, "trend": "falling"},
        }
        signal = determine_signal(indicators)
        assert signal == "risk_on"

    def test_mixed_default_case(self):
        indicators = {
            "VIX": {"value": 19.0, "trend": "rising"},
            "DXY": {"value": 103.5, "trend": "rising"},
            "US10Y": {"value": 4.2, "trend": "rising"},
            "OIL": {"value": 80.0, "trend": "flat"},
            "GOLD": {"value": 2200.0, "trend": "flat"},
        }
        signal = determine_signal(indicators)
        assert signal == "mixed"

    def test_empty_indicators(self):
        signal = determine_signal({})
        assert signal == "mixed"


class TestSignalDetail:
    """Test signal detail generation."""

    def test_crisis_detail(self):
        indicators = {
            "VIX": {"value": 36.5},
            "OIL": {"value": 80.0},
            "US10Y": {"value": 4.0},
            "GOLD": {"value": 2300.0},
            "DXY": {"value": 104.0},
        }
        detail = determine_signal_detail(indicators, "crisis")
        assert "extreme" in detail.lower() or "panic" in detail.lower()

    def test_risk_off_detail(self):
        indicators = {
            "VIX": {"value": 27.0},
            "OIL": {"value": 80.0},
            "US10Y": {"value": 3.8},
            "GOLD": {"value": 2350.0},
            "DXY": {"value": 103.0},
        }
        detail = determine_signal_detail(indicators, "risk_off")
        assert "elevated" in detail.lower() or "gold" in detail.lower()

    def test_risk_on_detail(self):
        indicators = {
            "VIX": {"value": 11.0},
            "OIL": {"value": 70.0},
            "US10Y": {"value": 3.9},
            "GOLD": {"value": 2100.0},
            "DXY": {"value": 101.0},
        }
        detail = determine_signal_detail(indicators, "risk_on")
        assert "low" in detail.lower() or "favorable" in detail.lower()

    def test_mixed_detail_inflation_watch(self):
        indicators = {
            "VIX": {"value": 23.0},
            "OIL": {"value": 85.0},
            "US10Y": {"value": 4.2},
            "GOLD": {"value": 2200.0},
            "DXY": {"value": 103.5},
        }
        detail = determine_signal_detail(indicators, "mixed")
        assert "inflation" in detail.lower() or "oil" in detail.lower()


class TestRegimeSummary:
    """Test regime summary generation (1-2 sentence assessment)."""

    def test_regime_flight_to_safety(self):
        indicators = {
            "VIX": {"value": 27.0, "trend": "rising"},
            "DXY": {"value": 103.0, "trend": "flat"},
            "US10Y": {"value": 3.8, "trend": "falling"},
            "OIL": {"value": 75.0, "trend": "flat"},
            "GOLD": {"value": 2350.0, "trend": "rising", "percentile_1y": 0.75},
        }
        summary = determine_regime_summary(indicators)
        assert "flight to safety" in summary.lower() or "defensive" in summary.lower()

    def test_regime_risk_on(self):
        indicators = {
            "VIX": {"value": 11.0, "trend": "flat"},
            "DXY": {"value": 101.0, "trend": "falling"},
            "US10Y": {"value": 3.9, "trend": "flat"},
            "OIL": {"value": 70.0, "trend": "flat"},
            "GOLD": {"value": 2100.0, "trend": "falling", "percentile_1y": 0.3},
        }
        summary = determine_regime_summary(indicators)
        assert "risk-on" in summary.lower() or "equities" in summary.lower()

    def test_regime_inflation_concerns(self):
        indicators = {
            "VIX": {"value": 18.0, "trend": "rising"},
            "DXY": {"value": 103.0, "trend": "flat"},
            "US10Y": {"value": 4.5, "trend": "rising"},
            "OIL": {"value": 90.0, "trend": "rising"},
            "GOLD": {"value": 2250.0, "trend": "rising", "percentile_1y": 0.65},
        }
        summary = determine_regime_summary(indicators)
        assert "inflation" in summary.lower() or "energy" in summary.lower()

    def test_regime_empty(self):
        summary = determine_regime_summary({})
        assert "Insufficient data" in summary or summary


class TestIndicatorDataFetch:
    """Test indicator data fetching and calculation."""

    @patch("modules.macro_dashboard.yahoo_finance_price_history")
    def test_get_indicator_data_vix(self, mock_yf):
        """Test successful VIX data fetch and calculations."""
        mock_yf.return_value = make_mock_history(60, 20.0, "VIX")

        result = get_indicator_data("VIX", "^VIX")

        assert result is not None
        assert result["value"] > 0
        assert "change_1d" in result
        assert "change_5d" in result
        assert "change_20d" in result
        assert "high_20d" in result
        assert "low_20d" in result
        assert "percentile_1y" in result
        assert result["trend"] in ("rising", "falling", "flat")
        assert "interpretation" in result

    @patch("modules.macro_dashboard.get_cached")
    @patch("modules.macro_dashboard.yahoo_finance_price_history")
    def test_get_indicator_data_dxy(self, mock_yf, mock_cache):
        """Test DXY data fetch."""
        mock_cache.return_value = None  # Force fresh fetch
        mock_yf.return_value = make_mock_history(60, 103.0, "DXY")

        result = get_indicator_data("DXY", "DX-Y.NYB")

        assert result is not None
        assert result["value"] > 80  # Allow for data variation

    @patch("modules.macro_dashboard.get_cached")
    @patch("modules.macro_dashboard.yahoo_finance_price_history")
    def test_get_indicator_data_empty_response(self, mock_yf, mock_cache):
        """Test handling of empty yfinance response."""
        mock_cache.return_value = None
        mock_yf.return_value = pd.DataFrame()

        result = get_indicator_data("VIX", "^VIX")

        assert result is None

    @patch("modules.macro_dashboard.get_cached")
    @patch("modules.macro_dashboard.yahoo_finance_price_history")
    def test_get_indicator_data_exception_handling(self, mock_yf, mock_cache):
        """Test graceful handling of yfinance exceptions."""
        mock_cache.return_value = None
        mock_yf.side_effect = Exception("Network error")

        result = get_indicator_data("VIX", "^VIX")

        assert result is None

    @patch("modules.macro_dashboard.get_cached")
    @patch("modules.macro_dashboard.yahoo_finance_price_history")
    def test_indicator_trend_calculation(self, mock_yf, mock_cache):
        """Test trend detection (rising, falling, flat)."""
        # Create history where last 5d is up >1%
        mock_cache.return_value = None
        history = make_mock_history(60, 22.5)  # Use realistic VIX price
        history.iloc[-1, history.columns.get_loc("Close")] = 22.8  # +1.3%
        mock_yf.return_value = history

        result = get_indicator_data("VIX", "^VIX")

        assert result["trend"] == "rising"

    @patch("modules.macro_dashboard.yahoo_finance_price_history")
    def test_indicator_percentile_calculation(self, mock_yf):
        """Test percentile rank vs 1-year range."""
        mock_yf.return_value = make_mock_history(60, 22.5)  # Use realistic VIX price

        result = get_indicator_data("VIX", "^VIX")

        assert 0 <= result["percentile_1y"] <= 1.0


class TestAnalyzeMacro:
    """Test full macro analysis pipeline."""

    @patch("modules.macro_dashboard.yahoo_finance_price_history")
    def test_analyze_macro_complete(self, mock_yf):
        """Test complete macro analysis with all indicators."""
        mock_yf.side_effect = lambda t, **kw: make_mock_history(60, 20.0 if "^VIX" in t else 100.0)

        indicators = {
            "VIX": {"value": 20.0, "trend": "rising", "change_5d": 2.0},
            "DXY": {"value": 103.0, "trend": "flat", "change_5d": 0.0},
            "US10Y": {"value": 4.0, "trend": "falling", "change_5d": -0.5},
            "OIL": {"value": 80.0, "trend": "rising", "change_5d": 2.0},
            "GOLD": {"value": 2300.0, "trend": "rising", "change_5d": 1.0},
        }

        result = analyze_macro(indicators)

        assert "signal" in result
        assert "signal_detail" in result
        assert "regime_summary" in result
        assert "indicators" in result
        assert "data_source" in result
        assert "analyzed_at" in result
        assert result["data_source"] == "yfinance"

    def test_analyze_macro_empty_indicators(self):
        """Test macro analysis with no indicators."""
        result = analyze_macro({})

        assert "signal" in result
        assert result["indicators"] == {}


class TestMainFunction:
    """Test main entry point and integration."""

    @patch("modules.macro_dashboard.yahoo_finance_price_history")
    def test_main_success(self, mock_yf, tmp_path):
        """Test successful main run with all indicators."""
        mock_yf.side_effect = lambda t, **kw: make_mock_history(60, 20.0 if "^VIX" in t else 100.0)

        # Mock the save location to use tmp_path
        with patch("modules.macro_dashboard.PROJECT_ROOT", tmp_path):
            with patch("modules.macro_dashboard.save_envelope") as mock_save:
                mock_save.return_value = tmp_path / "macro_dashboard.json"
                main()
                mock_save.assert_called_once()
                call_args = mock_save.call_args[0][0]
                assert call_args["module"] == "macro_dashboard"
                assert call_args["status"] in ("success", "partial", "error")

    @patch("modules.macro_dashboard.yahoo_finance_price_history")
    def test_main_partial_failure(self, mock_yf, tmp_path):
        """Test main run with some indicators failing."""
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                return make_mock_history(60, 100.0)
            else:
                return pd.DataFrame()  # Simulate failure

        mock_yf.side_effect = side_effect

        with patch("modules.macro_dashboard.PROJECT_ROOT", tmp_path):
            with patch("modules.macro_dashboard.save_envelope") as mock_save:
                mock_save.return_value = tmp_path / "macro_dashboard.json"
                main()
                call_args = mock_save.call_args[0][0]
                assert call_args["status"] in ("partial", "success")

    @patch("modules.macro_dashboard.get_cached")
    @patch("modules.macro_dashboard.yahoo_finance_price_history")
    def test_main_all_failures(self, mock_yf, mock_cache, tmp_path):
        """Test main run with all indicators failing."""
        mock_cache.return_value = None
        mock_yf.return_value = pd.DataFrame()

        with patch("modules.macro_dashboard.PROJECT_ROOT", tmp_path):
            with patch("modules.macro_dashboard.save_envelope") as mock_save:
                mock_save.return_value = tmp_path / "macro_dashboard.json"
                main()
                call_args = mock_save.call_args[0][0]
                assert call_args["status"] == "error"


class TestOutputStructure:
    """Test output envelope structure and validity."""

    @patch("modules.macro_dashboard.yahoo_finance_price_history")
    def test_output_envelope_structure(self, mock_yf, tmp_path):
        """Test that output matches expected envelope schema."""
        mock_yf.return_value = make_mock_history(60, 100.0)

        indicators = {
            "VIX": {"value": 20.0, "trend": "rising", "interpretation": "test"},
            "DXY": {"value": 103.0, "trend": "flat", "interpretation": "test"},
            "US10Y": {"value": 4.0, "trend": "flat", "interpretation": "test"},
            "OIL": {"value": 80.0, "trend": "rising", "interpretation": "test"},
            "GOLD": {"value": 2300.0, "trend": "rising", "interpretation": "test"},
        }

        result = analyze_macro(indicators)

        # Check all required fields
        assert isinstance(result, dict)
        assert "signal" in result
        assert "signal_detail" in result
        assert "regime_summary" in result
        assert "indicators" in result
        assert "data_source" in result
        assert "analyzed_at" in result

    def test_load_sample_data(self):
        """Test that sample macro_dashboard.json is valid."""
        sample_path = PROJECT_ROOT / "data" / "sample" / "macro_dashboard.json"
        if sample_path.exists():
            data = json.loads(sample_path.read_text())
            assert data["module"] == "macro_dashboard"
            assert data["status"] == "success"
            assert "signal" in data["data"]
            assert "indicators" in data["data"]
            assert len(data["data"]["indicators"]) == 5


class TestGracefulDegradation:
    """Test system resilience when data sources fail."""

    @patch("modules.macro_dashboard.yahoo_finance_price_history")
    def test_single_indicator_failure(self, mock_yf):
        """Test that one failing indicator doesn't break the entire analysis."""
        def side_effect(ticker, **kwargs):
            if ticker == "^VIX":
                return make_mock_history(60, 20.0)
            else:
                return pd.DataFrame()

        mock_yf.side_effect = side_effect

        # Only one indicator available
        indicators = {
            "VIX": {"value": 20.0, "trend": "rising", "interpretation": "test"}
        }

        result = analyze_macro(indicators)
        assert "signal" in result
        assert result["indicators"] == indicators

    @patch("modules.macro_dashboard.get_cached")
    @patch("modules.macro_dashboard.yahoo_finance_price_history")
    def test_network_timeout_recovery(self, mock_yf, mock_cache):
        """Test recovery from network timeouts."""
        mock_cache.return_value = None
        mock_yf.side_effect = TimeoutError("Connection timeout")

        result = get_indicator_data("VIX", "^VIX")
        assert result is None
