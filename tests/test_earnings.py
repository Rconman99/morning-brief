"""Tests for earnings module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from unittest.mock import patch, MagicMock

from modules.earnings import (
    analyze_transcript,
    analyze_transcript_regex,
    analyze_transcript_ai,
    _validate_ai_response,
    main,
)
import modules.earnings

HAS_ANTHROPIC = getattr(modules.earnings, 'HAS_ANTHROPIC', False)


SAMPLE_TRANSCRIPT = """
We are confident in our growth trajectory. We have decided to invest heavily in AI.
We will continue to expand internationally. Our plan is to reach profitability by Q3.
However, we anticipate some headwinds from regulatory costs. We expect slower growth
in the near term. We believe the market may correct. We might face challenges from
competitors. There could be risk from supply chain disruptions. Potentially lower
margins ahead due to uncertainty in consumer spending. The pressure on hardware
margins is a challenge we are monitoring. We hope to offset this with services growth.
"""

MOCK_AI_RESPONSE = json.dumps({
    "tone_score": 1.5,
    "confidence_score": 0.65,
    "hedge_count": 7,
    "definitive_count": 4,
    "risk_factors": ["regulatory costs", "supply chain disruptions", "margin pressure"],
    "summary": "Management struck a cautiously optimistic tone with raised guidance. Key risks include regulatory headwinds and supply chain constraints.",
})


# --- Existing regex tests (unchanged) ---

def test_analyze_transcript_counts():
    result = analyze_transcript_regex(SAMPLE_TRANSCRIPT, "TEST")
    assert result["ticker"] == "TEST"
    assert result["definitive_count"] >= 4
    assert result["hedge_count"] >= 6
    assert -5 <= result["tone_score"] <= 5


def test_analyze_transcript_risk_factors():
    result = analyze_transcript_regex(SAMPLE_TRANSCRIPT, "TEST")
    assert len(result["risk_factors"]) >= 1


def test_analyze_transcript_confidence_range():
    result = analyze_transcript_regex(SAMPLE_TRANSCRIPT, "TEST")
    assert 0 <= result["confidence_score"] <= 1


def test_main_creates_output(tmp_path):
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    outputs = tmp_path / "data" / "outputs"
    outputs.mkdir(parents=True)

    # Create config
    config = tmp_path / "config"
    config.mkdir()
    (config / "watchlist.json").write_text(json.dumps({
        "tickers": ["TEST"],
        "comparison_pairs": [],
        "earnings_watch": ["TEST"],
    }))

    # Create sample transcript
    sample = tmp_path / "data" / "sample"
    sample.mkdir(parents=True)
    (sample / "earnings_transcript.txt").write_text(SAMPLE_TRANSCRIPT)

    with patch("modules.earnings.PROJECT_ROOT", tmp_path), \
         patch("lib.data_envelope.PROCESSED_DIR", processed):
        main()

    output_file = processed / "earnings_tone.json"
    assert output_file.exists()
    envelope = json.loads(output_file.read_text())
    assert envelope["module"] == "earnings_tone"
    assert envelope["status"] in ("success", "partial")
    assert len(envelope["data"]["results"]) >= 1


# --- AI analysis tests ---

@pytest.mark.skipif(not HAS_ANTHROPIC, reason="anthropic SDK not installed")
def test_analyze_transcript_ai_mock():
    """Mock Anthropic API and verify JSON parsing + output schema."""
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text=MOCK_AI_RESPONSE)]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message

    with patch("modules.earnings.anthropic") as mock_anthropic, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        mock_anthropic.Anthropic.return_value = mock_client
        result = analyze_transcript_ai(SAMPLE_TRANSCRIPT, "TEST")

    assert result["ticker"] == "TEST"
    assert result["tone_score"] == 1.5
    assert result["confidence_score"] == 0.65
    assert result["hedge_count"] == 7
    assert result["definitive_count"] == 4
    assert len(result["risk_factors"]) == 3
    assert isinstance(result["summary"], str)


@pytest.mark.skipif(not HAS_ANTHROPIC, reason="anthropic SDK not installed")
def test_analyze_transcript_ai_handles_code_fences():
    """Verify that markdown code fences around JSON are stripped."""
    fenced_response = f"```json\n{MOCK_AI_RESPONSE}\n```"
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text=fenced_response)]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message

    with patch("modules.earnings.anthropic") as mock_anthropic, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        mock_anthropic.Anthropic.return_value = mock_client
        result = analyze_transcript_ai(SAMPLE_TRANSCRIPT, "TEST")

    assert result["ticker"] == "TEST"
    assert result["tone_score"] == 1.5


@pytest.mark.skipif(not HAS_ANTHROPIC, reason="anthropic SDK not installed")
def test_analyze_transcript_ai_fallback_on_api_error():
    """API exception triggers fallback to regex analysis."""
    with patch("modules.earnings.anthropic") as mock_anthropic, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        mock_anthropic.Anthropic.return_value.messages.create.side_effect = Exception("API down")
        result = analyze_transcript(SAMPLE_TRANSCRIPT, "TEST")

    # Should get regex results (same as analyze_transcript_regex)
    regex_result = analyze_transcript_regex(SAMPLE_TRANSCRIPT, "TEST")
    assert result["hedge_count"] == regex_result["hedge_count"]
    assert result["definitive_count"] == regex_result["definitive_count"]
    assert result["tone_score"] == regex_result["tone_score"]


@pytest.mark.skipif(not HAS_ANTHROPIC, reason="anthropic SDK not installed")
def test_analyze_transcript_ai_fallback_on_bad_json():
    """Invalid JSON from API triggers fallback to regex."""
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="This is not JSON at all")]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message

    with patch("modules.earnings.anthropic") as mock_anthropic, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        mock_anthropic.Anthropic.return_value = mock_client
        result = analyze_transcript(SAMPLE_TRANSCRIPT, "TEST")

    regex_result = analyze_transcript_regex(SAMPLE_TRANSCRIPT, "TEST")
    assert result["tone_score"] == regex_result["tone_score"]


@pytest.mark.skipif(not HAS_ANTHROPIC, reason="anthropic SDK not installed")
def test_analyze_transcript_dispatches_with_key():
    """With API key set, dispatches to AI path."""
    with patch("modules.earnings.analyze_transcript_ai") as mock_ai, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        mock_ai.return_value = {"ticker": "TEST", "tone_score": 2.0,
                                "confidence_score": 0.7, "hedge_count": 3,
                                "definitive_count": 8, "risk_factors": [],
                                "summary": "Bullish tone."}
        result = analyze_transcript(SAMPLE_TRANSCRIPT, "TEST")

    mock_ai.assert_called_once_with(SAMPLE_TRANSCRIPT, "TEST")
    assert result["tone_score"] == 2.0


def test_analyze_transcript_dispatches_without_key():
    """Without API key, dispatches to regex path."""
    with patch("modules.earnings.analyze_transcript_ai") as mock_ai, \
         patch.dict("os.environ", {}, clear=True):
        result = analyze_transcript(SAMPLE_TRANSCRIPT, "TEST")

    mock_ai.assert_not_called()
    assert result["ticker"] == "TEST"
    assert -5 <= result["tone_score"] <= 5


def test_validate_ai_response_valid():
    """Valid response passes validation."""
    data = json.loads(MOCK_AI_RESPONSE)
    assert _validate_ai_response(data) is True


def test_validate_ai_response_missing_field():
    """Missing required field fails validation."""
    data = json.loads(MOCK_AI_RESPONSE)
    del data["summary"]
    assert _validate_ai_response(data) is False


def test_validate_ai_response_out_of_range():
    """Tone score out of [-5, 5] range fails validation."""
    data = json.loads(MOCK_AI_RESPONSE)
    data["tone_score"] = 10.0
    assert _validate_ai_response(data) is False
