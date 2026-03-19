"""File-based cache for API responses."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import hashlib
import logging
from datetime import datetime, timezone

import pandas as pd

logger = logging.getLogger(__name__)

CACHE_DIR = PROJECT_ROOT / "data" / "raw"


def make_cache_key(source: str, endpoint: str, params: dict) -> str:
    """Generate a deterministic cache key from source, endpoint, and params."""
    param_str = json.dumps(params, sort_keys=True)
    hash_val = hashlib.sha256(param_str.encode()).hexdigest()[:12]
    date_str = datetime.now().strftime("%Y%m%d")
    return f"{source}_{endpoint}_{hash_val}_{date_str}"


def get_cached(key: str, max_age_hours: int = 24) -> dict | None:
    """Return cached data if fresh enough, else None."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        cached_time = datetime.fromisoformat(data.get("_cached_at", "2000-01-01T00:00:00+00:00"))
        age_hours = (datetime.now(timezone.utc) - cached_time.astimezone(timezone.utc)).total_seconds() / 3600
        if age_hours > max_age_hours:
            logger.info("Cache expired for %s (%.1f hours old)", key, age_hours)
            return None
        return data.get("_payload")
    except Exception as e:
        logger.warning("Cache read error for %s: %s", key, e)
        return None


def set_cached(key: str, data: dict) -> Path:
    """Save data to cache file."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{key}.json"
    wrapper = {
        "_cached_at": datetime.now(timezone.utc).isoformat(),
        "_payload": data,
    }
    path.write_text(json.dumps(wrapper, indent=2, default=str))
    logger.info("Cached data to %s", path)
    return path


# --- Ticker-level cache (shares data across modules for the same ticker/day) ---

def _ticker_cache_key(ticker: str, data_type: str) -> str:
    """Build a ticker cache key: ticker_{TICKER}_{data_type}_{YYYYMMDD}"""
    date_str = datetime.now().strftime("%Y%m%d")
    return f"ticker_{ticker}_{data_type}_{date_str}"


def get_ticker_cache(ticker: str, data_type: str, max_age_hours: int = 12) -> dict | pd.DataFrame | None:
    """Get cached ticker data. data_type: 'price_history', 'info', 'options'"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = _ticker_cache_key(ticker, data_type)
    path = CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        cached_time = datetime.fromisoformat(raw.get("_cached_at", "2000-01-01T00:00:00+00:00"))
        age_hours = (datetime.now(timezone.utc) - cached_time.astimezone(timezone.utc)).total_seconds() / 3600
        if age_hours > max_age_hours:
            logger.info("Ticker cache expired for %s (%.1f hours old)", key, age_hours)
            return None
        payload = raw.get("_payload")
        # Deserialize DataFrames that were stored as JSON strings
        if raw.get("_is_dataframe"):
            return pd.read_json(payload)
        return payload
    except Exception as e:
        logger.warning("Ticker cache read error for %s: %s", key, e)
        return None


def set_ticker_cache(ticker: str, data_type: str, data) -> None:
    """Cache ticker data for the day."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = _ticker_cache_key(ticker, data_type)
    path = CACHE_DIR / f"{key}.json"
    is_df = isinstance(data, pd.DataFrame)
    wrapper = {
        "_cached_at": datetime.now(timezone.utc).isoformat(),
        "_is_dataframe": is_df,
        "_payload": data.to_json() if is_df else data,
    }
    path.write_text(json.dumps(wrapper, indent=2, default=str))
    logger.info("Ticker cache saved to %s", path)
