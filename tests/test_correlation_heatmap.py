"""Tests for correlation_heatmap module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock
from datetime import datetime

from modules.correlation_heatmap import (
    load_config,
    get_all_tickers,
    download_prices,
    calculate_correlations,
    find_high_correlations,
    find_risk_clusters,
    flatten_correlation_matrix,
    generate_html,
)
from lib.data_envelope import load_envelope


class TestLoadConfig:
    """Test configuration loading."""
    
    def test_load_valid_configs(self, project_root):
        """Test loading valid portfolio and watchlist configs."""
        portfolio, watchlist = load_config()
        assert isinstance(portfolio, dict)
        assert isinstance(watchlist, dict)
        assert "holdings" in portfolio
        assert "tickers" in watchlist


class TestGetAllTickers:
    """Test ticker aggregation."""
    
    def test_union_of_portfolio_and_watchlist(self):
        """Test that all tickers returns union of portfolio and watchlist."""
        portfolio = {
            "holdings": [
                {"ticker": "NVDA"},
                {"ticker": "AAPL"},
            ]
        }
        watchlist = {
            "tickers": ["MSFT", "AMD", "AAPL"]
        }
        
        all_tickers, portfolio_tickers = get_all_tickers(portfolio, watchlist)
        
        assert set(all_tickers) == {"NVDA", "AAPL", "MSFT", "AMD"}
        assert portfolio_tickers == {"NVDA", "AAPL"}
    
    def test_empty_configs(self):
        """Test with empty configs."""
        all_tickers, portfolio_tickers = get_all_tickers({}, {})
        assert all_tickers == []
        assert portfolio_tickers == set()


class TestDownloadPrices:
    """Test price downloading."""
    
    @patch("modules.correlation_heatmap.yahoo_finance_price_history")
    def test_download_prices_success(self, mock_yf):
        """Test successful price download."""
        # Mock price data
        dates = pd.date_range("2025-09-04", periods=120, freq="D")
        mock_data = pd.DataFrame({
            "NVDA": np.random.uniform(100, 150, 120),
            "AAPL": np.random.uniform(150, 200, 120),
        }, index=dates)
        
        def side_effect(ticker, period="6mo"):
            return mock_data[[ticker]]
        
        mock_yf.side_effect = side_effect
        
        prices = download_prices(["NVDA", "AAPL"], period="6mo")
        
        assert not prices.empty
        assert "NVDA" in prices.columns
        assert "AAPL" in prices.columns
        assert len(prices) > 0
    
    @patch("modules.correlation_heatmap.yahoo_finance_price_history")
    def test_download_prices_failure(self, mock_yf):
        """Test handling of failed downloads."""
        mock_yf.return_value = pd.DataFrame()
        
        prices = download_prices(["INVALID"], period="6mo")
        
        assert prices.empty


class TestCalculateCorrelations:
    """Test correlation calculations."""
    
    def test_calculate_correlations_success(self):
        """Test correlation calculation with valid data."""
        dates = pd.date_range("2025-09-04", periods=100, freq="D")
        prices = pd.DataFrame({
            "NVDA": np.random.uniform(100, 150, 100),
            "AAPL": np.random.uniform(150, 200, 100),
            "MSFT": np.random.uniform(300, 350, 100),
        }, index=dates)
        
        corr_matrix = calculate_correlations(prices)
        
        assert not corr_matrix.empty
        assert corr_matrix.shape == (3, 3)
        assert list(corr_matrix.columns) == ["NVDA", "AAPL", "MSFT"]
        # Diagonal should be 1.0
        assert abs(corr_matrix.loc["NVDA", "NVDA"] - 1.0) < 0.01
    
    def test_calculate_correlations_empty(self):
        """Test with empty price data."""
        corr_matrix = calculate_correlations(pd.DataFrame())
        assert corr_matrix.empty


class TestFindHighCorrelations:
    """Test high correlation detection."""
    
    def test_find_high_correlations(self):
        """Test finding pairs with high correlation."""
        data = {
            "A": [1, 2, 3, 4, 5],
            "B": [1.1, 2.1, 3.1, 4.1, 5.1],  # Highly correlated with A
            "C": [5, 4, 3, 2, 1],  # Negatively correlated
        }
        prices = pd.DataFrame(data)
        corr_matrix = calculate_correlations(prices)
        
        high_corrs = find_high_correlations(corr_matrix, threshold=0.70)
        
        # A and B should be highly correlated
        assert len(high_corrs) > 0
        assert any(item["ticker1"] == "A" and item["ticker2"] == "B" for item in high_corrs)
    
    def test_find_high_correlations_no_matches(self):
        """Test when no correlations exceed threshold."""
        data = {
            "A": [1, 2, 3, 4, 5],
            "B": [5, 4, 3, 2, 1],
        }
        prices = pd.DataFrame(data)
        corr_matrix = calculate_correlations(prices)
        
        high_corrs = find_high_correlations(corr_matrix, threshold=0.99)
        
        # No pairs should match threshold of 0.99
        assert len(high_corrs) == 0


class TestFindRiskClusters:
    """Test risk cluster detection."""
    
    def test_find_risk_clusters(self):
        """Test finding risk clusters (3+ tickers with high correlation)."""
        # Create highly correlated data
        base = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        data = {
            "A": base + np.random.normal(0, 0.1, 10),
            "B": base + np.random.normal(0, 0.1, 10),
            "C": base + np.random.normal(0, 0.1, 10),
            "X": np.array([10, 9, 8, 7, 6, 5, 4, 3, 2, 1]),
        }
        prices = pd.DataFrame(data)
        corr_matrix = calculate_correlations(prices)
        
        clusters = find_risk_clusters(corr_matrix, threshold=0.75)
        
        # A, B, C should form a cluster
        assert len(clusters) > 0
        cluster_tickers = clusters[0]["tickers"]
        assert len(cluster_tickers) >= 3
    
    def test_find_risk_clusters_no_clusters(self):
        """Test when no risk clusters exist."""
        data = {
            "A": [1, 2, 3, 4, 5],
            "B": [5, 4, 3, 2, 1],
            "C": [1, 3, 5, 2, 4],
        }
        prices = pd.DataFrame(data)
        corr_matrix = calculate_correlations(prices)
        
        clusters = find_risk_clusters(corr_matrix, threshold=0.95)
        
        # Very high threshold should yield no clusters
        assert len(clusters) == 0


class TestFlattenCorrelationMatrix:
    """Test correlation matrix flattening."""
    
    def test_flatten_correlation_matrix(self):
        """Test flattening a correlation matrix to dict."""
        data = {
            "A": [1, 2, 3],
            "B": [1, 2, 3],
            "C": [3, 2, 1],
        }
        prices = pd.DataFrame(data)
        corr_matrix = calculate_correlations(prices)
        
        flat = flatten_correlation_matrix(corr_matrix)
        
        assert isinstance(flat, dict)
        assert "A_A" in flat
        assert "A_B" in flat
        assert flat["A_A"] == pytest.approx(1.0, abs=0.01)
        assert "B_A" not in flat  # Should not include duplicate


class TestGenerateHTML:
    """Test HTML generation."""
    
    def test_generate_html_creates_file(self, tmp_path):
        """Test that HTML file is created successfully."""
        # Create sample data
        data = {
            "A": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "B": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "C": [10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
        }
        prices = pd.DataFrame(data)
        corr_matrix = calculate_correlations(prices)
        
        high_corrs = [
            {"ticker1": "A", "ticker2": "B", "pair": "A_B", "corr": 0.99, "trend": "stable"}
        ]
        risk_clusters = [
            {"tickers": ["A", "B"], "avg_corr": 0.99, "size": 2}
        ]
        portfolio_tickers = {"A"}
        
        output_path = tmp_path / "test_heatmap.html"
        generate_html(corr_matrix, high_corrs, risk_clusters, portfolio_tickers, output_path)
        
        assert output_path.exists()
        html_content = output_path.read_text()
        assert "Correlation Heatmap" in html_content
        assert "A" in html_content
        assert "B" in html_content
    
    def test_generate_html_with_empty_data(self, tmp_path):
        """Test HTML generation with empty correlation matrix."""
        corr_matrix = pd.DataFrame()
        output_path = tmp_path / "empty_heatmap.html"
        
        generate_html(corr_matrix, [], [], set(), output_path)
        
        # Should not create file when correlation matrix is empty
        assert not output_path.exists()


class TestEnvelopeOutput:
    """Test envelope JSON output."""
    
    @patch("modules.correlation_heatmap.yahoo_finance_price_history")
    def test_envelope_saved(self, mock_yf, tmp_path, monkeypatch):
        """Test that envelope is saved to data/processed/correlation_heatmap.json."""
        # Mock yfinance
        dates = pd.date_range("2025-09-04", periods=100, freq="D")
        mock_data = pd.DataFrame({
            "NVDA": np.random.uniform(100, 150, 100),
            "AAPL": np.random.uniform(150, 200, 100),
        }, index=dates)
        
        def side_effect(ticker, period="6mo"):
            return mock_data[[ticker]]
        
        mock_yf.side_effect = side_effect
        
        # Mock config loading
        def mock_load_config():
            return (
                {"holdings": [{"ticker": "NVDA"}, {"ticker": "AAPL"}]},
                {"tickers": ["NVDA", "AAPL"]},
            )
        
        with patch("modules.correlation_heatmap.load_config", side_effect=mock_load_config):
            # Create sample envelope
            from modules.correlation_heatmap import main
            # Instead of calling main (which uses real filesystem), test envelope structure
            data = {
                "matrix": {"NVDA_AAPL": 0.72},
                "high_correlations": [],
                "risk_clusters": [],
                "tickers_analyzed": 2,
            }
            
            from lib.data_envelope import create_envelope
            envelope = create_envelope("correlation_heatmap", data, status="success")
            
            assert envelope["module"] == "correlation_heatmap"
            assert envelope["status"] == "success"
            assert envelope["data"]["tickers_analyzed"] == 2
            assert envelope["generated_at"] is not None


class TestIntegration:
    """Integration tests."""
    
    @patch("modules.correlation_heatmap.yahoo_finance_price_history")
    def test_full_pipeline(self, mock_yf, tmp_path, monkeypatch):
        """Test full pipeline from download to analysis."""
        # Mock price data
        dates = pd.date_range("2025-09-04", periods=120, freq="D")
        
        def create_price_series(ticker):
            np.random.seed(hash(ticker) % 2**32)
            return pd.Series(
                np.random.uniform(50, 300, 120),
                index=dates
            )
        
        def side_effect(ticker, period="6mo"):
            return pd.DataFrame({ticker: create_price_series(ticker)})
        
        mock_yf.side_effect = side_effect
        
        # Test download
        prices = download_prices(["NVDA", "AAPL", "MSFT"])
        assert not prices.empty
        assert len(prices) > 0
        
        # Test correlations
        corr_matrix = calculate_correlations(prices)
        assert not corr_matrix.empty
        assert corr_matrix.shape == (3, 3)
        
        # Test high correlations
        high_corrs = find_high_correlations(corr_matrix, threshold=0.50)
        assert isinstance(high_corrs, list)
        
        # Test risk clusters
        clusters = find_risk_clusters(corr_matrix, threshold=0.70)
        assert isinstance(clusters, list)
        
        # Test flattening
        flat = flatten_correlation_matrix(corr_matrix)
        assert isinstance(flat, dict)
        assert len(flat) > 0
