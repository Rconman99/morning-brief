"""Earnings transcript tone analysis.

Requires manual transcript input. No free API for transcripts.
Place transcripts at data/raw/earnings/{TICKER}_transcript.txt

When ANTHROPIC_API_KEY is set, uses Claude for semantic analysis.
Otherwise falls back to regex phrase counting.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
import os
import re

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

from dotenv import load_dotenv

from lib.data_envelope import create_envelope, save_envelope

load_dotenv(PROJECT_ROOT / ".env")

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

EARNINGS_PROMPT = """Analyze this earnings call transcript for {ticker}. Return a JSON object:
{{
  "tone_score": <float -5 to +5, negative=bearish/hedging, positive=bullish/confident>,
  "confidence_score": <float 0 to 1, ratio of confident vs hedging language>,
  "hedge_count": <int, number of hedging/uncertain statements>,
  "definitive_count": <int, number of confident/definitive statements>,
  "risk_factors": [<string>, ...up to 5 key risks mentioned],
  "summary": "<2-3 sentence summary of management tone and key takeaways>"
}}

Scoring guide:
- -5: Extremely bearish, crisis language, major downgrades
- -2 to -1: Cautious hedging, soft guidance, multiple caveats
- 0: Balanced/neutral
- +1 to +2: Cautiously optimistic, raised guidance, confident tone
- +5: Extremely bullish, blowout results, aggressive forward guidance

Focus on: forward guidance strength, management confidence level, risk acknowledgment vs. dismissal, specific vs. vague commitments.
Return ONLY the JSON object, no other text."""

REQUIRED_FIELDS = {
    "tone_score": (int, float),
    "confidence_score": (int, float),
    "hedge_count": (int,),
    "definitive_count": (int,),
    "risk_factors": (list,),
    "summary": (str,),
}


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


def _validate_ai_response(data: dict) -> bool:
    """Validate that AI response has all required fields with correct types."""
    for field, types in REQUIRED_FIELDS.items():
        if field not in data:
            return False
        if not isinstance(data[field], types):
            return False
    if not (-5 <= data["tone_score"] <= 5):
        return False
    if not (0 <= data["confidence_score"] <= 1):
        return False
    return True


def analyze_transcript_regex(text: str, ticker: str) -> dict:
    """Analyze a single earnings transcript using regex phrase counting."""
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


def analyze_transcript_ai(text: str, ticker: str) -> dict:
    """Analyze a single earnings transcript using local Ollama or Claude."""
    from lib.llm import generate_json

    prompt = EARNINGS_PROMPT.format(ticker=ticker)
    full_prompt = f"{prompt}\n\nTranscript:\n{text}"

    parsed = generate_json(full_prompt, max_tokens=1024)
    if parsed is None:
        raise ValueError("LLM returned no parseable JSON")

    if not _validate_ai_response(parsed):
        raise ValueError("AI response failed schema validation")

    # Clamp values to valid ranges
    parsed["tone_score"] = round(max(-5, min(5, float(parsed["tone_score"]))), 1)
    parsed["confidence_score"] = round(max(0, min(1, float(parsed["confidence_score"]))), 4)
    parsed["hedge_count"] = int(parsed["hedge_count"])
    parsed["definitive_count"] = int(parsed["definitive_count"])
    parsed["risk_factors"] = [str(r) for r in parsed["risk_factors"][:10]]
    parsed["summary"] = str(parsed["summary"])
    parsed["ticker"] = ticker

    return parsed


def analyze_transcript(text: str, ticker: str) -> dict:
    """Analyze a single earnings transcript for tone and risk factors.

    Uses local Ollama (free) → Claude Haiku (paid) → regex fallback.
    """
    try:
        result = analyze_transcript_ai(text, ticker)
        logger.info("AI analysis succeeded for %s", ticker)
        return result
    except Exception as e:
        logger.warning("AI analysis failed for %s, falling back to regex: %s", ticker, e)

    return analyze_transcript_regex(text, ticker)


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
