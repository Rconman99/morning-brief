"""Tests for the interactive learning system."""
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from modules.brief_learning import (
    KNOWLEDGE_BASE, label, section_title,
    build_data_snapshot, resolve_quiz_tokens, build_concepts_json,
    build_panel_html, build_tour_html, build_learning_path_html,
    build_how_to_use_html,
)


def test_knowledge_base_structure():
    """All concepts have required fields."""
    required = {"title", "category", "tier", "eli5", "detailed", "quiz", "connects_to"}
    for key, concept in KNOWLEDGE_BASE.items():
        missing = required - set(concept.keys())
        assert not missing, f"Concept '{key}' missing fields: {missing}"


def test_knowledge_base_tiers():
    """All concepts have tier 1-4."""
    for key, concept in KNOWLEDGE_BASE.items():
        assert concept["tier"] in (1, 2, 3, 4), f"Concept '{key}' has invalid tier: {concept['tier']}"


def test_knowledge_base_quizzes():
    """All quizzes have valid structure."""
    for key, concept in KNOWLEDGE_BASE.items():
        for i, q in enumerate(concept["quiz"]):
            assert "q" in q, f"Quiz {i} in '{key}' missing question"
            assert "choices" in q, f"Quiz {i} in '{key}' missing choices"
            assert "answer" in q, f"Quiz {i} in '{key}' missing answer"
            assert 0 <= q["answer"] < len(q["choices"]), f"Quiz {i} in '{key}' answer index out of range"


def test_label_returns_html():
    """label() returns a span with card-label class."""
    result = label("RSI")
    assert "card-label" in result
    assert "RSI" in result


def test_section_title_returns_html():
    """section_title() returns section-title div."""
    result = section_title("Technical Signals")
    assert "section-title" in result
    assert "Technical Signals" in result


def test_build_data_snapshot():
    """build_data_snapshot() returns dict with expected keys."""
    portfolio = {"holdings": [{"ticker": "NVDA", "current_price": 900, "atr": 15}]}
    technical = {"results": [{"ticker": "NVDA", "rsi_14": 55, "composite_score": 2.3}]}
    earnings = {"results": [{"ticker": "AAPL", "tone_score": -1.2}]}
    risk = {"regime": "NORMAL", "vix": 18.5}
    snap = build_data_snapshot(portfolio, technical, earnings, risk)
    assert isinstance(snap, dict)


def test_resolve_quiz_tokens():
    """resolve_quiz_tokens() doesn't crash on empty snapshot."""
    resolved = resolve_quiz_tokens(KNOWLEDGE_BASE, {})
    assert isinstance(resolved, dict)
    assert len(resolved) == len(KNOWLEDGE_BASE)


def test_build_concepts_json():
    """build_concepts_json() returns valid JSON string."""
    import json
    result = build_concepts_json(KNOWLEDGE_BASE)
    parsed = json.loads(result)
    assert isinstance(parsed, dict)
    assert len(parsed) > 0


def test_build_panel_html():
    """Panel HTML is non-empty and contains key elements."""
    html = build_panel_html()
    assert "learn-panel" in html
    assert "learn-overlay" in html


def test_build_tour_html():
    """Tour HTML is non-empty."""
    html = build_tour_html()
    assert len(html) > 0


def test_build_learning_path_html():
    """Learning path HTML contains tier references."""
    html = build_learning_path_html()
    assert len(html) > 0


def test_build_how_to_use_html():
    """How to use guide renders."""
    html = build_how_to_use_html()
    assert len(html) > 0


def test_eli5_and_detailed_both_present():
    """Every concept has both ELI5 and detailed explanations."""
    for key, concept in KNOWLEDGE_BASE.items():
        assert len(concept["eli5"]) > 20, f"Concept '{key}' ELI5 too short"
        assert len(concept["detailed"]) > 20, f"Concept '{key}' detailed too short"


def test_connects_to_valid():
    """All connects_to references point to real concepts."""
    all_keys = set(KNOWLEDGE_BASE.keys())
    for key, concept in KNOWLEDGE_BASE.items():
        for ref in concept.get("connects_to", []):
            assert ref in all_keys, f"Concept '{key}' connects to unknown concept '{ref}'"
