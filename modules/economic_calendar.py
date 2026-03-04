"""Economic calendar module: FOMC dates, NFP, CPI, PPI, GDP tracking.

Tracks upcoming US economic events and their market impact levels.
Uses FRED API for economic data (optional), falls back to sample data.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

try:
    import requests
except ImportError:
    requests = None

from dotenv import load_dotenv

from lib.data_envelope import create_envelope, save_envelope
from lib.cache import get_cached, set_cached, make_cache_key

load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)

# FOMC meeting dates for 2026 (hardcoded)
FOMC_DATES_2026 = [
    ("2026-01-28", "2026-01-29"),
    ("2026-03-18", "2026-03-19"),
    ("2026-05-06", "2026-05-07"),
    ("2026-06-17", "2026-06-18"),
    ("2026-07-29", "2026-07-30"),
    ("2026-09-16", "2026-09-17"),
    ("2026-10-28", "2026-10-29"),
    ("2026-12-09", "2026-12-10"),
]

# Economic indicators to track from FRED
FRED_SERIES = {
    "CPIAUCSL": {"name": "CPI (All Urban Consumers)", "unit": "%"},
    "UNRATE": {"name": "Unemployment Rate", "unit": "%"},
    "GDPC1": {"name": "Real GDP", "unit": "Billions $"},
    "FEDFUNDS": {"name": "Federal Funds Rate", "unit": "%"},
    "DGS10": {"name": "10-Year Treasury Yield", "unit": "%"},
    "T10YIE": {"name": "10Y Breakeven Inflation", "unit": "%"},
}


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


def get_us_eastern_now():
    """Get current time in US Eastern timezone."""
    return datetime.now(ZoneInfo("America/New_York"))


def get_this_week_range():
    """Return (start_date, end_date) for this week (Mon-Sun)."""
    now = get_us_eastern_now()
    # Monday = 0, Sunday = 6
    days_since_monday = now.weekday()
    monday = (now - timedelta(days=days_since_monday)).date()
    sunday = (monday + timedelta(days=6))
    return monday, sunday


def get_first_friday_of_month(date):
    """Return the first Friday of the month containing the given date."""
    first_day = date.replace(day=1)
    # Friday = 4
    days_until_friday = (4 - first_day.weekday()) % 7
    if days_until_friday == 0:
        days_until_friday = 0
    first_friday = first_day + timedelta(days=days_until_friday)
    return first_friday


def detect_events(today):
    """Detect upcoming economic events this week.

    Returns: list of event dicts with name, date, impact, description
    """
    events = []
    monday, sunday = get_this_week_range()

    # FOMC meetings (check 1 day before for alerts)
    for start_str, end_str in FOMC_DATES_2026:
        start_date = datetime.fromisoformat(start_str).date()
        end_date = datetime.fromisoformat(end_str).date()

        # Alert 1 day before meeting
        alert_start = start_date - timedelta(days=1)
        if monday <= alert_start <= sunday:
            events.append({
                "name": "FOMC Meeting",
                "date": start_str,
                "impact": "high",
                "description": "Federal Reserve interest rate decision and policy statement",
            })
        elif monday <= start_date <= sunday:
            events.append({
                "name": "FOMC Meeting",
                "date": start_str,
                "impact": "high",
                "description": "Federal Reserve interest rate decision and policy statement",
            })

    # NFP (Non-Farm Payroll) - first Friday of month
    first_friday = get_first_friday_of_month(today)
    if monday <= first_friday <= sunday:
        events.append({
            "name": "Non-Farm Payroll (NFP)",
            "date": first_friday.isoformat(),
            "impact": "high",
            "description": "US employment data release",
        })

    # CPI - typically 13th of month
    cpi_day = today.replace(day=13)
    if monday <= cpi_day <= sunday:
        events.append({
            "name": "CPI Release",
            "date": cpi_day.isoformat(),
            "impact": "medium",
            "description": "Consumer Price Index - inflation data",
        })

    # PPI - typically 14th of month
    ppi_day = today.replace(day=14)
    if monday <= ppi_day <= sunday:
        events.append({
            "name": "PPI Release",
            "date": ppi_day.isoformat(),
            "impact": "medium",
            "description": "Producer Price Index - inflation at producer level",
        })

    # GDP - last week of quarter-end months (Mar, Jun, Sep, Dec)
    # Typically third Wednesday or last week
    if today.month in [3, 6, 9, 12]:
        # GDP release is last Friday of the month for quarter-end
        next_month = today.month % 12 + 1
        next_year = today.year if next_month != 1 else today.year + 1
        first_of_next = today.replace(year=next_year, month=next_month, day=1)
        last_day = first_of_next - timedelta(days=1)
        last_friday = last_day - timedelta(days=(last_day.weekday() - 4) % 7)
        if monday <= last_friday <= sunday:
            events.append({
                "name": "GDP Release",
                "date": last_friday.isoformat(),
                "impact": "high",
                "description": "Gross Domestic Product - quarterly economic growth data",
            })

    return sorted(events, key=lambda x: x["date"])


def determine_signal(events):
    """Determine market signal level based on events.

    Returns: (signal_level, signal_detail)
    - "high_impact": FOMC or NFP this week
    - "moderate": CPI, PPI, or GDP this week
    - "low": other events or no events
    - "calm": nothing notable
    """
    if not events:
        return "calm", "No significant economic events this week"

    high_impact_names = {"FOMC Meeting", "Non-Farm Payroll (NFP)"}
    moderate_impact_names = {"CPI Release", "PPI Release", "GDP Release"}

    high_events = [e for e in events if e["name"] in high_impact_names]
    moderate_events = [e for e in events if e["name"] in moderate_impact_names]

    if high_events:
        detail = f"{high_events[0]['name']} {high_events[0]['date']}"
        return "high_impact", detail
    elif moderate_events:
        detail = f"{moderate_events[0]['name']} {moderate_events[0]['date']}"
        return "moderate", detail
    else:
        return "low", "Minor economic data releases"


def fetch_fred_series(series_id: str, api_key: str, observation_start: str = None):
    """Fetch latest observation from FRED API.

    Args:
        series_id: FRED series code (e.g., "CPIAUCSL")
        api_key: FRED API key
        observation_start: optional start date (YYYY-MM-DD)

    Returns: dict with value, date, or empty dict on failure
    """
    if not requests:
        logger.warning("requests library not available, skipping FRED API call")
        return {}

    # Check cache first
    params = {"series_id": series_id, "observation_start": observation_start or ""}
    cache_key = make_cache_key("fred", "series_observations", params)
    cached = get_cached(cache_key, max_age_hours=24)
    if cached:
        logger.info("Using cached FRED data for %s", series_id)
        return cached

    try:
        url = "https://api.stlouisfed.org/fred/series/observations"
        request_params = {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 5,
        }
        if observation_start:
            request_params["observation_start"] = observation_start

        response = requests.get(url, params=request_params, timeout=10)
        response.raise_for_status()
        data = response.json()

        # Extract latest observation
        observations = data.get("observations", [])
        if observations:
            latest = observations[0]
            result = {
                "value": float(latest.get("value", 0)),
                "date": latest.get("date"),
                "series": series_id,
            }
            set_cached(cache_key, result)
            return result

        return {}

    except Exception as e:
        logger.warning("FRED API error for %s: %s", series_id, e)
        return {}


def fetch_recent_indicators(api_key: str = None):
    """Fetch recent economic indicators from FRED.

    Returns: dict mapping indicator names to value/date info
    """
    indicators = {}

    if not api_key or api_key == "demo":
        logger.info("FRED API key not configured, skipping FRED data")
        return indicators

    for series_id, meta in FRED_SERIES.items():
        data = fetch_fred_series(series_id, api_key)
        if data:
            indicators[meta["name"]] = data
            logger.info("Fetched %s: %.2f", meta["name"], data.get("value", 0))

    return indicators


def load_sample_calendar():
    """Load sample economic calendar data."""
    sample_path = PROJECT_ROOT / "data" / "sample" / "economic_calendar.json"
    if sample_path.exists():
        try:
            return json.loads(sample_path.read_text())
        except Exception as e:
            logger.warning("Failed to load sample calendar: %s", e)
    return None


def main():
    """Main entry point."""
    setup_logging()
    logger.info("=== Economic Calendar Module ===")

    today = get_us_eastern_now().date()
    logger.info("Running for date: %s", today)

    # Detect events this week
    events = detect_events(today)
    logger.info("Detected %d events this week", len(events))

    # Determine signal
    signal, signal_detail = determine_signal(events)
    logger.info("Signal: %s - %s", signal, signal_detail)

    # Fetch economic indicators
    fred_key = os.getenv("FRED_API_KEY", "demo")
    recent_indicators = fetch_recent_indicators(fred_key)

    if not recent_indicators and fred_key != "demo":
        logger.warning("FRED API failed, falling back to sample data")

    # Find next FOMC meeting
    fomc_next = None
    for start_str, end_str in FOMC_DATES_2026:
        start_date = datetime.fromisoformat(start_str).date()
        if start_date >= today:
            fomc_next = {
                "date": start_str,
                "days_until": (start_date - today).days,
            }
            break

    # Determine data source
    data_source = "fred_api" if recent_indicators else "sample_data"

    # Build output
    data = {
        "signal": signal,
        "signal_detail": signal_detail,
        "upcoming_events": events,
        "recent_indicators": recent_indicators,
        "fomc_next": fomc_next,
        "data_source": data_source,
        "analyzed_at": datetime.now().astimezone().isoformat(),
    }

    envelope = create_envelope("economic_calendar", data)
    save_envelope(envelope, "economic_calendar.json")

    logger.info("Economic calendar module complete")
    print(f"Signal: {signal}")
    print(f"Events this week: {len(events)}")


if __name__ == "__main__":
    main()
