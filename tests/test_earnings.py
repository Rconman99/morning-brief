"""Tests for earnings module."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest
from unittest.mock import patch

from modules.earnings import analyze_transcript, main


SAMPLE_TRANSCRIPT = """
We are confident in our growth trajectory. We have decided to invest heavily in AI.
We will continue to expand internationally. Our plan is to reach profitability by Q3.
However, we anticipate some headwinds from regulatory costs. We expect slower growth
in the near term. We believe the market may correct. We might face challenges from
competitors. There could be risk from supply chain disruptions. Potentially lower
margins ahead due to uncertainty in consumer spending. The pressure on hardware
margins is a challenge we are monitoring. We hope to offset this with services growth.
"""


def test_analyze_transcript_counts():
    result = analyze_transcript(SAMPLE_TRANSCRIPT, "TEST")
    assert result["ticker"] == "TEST"
    assert result["definitive_count"] >= 4
    assert result["hedge_count"] >= 6
    assert -5 <= result["tone_score"] <= 5


def test_analyze_transcript_risk_factors():
    result = analyze_transcript(SAMPLE_TRANSCRIPT, "TEST")
    assert len(result["risk_factors"]) >= 1


def test_analyze_transcript_confidence_range():
    result = analyze_transcript(SAMPLE_TRANSCRIPT, "TEST")
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
