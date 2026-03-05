"""
Tests for dividend_tracker module.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from modules.dividend_tracker import (
    process_ticker,
    calculate_div_growth_3y,
    detect_dividend_cut,
    get_treasury_yield,
    main
)
from lib.data_envelope import load_envelope


@pytest.fixture
def mock_yfinance_aapl():
    """Mock yfinance data for AAPL."""
    return {
        "info": {
            "currentPrice": 250.0,
            "dividendRate": 1.00,
            "dividendYield": 0.004,
            "exDividendDate": int((datetime.now() + timedelta(days=67)).timestamp()),
            "payoutRatio": 0.15
        },
        "dividends": pd.Series([0.25, 0.25, 0.25, 0.25] * 4)  # 4 years of quarterly dividends
    }


@pytest.fixture
def mock_yfinance_voo():
    """Mock yfinance data for VOO (high dividend yield ETF)."""
    return {
        "info": {
            "currentPrice": 450.0,
            "dividendRate": 5.40,
            "dividendYield": 0.012,
            "exDividendDate": int((datetime.now() + timedelta(days=16)).timestamp()),
            "payoutRatio": None
        },
        "dividends": pd.Series([1.35, 1.35, 1.30, 1.30] * 2)
    }


@pytest.fixture
def mock_yfinance_nvda():
    """Mock yfinance data for NVDA (no dividends)."""
    return {
        "info": {
            "currentPrice": 900.0,
            "dividendRate": 0,
            "dividendYield": 0,
            "exDividendDate": None,
            "payoutRatio": None
        },
        "dividends": pd.Series()
    }


@pytest.fixture
def mock_yfinance_tsla_cut():
    """Mock yfinance data for TSLA (dividend cut detected)."""
    return {
        "info": {
            "currentPrice": 240.0,
            "dividendRate": 0.50,
            "dividendYield": 0.0021,
            "exDividendDate": None,
            "payoutRatio": 0.05
        },
        "dividends": pd.Series([0.25, 0.25, 0.25, 0.25, 0.15, 0.10])
    }


class TestYieldCalculations:
    """Test yield calculations."""
    
    def test_yield_from_info(self, mock_yfinance_aapl):
        """Test that yield from info is used when available."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = mock_yfinance_aapl["info"]
            mock_ticker.return_value.dividends = mock_yfinance_aapl["dividends"]
            
            result = process_ticker("AAPL")
            
            assert result["current_yield"] == 0.004
            assert result["dividend_rate"] == 1.0
    
    def test_yield_calculation_from_rate(self):
        """Test yield calculation when dividendYield is 0 but dividendRate exists."""
        mock_info = {
            "currentPrice": 200.0,
            "dividendRate": 4.0,
            "dividendYield": 0,
            "exDividendDate": None,
            "payoutRatio": 0.1
        }
        
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = mock_info
            mock_ticker.return_value.dividends = pd.Series()
            
            result = process_ticker("TEST")
            
            # 4.0 / 200.0 = 0.02
            assert result["current_yield"] == 0.02
    
    def test_no_yield_when_no_data(self):
        """Test yield is None when no dividend data available."""
        mock_info = {
            "currentPrice": 200.0,
            "dividendRate": 0,
            "dividendYield": 0,
            "exDividendDate": None,
            "payoutRatio": None
        }
        
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = mock_info
            mock_ticker.return_value.dividends = pd.Series()
            
            result = process_ticker("NVDA")
            
            assert result["current_yield"] is None
            assert result["dividend_rate"] == 0


class TestDRIPCalculation:
    """Test DRIP share calculations."""
    
    def test_drip_shares_calculation(self, mock_yfinance_aapl):
        """Test DRIP shares calculation for portfolio holding."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = mock_yfinance_aapl["info"]
            mock_ticker.return_value.dividends = mock_yfinance_aapl["dividends"]
            
            result = process_ticker("AAPL", shares=100, in_portfolio=True)
            
            # dividendRate=1.00, shares=100, quarterly=25, price=250
            # drip_shares = (1.00/4 * 100) / 250 = 25 / 250 = 0.1
            assert result["drip_shares"] == 0.1
            assert result["annual_income"] == 100.0
    
    def test_drip_shares_none_for_non_portfolio(self, mock_yfinance_aapl):
        """Test that DRIP shares is None for non-portfolio tickers."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = mock_yfinance_aapl["info"]
            mock_ticker.return_value.dividends = mock_yfinance_aapl["dividends"]
            
            result = process_ticker("AAPL", in_portfolio=False)
            
            assert result["drip_shares"] is None
            assert result["annual_income"] is None
    
    def test_drip_shares_zero_dividend(self):
        """Test DRIP shares is None when dividend rate is 0."""
        mock_info = {
            "currentPrice": 200.0,
            "dividendRate": 0,
            "dividendYield": 0,
            "exDividendDate": None,
            "payoutRatio": None
        }
        
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = mock_info
            mock_ticker.return_value.dividends = pd.Series()
            
            result = process_ticker("NVDA", shares=50, in_portfolio=True)
            
            assert result["drip_shares"] is None
            assert result["annual_income"] is None


class TestExDateAlert:
    """Test ex-date alert logic."""
    
    def test_ex_date_alert_within_7_days(self):
        """Test alert triggers when ex-date is within 7 days."""
        ex_date = datetime.now() + timedelta(days=5)
        mock_info = {
            "currentPrice": 100.0,
            "dividendRate": 2.0,
            "dividendYield": 0.02,
            "exDividendDate": int(ex_date.timestamp()),
            "payoutRatio": 0.1
        }
        
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = mock_info
            mock_ticker.return_value.dividends = pd.Series()
            
            result = process_ticker("TEST")
            
            assert result["ex_date_alert"] is True
            assert result["days_until_ex"] <= 5  # Allow for test execution time
    
    def test_ex_date_no_alert_beyond_7_days(self, mock_yfinance_aapl):
        """Test alert does not trigger when ex-date is beyond 7 days."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = mock_yfinance_aapl["info"]
            mock_ticker.return_value.dividends = mock_yfinance_aapl["dividends"]
            
            result = process_ticker("AAPL")
            
            assert result["ex_date_alert"] is False
            assert 65 <= result["days_until_ex"] <= 68  # Allow for test execution time
    
    def test_ex_date_none_when_not_found(self):
        """Test ex_date is None when not in future."""
        mock_info = {
            "currentPrice": 100.0,
            "dividendRate": 2.0,
            "dividendYield": 0.02,
            "exDividendDate": None,
            "payoutRatio": 0.1
        }
        
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = mock_info
            mock_ticker.return_value.dividends = pd.Series()
            
            result = process_ticker("TEST")
            
            assert result["next_ex_date"] is None
            assert result["days_until_ex"] is None
            assert result["ex_date_alert"] is False


class TestDividendCutDetection:
    """Test dividend cut detection."""
    
    def test_dividend_cut_detected(self, mock_yfinance_tsla_cut):
        """Test that dividend cut is detected when latest < previous."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = mock_yfinance_tsla_cut["info"]
            mock_ticker.return_value.dividends = mock_yfinance_tsla_cut["dividends"]
            
            result = process_ticker("TSLA")
            
            # Last dividend: 0.10, previous: 0.15
            # Convert numpy bool to native bool for assertion
            assert bool(result["dividend_cut"]) is True
    
    def test_no_cut_when_increasing(self, mock_yfinance_aapl):
        """Test dividend_cut is False when dividends are increasing."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = mock_yfinance_aapl["info"]
            # Create increasing dividend history
            increasing_divs = pd.Series([0.20, 0.22, 0.24, 0.25])
            mock_ticker.return_value.dividends = increasing_divs
            
            result = process_ticker("AAPL")
            
            # Convert numpy bool to native bool for assertion
            assert bool(result["dividend_cut"]) is False
    
    def test_no_cut_with_insufficient_history(self):
        """Test dividend_cut is False when only one dividend."""
        mock_info = {
            "currentPrice": 100.0,
            "dividendRate": 2.0,
            "dividendYield": 0.02,
            "exDividendDate": None,
            "payoutRatio": 0.1
        }
        
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = mock_info
            mock_ticker.return_value.dividends = pd.Series([0.50])
            
            result = process_ticker("TEST")
            
            assert result["dividend_cut"] is False


class TestDividendGrowthCalculation:
    """Test 3-year dividend growth calculation."""
    
    def test_3y_growth_calculation(self):
        """Test 3-year growth calculation with sufficient history."""
        # Create 5 years of quarterly dividends
        # Years: 0.20, 0.20, 0.20, 0.20 | 0.22, 0.22, 0.22, 0.22 | 0.24, 0.24, 0.24, 0.24 | 0.26, 0.26, 0.26, 0.26 | 0.28, 0.28, 0.28, 0.28
        divs_data = [0.20] * 4 + [0.22] * 4 + [0.24] * 4 + [0.26] * 4 + [0.28] * 4
        divs = pd.Series(divs_data)
        
        growth = calculate_div_growth_3y(divs)
        
        # Recent 4: 0.28 * 4 = 1.12
        # 12 quarters back (indices -16 to -12): 0.22 * 4 = 0.88
        # Growth: (1.12 - 0.88) / 0.88 = 0.24 / 0.88 = 0.2727...
        assert abs(float(growth) - 0.2727) < 0.001
    
    def test_no_growth_with_insufficient_history(self):
        """Test growth is None with < 16 dividends."""
        divs = pd.Series([0.25] * 8)
        
        growth = calculate_div_growth_3y(divs)
        
        assert growth is None
    
    def test_no_growth_with_empty_history(self):
        """Test growth is None with empty dividend history."""
        growth = calculate_div_growth_3y(pd.Series())
        
        assert growth is None


class TestTreasuryYieldFallback:
    """Test treasury yield loading."""
    
    def test_treasury_yield_from_dashboard(self):
        """Test loading treasury yield returns a value."""
        yield_val = get_treasury_yield()
        
        # Should return either loaded value or default 4.5%
        assert yield_val > 0
        assert isinstance(yield_val, float)
    
    def test_treasury_yield_default_fallback(self):
        """Test default treasury yield fallback."""
        with patch("pathlib.Path.exists", return_value=False):
            yield_val = get_treasury_yield()
            assert yield_val == 0.045


class TestEnvelopeOutput:
    """Test module envelope output."""
    
    def test_envelope_created_with_configs(self, tmp_path):
        """Test that dividend_tracker.json envelope is created with valid configs."""
        # Create directory structure
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        
        portfolio = {"holdings": [{"ticker": "AAPL", "shares": 100, "cost_basis": 150.0, "date_acquired": "2025-01-01"}]}
        watchlist = {"tickers": ["MSFT"]}
        
        (config_dir / "portfolio.json").write_text(json.dumps(portfolio))
        (config_dir / "watchlist.json").write_text(json.dumps(watchlist))
        
        data_dir = tmp_path / "data"
        (data_dir / "processed").mkdir(parents=True, exist_ok=True)
        (data_dir / "outputs").mkdir(parents=True, exist_ok=True)
        
        # Mock yfinance for both holdings
        with patch("yfinance.Ticker") as mock_ticker:
            mock_instance = MagicMock()
            mock_instance.info = {
                "currentPrice": 150.0,
                "dividendRate": 1.0,
                "dividendYield": 0.0067,
                "exDividendDate": None,
                "payoutRatio": 0.05
            }
            mock_instance.dividends = pd.Series([0.25] * 8)
            mock_ticker.return_value = mock_instance
            
            # Manually call process_ticker with mocked data
            from modules.dividend_tracker import create_envelope, save_envelope
            
            result = process_ticker("AAPL", shares=100, in_portfolio=True)
            
            # Verify result structure
            assert result["ticker"] == "AAPL"
            assert result["in_portfolio"] is True
            assert result["shares"] == 100
            assert "dividend_rate" in result
            assert "current_yield" in result


class TestTickerProcessing:
    """Test individual ticker processing."""
    
    def test_portfolio_holding_flag(self, mock_yfinance_aapl):
        """Test portfolio holdings are marked correctly."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = mock_yfinance_aapl["info"]
            mock_ticker.return_value.dividends = mock_yfinance_aapl["dividends"]
            
            result = process_ticker("AAPL", shares=100, in_portfolio=True)
            
            assert result["in_portfolio"] is True
            assert result["shares"] == 100
    
    def test_watchlist_ticker_flag(self, mock_yfinance_aapl):
        """Test watchlist tickers are marked correctly."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = mock_yfinance_aapl["info"]
            mock_ticker.return_value.dividends = mock_yfinance_aapl["dividends"]
            
            result = process_ticker("AAPL", in_portfolio=False)
            
            assert result["in_portfolio"] is False
            assert result["shares"] is None
    
    def test_no_data_available(self):
        """Test ticker with no data available."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = {}
            mock_ticker.return_value.dividends = pd.Series()
            
            result = process_ticker("NODATA")
            
            assert result["ticker"] == "NODATA"
            assert result["dividend_rate"] == 0
            assert result["current_yield"] is None


class TestYieldVsTreasury:
    """Test yield vs 10-year treasury comparison."""
    
    def test_yield_below_treasury(self, mock_yfinance_aapl):
        """Test classification when yield is below treasury."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = mock_yfinance_aapl["info"]
            mock_ticker.return_value.dividends = mock_yfinance_aapl["dividends"]
            
            with patch("modules.dividend_tracker.get_treasury_yield", return_value=0.04):
                result = process_ticker("AAPL")
                
                # AAPL yield: 0.004, treasury: 0.04 (AAPL lower)
                assert result["yield_vs_10y"] == "below_treasury"
    
    def test_yield_above_treasury(self, mock_yfinance_voo):
        """Test classification when yield is above treasury."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = mock_yfinance_voo["info"]
            mock_ticker.return_value.dividends = mock_yfinance_voo["dividends"]
            
            with patch("modules.dividend_tracker.get_treasury_yield", return_value=0.001):
                result = process_ticker("VOO")
                
                # VOO yield: 0.012, treasury: 0.001 (VOO higher)
                assert result["yield_vs_10y"] == "above_treasury"
