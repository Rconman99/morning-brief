"""Tests for Congressional Trading Tracker module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from unittest.mock import patch, MagicMock
from modules.congress_tracker import (
    analyze_trades, _parse_amount, fetch_all_trades,
    load_sample_trades, run_congress_tracker, AMOUNT_MIDPOINTS,
)


@pytest.fixture
def sample_trades():
    """Load sample congressional trades."""
    sample_path = PROJECT_ROOT / "data" / "sample" / "congress_trades.json"
    data = json.loads(sample_path.read_text())
    return data["trades"]


@pytest.fixture
def watchlist_tickers():
    return ["AAPL", "NVDA", "MSFT", "AMD", "GOOGL", "AMZN", "TSLA", "META"]


class TestParseAmount:
    def test_known_ranges(self):
        assert _parse_amount("$1,000,001 - $5,000,000") == 3000000
        assert _parse_amount("$100,001 - $250,000") == 175000
        assert _parse_amount("$15,001 - $50,000") == 32500

    def test_empty_string(self):
        assert _parse_amount("") == 0

    def test_none(self):
        assert _parse_amount(None) == 0

    def test_unknown_format(self):
        result = _parse_amount("about $50000")
        assert result > 0


class TestAnalyzeTrades:
    def test_empty_trades(self, watchlist_tickers):
        result = analyze_trades([], watchlist_tickers)
        assert result["signal"] == "no_data"
        assert result["watchlist_trades"] == []
        assert result["cluster_signals"] == []

    def test_watchlist_filtering(self, sample_trades, watchlist_tickers):
        result = analyze_trades(sample_trades, watchlist_tickers)
        assert len(result["watchlist_trades"]) > 0
        for t in result["watchlist_trades"]:
            assert t["ticker"] in watchlist_tickers

    def test_notable_traders_detected(self, sample_trades, watchlist_tickers):
        result = analyze_trades(sample_trades, watchlist_tickers)
        notable = result["notable_trades"]
        assert len(notable) > 0
        names = [t["politician"].lower() for t in notable]
        assert any("pelosi" in n for n in names)

    def test_cluster_signal_detection(self, sample_trades, watchlist_tickers):
        """NVDA has 3 different politicians buying — should trigger cluster."""
        result = analyze_trades(sample_trades, watchlist_tickers)
        clusters = result["cluster_signals"]
        nvda_clusters = [c for c in clusters if c["ticker"] == "NVDA"]
        assert len(nvda_clusters) > 0
        assert nvda_clusters[0]["politician_count"] >= 3

    def test_big_trades_detected(self, sample_trades, watchlist_tickers):
        result = analyze_trades(sample_trades, watchlist_tickers)
        big = result["big_trades"]
        assert len(big) > 0
        for t in big:
            assert _parse_amount(t["amount_range"]) >= 500000

    def test_summary_stats(self, sample_trades, watchlist_tickers):
        result = analyze_trades(sample_trades, watchlist_tickers)
        stats = result["summary_stats"]
        assert stats["total_trades"] > 0
        assert stats["buy_count"] + stats["sell_count"] <= stats["total_trades"]
        assert stats["unique_politicians"] > 0
        assert "top_tickers" in stats
        assert "partisan_breakdown" in stats

    def test_signal_with_clusters(self, sample_trades, watchlist_tickers):
        result = analyze_trades(sample_trades, watchlist_tickers)
        assert result["signal"] in ("cluster_detected", "significant_activity", "active", "normal")

    def test_partisan_breakdown(self, sample_trades, watchlist_tickers):
        result = analyze_trades(sample_trades, watchlist_tickers)
        partisan = result["summary_stats"]["partisan_breakdown"]
        assert "democrat" in partisan
        assert "republican" in partisan
        assert partisan["democrat"]["buys"] >= 0
        assert partisan["republican"]["buys"] >= 0


class TestFetchAllTrades:
    @patch("modules.congress_tracker.fetch_house_trades", return_value=[])
    @patch("modules.congress_tracker.fetch_senate_trades", return_value=[])
    def test_fallback_to_sample(self, mock_senate, mock_house):
        trades, source = fetch_all_trades()
        assert source == "sample_data"
        assert len(trades) > 0

    @patch("modules.congress_tracker.fetch_house_trades")
    @patch("modules.congress_tracker.fetch_senate_trades", return_value=[])
    def test_house_only(self, mock_senate, mock_house):
        mock_house.return_value = [{"ticker": "AAPL", "politician": "Test"}]
        trades, source = fetch_all_trades()
        assert source == "house_api"
        assert len(trades) == 1


class TestRunCongressTracker:
    @patch("modules.congress_tracker.fetch_house_trades", return_value=[])
    @patch("modules.congress_tracker.fetch_senate_trades", return_value=[])
    def test_runs_with_sample_fallback(self, mock_senate, mock_house):
        result = run_congress_tracker()
        assert "data_source" in result
        assert "signal" in result
        assert "summary_stats" in result

    @patch("modules.congress_tracker.fetch_house_trades", return_value=[])
    @patch("modules.congress_tracker.fetch_senate_trades", return_value=[])
    def test_envelope_created(self, mock_senate, mock_house):
        """Test that main() creates a valid envelope."""
        from modules.congress_tracker import main

        with patch("modules.congress_tracker.save_envelope") as mock_save:
            main()
            mock_save.assert_called_once()
            envelope = mock_save.call_args[0][0]
            assert envelope["module"] == "congress_tracker"
            assert envelope["status"] in ("success", "partial", "error")
            assert "data" in envelope
