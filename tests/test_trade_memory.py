"""Tests for trade_memory module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from unittest.mock import patch, MagicMock

from modules.trade_memory import (
    create_fingerprint, match_fingerprint, analyze_trade_memory, main,
)


# ── Fingerprint creation tests ──

def test_fingerprint_rsi_oversold():
    result = create_fingerprint({"rsi_14": 25.0, "macd_signal": "bullish", "bb_position": "lower", "sma_trend": "above_200", "volume_ratio": 1.0})
    assert result["rsi_bucket"] == "oversold"


def test_fingerprint_rsi_low():
    result = create_fingerprint({"rsi_14": 40.0})
    assert result["rsi_bucket"] == "low"


def test_fingerprint_rsi_neutral():
    result = create_fingerprint({"rsi_14": 50.0})
    assert result["rsi_bucket"] == "neutral"


def test_fingerprint_rsi_high():
    result = create_fingerprint({"rsi_14": 60.0})
    assert result["rsi_bucket"] == "high"


def test_fingerprint_rsi_overbought():
    result = create_fingerprint({"rsi_14": 75.0})
    assert result["rsi_bucket"] == "overbought"


def test_fingerprint_rsi_none():
    result = create_fingerprint({})
    assert result["rsi_bucket"] == "unknown"


def test_fingerprint_volume_low():
    result = create_fingerprint({"volume_ratio": 0.5})
    assert result["volume_bucket"] == "low"


def test_fingerprint_volume_normal():
    result = create_fingerprint({"volume_ratio": 1.0})
    assert result["volume_bucket"] == "normal"


def test_fingerprint_volume_elevated():
    result = create_fingerprint({"volume_ratio": 1.5})
    assert result["volume_bucket"] == "elevated"


def test_fingerprint_volume_surge():
    result = create_fingerprint({"volume_ratio": 2.5})
    assert result["volume_bucket"] == "surge"


def test_fingerprint_volume_none():
    result = create_fingerprint({})
    assert result["volume_bucket"] == "unknown"


def test_fingerprint_all_fields():
    result = create_fingerprint({
        "rsi_14": 50.0,
        "macd_signal": "bullish_crossover",
        "bb_position": "middle",
        "sma_trend": "golden_cross",
        "volume_ratio": 1.2,
    })
    assert result == {
        "rsi_bucket": "neutral",
        "macd_direction": "bullish_crossover",
        "bb_position": "middle",
        "sma_trend": "golden_cross",
        "volume_bucket": "normal",
    }


# ── Matching tests ──

def _make_hist(fp_dict, outcome="win"):
    return {"fingerprint": fp_dict, "outcome": outcome, "ticker": "TEST"}


def test_match_empty_history():
    result = match_fingerprint({"rsi_bucket": "neutral"}, [])
    assert result["matches"] == 0
    assert result["confidence"] == "no_data"


def test_match_no_matches():
    current = {"rsi_bucket": "oversold", "macd_direction": "bearish", "bb_position": "lower", "sma_trend": "death_cross", "volume_bucket": "surge"}
    hist = [_make_hist({"rsi_bucket": "overbought", "macd_direction": "bullish", "bb_position": "upper", "sma_trend": "golden_cross", "volume_bucket": "low"})]
    result = match_fingerprint(current, hist)
    assert result["matches"] == 0
    assert result["confidence"] == "no_history"


def test_match_3_of_5_matches():
    """3 matching dimensions should count as a match."""
    current = {"rsi_bucket": "neutral", "macd_direction": "bullish", "bb_position": "middle", "sma_trend": "above_200", "volume_bucket": "normal"}
    hist = [_make_hist({"rsi_bucket": "neutral", "macd_direction": "bullish", "bb_position": "middle", "sma_trend": "death_cross", "volume_bucket": "surge"})]
    result = match_fingerprint(current, hist)
    assert result["matches"] == 1


def test_match_2_of_5_no_match():
    """Only 2 matching dimensions should NOT count."""
    current = {"rsi_bucket": "neutral", "macd_direction": "bullish", "bb_position": "middle", "sma_trend": "above_200", "volume_bucket": "normal"}
    hist = [_make_hist({"rsi_bucket": "neutral", "macd_direction": "bullish", "bb_position": "upper", "sma_trend": "death_cross", "volume_bucket": "surge"})]
    result = match_fingerprint(current, hist)
    assert result["matches"] == 0


def test_match_5_of_5_matches():
    """5/5 match should be counted."""
    fp = {"rsi_bucket": "neutral", "macd_direction": "bullish", "bb_position": "middle", "sma_trend": "above_200", "volume_bucket": "normal"}
    hist = [_make_hist(fp)]
    result = match_fingerprint(fp, hist)
    assert result["matches"] == 1


def test_unknown_dimensions_not_counted():
    """'unknown' dimensions should not count toward match."""
    current = {"rsi_bucket": "unknown", "macd_direction": "unknown", "bb_position": "unknown", "sma_trend": "above_200", "volume_bucket": "normal"}
    hist = [_make_hist({"rsi_bucket": "unknown", "macd_direction": "unknown", "bb_position": "unknown", "sma_trend": "above_200", "volume_bucket": "normal"})]
    result = match_fingerprint(current, hist)
    # Only 2 non-unknown matches → not enough
    assert result["matches"] == 0


# ── Confidence scoring ──

def test_confidence_high_win():
    fp = {"rsi_bucket": "neutral", "macd_direction": "bullish", "bb_position": "middle", "sma_trend": "above_200", "volume_bucket": "normal"}
    hist = [
        _make_hist(fp, "win"),
        _make_hist(fp, "win"),
        _make_hist(fp, "win"),
        _make_hist(fp, "win"),
        _make_hist(fp, "loss"),
    ]
    result = match_fingerprint(fp, hist)
    assert result["confidence"] == "high_win"
    assert result["win_rate"] == 0.8


def test_confidence_high_loss():
    fp = {"rsi_bucket": "neutral", "macd_direction": "bullish", "bb_position": "middle", "sma_trend": "above_200", "volume_bucket": "normal"}
    hist = [
        _make_hist(fp, "loss"),
        _make_hist(fp, "loss"),
        _make_hist(fp, "loss"),
        _make_hist(fp, "loss"),
        _make_hist(fp, "win"),
    ]
    result = match_fingerprint(fp, hist)
    assert result["confidence"] == "high_loss"


def test_confidence_mixed():
    fp = {"rsi_bucket": "neutral", "macd_direction": "bullish", "bb_position": "middle", "sma_trend": "above_200", "volume_bucket": "normal"}
    hist = [
        _make_hist(fp, "win"),
        _make_hist(fp, "loss"),
        _make_hist(fp, "win"),
        _make_hist(fp, "loss"),
    ]
    result = match_fingerprint(fp, hist)
    assert result["confidence"] == "mixed"


def test_confidence_no_scored():
    """All breakeven trades → no_scored."""
    fp = {"rsi_bucket": "neutral", "macd_direction": "bullish", "bb_position": "middle", "sma_trend": "above_200", "volume_bucket": "normal"}
    hist = [_make_hist(fp, "breakeven")]
    result = match_fingerprint(fp, hist)
    assert result["confidence"] == "no_scored"


# ── Full analysis ──

def test_analyze_trade_memory_no_tech_data():
    """Should handle missing technical data gracefully."""
    mock_env = {"module": "technical_signals", "status": "missing", "data": {"results": []}, "error_message": None, "generated_at": None}
    with patch("modules.trade_memory.load_envelope", return_value=mock_env), \
         patch("modules.trade_memory.get_all_tickers", return_value=["NVDA"]), \
         patch("modules.trade_memory.build_historical_fingerprints", return_value=[]), \
         patch("modules.trade_memory.MEMORY_PATH", Path("/tmp/test_memory.json")):
        data = analyze_trade_memory()
        assert len(data["results"]) == 1
        assert data["results"][0]["signal"] == "No technical data available"


# ── Output envelope ──

def test_main_creates_output(tmp_path):
    """main() should create trade_memory.json."""
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    outputs = tmp_path / "data" / "outputs"
    outputs.mkdir(parents=True)
    config = tmp_path / "config"
    config.mkdir()
    (config / "watchlist.json").write_text(json.dumps({"tickers": ["NVDA"]}))
    (config / "portfolio.json").write_text(json.dumps({"holdings": []}))

    mock_env = {"module": "technical_signals", "status": "success", "data": {"results": [
        {"ticker": "NVDA", "rsi_14": 50.0, "macd_signal": "neutral", "bb_position": "middle", "sma_trend": "above_200", "volume_ratio": 1.0},
    ]}, "error_message": None, "generated_at": "2026-03-01T08:00:00-07:00"}

    with patch("modules.trade_memory.PROJECT_ROOT", tmp_path), \
         patch("lib.data_envelope.PROCESSED_DIR", processed), \
         patch("modules.trade_memory.load_envelope", return_value=mock_env), \
         patch("modules.trade_memory.MEMORY_PATH", tmp_path / "data" / "processed" / "trade_memory_cache.json"):
        main()

    output_file = processed / "trade_memory.json"
    assert output_file.exists()
    envelope = json.loads(output_file.read_text())
    assert envelope["module"] == "trade_memory"
    assert envelope["status"] in ("success", "error")
