"""Weather settlement truth: station, local-standard-time day, inclusive brackets.

Polymarket US daily-high markets settle on the NWS Daily Climate Report (CLI)
for one station. The climate day is local *standard* time all year — daylight
saving does not move midnight. Bracket labels are whole degrees Fahrenheit
with inclusive edges ("62 to 63" contains 62 and 63; "61 or below" contains 61).

This module is pure (no network). `agent/weather_settlement_backtest.py` compares
it to past settlements. `weather_us.py` skips a city only when that backtest
file reports a mismatch, unless an env flag says otherwise.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = PROJECT_ROOT / "data" / "processed" / "weather_settlement_backtest.json"

# slug code -> canonical CLI station. Offsets are standard time, never DST
# (Eastern -5, Central -6, Pacific -8). NYC is Central Park, not LaGuardia/JFK.
# Chicago is Midway (KMDW), not O'Hare (KORD).
CITIES = {
    "mia": {
        "icao": "KMIA",
        "label": "Miami International Airport",
        "lst_offset_hours": -5,
        "aliases": ("kmia", "miami international"),
    },
    "nyc": {
        "icao": "KNYC",
        "label": "Central Park",
        "lst_offset_hours": -5,
        "aliases": ("knyc", "central park"),
    },
    "mdw": {
        "icao": "KMDW",
        "label": "Chicago Midway",
        "lst_offset_hours": -6,
        "aliases": ("kmdw", "midway"),
    },
    "sfo": {
        "icao": "KSFO",
        "label": "San Francisco International",
        "lst_offset_hours": -8,
        "aliases": ("ksfo", "san francisco international"),
    },
    "lax": {
        "icao": "KLAX",
        "label": "Los Angeles International",
        "lst_offset_hours": -8,
        "aliases": ("klax", "los angeles international"),
    },
}

# Names that mean a *different* station than the one these markets use.
CONFLICT_NAMES = {
    "mia": ("fort lauderdale", "opa-locka", "opa locka", "kendall-tamiami", "kendall tamiami"),
    "nyc": ("laguardia", "la guardia", "jfk", "kennedy", "newark"),
    "mdw": ("o'hare", "ohare", "o’hare"),
    "sfo": ("oakland", "san jose"),
    "lax": ("burbank", "santa monica", "long beach", "john wayne"),
}

# Airport codes we treat as a station claim. Anything else in the rules is ignored
# so a random "k"-word cannot look like a mismatch.
WATCH_ICAO = {
    "kmia", "knyc", "kmdw", "ksfo", "klax",
    "kjfk", "klga", "kewr", "kord", "koak", "ksjc", "kbur", "ksmo", "kfll",
    "kmco", "kbos", "kdca", "kphl", "katl", "ksea", "kden", "klgb", "ksna",
    "kcqt", "kpwk", "kopf",
}

_RULE_KEYS = (
    "rules", "description", "resolutionRules", "marketRules", "rulesPrimary",
    "rulesSecondary", "settlementSource", "resolutionSource", "finePrint",
    "outcomeDescription", "rulesText",
)


def lst_timezone(offset_hours: int) -> timezone:
    """Fixed-offset zone. Not America/New_York — that zone observes DST."""
    return timezone(timedelta(hours=int(offset_hours)))


def climate_day(moment: datetime, offset_hours: int) -> date:
    """NWS climate-report date for an instant, in local standard time.

    During daylight saving, civil local midnight is an hour earlier than the
    climate day. 04:30 UTC on a July morning is already the next calendar date
    in Eastern Daylight Time, and still the previous climate date in EST.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(lst_timezone(offset_hours)).date()


def climate_window_utc(day: date, offset_hours: int) -> tuple[datetime, datetime]:
    """[start, end) of the climate day, in UTC. `day` is the LST report date."""
    tz = lst_timezone(offset_hours)
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def parse_bracket(title: str) -> tuple[float, float] | None:
    """Inclusive whole-degree range from a market outcome title.

    "62 to 63" -> (62, 63), "61 or below" -> (-inf-ish, 61), "70 or above" -> (70, +inf-ish).
    """
    t = (title or "").lower().strip()
    m = re.match(r"^(-?\d+)\s*to\s*(-?\d+)$", t)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.match(r"^(-?\d+)\s*or below$", t)
    if m:
        return -1e6, float(m.group(1))
    m = re.match(r"^(-?\d+)\s*or above$", t)
    if m:
        return float(m.group(1)), 1e6
    return None


def parse_bracket_slug(slug: str) -> tuple[float, float] | None:
    """Fallback when the title is missing.

    Polymarket US labels a two-degree bracket `gte93lt94` for "93 to 94"
    (both ends inclusive). The `lt` token is the upper label, not an exclusive
    bound — otherwise 94°F would fall in a gap between brackets.
    """
    m = re.search(r"gte(-?\d+)lt(-?\d+)", (slug or "").lower())
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    lo, hi = (a, b) if a <= b else (b, a)
    return float(lo), float(hi)


def bracket_of(market: dict) -> tuple[float, float] | None:
    """Title wins. The slug is only a fallback so a missing title can still be scored."""
    parsed = parse_bracket(market.get("title") or "")
    if parsed:
        return parsed
    return parse_bracket_slug(market.get("slug") or "")


def whole_degree_high(value) -> int | None:
    """CLI highs are whole °F. Reject values that are not already on an integer."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return int(value)


def contains_high(bracket: tuple[float, float], high: int) -> bool:
    """Inclusive on both ends. 63 is inside "62 to 63" and outside "64 to 65"."""
    lo, hi = bracket
    return lo <= high <= hi


def brackets_tile(brackets: list[tuple[float, float]]) -> bool:
    """True when brackets cover every integer: a tail below, contiguous ranges, a tail above."""
    bks = sorted(brackets)
    if not bks or bks[0][0] > -1e5 or bks[-1][1] < 1e5:
        return False
    return all(abs(bks[i + 1][0] - (bks[i][1] + 1)) < 1e-9 for i in range(len(bks) - 1))


def rules_blob(market: dict) -> str:
    """Concatenate any rules-like strings the exchange attached to this market."""
    if not isinstance(market, dict):
        return ""
    parts: list[str] = []
    for key in _RULE_KEYS:
        val = market.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val)
    for key in ("event", "metadata"):
        sub = market.get(key)
        if isinstance(sub, dict):
            nested = rules_blob(sub)
            if nested:
                parts.append(nested)
    return "\n".join(parts)


def _alias_in(text: str, alias: str) -> bool:
    if len(alias) <= 4:
        return re.search(rf"\b{re.escape(alias)}\b", text) is not None
    return alias in text


def station_matches_rules(text: str | None, city: str) -> bool | None:
    """True if rules name our station, False if they name a different one, None if unstated.

    Mentioning the right station wins, even if a rejected alternative is also
    named ("Central Park (KNYC), not LaGuardia"). A rules blurb that only names
    the wrong station is a mismatch. No station mention is "unknown", not a failure.
    """
    if city not in CITIES:
        return None
    if not text or not str(text).strip():
        return None
    low = str(text).lower()
    spec = CITIES[city]
    ours = spec["icao"].lower()
    icaos = set(re.findall(r"\b(k[a-z]{3})\b", low)) & WATCH_ICAO
    named = ours in icaos or any(_alias_in(low, a) for a in spec["aliases"])
    if named:
        return True
    other = icaos - {ours}
    name_conflict = any(n in low for n in CONFLICT_NAMES.get(city, ()))
    if other or name_conflict:
        return False
    return None


def table_bad_codes(stations: dict) -> set[str]:
    """City codes whose (ICAO, LST offset) disagree with CITIES, plus unknown codes."""
    bad = set()
    for code, row in (stations or {}).items():
        spec = CITIES.get(code)
        if not spec or len(row) < 4:
            bad.add(code)
            continue
        icao, _lat, _lon, off = row[0], row[1], row[2], row[3]
        if icao != spec["icao"] or int(off) != spec["lst_offset_hours"]:
            bad.add(code)
    for code in CITIES:
        if code not in (stations or {}):
            bad.add(code)
    return bad


def load_backtest_report(path: Path | None = None) -> dict | None:
    p = Path(path) if path else REPORT_PATH
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def skip_reasons(stations: dict | None = None, report: dict | None = None, load: bool = True) -> dict[str, str]:
    """Why a city should not receive weather_us proposals. Empty means price it.

    Default: skip only cities the backtest file lists under skip_cities.
    A synthetic self-check (source == "synthetic") never skips anyone.
    WX_SKIP_MISMATCH_CITIES=0 ignores the file. WX_SKIP_CITIES=lax,nyc always skips those.
    A station-table disagreement is always skipped — that is a code bug, not a backtest.
    """
    if report is None and load:
        report = load_backtest_report()
    reasons: dict[str, str] = {}
    flag = os.environ.get("WX_SKIP_MISMATCH_CITIES", "1").strip().lower()
    if flag not in ("0", "false", "no", "off") and report and report.get("source") != "synthetic":
        for code in report.get("skip_cities") or []:
            reasons[str(code).lower()] = "settlement backtest reported a mismatch"
    for code in os.environ.get("WX_SKIP_CITIES", "").split(","):
        code = code.strip().lower()
        if code:
            reasons[code] = "listed in WX_SKIP_CITIES"
    if stations is not None:
        for code in table_bad_codes(stations):
            reasons[code] = "station map does not match canonical LST stations (KMIA/KNYC/KMDW/KSFO/KLAX)"
    return reasons


def evaluate_group(markets: list[dict], cli_high) -> dict:
    """Compare one city-day's settled brackets with the CLI high.

    `counted` is False when the book is incomplete, the CLI high is missing, or
    the settlement is a void. Those days do not make a city look mismatched.
    """
    city = (markets[0].get("city") if markets else "") or ""
    day = (markets[0].get("date") if markets else "") or ""
    parsed = []
    for m in markets:
        b = bracket_of(m)
        if b:
            parsed.append((m, b))
    rules_flags = [station_matches_rules(m.get("rules") or rules_blob(m), city) for m in markets]
    if any(v is False for v in rules_flags):
        rules = "mismatch"
    elif any(v is True for v in rules_flags):
        rules = "match"
    else:
        rules = "unknown"

    base = {
        "city": city,
        "date": day,
        "station": (CITIES.get(city) or {}).get("icao"),
        "cli_high": None,
        "computed_slug": None,
        "resolved_slug": None,
        "computed_title": None,
        "resolved_title": None,
        "rules": rules,
        "match": False,
        "counted": False,
        "reason": "incomplete",
    }
    if not parsed or not brackets_tile([b for _, b in parsed]):
        base["reason"] = "incomplete"
        return base
    if any(m.get("settlement") is None for m, _ in parsed):
        base["reason"] = "no_settlement"
        return base
    high = whole_degree_high(cli_high) if cli_high is not None else None
    if high is None:
        base["reason"] = "no_cli"
        return base
    base["cli_high"] = high

    hits = [(m, b) for m, b in parsed if contains_high(b, high)]
    if len(hits) == 1:
        base["computed_slug"] = hits[0][0].get("slug")
        base["computed_title"] = hits[0][0].get("title") or hits[0][0].get("slug")
    else:
        base["counted"] = True
        base["reason"] = "gap_or_overlap"
        base["match"] = False
        return base

    partial = [m for m, _ in parsed if 0.001 < float(m["settlement"]) < 0.999]
    yes = [m for m, _ in parsed if float(m["settlement"]) >= 0.999]
    if partial and len(yes) != 1:
        base["reason"] = "void_or_partial"
        return base
    if len(yes) != 1:
        base["counted"] = True
        base["reason"] = "ambiguous_or_no_yes"
        return base
    base["resolved_slug"] = yes[0].get("slug")
    base["resolved_title"] = yes[0].get("title") or yes[0].get("slug")
    base["counted"] = True
    if rules == "mismatch":
        base["reason"] = "rules"
        base["match"] = False
        return base
    if base["computed_slug"] == base["resolved_slug"]:
        base["match"] = True
        base["reason"] = "ok"
    else:
        base["reason"] = "bracket"
        base["match"] = False
    return base


def assemble_report(days: list[dict], source: str) -> dict:
    """Per-city match rates. skip_cities lists any city with a counted mismatch."""
    by: dict[str, list] = {c: [] for c in CITIES}
    for row in days:
        by.setdefault(row.get("city"), []).append(row)
    cities = {}
    skip = []
    for code, rows in by.items():
        judged = [r for r in rows if r.get("counted")]
        matches = sum(1 for r in judged if r.get("match"))
        mismatches = len(judged) - matches
        cities[code] = {
            "station": (CITIES.get(code) or {}).get("icao"),
            "n": len(judged),
            "matches": matches,
            "mismatches": mismatches,
            "match_rate": (matches / len(judged)) if judged else None,
            "examples": [r for r in judged if not r.get("match")][:5],
        }
        if mismatches > 0:
            skip.append(code)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "cities": cities,
        "skip_cities": skip,
        "days": days,
    }


def run_from_inputs(markets: list[dict], highs: dict) -> list[dict]:
    """Group markets by city+date and score each group against CLI highs.

    `highs` is {ICAO or city code: {YYYY-MM-DD: whole-degree high}}.
    """
    groups: dict[tuple, list] = {}
    for m in markets:
        key = (m.get("city"), m.get("date"))
        groups.setdefault(key, []).append(m)
    days = []
    for (city, day), group in sorted(groups.items()):
        station = (CITIES.get(city) or {}).get("icao", "")
        table = highs.get(station) or highs.get(city) or {}
        days.append(evaluate_group(group, table.get(day)))
    return days


def format_report(report: dict) -> str:
    lines = [f"Weather settlement backtest ({report.get('source')})"]
    if report.get("note"):
        lines.append(report["note"])
    if report.get("source") == "synthetic":
        lines.append(
            "  matcher: " + (
                "ok — a correct CLI high matched, and a planted wrong YES was flagged"
                if report.get("ok") else "FAILED"
            )
        )
        lines.append("Synthetic result is not used to skip cities.")
        return "\n".join(lines)
    for code, row in (report.get("cities") or {}).items():
        n = row.get("n") or 0
        matches = row.get("matches") or 0
        rate = row.get("match_rate")
        rate_s = f"{rate * 100:.0f}%" if rate is not None else "n/a"
        flag = "  SKIP" if code in (report.get("skip_cities") or []) else ""
        lines.append(f"  {code:<4} {row.get('station') or '-':<5} {matches}/{n:<4} {rate_s}{flag}")
    if report.get("skip_cities"):
        lines.append("skip cities: " + ", ".join(report["skip_cities"]))
    else:
        lines.append("skip cities: (none)")
    return "\n".join(lines)
