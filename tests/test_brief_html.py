"""Tests for the HTML dashboard generator."""
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from modules.brief_html import generate_html


class TestGenerateHTML:
    """Tests for the generate_html() function."""

    def test_returns_valid_html(self, mock_envelopes, tmp_path):
        """generate_html() returns a string containing valid HTML structure."""
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        html = generate_html(processed_dir=mock_envelopes, outputs_dir=outputs)
        assert isinstance(html, str)
        assert "<html" in html
        assert "</html>" in html
        assert "<head>" in html
        assert "<body>" in html

    def test_learning_panel_present(self, mock_envelopes, tmp_path):
        """Learning system slide-out panel is injected into HTML."""
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        html = generate_html(processed_dir=mock_envelopes, outputs_dir=outputs)
        assert "learn-panel" in html
        assert "learn-overlay" in html

    def test_learning_concepts_json_injected(self, mock_envelopes, tmp_path):
        """LEARN_CONCEPTS script tag is present with JSON data."""
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        html = generate_html(processed_dir=mock_envelopes, outputs_dir=outputs)
        assert "LEARN_CONCEPTS" in html

    def test_data_concept_attributes(self, mock_envelopes, tmp_path):
        """data-concept attributes are present for inline learning links."""
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        html = generate_html(processed_dir=mock_envelopes, outputs_dir=outputs)
        assert "data-concept" in html

    def test_all_sections_render(self, mock_envelopes, tmp_path):
        """All major sections render with section-title class."""
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        html = generate_html(processed_dir=mock_envelopes, outputs_dir=outputs)
        expected_sections = [
            "Risk Dashboard",
            "Watchlist Verdicts",
            "Position Sizing",
            "Opportunity Scanner",
            "Trade Memory",
            "Insider Activity",
            "Scenario Valuations",
            "Verdict Scorecard",
            "Portfolio Holdings",
            "Trade Journal",
            "Earnings Tone",
            "Valuation Comparisons",
            "Technical Signals",
            "News Sentiment",
            "Options Analysis",
        ]
        for section in expected_sections:
            assert section in html, f"Section '{section}' not found in HTML output"

    def test_verdict_cards_render(self, mock_envelopes, tmp_path):
        """Verdict cards render for tickers in mock data."""
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        html = generate_html(processed_dir=mock_envelopes, outputs_dir=outputs)
        # Mock portfolio has NVDA, AAPL, VOO — verdict cards should reference them
        assert "NVDA" in html
        assert "AAPL" in html

    def test_module_status_indicators(self, mock_envelopes, tmp_path):
        """Module status indicators (success/error/missing) are present."""
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        html = generate_html(processed_dir=mock_envelopes, outputs_dir=outputs)
        # All mock envelopes have status "success", so we should see success indicators
        assert "status-pill" in html or "success" in html.lower()

    def test_returns_nonempty_string(self, mock_envelopes, tmp_path):
        """generate_html() returns a substantial HTML string (file writing is done by main())."""
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        html = generate_html(processed_dir=mock_envelopes, outputs_dir=outputs)
        # generate_html() returns the string; main() writes to disk
        assert len(html) > 5000, "Expected substantial HTML output"

    def test_disclaimer_present(self, mock_envelopes, tmp_path):
        """Legal disclaimer is present in the HTML output."""
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        html = generate_html(processed_dir=mock_envelopes, outputs_dir=outputs)
        assert "not financial advice" in html.lower() or "trading decisions" in html.lower()

    def test_css_styles_included(self, mock_envelopes, tmp_path):
        """CSS styles are included inline (self-contained HTML)."""
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        html = generate_html(processed_dir=mock_envelopes, outputs_dir=outputs)
        assert "<style>" in html
        assert "--bg-primary" in html  # CSS custom property from the theme
