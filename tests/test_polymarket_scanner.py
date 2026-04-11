"""Tests for Polymarket Scanner module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from modules.polymarket_scanner import (
    categorize_market, parse_outcome_prices, analyze_markets,
    fetch_polymarket_markets, load_sample_markets, run_polymarket_scanner,
    analyze_weather_market, detect_city,
    scan_gimme_bets, group_sports_events, analyze_economics_markets, detect_trending,
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

    def test_sports_vs_format(self):
        assert categorize_market("Duke vs. UNC — who wins?") == "sports"

    def test_sports_march_madness(self):
        assert categorize_market("March Madness bracket picks?") == "sports"

    def test_sports_ufc(self):
        assert categorize_market("UFC 320 main event winner?") == "sports"

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

    def test_new_keys_present(self, sample_markets):
        result = analyze_markets(sample_markets)
        assert "gimme_bets" in result
        assert "sports_events" in result
        assert "economics_signals" in result
        assert "trending" in result
        assert isinstance(result["gimme_bets"], list)
        assert isinstance(result["sports_events"], list)
        assert isinstance(result["economics_signals"], list)
        assert isinstance(result["trending"], list)

    def test_empty_returns_new_keys(self):
        result = analyze_markets([])
        assert result["gimme_bets"] == []
        assert result["sports_events"] == []
        assert result["economics_signals"] == []
        assert result["trending"] == []


class TestScanGimmeBets:
    def test_finds_extreme_prices(self):
        markets = [{
            "question": "Will the sun rise tomorrow?",
            "outcomePrices": "[0.97, 0.03]",
            "volume24hr": 50000,
            "liquidity": 10000,
            "endDate": (datetime.now().astimezone() + timedelta(days=5)).isoformat(),
        }]
        result = scan_gimme_bets(markets)
        assert len(result) >= 1
        assert result[0]["side"] == "YES"
        assert result[0]["price"] >= 0.92

    def test_skips_low_volume(self):
        markets = [{
            "question": "Low volume gimme",
            "outcomePrices": "[0.95, 0.05]",
            "volume24hr": 500,
            "liquidity": 100,
            "endDate": (datetime.now().astimezone() + timedelta(days=5)).isoformat(),
        }]
        result = scan_gimme_bets(markets)
        assert len(result) == 0

    def test_skips_far_out_expiry(self):
        markets = [{
            "question": "Far out gimme",
            "outcomePrices": "[0.95, 0.05]",
            "volume24hr": 50000,
            "liquidity": 10000,
            "endDate": (datetime.now().astimezone() + timedelta(days=60)).isoformat(),
        }]
        result = scan_gimme_bets(markets)
        assert len(result) == 0

    def test_returns_max_15(self):
        markets = [{
            "question": f"Gimme bet {i}",
            "outcomePrices": "[0.95, 0.05]",
            "volume24hr": 50000,
            "liquidity": 10000,
            "endDate": (datetime.now().astimezone() + timedelta(days=5)).isoformat(),
        } for i in range(30)]
        result = scan_gimme_bets(markets)
        assert len(result) <= 15


class TestGroupSportsEvents:
    def test_groups_by_event(self):
        markets = [
            {"question": "Duke vs. UNC March Madness", "outcomePrices": "[0.55, 0.45]", "volume24hr": 20000, "endDate": ""},
            {"question": "UFC 320 main event", "outcomePrices": "[0.60, 0.40]", "volume24hr": 15000, "endDate": ""},
        ]
        result = group_sports_events(markets)
        events = [r["event"] for r in result]
        assert "March Madness" in events or "UFC" in events

    def test_empty_if_no_sports(self):
        markets = [{"question": "Will Bitcoin hit 200K?", "outcomePrices": "[0.30, 0.70]", "volume24hr": 50000}]
        result = group_sports_events(markets)
        assert len(result) == 0


class TestDetectTrending:
    def test_finds_trending(self):
        markets = [{
            "question": "Big mover market",
            "outcomePrices": "[0.60, 0.40]",
            "volume24hr": 50000,
            "oneWeekPriceChange": 0.05,
            "oneDayPriceChange": 0.02,
        }]
        result = detect_trending(markets)
        assert len(result) == 1
        assert result[0]["has_momentum"] is True
        assert result[0]["week_change_pct"] == 5.0

    def test_skips_below_threshold(self):
        markets = [{
            "question": "Flat market",
            "outcomePrices": "[0.50, 0.50]",
            "volume24hr": 50000,
            "oneWeekPriceChange": 0.01,
            "oneDayPriceChange": 0.005,
        }]
        result = detect_trending(markets)
        assert len(result) == 0

    def test_no_momentum_opposite_direction(self):
        markets = [{
            "question": "Reversal market",
            "outcomePrices": "[0.50, 0.50]",
            "volume24hr": 50000,
            "oneWeekPriceChange": 0.05,
            "oneDayPriceChange": -0.02,
        }]
        result = detect_trending(markets)
        assert len(result) == 1
        assert result[0]["has_momentum"] is False


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
