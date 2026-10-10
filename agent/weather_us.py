"""Weather model for Polymarket US daily-high temperature markets.

Markets: "Highest temperature in <city> on <date>?" with 2-degree brackets
("62 to 63", "61 or below", "70 or above"), settled on the NWS Daily
Climatological Report (CLI) high at one airport/park station.

Model (per city, per lead time):
  1. Forecast daily high = max of Open-Meteo hourly temperature at the station,
     aggregated over the NWS climate day (local STANDARD time midnight-midnight).
  2. Error distribution = this station's own recent forecast errors:
     archived Open-Meteo forecasts (previous-runs API) vs the actual CLI highs
     (Iowa Environmental Mesonet archive), last ~75 days. Empirical residuals,
     kernel-smoothed, so bias and spread are measured, not assumed.
  3. Same day: the NWS observed max so far is a floor (the day's high can't be
     lower), so probability below it is removed and the rest renormalized.
  4. P(bracket) = P(integer high in [lo, hi]).

Trade rule ("high-conviction model" pattern from the leaderboard audit):
  buy the side (YES/NO) whose model probability >= WX_MIN_PROB (0.80) and
  exceeds the price we'd pay by >= WX_MIN_EDGE (0.04) after fees; limit price is
  capped at model_prob - WX_MIN_EDGE, and the executor rests it as a maker bid.

Run (on the droplet; needs outbound HTTPS to open-meteo, IEM, weather.gov):
    .venv/bin/python agent/weather_us.py              # refresh calibration if stale, price markets, write envelope
    .venv/bin/python agent/weather_us.py --print      # also print every bracket's model vs market
    .venv/bin/python agent/weather_us.py --calibrate  # force recalibration + reliability report
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import logging
import math
import os
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import requests

from lib.data_envelope import create_envelope, save_envelope

logger = logging.getLogger(__name__)

# slug code -> (station, lat, lon, UTC offset of LOCAL STANDARD time in hours)
STATIONS = {
    "mia": ("KMIA", 25.7906, -80.3164, -5),
    "nyc": ("KNYC", 40.7789, -73.9692, -5),
    "mdw": ("KMDW", 41.7860, -87.7524, -6),
    "sfo": ("KSFO", 37.6197, -122.3647, -8),
    "lax": ("KLAX", 33.9382, -118.3866, -8),
}
UA = {"User-Agent": "polymarket-agent weather model (contact: repo owner)"}
CAL_PATH = PROJECT_ROOT / "data" / "processed" / "weather_us_calibration.json"
CAL_DAYS = int(os.environ.get("WX_CAL_DAYS", "75"))
CAL_MAX_AGE_H = 20
KERNEL_SD = 0.8           # smoothing on top of empirical residuals (degF)
MIN_SD = {0: 1.2, 1: 1.8}  # floor on spread so a lucky calibration can't make the model overconfident
MIN_PROB = float(os.environ.get("WX_MIN_PROB", "0.80"))
MIN_EDGE = float(os.environ.get("WX_MIN_EDGE", "0.04"))
SLUG_RE = re.compile(r"^tc-temp-([a-z]{3})high-(\d{4}-\d{2}-\d{2})-")


# ----------------------------------------------------------------- data ----

def _get(url, params=None, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=25)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 * (i + 1))
                continue
            logger.warning("GET %s -> %s", url, r.status_code)
            return None
        except requests.RequestException as e:
            logger.debug("GET %s failed: %s", url, e)
            time.sleep(2 * (i + 1))
    return None


def _lst(offset_h: int) -> timezone:
    return timezone(timedelta(hours=offset_h))


def cli_highs(station: str, years: list[int]) -> dict:
    """{date_iso: high_F} from the NWS Daily Climate Report archive (IEM)."""
    out = {}
    for y in years:
        d = _get("https://mesonet.agron.iastate.edu/json/cli.py", {"station": station, "year": y}) or {}
        for r in d.get("results", []):
            h = r.get("high")
            if isinstance(h, (int, float)) and h > -100:
                out[r["valid"]] = int(h)
    return out


def _daily_max_lst(times: list, temps: list, offset_h: int) -> dict:
    """Hourly (UTC ISO) series -> {LST date: max} for complete-ish days."""
    by = defaultdict(list)
    tz = _lst(offset_h)
    for t, v in zip(times, temps):
        if v is None:
            continue
        dt = datetime.fromisoformat(t).replace(tzinfo=timezone.utc).astimezone(tz)
        by[dt.date().isoformat()].append(v)
    return {d: max(v) for d, v in by.items() if len(v) >= 20}


def archived_forecasts(lat, lon, offset_h, start: date, end: date) -> dict:
    """{lead: {date: forecast_high}} for lead 0 (same-day run) and 1 (day-before run)."""
    d = _get("https://previous-runs-api.open-meteo.com/v1/forecast", {
        "latitude": lat, "longitude": lon, "timezone": "GMT",
        "hourly": "temperature_2m,temperature_2m_previous_day1",
        "temperature_unit": "fahrenheit",
        "start_date": start.isoformat(), "end_date": end.isoformat(),
    }) or {}
    h = d.get("hourly", {})
    times = h.get("time", [])
    return {0: _daily_max_lst(times, h.get("temperature_2m", []), offset_h),
            1: _daily_max_lst(times, h.get("temperature_2m_previous_day1", []), offset_h)}


def current_forecast(lat, lon, offset_h) -> dict:
    """{date: forecast_high} for today and the next 2 LST days (latest run)."""
    d = _get("https://api.open-meteo.com/v1/forecast", {
        "latitude": lat, "longitude": lon, "timezone": "GMT",
        "hourly": "temperature_2m", "temperature_unit": "fahrenheit",
        "past_days": 1, "forecast_days": 3,
    }) or {}
    h = d.get("hourly", {})
    return _daily_max_lst(h.get("time", []), h.get("temperature_2m", []), offset_h)


def observed_max_so_far(station: str, offset_h: int, day: str) -> float | None:
    """Max observed temp (F) at the station since the start of the LST climate day."""
    tz = _lst(offset_h)
    start = datetime.fromisoformat(day).replace(tzinfo=tz)
    d = _get(f"https://api.weather.gov/stations/{station}/observations",
             {"start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}) or {}
    best = None
    for f in d.get("features", []):
        p = f.get("properties", {})
        for key in ("maxTemperatureLast24Hours", "temperature"):
            v = (p.get(key) or {}).get("value")
            if key == "maxTemperatureLast24Hours":
                continue  # 24h window crosses the climate-day boundary; don't use
            if v is not None:
                fval = v * 9 / 5 + 32
                best = fval if best is None else max(best, fval)
    return best


# ---------------------------------------------------------- calibration ----

def calibrate(force: bool = False) -> dict:
    """Per city & lead: residuals (actual - forecast), bias, sd. Cached ~daily."""
    if not force and CAL_PATH.exists():
        try:
            env = json.loads(CAL_PATH.read_text())
            age = datetime.now(timezone.utc) - datetime.fromisoformat(env["data"]["built_at"])
            if age < timedelta(hours=CAL_MAX_AGE_H):
                return env["data"]
        except (KeyError, ValueError, json.JSONDecodeError):
            pass
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=CAL_DAYS)
    out = {"built_at": datetime.now(timezone.utc).isoformat(), "window": [start.isoformat(), end.isoformat()], "cities": {}}
    for code, (stn, lat, lon, off) in STATIONS.items():
        truth = cli_highs(stn, sorted({start.year, end.year}))
        fc = archived_forecasts(lat, lon, off, start, end)
        city = {}
        for lead in (0, 1):
            res = [truth[d] - fc[lead][d] for d in fc[lead] if d in truth and start.isoformat() <= d <= end.isoformat()]
            if len(res) < 15:
                city[str(lead)] = {"n": len(res), "residuals": res, "bias": 0.0, "sd": 3.0}
                continue
            m = sum(res) / len(res)
            sd = math.sqrt(sum((r - m) ** 2 for r in res) / (len(res) - 1))
            city[str(lead)] = {"n": len(res), "residuals": [round(r, 2) for r in res],
                               "bias": round(m, 2), "sd": round(sd, 2)}
        out["cities"][code] = city
        logger.info("calibrated %s: lead0 n=%d bias=%+.2f sd=%.2f | lead1 n=%d bias=%+.2f sd=%.2f", code,
                    city["0"]["n"], city["0"]["bias"], city["0"]["sd"], city["1"]["n"], city["1"]["bias"], city["1"]["sd"])
    save_envelope(create_envelope("weather_us_calibration", out), CAL_PATH.name)
    return out


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bracket_probs(forecast: float, cal: dict, lead: int, brackets: list[tuple], floor: float | None = None) -> list[float]:
    """P(integer CLI high in [lo, hi]) for each bracket, from empirical residuals.

    Each residual r gives a scenario high = forecast + r, smoothed with a normal
    kernel. The kernel is widened if needed so total spread >= MIN_SD[lead].
    A same-day observed floor removes mass below it (renormalized).
    """
    res = cal.get("residuals") or [0.0]
    sd_emp = cal.get("sd", 3.0)
    k = KERNEL_SD
    floor_sd = MIN_SD.get(lead, 2.0)
    if sd_emp < floor_sd:
        k = math.sqrt(max(KERNEL_SD ** 2, floor_sd ** 2 - sd_emp ** 2))

    def cdf(x):  # P(continuous high < x)
        return sum(_phi((x - (forecast + r)) / k) for r in res) / len(res)

    lo_cut = -1e9
    if floor is not None:
        lo_cut = round(floor) - 0.5  # integer high >= rounded observed max
    base = 1.0 - cdf(lo_cut) if floor is not None else 1.0
    base = max(base, 1e-9)
    out = []
    for lo, hi in brackets:
        a = max(lo - 0.5, lo_cut)
        b = hi + 0.5
        p = max(0.0, cdf(b) - cdf(a)) if b > a else 0.0
        out.append(min(1.0, p / base))
    return out


def parse_bracket(title: str) -> tuple[float, float] | None:
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


# --------------------------------------------------------------- pricing ---

def price_markets(cal_all: dict) -> dict:
    """Model vs market for every open temperature bracket; returns envelope data."""
    from agent import pm_us
    markets = []
    off = 0
    while off < 12000:
        page = pm_us.list_markets({"limit": 100, "offset": off, "active": True, "closed": False,
                                   "orderBy": ["end_date"], "orderDirection": "asc"})
        if not page:
            break
        markets += [m for m in page if SLUG_RE.match(m.get("slug", ""))]
        if len(page) < 100:
            break
        # temperature markets resolve within ~2 days; stop once we're past them
        if pm_us.days_until(page[-1].get("endDate")) and pm_us.days_until(page[-1].get("endDate")) > 4:
            break
        off += 100

    groups = defaultdict(list)
    for m in markets:
        code, day = SLUG_RE.match(m["slug"]).groups()
        b = parse_bracket(m.get("title", ""))
        if code in STATIONS and b:
            groups[(code, day)].append((m, b))

    fc_cache, rows, proposals = {}, [], []
    for (code, day), items in sorted(groups.items()):
        stn, lat, lon, offh = STATIONS[code]
        if code not in fc_cache:
            fc_cache[code] = current_forecast(lat, lon, offh)
        fc = fc_cache[code].get(day)
        today_lst = datetime.now(_lst(offh)).date()
        lead = (date.fromisoformat(day) - today_lst).days
        if fc is None or lead < 0 or lead > 1:
            continue
        cal = cal_all.get("cities", {}).get(code, {}).get(str(lead), {})
        floor = observed_max_so_far(stn, offh, day) if lead == 0 else None
        bias = cal.get("bias", 0.0)
        probs = bracket_probs(fc, cal, lead, [b for _, b in items], floor)
        total = sum(probs) or 1.0
        probs = [p / total for p in probs]  # brackets are exhaustive
        for (m, b), p_yes in zip(items, probs):
            bbo = pm_us.get_bbo(m["slug"])
            if not bbo or bbo.get("state") != "MARKET_STATE_OPEN":
                continue
            yes_ask, no_ask = bbo.get("yes_ask") or 0, bbo.get("no_ask") or 0
            row = {"slug": m["slug"], "city": code, "date": day, "lead": lead, "bracket": m.get("title"),
                   "forecast": round(fc, 1), "bias": bias, "obs_floor": round(floor, 1) if floor else None,
                   "p_yes": round(p_yes, 4), "yes_ask": yes_ask, "no_ask": no_ask,
                   "yes_bid": bbo.get("yes_bid") or 0}
            rows.append(row)
            for side, p, ask in (("yes", p_yes, yes_ask), ("no", 1 - p_yes, no_ask)):
                if not ask or p < MIN_PROB:
                    continue
                fee = 0.0695 * ask * (1 - ask)  # worst case (taker) fee per share
                edge = p - ask - fee
                if edge < MIN_EDGE:
                    continue
                limit = round(min(ask, p - MIN_EDGE), 3)
                proposals.append({
                    "venue": "us", "slug": m["slug"], "question": f"{m.get('question','')} — {m.get('title','')}",
                    "side": side.upper(), "price": limit, "ask": ask, "model_prob": round(p, 4),
                    "edge": round(edge, 4), "lead": lead, "city": code, "date": day,
                    "forecast": round(fc, 1), "obs_floor": row["obs_floor"],
                    "depth_shares": bbo.get("ask_shares") if side == "yes" else bbo.get("bid_shares"),
                })
    proposals.sort(key=lambda x: x["edge"], reverse=True)
    return {"priced_at": datetime.now(timezone.utc).isoformat(), "markets": len(rows),
            "rows": rows, "proposals": proposals}


def reliability_report(cal_all: dict) -> None:
    """Out-of-sample-ish check: leave-one-out bracket probabilities vs what happened."""
    print("\nCalibration (leave-one-out, 2-degree brackets around the forecast):")
    for code, c in cal_all.get("cities", {}).items():
        for lead in ("0", "1"):
            res = c.get(lead, {}).get("residuals", [])
            if len(res) < 20:
                continue
            brier, hits, n_conf, conf_hits = 0.0, 0, 0, 0
            for i, r in enumerate(res):
                others = res[:i] + res[i + 1:]
                sd = math.sqrt(sum((x - sum(others) / len(others)) ** 2 for x in others) / (len(others) - 1))
                sub = {"residuals": others, "sd": sd}
                fc = 70.0
                actual = round(fc + r)
                br = [(lo, lo + 1) for lo in range(round(fc) - 9, round(fc) + 9, 2)]
                ps = bracket_probs(fc, sub, int(lead), br)
                for (lo, hi), p in zip(br, ps):
                    y = 1.0 if lo <= actual <= hi else 0.0
                    brier += (p - y) ** 2
                    if 1 - p >= MIN_PROB:   # a NO we would consider
                        n_conf += 1
                        conf_hits += (1 - y)
            print(f"  {code} lead{lead}: n={len(res)} bias={c[lead]['bias']:+.2f} sd={c[lead]['sd']:.2f}  "
                  f"Brier/bracket={brier/(len(res)*9):.4f}  NO-side >= {MIN_PROB:.0%}: {n_conf} calls, "
                  f"{conf_hits/n_conf*100 if n_conf else 0:.1f}% correct")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true")
    ap.add_argument("--calibrate", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        cal = calibrate(force=args.calibrate)
        if args.calibrate:
            reliability_report(cal)
        data = price_markets(cal)
        env = create_envelope("weather_us", data, status="success" if data["markets"] else "partial")
    except Exception as e:
        logger.exception("weather_us failed")
        env = create_envelope("weather_us", {"proposals": [], "rows": []}, status="error", error=str(e))
    save_envelope(env, "weather_us.json")
    d = env["data"]
    logger.info("weather_us: %d brackets priced, %d proposals", d.get("markets", 0), len(d.get("proposals", [])))
    if args.print:
        for r in d.get("rows", []):
            flag = ""
            if r["p_yes"] >= MIN_PROB or 1 - r["p_yes"] >= MIN_PROB:
                flag = " *"
            print(f"{r['city']} {r['date']} L{r['lead']} fc {r['forecast']:>5} floor {str(r['obs_floor']):>5} "
                  f"{r['bracket']:<12} model YES {r['p_yes']*100:5.1f}%  mkt YES ask {r['yes_ask']:.3f} NO ask {r['no_ask']:.3f}{flag}")
        for p in d.get("proposals", []):
            print(f"PROPOSAL {p['side']} {p['question'][:60]} model {p['model_prob']:.3f} ask {p['ask']:.3f} "
                  f"limit {p['price']:.3f} edge {p['edge']:.3f}")


if __name__ == "__main__":
    main()
