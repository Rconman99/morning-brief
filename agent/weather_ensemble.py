"""Ensemble daily-high probabilities for Polymarket US weather markets.

Free Open-Meteo ensemble members (GEFS, ECMWF ENS, ICON when it responds,
Canadian GEM) become one probability per inclusive °F bracket. The climate
day is local standard time, same as agent/weather_settlement_check.py.

EMOS-style calibration shifts the ensemble mean by a fitted bias and widens
it to a fitted spread, using the station's recent forecast errors (about
40–75 days). Too little history, or a failed download, falls back instead
of raising: first to the raw ensemble, then to the existing residual model.

On the market day, brackets whose whole range sits below the observed high
are zeroed and the rest are renormalized.

WX_ENSEMBLE=0 (also false/no/off) leaves this path unused.
"""

from __future__ import annotations

import logging
import math
import os
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests

from agent.weather_settlement_check import climate_day

logger = logging.getLogger(__name__)

UA = {"User-Agent": "polymarket-agent weather model (contact: repo owner)"}
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
# GEFS (gfs_seamless, 31), ECMWF ENS (ecmwf_ifs025, 51), ICON, Canadian GEM.
# ICON often 500s on the free ensemble host; a failed model is skipped.
ENSEMBLE_MODELS = ("gfs_seamless", "ecmwf_ifs025", "icon_seamless", "gem_global")
_OFF = ("0", "false", "no", "off")
# Worst-case taker fee coefficient. Matches weather_us.price_markets.
FEE_COEF = 0.0695


def ensemble_enabled() -> bool:
    """New path is on unless WX_ENSEMBLE is 0/false/no/off."""
    return os.environ.get("WX_ENSEMBLE", "1").strip().lower() not in _OFF


def model_weight() -> float:
    """Share of the model in the log-odds blend. The rest is the market mid."""
    try:
        w = float(os.environ.get("WX_MODEL_WEIGHT", "0.40"))
    except ValueError:
        w = 0.40
    return min(1.0, max(0.0, w))


def emos_min_days() -> int:
    try:
        n = int(os.environ.get("WX_EMOS_MIN_DAYS", "40"))
    except ValueError:
        n = 40
    return max(5, n)


def floor_margin_f() -> float:
    """Degrees the observed high may understate the CLI integer (METAR quantization)."""
    try:
        m = float(os.environ.get("WX_OBS_FLOOR_MARGIN", "1.0"))
    except ValueError:
        m = 1.0
    return max(0.0, m)


def _clip_prob(p: float) -> float:
    return min(1.0 - 1e-6, max(1e-6, float(p)))


def logit(p: float) -> float:
    p = _clip_prob(p)
    return math.log(p / (1.0 - p))


def expit(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def blend_log_odds(model_p: float, market_p: float | None, weight: float | None = None) -> float:
    """Probability pulled toward the market in log-odds space.

    weight=1 keeps the model. A missing market quote keeps the model.
    """
    w = model_weight() if weight is None else min(1.0, max(0.0, float(weight)))
    if market_p is None or w >= 0.999:
        return float(model_p)
    if w <= 0.0:
        return float(market_p)
    z = w * logit(model_p) + (1.0 - w) * logit(market_p)
    return expit(z)


def quote_prob(ask, bid) -> float | None:
    """Mid when both quotes are inside (0, 1), else the one quote we have."""
    vals = []
    for raw in (ask, bid):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        p = float(raw)
        if 0.0 < p < 1.0:
            vals.append(p)
    if len(vals) == 2:
        return 0.5 * (vals[0] + vals[1])
    if len(vals) == 1:
        return vals[0]
    return None


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _norm_pdf(z: float) -> float:
    return math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def crps_gaussian(y: float, mu: float, sigma: float) -> float:
    """Closed-form CRPS of a normal forecast. Lower is better.

    σ * {z (2Φ(z) - 1) + 2φ(z) - 1/√π}, z = (y - μ) / σ.
    """
    if sigma < 1e-6:
        return abs(y - mu)
    z = (y - mu) / sigma
    return sigma * (z * (2.0 * _norm_cdf(z) - 1.0) + 2.0 * _norm_pdf(z) - 1.0 / math.sqrt(math.pi))


def _frange(start: float, stop: float, step: float) -> list[float]:
    if step <= 0:
        return [start]
    n = int(round((stop - start) / step))
    return [round(start + i * step, 6) for i in range(max(0, n) + 1)]


def fit_emos(residuals: list[float] | None, min_n: int | None = None) -> dict | None:
    """Fit an additive bias and a single spread by minimizing CRPS.

    Residuals are (observed high − forecast high) in °F. Returns None when
    fewer than min_n points are available. The live ensemble spread is applied
    later; this fit has no historical member spread to regress on, because the
    free ensemble archive does not retain past members.
    """
    need = emos_min_days() if min_n is None else int(min_n)
    if not residuals or len(residuals) < need:
        return None
    errs = []
    for r in residuals:
        if isinstance(r, bool) or not isinstance(r, (int, float)):
            continue
        errs.append(float(r))
    if len(errs) < need:
        return None
    n = len(errs)
    mean_err = sum(errs) / n
    var = sum((e - mean_err) ** 2 for e in errs) / max(1, n - 1)
    sd = math.sqrt(var)
    best = None
    bias_grid = _frange(mean_err - 1.0, mean_err + 1.0, 0.25)
    spread_lo = 0.4 if sd < 0.4 else max(0.4, sd * 0.55)
    spread_hi = max(spread_lo + 0.4, sd * 1.45, 1.2)
    for bias in bias_grid:
        for spread in _frange(spread_lo, spread_hi, 0.15):
            total = 0.0
            for e in errs:
                total += crps_gaussian(e, bias, spread)
            if best is None or total < best[0]:
                best = (total, bias, spread)
    if best is None:
        return None
    return {
        "bias": round(best[1], 3),
        "spread": round(best[2], 3),
        "n": n,
        "crps": round(best[0] / n, 4),
    }


def gaussian_bracket_probs(mu: float, sigma: float, brackets: list[tuple[float, float]]) -> list[float]:
    """P(CLI integer high lands in each inclusive bracket).

    The continuous high is rounded to a whole °F, so bracket [lo, hi] is the
    interval [lo - 0.5, hi + 0.5]. Open tails use the ±1e6 sentinels from
    parse_bracket ("or below" / "or above").
    """
    sigma = max(float(sigma), 0.05)
    mu = float(mu)

    def cdf(x: float) -> float:
        if x >= 1e8:
            return 1.0
        if x <= -1e8:
            return 0.0
        return _norm_cdf((x - mu) / sigma)

    out = []
    for lo, hi in brackets:
        left = -1e9 if lo <= -1e5 else lo - 0.5
        right = 1e9 if hi >= 1e5 else hi + 0.5
        out.append(max(0.0, cdf(right) - cdf(left)))
    return out


def apply_observed_floor(probs: list[float], brackets: list[tuple[float, float]],
                         observed_f: float | None, margin_f: float | None = None) -> tuple[list[float], bool]:
    """Zero every bracket that sits entirely below the observed high, then renormalize.

    A bracket [lo, hi] is entirely below when hi < observed − margin. The
    margin (default 1°F) is there because hourly NWS observations are coarse
    (tenths of a °C, missed peaks) and can understate the integer CLI high.
    If the floor would erase every bracket, the original probabilities are
    kept and the second value is False.
    """
    if observed_f is None or not probs:
        return list(probs), False
    margin = floor_margin_f() if margin_f is None else max(0.0, float(margin_f))
    cutoff = float(observed_f) - margin
    if not any(hi < cutoff for _lo, hi in brackets):
        return list(probs), False
    kept = []
    for p, (_lo, hi) in zip(probs, brackets):
        if hi < cutoff:
            kept.append(0.0)
        else:
            kept.append(max(0.0, float(p)))
    total = sum(kept)
    if total <= 1e-12:
        return list(probs), False
    return [p / total for p in kept], True


def build_bucket_distribution(stats: dict | None, brackets: list[tuple[float, float]],
                              residuals: list[float] | None, lead: int,
                              observed_f: float | None, sigma_floor: float) -> dict | None:
    """Calibrated (or raw) bracket probabilities for one station-day.

    Never raises. Returns None when the ensemble summary is unusable so the
    caller can fall back to the residual kernel.
    """
    if not stats or not brackets:
        return None
    try:
        mu_ens = float(stats["mean"])
        spread = float(stats.get("spread") or 0.0)
    except (TypeError, ValueError, KeyError):
        return None
    if not math.isfinite(mu_ens) or not math.isfinite(spread):
        return None
    emos = None
    try:
        emos = fit_emos(residuals)
    except Exception:
        logger.exception("EMOS fit failed; using the raw ensemble")
        emos = None
    floor_sd = max(0.05, float(sigma_floor))
    if emos:
        mu = mu_ens + float(emos["bias"])
        sigma = max(floor_sd, float(emos["spread"]), spread)
        method = "emos"
        fallback = None
    else:
        mu = mu_ens
        sigma = max(floor_sd, spread)
        method = "ensemble_raw"
        n_res = len(residuals) if residuals else 0
        fallback = "short_history" if n_res else "no_history"
    try:
        probs = gaussian_bracket_probs(mu, sigma, brackets)
    except Exception:
        logger.exception("bracket probabilities failed")
        return None
    if not probs or sum(probs) <= 1e-12:
        return None
    total = sum(probs)
    probs = [p / total for p in probs]
    floored = False
    if lead == 0 and observed_f is not None:
        probs, floored = apply_observed_floor(probs, brackets, observed_f)
        if sum(probs) > 0:
            total = sum(probs)
            probs = [p / total for p in probs]
    return {
        "probs": probs,
        "prob_method": method,
        "prob_fallback": fallback,
        "ensemble_mean": round(mu_ens, 3),
        "ensemble_spread": round(spread, 3),
        "calibrated_mean": round(mu, 3),
        "calibrated_spread": round(sigma, 3),
        "emos_bias": None if not emos else emos["bias"],
        "emos_spread": None if not emos else emos["spread"],
        "emos_n": None if not emos else emos["n"],
        "obs_floor_applied": floored,
        "n_members": int(stats.get("n_members") or 0),
        "n_models": int(stats.get("n_models") or 0),
        "ensemble_models": ",".join(stats.get("models") or []),
    }


def decide_from_yes(p_yes: float, side: str, yes_ask, yes_bid, no_ask, *,
                    min_prob: float, min_edge: float, max_edge: float,
                    blend: bool = True, weight: float | None = None) -> dict | None:
    """Edge test for one side, after an optional log-odds blend with the YES mid.

    model_prob in the result is the unblended probability of the side (what
    the proposal log scores). trade_prob is what the edge and the limit use.
    Returns {"_skip": "humility"} when the edge is wider than max_edge, and
    None when the side simply does not qualify.
    """
    side = (side or "").lower()
    if side not in ("yes", "no"):
        return None
    try:
        p_yes_f = float(p_yes)
    except (TypeError, ValueError):
        return None
    if not 0.0 <= p_yes_f <= 1.0:
        return None
    market_yes = quote_prob(yes_ask, yes_bid)
    w = model_weight() if weight is None else weight
    blended_yes = blend_log_odds(p_yes_f, market_yes, w) if blend else p_yes_f
    raw_side = p_yes_f if side == "yes" else 1.0 - p_yes_f
    trade_p = blended_yes if side == "yes" else 1.0 - blended_yes
    ask = yes_ask if side == "yes" else no_ask
    try:
        ask_f = float(ask)
    except (TypeError, ValueError):
        return None
    if not 0.0 < ask_f < 1.0:
        return None
    fee = FEE_COEF * ask_f * (1.0 - ask_f)
    edge = trade_p - ask_f - fee
    if edge > max_edge:
        return {
            "_skip": "humility",
            "edge": edge,
            "trade_prob": trade_p,
            "model_prob": raw_side,
            "ask": ask_f,
        }
    if trade_p < min_prob or edge < min_edge:
        return None
    limit = round(min(ask_f, trade_p - min_edge), 3)
    if limit <= 0:
        return None
    return {
        "model_prob": round(raw_side, 4),
        "trade_prob": round(trade_p, 4),
        "edge": round(edge, 4),
        "price": limit,
        "ask": ask_f,
        "model_weight": None if weight is None else round(float(w), 3),
    }


def _env_float(name: str, default: str) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def reapply_weather_gate(proposal: dict, *, min_prob: float | None = None,
                         min_edge: float | None = None, max_edge: float | None = None) -> dict | None:
    """Blend and re-check the edge. Flag off, residual fallback, or a legacy row passes through.

    The humility cap, the 4¢ minimum edge, and the 80% probability floor are the
    same limits weather_us already applies. This is the strategy-side check so
    a proposal is judged on the blended probability.
    """
    if not ensemble_enabled() or not isinstance(proposal, dict):
        return proposal
    if proposal.get("prob_method") not in ("emos", "ensemble_raw"):
        return proposal
    if "p_yes" not in proposal:
        return proposal
    if min_prob is None:
        min_prob = _env_float("WX_MIN_PROB", "0.80")
    if min_edge is None:
        min_edge = _env_float("WX_MIN_EDGE", "0.04")
    if max_edge is None:
        max_edge = _env_float("WX_MAX_EDGE", "0.25")
    side = str(proposal.get("side") or "").lower()
    gated = decide_from_yes(
        proposal.get("p_yes"), side,
        proposal.get("yes_ask"), proposal.get("yes_bid"), proposal.get("no_ask"),
        min_prob=min_prob, min_edge=min_edge, max_edge=max_edge, blend=True,
    )
    if not gated or gated.get("_skip"):
        return None
    out = dict(proposal)
    out["model_prob"] = gated["model_prob"]
    out["trade_prob"] = gated["trade_prob"]
    out["edge"] = gated["edge"]
    out["price"] = gated["price"]
    out["model_weight"] = gated["model_weight"] if gated.get("model_weight") is not None else model_weight()
    return out


# ---------------------------------------------------------------- HTTP ----

def _response_json(resp) -> dict | None:
    status = getattr(resp, "status_code", None)
    if status != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _get_json(url: str, params: dict, get=None, tries: int = 2, timeout: float = 12,
              pause: float = 0.4) -> dict | None:
    """GET JSON. Returns None on any network or HTTP failure. Does not raise."""
    getter = get or requests.get
    for i in range(max(1, tries)):
        try:
            resp = getter(url, params=params, headers=UA, timeout=timeout)
        except Exception as exc:
            logger.warning("GET %s failed: %s", url, exc)
            if i + 1 < tries and pause:
                time.sleep(pause * (i + 1))
            continue
        status = getattr(resp, "status_code", None)
        if status == 200:
            return _response_json(resp)
        if status in (429, 500, 502, 503, 504) and i + 1 < tries and pause:
            time.sleep(pause * (i + 1))
            continue
        logger.warning("GET %s -> %s", url, status)
        return None
    return None


def parse_member_series(hourly: dict) -> tuple[list, list[list]]:
    """Hourly payload -> (times, list of member series).

    Accepts a single-model body (`temperature_2m`, `temperature_2m_member01`)
    and a multi-model body (`temperature_2m_member01_ncep_gefs_seamless`).
    The unsuffixed series is the control member, not the mean, so it is kept.
    """
    if not isinstance(hourly, dict):
        return [], []
    times = hourly.get("time") or []
    if not isinstance(times, list):
        return [], []
    series = []
    for key, vals in hourly.items():
        if key == "time" or not isinstance(key, str) or not key.startswith("temperature_2m"):
            continue
        if not isinstance(vals, list):
            continue
        series.append(vals)
    return times, series


def member_daily_highs(times: list, series_list: list[list], offset_h: int,
                       min_hours: int = 18) -> dict[str, list[float]]:
    """Each member's max temperature on the local-standard-time climate day.

    Hours with a missing value are skipped. A member counts for a day only
    when it has at least `min_hours` readings, so a truncated download is
    not treated as the daily high.
    """
    by_member: dict[str, list[float]] = defaultdict(list)
    for series in series_list:
        per_day: dict[str, list[float]] = defaultdict(list)
        for t, v in zip(times, series):
            if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            if not isinstance(t, str):
                continue
            try:
                dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            per_day[climate_day(dt, offset_h).isoformat()].append(float(v))
        for day, vals in per_day.items():
            if len(vals) >= min_hours:
                by_member[day].append(max(vals))
    return dict(by_member)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _sample_sd(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mu = _mean(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))


def combine_model_highs(per_model: dict[str, dict[str, list[float]]],
                        min_members: int = 8) -> dict[str, dict]:
    """Equal weight per model that returned enough members for that day.

    Spread is the mixture of within-model variance and between-model variance,
    so a 51-member model does not outvote a 31-member one, and two models
    that disagree still widen the forecast.
    """
    dates: set[str] = set()
    for days in per_model.values():
        dates.update(days)
    out = {}
    for day in dates:
        groups = []
        for model, days in per_model.items():
            highs = days.get(day) or []
            if len(highs) >= min_members:
                groups.append((model, highs))
        if not groups:
            continue
        means = [_mean(h) for _, h in groups]
        mu = _mean(means)
        within = 0.0
        for (_, highs), m in zip(groups, means):
            within += _sample_sd(highs) ** 2
        within /= len(groups)
        if len(means) > 1:
            between = sum((m - mu) ** 2 for m in means) / (len(means) - 1)
        else:
            between = 0.0
        models = [m for m, _ in groups]
        out[day] = {
            "mean": mu,
            "spread": math.sqrt(max(0.0, within + between)),
            "n_members": sum(len(h) for _, h in groups),
            "n_models": len(groups),
            "models": models,
        }
    return out


def _fetch_model_highs(lat: float, lon: float, offset_h: int, model: str, get=None) -> dict[str, list[float]] | None:
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m",
        "models": model,
        "temperature_unit": "fahrenheit",
        "timezone": "GMT",
        "past_days": 1,
        "forecast_days": 3,
    }
    payload = _get_json(ENSEMBLE_URL, params, get=get, tries=2, timeout=12, pause=0.4)
    if not payload:
        return None
    times, series = parse_member_series(payload.get("hourly") or {})
    if not times or not series:
        return None
    highs = member_daily_highs(times, series, offset_h)
    return highs or None


def fetch_ensemble_summary(lat: float, lon: float, offset_h: int, get=None) -> dict | None:
    """{LST date: mean/spread/n/...} or None when every model fails.

    Does not raise. One model's HTTP error leaves the others in the mix.
    """
    per_model: dict[str, dict[str, list[float]]] = {}
    try:
        for model in ENSEMBLE_MODELS:
            try:
                highs = _fetch_model_highs(lat, lon, offset_h, model, get=get)
            except Exception as exc:
                logger.warning("ensemble %s failed: %s", model, exc)
                continue
            if highs:
                per_model[model] = highs
                logger.info("ensemble %s: %d days", model, len(highs))
            else:
                logger.info("ensemble %s: no usable members", model)
        if not per_model:
            return None
        combined = combine_model_highs(per_model)
        return combined or None
    except Exception:
        logger.exception("ensemble summary failed")
        return None
