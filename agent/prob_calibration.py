"""Shrink a model probability toward settled results before any edge or size decision.

The weather model has been more confident than its paper record. Picks it rates
near 88% have hit about half the time, and its Brier score is worse than the
market's. This module blends the raw side probability with the market mid.
The blend weight is learned from settled weather_us and gimme_bets proposals.
A prior of PROB_CAL_PRIOR_N pseudo-observations (default 80) keeps that weight
near zero until there is a real sample, so a small history trusts the market.

Platt scaling and a binned map are fit only after their own sample floors
(PROB_CAL_PLATT_MIN_N, default 80; PROB_CAL_BIN_MIN_N, default 200). Whatever
those methods return is then clamped:

- the calibrated probability is never further from 50% than the raw probability
- it never raises the side probability, so the edge versus the ask cannot grow

The raw probability is what the proposal log still scores. This module does not
place orders and does not read secrets. Paper or live, it only changes the
probability the edge test and the sizer see. It does not touch the humility
cap, the correlation cap, the loss pause, or the paper/live switch.

PROB_CAL_ENABLED=0 (also false/no/off) leaves the previous probability in place.
"""

from __future__ import annotations

import json
import logging
import math
import os

logger = logging.getLogger(__name__)

_OFF = ("0", "false", "no", "off")
_STRATEGIES = ("weather_us", "gimme_bets")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, default))


def calibration_enabled() -> bool:
    """On unless PROB_CAL_ENABLED is 0/false/no/off."""
    return os.environ.get("PROB_CAL_ENABLED", "1").strip().lower() not in _OFF


def prior_n() -> float:
    """Pseudo-observations that pull the model weight toward zero (the market)."""
    return max(0.0, _env_float("PROB_CAL_PRIOR_N", 80.0))


def platt_min_n() -> int:
    return max(2, _env_int("PROB_CAL_PLATT_MIN_N", 80))


def bin_min_n() -> int:
    return max(2, _env_int("PROB_CAL_BIN_MIN_N", 200))


def bin_min_count() -> int:
    return max(1, _env_int("PROB_CAL_BIN_MIN_COUNT", 20))


def _clip(p: float) -> float:
    return min(1.0 - 1e-6, max(1e-6, float(p)))


def _as_prob(raw):
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    p = float(raw)
    if 0.0 <= p <= 1.0:
        return p
    return None


def _as_outcome(raw):
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    y = float(raw)
    if 0.0 <= y <= 1.0:
        return y
    return None


def logit(p: float) -> float:
    p = _clip(p)
    return math.log(p / (1.0 - p))


def expit(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def clamp_side_probability(raw: float, candidate: float) -> float:
    """Keep ``candidate`` from being more extreme than ``raw``, and from raising edge.

    Edge of the side we might buy rises when that side's probability rises.
    The allowed interval is every probability at least as close to 50% as
    ``raw`` is, on the side of ``raw`` that does not increase it.
    """
    raw_p = _clip(raw)
    cand = _clip(candidate)
    if raw_p >= 0.5:
        lo, hi = 1.0 - raw_p, raw_p
    else:
        lo, hi = raw_p, 1.0 - raw_p
    return min(hi, max(lo, cand))


def shrinkage_weight(rows: list[dict] | None, prior: float | None = None) -> tuple[float, int]:
    """Weight on the model in ``w * model + (1 - w) * market``. Always in [0, 1].

    Closed form minimizes Brier score of that blend. Negative weights (the
    model is worse than the market) are clipped to 0, which is "use the
    market". The result is then shrunk toward 0 by ``prior`` pseudo-counts,
    so a handful of trades cannot hand the model the decision.
    """
    strength = prior_n() if prior is None else max(0.0, float(prior))
    num = 0.0
    den = 0.0
    n = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        model = _as_prob(row.get("model_prob"))
        market = _as_prob(row.get("market_prob"))
        outcome = _as_outcome(row.get("outcome"))
        if model is None or market is None or outcome is None:
            continue
        gap = model - market
        num += gap * (outcome - market)
        den += gap * gap
        n += 1
    if n == 0 or den <= 1e-12:
        w_hat = 0.0
    else:
        w_hat = num / den
    w_hat = min(1.0, max(0.0, w_hat))
    if n + strength <= 0.0:
        return 0.0, n
    return (n / (n + strength)) * w_hat, n


def fit_platt(rows: list[dict] | None) -> dict | None:
    """Logistic map ``expit(a + b * logit(model))``. None when the fit is unstable.

    The slope is kept in [0, 1] so the map cannot steepen the model. The
    caller still clamps the output. This is not used until the sample floor.
    """
    zs = []
    ys = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        model = _as_prob(row.get("model_prob"))
        outcome = _as_outcome(row.get("outcome"))
        if model is None or outcome is None:
            continue
        zs.append(logit(model))
        ys.append(outcome)
    if len(zs) < 8:
        return None
    a, b = 0.0, 1.0
    ridge = 1.0
    for _ in range(30):
        g_a = -ridge * a
        g_b = -ridge * (b - 1.0)
        h_aa = -ridge
        h_ab = 0.0
        h_bb = -ridge
        for z, y in zip(zs, ys):
            p = expit(a + b * z)
            weight = max(p * (1.0 - p), 1e-6)
            resid = y - p
            g_a += resid
            g_b += resid * z
            h_aa -= weight
            h_ab -= weight * z
            h_bb -= weight * z * z
        det = h_aa * h_bb - h_ab * h_ab
        if abs(det) < 1e-12:
            return None
        step_a = (h_bb * g_a - h_ab * g_b) / det
        step_b = (h_aa * g_b - h_ab * g_a) / det
        if not math.isfinite(step_a) or not math.isfinite(step_b):
            return None
        a += max(-1.0, min(1.0, step_a))
        b += max(-1.0, min(1.0, step_b))
        b = min(1.0, max(0.0, b))
    if not math.isfinite(a) or not math.isfinite(b):
        return None
    return {"a": float(a), "b": float(b)}


def apply_platt(raw: float, platt: dict | None) -> float | None:
    if not platt:
        return None
    try:
        a = float(platt["a"])
        b = float(platt["b"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(a) or not math.isfinite(b):
        return None
    return expit(a + min(1.0, max(0.0, b)) * logit(raw))


def fit_bins(rows: list[dict] | None, n_bins: int = 10, min_count: int | None = None) -> list | None:
    """Realized hit rate in equal probability bins. None when too few bins fill up.

    Empty bins are None. The caller ignores those and falls back to shrinkage.
    """
    count_floor = bin_min_count() if min_count is None else max(1, int(min_count))
    n_bins = max(2, int(n_bins))
    usable = []
    filled = 0
    for i in range(n_bins):
        lo = i / n_bins
        hi = (i + 1) / n_bins
        members = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            model = _as_prob(row.get("model_prob"))
            outcome = _as_outcome(row.get("outcome"))
            if model is None or outcome is None:
                continue
            inside = lo <= model <= hi if i == n_bins - 1 else lo <= model < hi
            if inside:
                members.append((model, outcome))
        if len(members) >= count_floor:
            filled += 1
            usable.append({
                "lo": lo,
                "hi": hi,
                "n": len(members),
                "mean_pred": sum(m for m, _ in members) / len(members),
                "realized": sum(y for _, y in members) / len(members),
            })
        else:
            usable.append(None)
    if filled < 3:
        return None
    return usable


def apply_bins(raw: float, bins: list | None) -> float | None:
    if not bins:
        return None
    p = float(raw)
    for i, bucket in enumerate(bins):
        if not bucket:
            continue
        lo = float(bucket["lo"])
        hi = float(bucket["hi"])
        last = i == len(bins) - 1
        if lo <= p < hi or (last and lo <= p <= hi):
            return float(bucket["realized"])
    return None


def fit_calibration(rows: list[dict] | None = None, *, prior: float | None = None,
                    platt_min: int | None = None, bins_min: int | None = None,
                    bin_count: int | None = None) -> dict:
    """Fit the shrinkage weight, and Platt / bins only past their sample floors."""
    strength = prior_n() if prior is None else max(0.0, float(prior))
    pmin = platt_min_n() if platt_min is None else max(2, int(platt_min))
    bmin = bin_min_n() if bins_min is None else max(2, int(bins_min))
    bcount = bin_min_count() if bin_count is None else max(1, int(bin_count))
    weight, n = shrinkage_weight(rows, strength)
    platt = fit_platt(rows) if n >= pmin else None
    bins = fit_bins(rows, min_count=bcount) if n >= bmin else None
    return {
        "enabled": True,
        "n": n,
        "model_weight": weight,
        "prior_n": strength,
        "platt": platt,
        "bins": bins,
        "platt_min_n": pmin,
        "bin_min_n": bmin,
        "bin_min_count": bcount,
    }


def _disabled_fit() -> dict:
    return {
        "enabled": False,
        "n": 0,
        "model_weight": 1.0,
        "prior_n": 0.0,
        "platt": None,
        "bins": None,
        "platt_min_n": platt_min_n(),
        "bin_min_n": bin_min_n(),
        "method": "disabled",
    }


def load_settled_rows() -> list[dict]:
    """Settled weather_us and gimme_bets proposals. Empty when the logs are absent.

    Uses the proposal log and the ledger settlement cache. One row per
    strategy, market, side, and US/Eastern day (the scorer's rule). Missing
    files, bad JSON, and import errors return [] so a cycle still runs.
    """
    try:
        from agent.proposal_log import PROPOSAL_LOG, eastern_day, load_records, score_proposal_records
    except Exception:
        logger.warning("probability calibration: proposal log unavailable")
        return []
    try:
        records = load_records(PROPOSAL_LOG)
    except Exception:
        logger.warning("probability calibration: could not read proposals")
        return []
    settlements = {}
    try:
        from agent.ledger import CACHE
        if CACHE.exists():
            payload = json.loads(CACHE.read_text())
            if isinstance(payload, dict):
                settlements = payload.get("settlements") or {}
    except Exception:
        logger.warning("probability calibration: could not read settlement cache")
        settlements = {}
    if not records or not isinstance(settlements, dict) or not settlements:
        return []
    try:
        scored = score_proposal_records(records, settlements)
    except Exception:
        logger.warning("probability calibration: scoring settled proposals failed")
        return []
    asks = {}
    for row in records:
        if not isinstance(row, dict) or row.get("record", "proposal") != "proposal":
            continue
        try:
            day = eastern_day(row.get("timestamp") or "")
        except (TypeError, ValueError):
            continue
        key = (row.get("strategy") or "unknown", row.get("slug"), row.get("side"), day)
        if key not in asks and row.get("market_ask") is not None:
            asks[key] = row.get("market_ask")
    out = []
    for row in scored:
        if row.get("strategy") not in _STRATEGIES:
            continue
        model = _as_prob(row.get("model_prob"))
        market = _as_prob(row.get("market_prob"))
        outcome = _as_outcome(row.get("outcome"))
        if model is None or market is None or outcome is None:
            continue
        item = {
            "strategy": row.get("strategy"),
            "slug": row.get("slug"),
            "side": row.get("side"),
            "model_prob": model,
            "market_prob": market,
            "outcome": outcome,
        }
        ask = asks.get((row.get("strategy"), row.get("slug"), row.get("side"), row.get("day")))
        ask_p = _as_prob(ask)
        if ask_p is not None and 0.0 < ask_p < 1.0:
            item["ask"] = ask_p
        out.append(item)
    return out


def current_fit(rows: list[dict] | None = None) -> dict:
    """Fit for this cycle. Pass ``rows`` in tests; otherwise read the logs.

    A failure fits the empty sample, which trusts the market. That is the
    safe direction: it cannot invent edge.
    """
    if not calibration_enabled():
        return _disabled_fit()
    try:
        sample = load_settled_rows() if rows is None else rows
        return fit_calibration(sample)
    except Exception:
        logger.exception("probability calibration fit failed; trusting the market")
        return fit_calibration([])


def calibrate_side(raw, market, fit: dict | None = None) -> dict:
    """Calibrated probability of the side we might buy.

    With calibration off, the raw probability is returned unchanged.
    With no market quote, the raw probability is returned (there is nothing
    honest to shrink toward). Otherwise the candidates are the shrinkage
    blend and, once their samples are large enough, Platt and the bin map.
    The least confident candidate is kept, then clamped.
    """
    raw_p = _as_prob(raw)
    market_p = _as_prob(market)
    if raw_p is None:
        return {
            "raw_prob": None,
            "market_prob": market_p,
            "calibrated_prob": None,
            "model_weight": None,
            "n": 0,
            "method": "missing",
        }
    if fit is None:
        fit = current_fit()
    if not fit.get("enabled", True):
        return {
            "raw_prob": raw_p,
            "market_prob": market_p,
            "calibrated_prob": raw_p,
            "model_weight": 1.0,
            "n": int(fit.get("n") or 0),
            "method": "disabled",
        }
    weight = float(fit.get("model_weight") or 0.0)
    weight = min(1.0, max(0.0, weight))
    n = int(fit.get("n") or 0)
    if market_p is None:
        shrink = raw_p
        shrink_method = "raw_no_market"
    else:
        shrink = weight * raw_p + (1.0 - weight) * market_p
        shrink_method = "market_shrink"
    candidates = [(shrink, shrink_method)]
    pmin = int(fit.get("platt_min_n") or platt_min_n())
    bmin = int(fit.get("bin_min_n") or bin_min_n())
    if fit.get("platt") and n >= pmin:
        platt_p = apply_platt(raw_p, fit.get("platt"))
        if platt_p is not None:
            candidates.append((platt_p, "platt"))
    if fit.get("bins") and n >= bmin:
        binned = apply_bins(raw_p, fit.get("bins"))
        if binned is not None:
            candidates.append((binned, "bins"))
    chosen, method = min(candidates, key=lambda item: abs(item[0] - 0.5))
    calibrated = clamp_side_probability(raw_p, chosen)
    if abs(calibrated - chosen) > 1e-4:
        method = method + "+clamp"
    return {
        "raw_prob": raw_p,
        "market_prob": market_p,
        "calibrated_prob": calibrated,
        "shrink_prob": shrink,
        "model_weight": weight,
        "n": n,
        "method": method,
    }


def edge_probability(pre_trade: float, calibrated: float, raw: float) -> float:
    """Probability the edge test is allowed to use.

    ``pre_trade`` is the probability the strategy already computed (the
    log-odds blend, or the raw probability when that blend is off). The
    result is never higher than that number and never higher than the raw
    model, so calibration cannot open an edge the previous rule rejected,
    and the humility cap — which is applied to ``pre_trade`` before this —
    cannot be loosened by shrinking a wide edge back under the cap.
    """
    try:
        pre = float(pre_trade)
        cal = float(calibrated)
        raw_p = float(raw)
    except (TypeError, ValueError):
        return float(pre_trade)
    capped = min(pre, raw_p, cal)
    return clamp_side_probability(raw_p, capped)


def confidence_scale(raw, calibrated) -> float:
    """How much of the raw confidence remains. Always in [0, 1]."""
    raw_p = _as_prob(raw)
    cal_p = _as_prob(calibrated)
    if raw_p is None or cal_p is None:
        return 1.0
    raw_conf = abs(raw_p - 0.5)
    if raw_conf <= 1e-12:
        return 1.0
    return min(1.0, max(0.0, abs(cal_p - 0.5) / raw_conf))


def resize_shares(shares: float, min_shares: float, scale: float) -> float:
    """Shrink a size that is above the share floor. Never grows it, never under the floor.

    A position already sitting on the floor (the 5-share minimum on a small
    bankroll) is left alone. The edge test is what drops those bets.
    """
    try:
        shares_f = float(shares)
        floor = float(min_shares)
        scale_f = min(1.0, max(0.0, float(scale)))
    except (TypeError, ValueError):
        return shares
    if shares_f <= floor + 1e-9 or scale_f >= 0.999:
        return shares_f
    return max(floor, shares_f * scale_f)


def passes_edge(prob, ask: float, min_edge: float, fee_coef: float = 0.0695,
                min_prob: float = 0.0) -> bool:
    """True when ``prob`` clears ``ask + taker fee + min_edge`` and ``min_prob``."""
    p = _as_prob(prob)
    try:
        px = float(ask)
    except (TypeError, ValueError):
        return False
    if p is None or not 0.0 < px < 1.0:
        return False
    if p + 1e-12 < float(min_prob):
        return False
    fee = fee_coef * px * (1.0 - px)
    return (p - px - fee) + 1e-12 >= float(min_edge)


def filter_effect(rows: list[dict] | None, *, min_edge: float = 0.04, min_prob: float = 0.0,
                  fee_coef: float = 0.0695, fit: dict | None = None) -> dict:
    """How many rows the edge rule would skip or resize if it used the calibrated probability.

    A row counts only when it has a model probability, a market probability,
    and an ask in (0, 1). ``skipped`` means the raw probability would have
    cleared the edge test and the calibrated one would not. ``resized`` means
    both clear and confidence was reduced. This does not place trades.
    """
    sample = [r for r in (rows or []) if isinstance(r, dict)]
    fitted = fit if fit is not None else fit_calibration(sample)
    skipped = resized = unchanged = still_out = missing_ask = 0
    for row in sample:
        if _as_prob(row.get("model_prob")) is None or _as_prob(row.get("market_prob")) is None:
            continue
        ask = row.get("ask", row.get("market_ask"))
        if _as_prob(ask) is None or not 0.0 < float(ask) < 1.0:
            missing_ask += 1
            continue
        raw = float(row["model_prob"])
        cal = calibrate_side(raw, row.get("market_prob"), fitted)
        cal_p = cal["calibrated_prob"]
        raw_ok = passes_edge(raw, ask, min_edge, fee_coef, min_prob)
        cal_ok = passes_edge(cal_p, ask, min_edge, fee_coef, min_prob)
        if raw_ok and not cal_ok:
            skipped += 1
        elif raw_ok and cal_ok and confidence_scale(raw, cal_p) < 0.999:
            resized += 1
        elif raw_ok and cal_ok:
            unchanged += 1
        else:
            still_out += 1
    considered = skipped + resized + unchanged + still_out
    return {
        "n_fit": int(fitted.get("n") or 0),
        "model_weight": fitted.get("model_weight"),
        "considered": considered,
        "missing_ask": missing_ask,
        "skipped": skipped,
        "resized": resized,
        "unchanged": unchanged,
        "still_out": still_out,
    }
