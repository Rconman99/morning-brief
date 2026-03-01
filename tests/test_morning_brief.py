"""Tests for morning_brief module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from unittest.mock import patch

from modules.morning_brief import generate_brief, determine_verdict, main


def test_generate_brief_with_mock_data(mock_envelopes, tmp_path):
    """Test brief generation using mock_envelopes fixture from conftest."""
    outputs = tmp_path / "outputs"
    outputs.mkdir()

    brief = generate_brief(processed_dir=mock_envelopes, outputs_dir=outputs)
    assert "Morning Brief" in brief
    assert "Module Status" in brief
    assert "Trade Journal" in brief
    assert "Earnings Tone" in brief
    assert "Valuation" in brief
    assert "Portfolio" in brief
    assert "Options" in brief
    assert "Technical Signals" in brief
    assert "News Sentiment" in brief
    assert "Risk Dashboard" in brief
    assert "Opportunity Scanner" in brief
    assert "Trade Memory" in brief
    assert "Insider Activity" in brief
    assert "Scenario Valuations" in brief
    assert "Position Sizing" in brief
    assert "financial advice" in brief.lower()


def test_generate_brief_missing_modules(tmp_path):
    """Brief should still generate when some modules are missing."""
    empty_processed = tmp_path / "processed"
    empty_processed.mkdir()
    outputs = tmp_path / "outputs"
    outputs.mkdir()

    brief = generate_brief(processed_dir=empty_processed, outputs_dir=outputs)
    assert "Morning Brief" in brief
    assert "Data unavailable" in brief


def test_determine_verdict_hold():
    verdict, _ = determine_verdict(
        "AAPL",
        {"comparisons": []},
        {"results": []},
        {"holdings": [{"ticker": "AAPL", "current_price": 195, "trailing_stop": 170}]},
        {"results": [{"ticker": "AAPL", "composite_score": 0.5}]},
        {"results": [{"ticker": "AAPL", "sentiment_score": 0.1}]},
    )
    assert verdict == "HOLD"


def test_determine_verdict_sell():
    """Trailing stop within 5% of price should trigger SELL."""
    verdict, _ = determine_verdict(
        "AAPL",
        {"comparisons": []},
        {"results": []},
        {"holdings": [{"ticker": "AAPL", "current_price": 100, "trailing_stop": 96}]},
    )
    assert verdict == "SELL"


def test_determine_verdict_sell_technical():
    """Technical composite < -3 should trigger SELL."""
    verdict, _ = determine_verdict(
        "AAPL",
        {"comparisons": []},
        {"results": []},
        {"holdings": []},
        {"results": [{"ticker": "AAPL", "composite_score": -3.5}]},
        {"results": []},
    )
    assert verdict == "SELL"


def test_determine_verdict_avoid_sentiment():
    """News sentiment < -0.5 should trigger AVOID."""
    verdict, _ = determine_verdict(
        "AAPL",
        {"comparisons": []},
        {"results": []},
        {"holdings": [{"ticker": "AAPL", "current_price": 195, "trailing_stop": 170}]},
        {"results": []},
        {"results": [{"ticker": "AAPL", "sentiment_score": -0.7}]},
    )
    assert verdict == "AVOID"


def test_determine_verdict_avoid_tone():
    """Tone score <= -2 should trigger AVOID."""
    verdict, _ = determine_verdict(
        "AAPL",
        {"comparisons": []},
        {"results": [{"ticker": "AAPL", "tone_score": -3.0}]},
        {"holdings": [{"ticker": "AAPL", "current_price": 195, "trailing_stop": 170}]},
        {"results": []},
        {"results": []},
    )
    assert verdict == "AVOID"


def test_determine_verdict_danger_regime_blocks_buy():
    """DANGER regime should downgrade BUY to HOLD."""
    verdict, reason = determine_verdict(
        "AAPL",
        {"comparisons": []},
        {"results": []},
        {"holdings": [{"ticker": "AAPL", "current_price": 195, "trailing_stop": 170}]},
        {"results": [{"ticker": "AAPL", "composite_score": 3.0}]},
        {"results": [{"ticker": "AAPL", "sentiment_score": 0.5}]},
        {"regime": "DANGER"},
    )
    assert verdict == "HOLD"
    assert "DANGER" in reason


def test_determine_verdict_normal_regime_allows_buy():
    """NORMAL regime should NOT block BUY."""
    verdict, _ = determine_verdict(
        "AAPL",
        {"comparisons": []},
        {"results": []},
        {"holdings": [{"ticker": "AAPL", "current_price": 195, "trailing_stop": 170}]},
        {"results": [{"ticker": "AAPL", "composite_score": 3.0}]},
        {"results": [{"ticker": "AAPL", "sentiment_score": 0.5}]},
        {"regime": "NORMAL"},
    )
    assert verdict == "BUY"


def test_determine_verdict_insider_boost():
    """Insider cluster buy with positive technicals should trigger BUY."""
    verdict, reason = determine_verdict(
        "AAPL",
        {"comparisons": []},
        {"results": []},
        {"holdings": [{"ticker": "AAPL", "current_price": 195, "trailing_stop": 170}]},
        {"results": [{"ticker": "AAPL", "composite_score": 1.5}]},
        {"results": [{"ticker": "AAPL", "sentiment_score": 0.1}]},
        None,  # risk_dashboard_data
        {"results": [{"ticker": "AAPL", "cluster_buy": True}]},  # insider_data
    )
    assert verdict == "BUY"
    assert "insider" in reason.lower()


def test_determine_verdict_scenario_rr_in_reason():
    """Favorable R/R should appear in BUY reason."""
    verdict, reason = determine_verdict(
        "AAPL",
        {"comparisons": []},
        {"results": []},
        {"holdings": [{"ticker": "AAPL", "current_price": 195, "trailing_stop": 170}]},
        {"results": [{"ticker": "AAPL", "composite_score": 3.0}]},
        {"results": [{"ticker": "AAPL", "sentiment_score": 0.5}]},
        {"regime": "NORMAL"},
        None,  # insider_data
        [{"ticker": "AAPL", "risk_reward": 2.0}],  # scenario_data
    )
    assert verdict == "BUY"
    assert "R/R" in reason


def test_determine_verdict_review():
    """Fewer than 2 data sources should trigger REVIEW."""
    verdict, _ = determine_verdict("AAPL", {"comparisons": []}, {"results": []}, {"holdings": []})
    assert verdict == "REVIEW"


def test_main_creates_output(mock_envelopes, tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()

    with patch("modules.morning_brief.PROCESSED_DIR", mock_envelopes), \
         patch("modules.morning_brief.OUTPUTS_DIR", outputs):
        main()

    md_files = list(outputs.glob("morning_brief_*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text()
    assert "Morning Brief" in content
    assert "financial advice" in content.lower()
