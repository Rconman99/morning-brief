"""Rules for US gimme bets.

A gimme is a heavy favorite only when three things are true at once:

1. The book is two-sided and the spread is tight. The ask alone is not a
   probability — on a one-sided book it is just the price nobody is bidding.
2. The bot's own model, not the quote, clears the price plus the Polymarket US
   taker fee plus a margin. Categories without a model are not traded.
3. The market is not about to resolve. Ranking uses expected value per dollar
   of premium, not annualized yield.

Crypto price slugs (``cpc*``) are skipped by default. Turning the regex off
still does not trade them until a model is registered in ``model_prob_for``.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

# Matches cpc-btc-..., cpceth-..., cpcsol-..., cpcxrp-..., and any later cpc* slug.
DEFAULT_SKIP_SLUG_REGEX = r"^cpc"
DEFAULT_MAX_SPREAD = 0.04
DEFAULT_MIN_EDGE = 0.03
DEFAULT_MIN_HOURS = 6.0
DEFAULT_PRICE_THRESHOLD = 0.92
# Polymarket US taker fee per share = TAKER_FEE_COEF * p * (1-p). C is share count.
TAKER_FEE_COEF = 0.0695

_CRYPTO_SLUG = re.compile(r"^cpc", re.IGNORECASE)
_CRYPTO_CATEGORIES = {
    "crypto", "cryptocurrency", "bitcoin", "btc", "ethereum", "eth",
    "solana", "sol", "xrp", "ripple",
}


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def skip_slug_pattern() -> str:
    """Active slug skip regex. Empty string disables the skip (env override)."""
    if "US_GIMME_SKIP_SLUG_REGEX" in os.environ:
        return os.environ.get("US_GIMME_SKIP_SLUG_REGEX") or ""
    return DEFAULT_SKIP_SLUG_REGEX


def slug_is_skipped(slug: str, pattern: str | None = None) -> bool:
    pat = skip_slug_pattern() if pattern is None else pattern
    if not pat:
        return False
    try:
        return re.search(pat, slug or "") is not None
    except re.error:
        return re.search(DEFAULT_SKIP_SLUG_REGEX, slug or "") is not None


def is_crypto(slug: str = "", category: str = "") -> bool:
    """True for any cpc* slug or a crypto category name, whatever the skip regex is."""
    if _CRYPTO_SLUG.search(slug or ""):
        return True
    return (category or "").strip().lower() in _CRYPTO_CATEGORIES


def normalize_category(category: str = "", slug: str = "") -> str:
    """Category stored on proposals and paper trades.

    Crypto underlyings share one category so the per-category cap can see them.
    Temperature brackets with no exchange category are climate.
    """
    if is_crypto(slug, category):
        return "crypto"
    cat = (category or "").strip().lower()
    if str(slug or "").startswith("tc-temp-") and cat in ("", "other"):
        return "climate"
    return cat or "other"


def taker_fee_per_share(price: float) -> float:
    """Taker fee in dollars per one share bought at ``price`` (the C=1 case)."""
    try:
        p = float(price)
    except (TypeError, ValueError):
        return 0.0
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return TAKER_FEE_COEF * p * (1.0 - p)


def edge_after_taker_fee(model_prob: float, price: float) -> float:
    """Model probability minus the price paid minus the taker fee, per share."""
    return float(model_prob) - float(price) - taker_fee_per_share(price)


def passes_own_edge(model_prob, price, margin: float) -> bool:
    """True when model_prob >= price + taker_fee(price) + margin."""
    if model_prob is None:
        return False
    try:
        prob = float(model_prob)
        px = float(price)
    except (TypeError, ValueError):
        return False
    if not 0.0 <= prob <= 1.0 or not 0.0 < px < 1.0:
        return False
    return edge_after_taker_fee(prob, px) + 1e-12 >= float(margin)


def ev_per_dollar(model_prob, price) -> float | None:
    """Expected value per dollar of premium, after the taker fee.

    This does not divide by time. Annualized yield is not used for ranking.
    """
    if model_prob is None:
        return None
    try:
        px = float(price)
        prob = float(model_prob)
    except (TypeError, ValueError):
        return None
    if px <= 0.0 or not 0.0 <= prob <= 1.0:
        return None
    return edge_after_taker_fee(prob, px) / px


def hours_until(iso: str | None, now: datetime | None = None) -> float | None:
    if not iso:
        return None
    try:
        end = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (end - moment).total_seconds() / 3600.0


def candidate_hours(candidate: dict, now: datetime | None = None) -> float | None:
    raw = candidate.get("hours_to_expiry")
    if raw is not None and raw != "":
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    return hours_until(candidate.get("end_date") or candidate.get("endDate"), now)


def _num(raw, default=0.0) -> float:
    if raw is None or raw == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def side_quotes(bbo: dict, side: str) -> dict:
    """Bid, ask, mid, spread, and ask-depth for the outcome we would buy.

    A binary book is two-sided only when that outcome has a real bid and a real
    ask. The NO bid is the complement of the YES ask (and the NO ask the
    complement of the YES bid) because those are the same resting orders.
    A lone ask with no bid returns bid 0 — it is not a probability.
    """
    side_u = (side or "").upper()
    yes_bid = _num(bbo.get("yes_bid"))
    yes_ask = _num(bbo.get("yes_ask"))
    no_ask_real = bbo.get("no_ask_real")
    if no_ask_real is None:
        # Dicts built before no_ask_real existed: a synthetic complement is flagged.
        if bbo.get("no_ask_synthetic"):
            no_ask_real_f = 0.0
        else:
            no_ask_real_f = _num(bbo.get("no_ask"))
    else:
        no_ask_real_f = _num(no_ask_real)

    if side_u == "YES":
        bid, ask = yes_bid, yes_ask
        depth = _num(bbo.get("ask_shares"))
        bid_depth = _num(bbo.get("bid_shares"))
    else:
        bid = _num(bbo.get("no_bid"))
        if bid <= 0.0 and 0.0 < yes_ask < 1.0:
            bid = round(1.0 - yes_ask, 4)
        ask = no_ask_real_f
        if ask <= 0.0 and 0.0 < yes_bid < 1.0:
            ask = round(1.0 - yes_bid, 4)
        depth = _num(bbo.get("no_ask_shares") or bbo.get("bid_shares"))
        bid_depth = _num(bbo.get("ask_shares"))

    bid = round(bid, 4) if bid else 0.0
    ask = round(ask, 4) if ask else 0.0
    spread = round(ask - bid, 4) if bid > 0.0 and ask > 0.0 else None
    mid = round((bid + ask) / 2.0, 4) if bid > 0.0 and ask > 0.0 else None
    return {
        "side": side_u,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread": spread,
        "depth": depth,
        "bid_depth": bid_depth,
    }


def quotes_from_candidate(candidate: dict) -> dict:
    """Book stored on a scanner row. Does not invent a bid from the ask/price."""
    bid = _num(candidate.get("market_bid"))
    ask = _num(candidate.get("market_ask"))
    mid_raw = candidate.get("market_mid")
    spread_raw = candidate.get("spread")
    mid = _num(mid_raw) if mid_raw not in (None, "") else None
    spread = _num(spread_raw) if spread_raw not in (None, "") else None
    if bid > 0.0 and ask > 0.0:
        if mid is None:
            mid = round((bid + ask) / 2.0, 4)
        if spread is None:
            spread = round(ask - bid, 4)
    else:
        mid = None
        spread = None
    return {
        "side": (candidate.get("side") or "").upper(),
        "bid": round(bid, 4) if bid else 0.0,
        "ask": round(ask, 4) if ask else 0.0,
        "mid": mid,
        "spread": spread,
        "depth": _num(candidate.get("depth_shares")),
        "bid_depth": _num(candidate.get("bid_depth")),
    }


def book_failure(quotes: dict, *, max_spread: float, min_depth: float) -> str | None:
    """Why this book cannot be used. None when it is two-sided, tight, and deep enough."""
    bid = quotes.get("bid") or 0.0
    ask = quotes.get("ask") or 0.0
    if bid <= 0.0:
        return "one_sided_no_bid"
    if ask <= 0.0 or ask >= 1.0:
        return "one_sided_no_ask"
    if ask <= bid:
        return "crossed_book"
    spread = quotes.get("spread")
    if spread is None:
        spread = round(ask - bid, 4)
    if spread > float(max_spread) + 1e-9:
        return "wide_spread"
    if (quotes.get("depth") or 0.0) < float(min_depth):
        return "thin_ask"
    return None


def passes_favorite(quotes: dict, threshold: float) -> bool:
    """The >= threshold is the midpoint, never the ask."""
    mid = quotes.get("mid")
    if mid is None:
        return False
    return float(threshold) <= float(mid) < 1.0


def model_prob_for(slug: str, side: str, p_yes) -> tuple[float | None, str]:
    """Own probability of ``side`` and where it came from.

    Weather brackets use the unblended ``p_yes`` from the EMOS / ensemble
    model (``weather_us`` rows). That number is the model's probability, not
    the market quote and not the log-odds blend. The US strategy shrinks it
    toward settled results before the edge test (``agent/prob_calibration.py``);
    this function still returns the raw probability. Every other category
    returns ``(None, "no_model")`` until a model is added here.
    """
    if str(slug or "").startswith("tc-temp-"):
        if p_yes is None or p_yes == "":
            return None, "weather_missing"
        try:
            p = float(p_yes)
        except (TypeError, ValueError):
            return None, "weather_missing"
        if not 0.0 <= p <= 1.0:
            return None, "weather_missing"
        side_p = p if str(side or "").upper() == "YES" else 1.0 - p
        return side_p, "weather_model"
    return None, "no_model"


def assess_us_gimme(
    candidate: dict,
    model_prob,
    *,
    min_price: float = DEFAULT_PRICE_THRESHOLD,
    max_spread: float = DEFAULT_MAX_SPREAD,
    min_edge: float = DEFAULT_MIN_EDGE,
    min_hours: float = DEFAULT_MIN_HOURS,
    min_depth: float = 100.0,
    model_reason: str = "no_model",
    now: datetime | None = None,
) -> dict:
    """Book, time, favorite-mid, and own-edge decision for one US candidate.

    ``keep`` is True only when every rule passes. ``pay_price`` is the ask
    (what a taker would pay); the favorite test uses the midpoint.
    """
    if candidate.get("market_bid") is not None or candidate.get("market_ask") is not None:
        quotes = quotes_from_candidate(candidate)
    else:
        quotes = {
            "side": (candidate.get("side") or "").upper(),
            "bid": 0.0,
            "ask": 0.0,
            "mid": None,
            "spread": None,
            "depth": _num(candidate.get("depth_shares")),
            "bid_depth": 0.0,
        }
    reason = candidate.get("book_reject") or None
    if not reason:
        reason = book_failure(quotes, max_spread=max_spread, min_depth=min_depth)
    if not reason and not passes_favorite(quotes, min_price):
        reason = "below_mid"
    hours = candidate_hours(candidate, now)
    if not reason:
        if hours is None:
            reason = "unknown_expiry"
        elif hours < float(min_hours):
            reason = "too_soon"
    pay = quotes["ask"] if quotes["ask"] else None
    edge = None
    ev = None
    if not reason:
        if model_prob is None:
            reason = model_reason or "no_model"
        elif pay is None or not passes_own_edge(model_prob, pay, min_edge):
            reason = "model_edge"
            if model_prob is not None and pay:
                edge = edge_after_taker_fee(model_prob, pay)
                ev = ev_per_dollar(model_prob, pay)
    if reason is None and model_prob is not None and pay:
        edge = edge_after_taker_fee(model_prob, pay)
        ev = ev_per_dollar(model_prob, pay)
    return {
        "keep": reason is None,
        "reason": reason,
        "bid": quotes["bid"],
        "ask": quotes["ask"],
        "mid": quotes["mid"],
        "spread": quotes["spread"],
        "depth": quotes["depth"],
        "hours": hours,
        "pay_price": pay,
        "edge": edge,
        "ev_per_dollar": ev,
        "model_prob": None if model_prob is None else float(model_prob),
    }


def replay_decision(
    row: dict,
    *,
    min_price: float = DEFAULT_PRICE_THRESHOLD,
    max_spread: float = DEFAULT_MAX_SPREAD,
    min_edge: float = DEFAULT_MIN_EDGE,
    min_hours: float = DEFAULT_MIN_HOURS,
    skip_pattern: str = DEFAULT_SKIP_SLUG_REGEX,
) -> dict:
    """Apply the new rules to one historical log row.

    Each rule is True, False, or None. None means the logged fields cannot
    support that rule (a missing bid is unknown, not a proven one-sided book).
    ``would_keep`` is True only when every rule is decidable and passes, False
    when a decidable rule fails, and None when the row is inconclusive.
    """
    slug = row.get("slug") or ""
    checks = {
        "crypto_slug_skip": not slug_is_skipped(slug, skip_pattern),
    }
    bid = row.get("market_bid")
    ask = row.get("market_ask")
    if ask is None:
        ask = row.get("ask")
    mid = row.get("market_mid")
    spread = row.get("spread")
    book_known = _positive(bid) and _positive(ask)
    if book_known:
        if spread is None:
            spread = float(ask) - float(bid)
        if mid is None:
            mid = (float(bid) + float(ask)) / 2.0
        checks["two_sided"] = True
        checks["spread"] = float(spread) <= float(max_spread) + 1e-9
        checks["mid_threshold"] = float(min_price) <= float(mid) < 1.0
    elif bid is not None and not _positive(bid):
        # An explicit zero bid was logged. That is a one-sided book.
        checks["two_sided"] = False
        checks["spread"] = None
        checks["mid_threshold"] = None
    else:
        checks["two_sided"] = None
        checks["spread"] = None
        checks["mid_threshold"] = None

    model = row.get("model_prob")
    pay = ask if _positive(ask) else (row.get("price") if _positive(row.get("price")) else None)
    if model is None or pay is None:
        checks["own_edge"] = None
    else:
        checks["own_edge"] = passes_own_edge(model, pay, min_edge)

    hours = row.get("hours_to_expiry")
    if hours is None:
        hours = hours_until(row.get("end_date") or row.get("endDate"))
    if hours is None:
        checks["min_hours"] = None
    else:
        checks["min_hours"] = float(hours) + 1e-9 >= float(min_hours)

    missing = [name for name, value in checks.items() if value is None]
    if any(value is False for value in checks.values()):
        would_keep = False
    elif missing:
        would_keep = None
    else:
        would_keep = True
    return {"checks": checks, "missing": missing, "would_keep": would_keep}


def _positive(raw) -> bool:
    try:
        return raw is not None and float(raw) > 0.0
    except (TypeError, ValueError):
        return False
