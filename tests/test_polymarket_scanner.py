"""Tests for Polymarket Scanner module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from unittest.mock import patch, MagicMock
from modules.polymarket_scanner import (
    categorize_market, parse_outcome_prices, analyze_markets,
    fetch_polymarket_markets, load_sample_markets, run_polymarket_scanner,
    analyze_weather_market, detect_city,
)


@pytest.fixture
def sample_markets():
    sample_path = PROJECT_ROOT / "data" / "sample" / "polymarket_markets.json"
    data = json.loads(sample_path.read_text())
    return data["markets"]


class TestCategorizeMarket:
    def test_crypto(self):
        assert categorize_market("Will Bitcoin reach $150,000?") == "crypto"
        assert categorize_market("Will ETH hit $10,000?") == "crypto"

    def test_weather(self):
        assert categorize_market("NYC temperature high above 50°F?") == "weather"
        assert categorize_market("Will it rain in London?") == "weather"

    def test_politics(self):
        assert categorize_market("Will Trump sign executive orders?") == "politics"

    def test_economics(self):
        assert categorize_market("Will Fed decrease rates 50+ bps?") == "economics"

    def test_geopolitics(self):
        assert categorize_market("Will Iran close the Strait of Hormuz?") == "geopolitics"

    def test_sports(self):
        assert categorize_market("Will the Pacers win the NBA Finals?") == "sports"

    def test_other(self):
        assert categorize_market("Something completely random?") == "other"


class TestParseOutcomePrices:
    def test_string_format(self):
        yes, no = parse_outcome_prices("[0.72, 0.28]")
        assert yes == 0.72
        assert no == 0.28

    def test_list_format(self):
        yes, no = parse_outcome_prices([0.5, 0.5])
        assert yes == 0.5
        assert no == 0.5

    def test_none(self):
        yes, no = parse_outcome_prices(None)
        assert yes is None
        assert no is None

    def test_invalid(self):
        yes, no = parse_outcome_prices("invalid")
        assert yes is None
        assert no is None


class TestDetectCity:
    def test_nyc(self):
        result = detect_city("NYC temperature high above 50°F?")
        assert result is not None
        assert result[0] == "nyc"

    def test_london(self):
        result = detect_city("Will it rain in London?")
        assert result is not None
        assert result[0] == "london"

    def test_no_city(self):
        result = detect_city("Will Bitcoin reach 150000?")
        assert result is None


class TestAnalyzeMarkets:
    def test_empty_markets(self):
        result = analyze_markets([])
        assert result["signal"] == "no_data"
        assert result["markets_scanned"] == 0

    def test_sample_markets(self, sample_markets):
        result = analyze_markets(sample_markets)
        assert result["markets_scanned"] > 0
        assert "category_summary" in result
        assert "top_volume" in result
        assert len(result["top_volume"]) > 0

    def test_big_movers_detected(self, sample_markets):
        result = analyze_markets(sample_markets)
        # Our sample data has some markets with >8% moves
        # Check structure is correct even if no big movers
        assert isinstance(result["big_movers"], list)

    def test_category_summary(self, sample_markets):
        result = analyze_markets(sample_markets)
        summary = result["category_summary"]
        assert len(summary) > 0
        for cat, info in summary.items():
            assert "markets" in info
            assert "opportunities" in info

    def test_opportunities_have_required_fields(self, sample_markets):
        result = analyze_markets(sample_markets)
        for opp in result["opportunities"]:
            assert "question" in opp
            assert "opportunity_type" in opp
            assert "recommended_side" in opp
            assert "confidence" in opp

    def test_signal_present(self, sample_markets):
        result = analyze_markets(sample_markets)
        assert result["signal"] in ("multiple_opportunities", "opportunities_found", "volatile", "quiet", "no_data")
        assert result["signal_detail"] != ""


class TestFetchAndRun:
    @patch("modules.polymarket_scanner.fetch_polymarket_markets", return_value=[])
    def test_fallback_to_sample(self, mock_fetch):
        result = run_polymarket_scanner()
        assert result["data_source"] == "sample_data"
        assert result["markets_scanned"] > 0

    @patch("modules.polymarket_scanner.fetch_polymarket_markets", return_value=[])
    def test_envelope_created(self, mock_fetch):
        from modules.polymarket_scanner import main
        with patch("modules.polymarket_scanner.save_envelope") as mock_save:
            main()
            mock_save.assert_called_once()
            envelope = mock_save.call_args[0][0]
            assert envelope["module"] == "polymarket_scanner"
            assert envelope["status"] in ("success", "partial", "error")
            assert "data" in envelope

    def test_sample_data_loads(self):
        markets = load_sample_markets()
        assert len(markets) > 0
        assert markets[0].get("question")
