"""Earnings transcript tone analysis.

Requires manual transcript input. No free API for transcripts.
Place transcripts at data/raw/earnings/{TICKER}_transcript.txt
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
import re

from lib.data_envelope import create_envelope, save_envelope

logger = logging.getLogger(__name__)

HEDGE_PHRASES = [
    "we anticipate", "we expect", "we believe", "we hope",
    "potentially", "may", "might", "could", "uncertain",
]

DEFINITIVE_PHRASES = [
    "we will", "we are confident", "we are committed",
    "we have decided", "our plan is", "we are certain",
]

RISK_KEYWORDS = ["risk", "headwind", "challenge", "uncertainty", "decline", "pressure"]


def setup_logging():
    log_dir = PROJECT_ROOT / "data" / "outputs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(log_dir / "run.log", mode="a"),
            ],
        )


def analyze_transcript(text: str, ticker: str) -> dict:
    """Analyze a single earnings transcript for tone and risk factors."""
    text_lower = text.lower()

    hedge_count = sum(text_lower.count(phrase) for phrase in HEDGE_PHRASES)
    definitive_count = sum(text_lower.count(phrase) for phrase in DEFINITIVE_PHRASES)

    confidence_score = definitive_count / max(1, definitive_count + hedge_count)
    tone_score = round((confidence_score * 10) - 5, 1)

    # Extract risk factor sentences
    sentences = re.split(r'[.!?]+', text)
    risk_factors = []
    for sentence in sentences:
        s_lower = sentence.lower().strip()
        if any(kw in s_lower for kw in RISK_KEYWORDS):
            clean = sentence.strip()
            if len(clean) > 10:
                risk_factors.append(clean)

    # Generate summary
    if tone_score > 2:
        tone_label = "strongly confident"
    elif tone_score > 0:
        tone_label = "cautiously optimistic"
    elif tone_score > -2:
        tone_label = "mixed"
    else:
        tone_label = "cautious/hedging"

    summary = (
        f"{ticker} management struck a {tone_label} tone with {definitive_count} definitive "
        f"and {hedge_count} hedging phrases. "
        f"Identified {len(risk_factors)} risk-related statements in the call."
    )

    return {
        "ticker": ticker,
        "hedge_count": hedge_count,
        "definitive_count": definitive_count,
        "confidence_score": round(confidence_score, 4),
        "tone_score": tone_score,
        "risk_factors": risk_factors[:10],
        "summary": summary,
    }


def main():
    setup_logging()
    logger.info("=== Earnings Module ===")

    watchlist_path = PROJECT_ROOT / "config" / "watchlist.json"
    if not watchlist_path.exists():
        envelope = create_envelope("earnings_tone", {}, status="error", error="watchlist.json not found")
        save_envelope(envelope, "earnings_tone.json")
        return

    watchlist = json.loads(watchlist_path.read_text())
    earnings_watch = watchlist.get("earnings_watch", [])

    results = []
    transcripts_found = 0

    for ticker in earnings_watch:
        transcript_path = PROJECT_ROOT / "data" / "raw" / "earnings" / f"{ticker}_transcript.txt"
        if transcript_path.exists():
            text = transcript_path.read_text()
            result = analyze_transcript(text, ticker)
            results.append(result)
            transcripts_found += 1
            logger.info("Analyzed %s: tone_score=%.1f", ticker, result["tone_score"])
        else:
            logger.info("No transcript for %s", ticker)

    # Fallback to sample transcript
    if transcripts_found == 0:
        sample_path = PROJECT_ROOT / "data" / "sample" / "earnings_transcript.txt"
        if sample_path.exists():
            text = sample_path.read_text()
            result = analyze_transcript(text, "SAMPLE_AAPL")
            results.append(result)
            logger.info("Used sample transcript as fallback")

    status = "success" if results else "error"
    error = None if results else "No transcripts found"
    if results and transcripts_found < len(earnings_watch):
        status = "partial"

    envelope = create_envelope("earnings_tone", {"results": results}, status=status, error=error)
    save_envelope(envelope, "earnings_tone.json")
    logger.info("Earnings analysis complete: %d transcripts analyzed", len(results))


if __name__ == "__main__":
    main()
