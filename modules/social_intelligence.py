"""Social Intelligence: runs last30days v3 for trading-relevant topics.

Searches Reddit, X, YouTube, HN, Polymarket across configurable topics
(default: Bitcoin, Fed rates, oil) and extracts sentiment signals,
top community takes, and engagement-weighted insights for the morning brief.

Requires: last30days v3 installed at ~/.claude/skills/last30days/
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
import subprocess
from datetime import datetime, date

from lib.data_envelope import create_envelope, save_envelope
from lib.cache import get_cached, set_cached

logger = logging.getLogger(__name__)

# last30days v3 script location
LAST30DAYS_SCRIPT = Path.home() / ".claude" / "skills" / "last30days" / "scripts" / "last30days.py"

# Python 3.12+ required by last30days v3
PYTHON_CANDIDATES = ["python3.14", "python3.13", "python3.12"]

# Default topics to research each morning — configurable via config/settings.json
DEFAULT_TOPICS = [
    "Bitcoin price prediction",
    "Federal Reserve interest rates",
    "oil prices crude",
]


def setup_logging():
    """Call this ONLY inside main(). NEVER at module level."""
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


def find_python() -> str:
    """Find a Python 3.12+ interpreter for last30days v3."""
    import shutil
    for candidate in PYTHON_CANDIDATES:
        path = shutil.which(candidate)
        if path:
            return path
    # Fallback to system python3
    return shutil.which("python3") or "python3"


def run_last30days(topic: str, timeout: int = 120) -> dict:
    """Run last30days v3 for a topic and return parsed JSON output."""
    if not LAST30DAYS_SCRIPT.exists():
        logger.warning("last30days not installed at %s", LAST30DAYS_SCRIPT)
        return {}

    cache_key = f"social_intel_{topic.replace(' ', '_')[:30]}_{date.today().isoformat()}"
    cached = get_cached(cache_key, max_age_hours=6)
    if cached:
        return cached

    python = find_python()
    try:
        result = subprocess.run(
            [python, str(LAST30DAYS_SCRIPT), topic, "--emit=json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(LAST30DAYS_SCRIPT.parent.parent),
        )

        if result.stdout:
            # last30days outputs JSON to stdout, status/progress to stderr
            # Find the JSON object in stdout (may have progress text before it)
            stdout = result.stdout.strip()
            json_start = stdout.find("{")
            if json_start >= 0:
                data = json.loads(stdout[json_start:])
                set_cached(cache_key, data)
                return data

    except subprocess.TimeoutExpired:
        logger.warning("last30days timed out for topic: %s", topic)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse last30days JSON for %s: %s", topic, e)
    except Exception as e:
        logger.warning("last30days failed for %s: %s", topic, e)

    return {}


def extract_social_signals(raw: dict, topic: str) -> dict:
    """Extract trading-relevant signals from last30days JSON output."""
    if not raw:
        return {"topic": topic, "status": "no_data"}

    candidates = raw.get("candidates", [])
    stats = raw.get("stats", {})
    clusters = raw.get("clusters", [])

    # Count by source
    source_counts = {}
    for c in candidates:
        for item in c.get("source_items", []):
            src = item.get("source", "unknown")
            source_counts[src] = source_counts.get(src, 0) + 1

    # Top items by engagement (RRF score from v3 fusion)
    top_items = []
    for c in sorted(candidates, key=lambda x: x.get("rrf_score", 0), reverse=True)[:8]:
        items = c.get("source_items", [])
        if not items:
            continue
        item = items[0]
        engagement = item.get("engagement", {})
        top_items.append({
            "title": c.get("title", "")[:100],
            "source": item.get("source", ""),
            "url": item.get("url", ""),
            "score": round(c.get("rrf_score", 0), 3),
            "upvotes": engagement.get("upvotes") or engagement.get("likes") or engagement.get("score", 0),
            "comments": engagement.get("comments", 0),
            "snippet": (c.get("snippet") or "")[:200],
            "published": item.get("published_at", ""),
        })

    # Extract top Reddit comments (the real alpha)
    reddit_takes = []
    for c in candidates:
        for item in c.get("source_items", []):
            if item.get("source") != "reddit":
                continue
            metadata = item.get("metadata", {})
            top_comments = metadata.get("top_comments", [])
            for comment in top_comments[:2]:
                if isinstance(comment, dict):
                    reddit_takes.append({
                        "text": comment.get("body", "")[:200],
                        "score": comment.get("score", 0),
                        "subreddit": metadata.get("subreddit", ""),
                    })

    reddit_takes.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Sentiment heuristic from titles/snippets
    bullish_words = ["bullish", "moon", "rally", "pump", "surge", "breakout", "accumulate", "buy", "long", "higher", "support"]
    bearish_words = ["bearish", "crash", "dump", "plunge", "sell", "short", "lower", "resistance", "collapse", "fear", "recession"]

    bull_count = 0
    bear_count = 0
    for c in candidates:
        text = (c.get("title", "") + " " + (c.get("snippet") or "")).lower()
        bull_count += sum(1 for w in bullish_words if w in text)
        bear_count += sum(1 for w in bearish_words if w in text)

    total_sentiment = bull_count + bear_count
    if total_sentiment > 0:
        sentiment_ratio = round(bull_count / total_sentiment, 2)
        if sentiment_ratio > 0.65:
            sentiment = "bullish"
        elif sentiment_ratio < 0.35:
            sentiment = "bearish"
        else:
            sentiment = "mixed"
    else:
        sentiment = "neutral"
        sentiment_ratio = 0.5

    return {
        "topic": topic,
        "status": "success",
        "total_results": len(candidates),
        "source_counts": source_counts,
        "sentiment": sentiment,
        "sentiment_ratio": sentiment_ratio,
        "bull_signals": bull_count,
        "bear_signals": bear_count,
        "top_items": top_items,
        "reddit_takes": reddit_takes[:5],
        "clusters": len(clusters),
        "searched_at": datetime.now().astimezone().isoformat(),
    }


def run_social_intelligence() -> dict:
    """Main entry: research all configured topics and aggregate signals."""
    # Load custom topics from settings
    settings_path = PROJECT_ROOT / "config" / "settings.json"
    topics = DEFAULT_TOPICS
    try:
        settings = json.loads(settings_path.read_text())
        custom_topics = settings.get("social_intelligence_topics")
        if custom_topics and isinstance(custom_topics, list):
            topics = custom_topics
    except Exception:
        pass

    results = []
    for topic in topics:
        logger.info("Researching: %s", topic)
        raw = run_last30days(topic)
        signals = extract_social_signals(raw, topic)
        results.append(signals)

    # Overall market mood from all topics
    total_bull = sum(r.get("bull_signals", 0) for r in results)
    total_bear = sum(r.get("bear_signals", 0) for r in results)
    total = total_bull + total_bear
    if total > 0:
        overall_ratio = round(total_bull / total, 2)
        if overall_ratio > 0.65:
            overall_mood = "bullish"
        elif overall_ratio < 0.35:
            overall_mood = "bearish"
        else:
            overall_mood = "mixed"
    else:
        overall_mood = "neutral"
        overall_ratio = 0.5

    return {
        "topics": results,
        "overall_mood": overall_mood,
        "overall_sentiment_ratio": overall_ratio,
        "total_bull_signals": total_bull,
        "total_bear_signals": total_bear,
        "topics_researched": len(results),
        "last30days_version": "3.0.0",
    }


def main():
    setup_logging()
    logger.info("=== Social Intelligence (last30days v3) ===")

    data = run_social_intelligence()

    successful = sum(1 for t in data["topics"] if t.get("status") == "success")
    if successful == 0:
        status = "error"
        error = "No social intelligence data available"
    elif successful < len(data["topics"]):
        status = "partial"
        error = f"{successful}/{len(data['topics'])} topics researched"
    else:
        status = "success"
        error = None

    envelope = create_envelope("social_intelligence", data, status=status, error=error)
    save_envelope(envelope, "social_intelligence.json")

    logger.info("Social intelligence complete: %s mood, %d topics, %d bull / %d bear signals",
                data["overall_mood"], len(data["topics"]),
                data["total_bull_signals"], data["total_bear_signals"])


if __name__ == "__main__":
    main()
