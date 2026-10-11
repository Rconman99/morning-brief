"""Compare settled Polymarket US temperature brackets with IEM CLI highs.

For each city-day whose brackets tile the whole line, the CLI high (whole °F,
inclusive edges, local-standard-time report date) picks one bracket. That
bracket should be the market that resolved YES. A city with any counted
mismatch is listed in data/processed/weather_settlement_backtest.json, and
weather_us skips it on later cycles (WX_SKIP_MISMATCH_CITIES defaults on).

Needs outbound HTTPS (Polymarket US public API + mesonet.agron.iastate.edu).
If the network is down, the script runs a synthetic self-check and does NOT
overwrite a live report (a fake mismatch must not silence a city).

    .venv/bin/python agent/weather_settlement_backtest.py
    .venv/bin/python agent/weather_settlement_backtest.py --fixture path.json
    .venv/bin/python agent/weather_settlement_backtest.py --max-settlements 300

Fixture JSON: {"markets": [...], "highs": {"KMIA": {"2026-07-01": 94}}}
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import logging
from datetime import date

from agent.weather_settlement_check import (
    CITIES,
    REPORT_PATH,
    assemble_report,
    format_report,
    rules_blob,
    run_from_inputs,
)

logger = logging.getLogger(__name__)


def _book_mia(high_title_settles_yes: str, rules: str, high_day: str = "2026-07-01") -> list[dict]:
    """A complete Miami book: below 61, 62-63, 64-65, 66 or above."""
    titles = ["61 or below", "62 to 63", "64 to 65", "66 or above"]
    out = []
    for title in titles:
        slug = f"tc-temp-miahigh-{high_day}-" + title.replace(" ", "")
        out.append({
            "slug": slug,
            "city": "mia",
            "date": high_day,
            "title": title,
            "settlement": 1.0 if title == high_title_settles_yes else 0.0,
            "rules": rules,
        })
    return out


def synthetic_self_check() -> dict:
    """Prove the matcher flags a wrong YES and accepts a right one. Never skips cities."""
    rules = "Resolves on the NWS Daily Climate Report at Miami International Airport (KMIA), local standard time."
    good_markets = _book_mia("62 to 63", rules)
    bad_markets = _book_mia("64 to 65", rules, high_day="2026-07-02")
    highs = {"KMIA": {"2026-07-01": 63, "2026-07-02": 63}}
    days = run_from_inputs(good_markets + bad_markets, highs)
    good = next(d for d in days if d["date"] == "2026-07-01")
    bad = next(d for d in days if d["date"] == "2026-07-02")
    ok = bool(good["match"] and good["counted"] and bad["counted"] and not bad["match"])
    report = assemble_report(days, source="synthetic")
    report["ok"] = ok
    report["skip_cities"] = []
    report["note"] = "Synthetic self-check only (network unavailable or no settled markets). Not used to skip cities."
    # Show the detection in the printed cities, but do not arm the skip list.
    return report


def fetch_settled_temperature_markets(max_pages: int = 20, max_settlements: int = 250) -> tuple[list[dict], dict]:
    """Public market list + settlement + CLI highs. Raises if the venue client fails."""
    from agent import pm_us
    from agent.weather_us import SLUG_RE, cli_highs

    found = []
    seen = set()
    offset = 0
    for _ in range(max_pages):
        page = pm_us.list_markets({
            "limit": 100,
            "offset": offset,
            "closed": True,
            "active": False,
            "orderBy": ["end_date"],
            "orderDirection": "desc",
        }) or []
        if not page:
            break
        for m in page:
            slug = m.get("slug") or ""
            if not slug or slug in seen or not SLUG_RE.match(slug):
                continue
            seen.add(slug)
            found.append(m)
        if len(page) < 100:
            break
        offset += 100

    markets = []
    lookups = 0
    years = set()
    for m in found:
        if lookups >= max_settlements:
            break
        slug = m["slug"]
        code, day = SLUG_RE.match(slug).groups()
        if code not in CITIES:
            continue
        settlement = pm_us.get_settlement(slug)
        lookups += 1
        if settlement is None:
            continue
        blob = rules_blob(m)
        if not blob and lookups < max_settlements:
            detail = pm_us.get_market(slug) or {}
            lookups += 1
            blob = rules_blob(detail) or rules_blob(m)
            if not m.get("title") and detail.get("title"):
                m = {**m, "title": detail.get("title")}
        markets.append({
            "slug": slug,
            "city": code,
            "date": day,
            "title": m.get("title") or "",
            "settlement": settlement,
            "rules": blob,
        })
        years.add(int(day[:4]))

    highs = {}
    if markets:
        year_list = sorted(years) or [date.today().year]
        for code in sorted({m["city"] for m in markets}):
            station = CITIES[code]["icao"]
            highs[station] = cli_highs(station, year_list)
    return markets, highs


def write_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backtest weather bracket settlement against CLI highs")
    ap.add_argument("--fixture", help="JSON file with markets and highs; no network")
    ap.add_argument("--out", help="Report path (default data/processed/weather_settlement_backtest.json)")
    ap.add_argument("--max-pages", type=int, default=20)
    ap.add_argument("--max-settlements", type=int, default=250)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    out = Path(args.out) if args.out else REPORT_PATH

    if args.fixture:
        payload = json.loads(Path(args.fixture).read_text())
        days = run_from_inputs(payload.get("markets") or [], payload.get("highs") or {})
        report = assemble_report(days, source="fixture")
        write_report(report, out)
        print(format_report(report))
        print(f"wrote {out}")
        return 0

    try:
        markets, highs = fetch_settled_temperature_markets(args.max_pages, args.max_settlements)
    except Exception as e:
        logger.warning("settlement backtest network failed: %s", e)
        report = synthetic_self_check()
        print(format_report(report))
        print("No live report written.")
        return 0 if report.get("ok") else 1

    if not markets:
        print("No settled temperature markets found.")
        report = synthetic_self_check()
        print(format_report(report))
        print("No live report written.")
        return 0 if report.get("ok") else 1

    days = run_from_inputs(markets, highs)
    report = assemble_report(days, source="live")
    write_report(report, out)
    print(format_report(report))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
