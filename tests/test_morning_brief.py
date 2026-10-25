"""Tests for morning_brief module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from unittest.mock import patch

from modules.morning_brief import (
    generate_brief,
    determine_verdict,
    main,
    spy_sma200_regime,
    spy_200d_trend_from_closes,
    spy_200d_trend_from_history,
    render_spy_200d_trend_line,
    SPY_TREND_UNAVAILABLE,
)


@pytest.fixture(autouse=True)
def _no_live_spy_download(monkeypatch):
    """Brief tests stay offline. The SPY line fail-softs when the download raises."""

    def _boom(period="2y"):
        raise RuntimeError("SPY download disabled in tests")

    monkeypatch.setattr("modules.morning_brief._download_spy_daily", _boom)


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
    assert SPY_TREND_UNAVAILABLE in brief
    assert "Macro Dashboard" in brief


def test_generate_brief_missing_modules(tmp_path):
    """Brief should still generate when some modules are missing."""
    empty_processed = tmp_path / "processed"
    empty_processed.mkdir()
    outputs = tmp_path / "outputs"
    outputs.mkdir()

    brief = generate_brief(processed_dir=empty_processed, outputs_dir=outputs)
    assert "Morning Brief" in brief
    assert "Data unavailable" in brief
    assert SPY_TREND_UNAVAILABLE in brief


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


@pytest.mark.parametrize(
    "close,sma,expected",
    [
        (101.0, 100.0, "ABOVE"),
        (100.999, 100.0, "NEUTRAL (inside buffer)"),
        (100.5, 100.0, "NEUTRAL (inside buffer)"),
        (100.0, 100.0, "NEUTRAL (inside buffer)"),
        (98.0, 100.0, "NEUTRAL (inside buffer)"),
        (97.001, 100.0, "NEUTRAL (inside buffer)"),
        (97.0, 100.0, "BELOW"),
        (90.0, 100.0, "BELOW"),
    ],
)
def test_spy_sma200_regime_buffer(close, sma, expected):
    """1% above is ABOVE, 3% below is BELOW, the band between is NEUTRAL."""
    assert spy_sma200_regime(close, sma) == expected


def test_spy_sma200_regime_exact_ratio_on_uneven_sma():
    sma = 487.33
    assert spy_sma200_regime(sma * 1.01, sma) == "ABOVE"
    assert spy_sma200_regime(sma * 0.97, sma) == "BELOW"
    assert spy_sma200_regime(sma * 1.005, sma) == "NEUTRAL (inside buffer)"
    assert spy_sma200_regime(sma * 0.98, sma) == "NEUTRAL (inside buffer)"


def test_spy_trend_line_from_synthetic_closes():
    """Last 200 closes: a jump on the final print is still context, not a signal."""
    closes = [100.0] * 199 + [110.0]
    line = spy_200d_trend_from_closes(closes)
    assert line.startswith("SPY 200-day trend (context, not a trade signal):")
    assert "SMA200" in line
    assert "ABOVE" in line
    assert "unavailable" not in line
    assert spy_200d_trend_from_closes([100.0] * 199) == SPY_TREND_UNAVAILABLE
    assert spy_200d_trend_from_closes([]) == SPY_TREND_UNAVAILABLE
    assert spy_200d_trend_from_closes(None) == SPY_TREND_UNAVAILABLE


def test_spy_trend_line_from_synthetic_history():
    import pandas as pd

    idx = pd.bdate_range("2024-01-02", periods=210)
    flat = pd.DataFrame({"Close": [100.0] * 210}, index=idx)
    neutral = spy_200d_trend_from_history(flat)
    assert "NEUTRAL (inside buffer)" in neutral
    assert "not a trade signal" in neutral

    cols = pd.MultiIndex.from_tuples([("Close", "SPY")])
    jumped = pd.DataFrame([[100.0]] * 209 + [[110.0]], index=idx[:210], columns=cols)
    assert "ABOVE" in spy_200d_trend_from_history(jumped)
    assert spy_200d_trend_from_history(pd.DataFrame()) == SPY_TREND_UNAVAILABLE


def test_render_spy_line_uses_injected_history(monkeypatch):
    import pandas as pd

    idx = pd.bdate_range("2024-01-02", periods=220)
    frame = pd.DataFrame({"Close": [200.0] * 219 + [250.0]}, index=idx)
    monkeypatch.setattr("modules.morning_brief._download_spy_daily", lambda period="2y": frame)
    line = render_spy_200d_trend_line()
    assert "context, not a trade signal" in line
    assert "ABOVE" in line
    assert line.startswith("SPY 200-day trend")


def test_spy_download_failure_does_not_break_brief(mock_envelopes, tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    brief = generate_brief(processed_dir=mock_envelopes, outputs_dir=outputs)
    assert SPY_TREND_UNAVAILABLE in brief
    assert "Morning Brief" in brief
    assert "financial advice" in brief.lower()


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
