"""Polymarket US market scanner — builds `gimme_bets` from the US exchange's own books.

Writes data/processed/polymarket_us.json in the same envelope/shape the strategies
expect from modules/polymarket_scanner.py, so `agent.strategies.scan_gimme_bets`
works unchanged when POLYMARKET_VENUE=us. Each entry carries the US market slug
directly (no cross-venue matching needed at execution time).

A candidate is a heavy favorite only when the book is two-sided (best bid > 0
on the side we would buy), the spread is within US_GIMME_MAX_SPREAD (default
4 cents), and the *midpoint* is at least US_GIMME_PRICE_THRESHOLD. The ask is
recorded and later used as the taker price; it is not treated as a probability.
Crypto price slugs (cpc*) are skipped by default.

Run (from /opt/morning-brief):
    .venv/bin/python agent/us_scanner.py            # scan + write envelope
    .venv/bin/python agent/us_scanner.py --print    # also print the top candidates

Budget: the public gateway throttles hard (429s well under the documented
20 req/s), so calls are paced (~3/s) and the list endpoint's own per-side quotes
(`marketSides[].price`) pre-filter candidates; a BBO call is made only for the
shortlist (to confirm the market is open and the book is two-sided).
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import logging
import os
from datetime import datetime, timezone

from lib.data_envelope import create_envelope, save_envelope
from agent.gimme_rules import (
    DEFAULT_MAX_SPREAD,
    DEFAULT_MIN_HOURS,
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_SKIP_SLUG_REGEX,
    book_failure,
    env_float,
    hours_until,
    passes_favorite,
    side_quotes,
    skip_slug_pattern,
    slug_is_skipped,
)

logger = logging.getLogger(__name__)

PRICE_THRESHOLD = float(os.environ.get("US_GIMME_PRICE_THRESHOLD", str(DEFAULT_PRICE_THRESHOLD)))
MAX_DAYS = int(os.environ.get("US_GIMME_MAX_DAYS", "30"))
# US non-sports short-dated markets (intraday BTC ranges, daily temperatures) are
# market-maker quoted: thousands of shares at the quote but near-zero open
# interest. Depth at the quote is the liquidity that matters for a fill.
MIN_OPEN_INTEREST = float(os.environ.get("US_GIMME_MIN_OI", "0"))         # shares
MIN_ASK_SHARES = float(os.environ.get("US_GIMME_MIN_ASK_SHARES", "100"))  # depth at the quote we'd hit
MAX_BBO_CALLS = int(os.environ.get("US_GIMME_MAX_BBO_CALLS", "120"))
MAX_PAGES = int(os.environ.get("US_GIMME_MAX_PAGES", "100"))
# The US exchange is ~85% sports and thousands of them sit at >=92%. The strategy
# skips sports by default (agent/config.py skip_categories), so the scanner does
# too unless told otherwise — it keeps the book-confirmation budget on the
# markets that can actually be traded.
SKIP_CATEGORIES = {c.strip().lower() for c in os.environ.get("US_GIMME_SKIP_CATEGORIES", "sports").split(",") if c.strip()}
# Intraday/daily crypto price brackets (cpc*, including cpceth-/cpcsol-/cpcxrp-)
# are fast-bot territory. Skipped by default until a spot+volatility model exists.
# US_GIMME_SKIP_SLUG_REGEX still overrides this (empty string turns the skip off).
SKIP_SLUG_REGEX = os.environ.get("US_GIMME_SKIP_SLUG_REGEX", DEFAULT_SKIP_SLUG_REGEX)
PAGE = 100


def iter_open_markets(max_pages: int = 60):
    """Open markets, soonest-expiring first. (`end_date` is the sort key the API honors.)"""
    from agent.pm_us import list_markets
    offset = 0
    for _ in range(max_pages):
        ms = list_markets({"limit": PAGE, "offset": offset, "active": True, "closed": False,
                           "orderBy": ["end_date"], "orderDirection": "asc"})
        if not ms:
            return
        for m in ms:
            yield m
        if len(ms) < PAGE:
            return
        offset += PAGE


def _settings() -> dict:
    """Read tunables at scan time so an env override applies without a re-import."""
    return {
        "threshold": env_float("US_GIMME_PRICE_THRESHOLD", DEFAULT_PRICE_THRESHOLD),
        "max_days": int(env_float("US_GIMME_MAX_DAYS", MAX_DAYS)),
        "min_oi": env_float("US_GIMME_MIN_OI", MIN_OPEN_INTEREST),
        "min_depth": env_float("US_GIMME_MIN_ASK_SHARES", MIN_ASK_SHARES),
        "max_spread": env_float("US_GIMME_MAX_SPREAD", DEFAULT_MAX_SPREAD),
        "min_hours": env_float("US_GIMME_MIN_HOURS", DEFAULT_MIN_HOURS),
        "skip_categories": {
            c.strip().lower()
            for c in os.environ.get("US_GIMME_SKIP_CATEGORIES", "sports").split(",")
            if c.strip()
        },
        "skip_pattern": skip_slug_pattern(),
    }


def _row(m: dict, side: str, quotes: dict, hours: float, bbo: dict, book_reject: str | None) -> dict:
    question = m.get("question", "")
    outcome = m.get("title") or ""
    label = f"{question} — {outcome}" if outcome and outcome.lower() not in ("yes", "no") else question
    bid, ask, mid = quotes["bid"], quotes["ask"], quotes["mid"]
    spread = quotes["spread"]
    depth = quotes["depth"]
    ref = mid if mid is not None else 0.0
    pay = ask or 0.0
    profit = round(1.0 - pay, 4) if pay else 0.0
    raw_ret = round(profit / pay * 100, 2) if pay else 0.0
    days = hours / 24.0
    risks = []
    if book_reject == "wide_spread":
        risks.append("wide_spread")
    if depth * (pay or 0) < 500:
        risks.append("low_liquidity")
    if hours <= 48:
        risks.append("imminent_resolution")
    if ref >= 0.98:
        risks.append("likely_settled")
    return {
        "venue": "us",
        "question": label,
        "slug": m["slug"],
        "us_slug": m["slug"],
        "event_question": question,
        "outcome": outcome,
        "side": side,
        # Reference price is the midpoint. The ask is stored separately and is
        # what a taker would pay; strategies must not read `price` as "the ask".
        "price": round(ref, 4) if ref else 0.0,
        "market_bid": round(bid, 4) if bid else 0.0,
        "market_ask": round(ask, 4) if ask else 0.0,
        "market_mid": round(mid, 4) if mid is not None else None,
        "spread": spread,
        "profit_per_share": profit,
        "raw_return_pct": raw_ret,
        "annualized_yield_pct": round(raw_ret * (365 / max(days, 1 / 24)), 1),
        "volume_24h": bbo.get("shares_traded", 0),
        "liquidity": round(depth * pay, 2) if pay else 0.0,
        "depth_shares": depth,
        "bid_depth": quotes.get("bid_depth") or 0.0,
        "open_interest": bbo.get("open_interest", 0),
        "days_to_expiry": round(days, 4),
        "hours_to_expiry": round(hours, 4),
        "end_date": m.get("endDate"),
        "risks": risks,
        "category": m.get("category", "other"),
        "market_type": m.get("marketType", ""),
        "tick": m.get("orderPriceMinTickSize", 0.001),
        "neg_risk": False,
        "group_size": 0,
        "group_title": "",
        "book_reject": book_reject,
    }


def scan(max_bbo_calls: int = MAX_BBO_CALLS, now: datetime | None = None) -> dict:
    from agent.pm_us import get_bbo, side_prices
    moment = now or datetime.now(timezone.utc)
    cfg = _settings()
    threshold = cfg["threshold"]
    max_spread = cfg["max_spread"]
    min_hours = cfg["min_hours"]
    max_hours = cfg["max_days"] * 24
    prefilter = max(0.0, threshold - max_spread)
    shortlist = []
    scanned = in_window = skipped_cat = skipped_slug = skipped_soon = 0
    for m in iter_open_markets(max_pages=MAX_PAGES):
        scanned += 1
        hours = hours_until(m.get("endDate"), moment)
        if hours is None or hours < 0:
            continue
        if hours > max_hours:
            break  # sorted by end_date asc — nothing later qualifies
        if m.get("closed") or not m.get("active", True):
            continue
        in_window += 1
        if hours < min_hours:
            skipped_soon += 1
            continue
        if (m.get("category") or "other").lower() in cfg["skip_categories"]:
            skipped_cat += 1
            continue
        if slug_is_skipped(m.get("slug", ""), cfg["skip_pattern"]):
            skipped_slug += 1
            continue
        yes_q, no_q = side_prices(m)
        q = max(yes_q, no_q)
        if prefilter <= q < 1.0:
            # Modeled weather brackets get the BBO budget first. Within a group,
            # sooner markets come first. Annualized yield is not the sort key.
            modeled = 0 if str(m.get("slug", "")).startswith("tc-temp-") else 1
            shortlist.append((modeled, hours, m))

    seen = set()
    shortlist = [t for t in shortlist if not (t[2]["slug"] in seen or seen.add(t[2]["slug"]))]
    shortlist.sort(key=lambda t: (t[0], t[1]))
    evaluated = []
    calls = 0
    rejected = {"one_sided_no_bid": 0, "one_sided_no_ask": 0, "crossed_book": 0,
                "wide_spread": 0, "thin_ask": 0, "below_mid": 0}
    for _, hours, m in shortlist:
        if calls >= max_bbo_calls:
            logger.warning("US scanner: hit BBO budget (%d) with %d candidates left",
                           max_bbo_calls, len(shortlist) - calls)
            break
        b = get_bbo(m["slug"])
        calls += 1
        if not b or b.get("state") != "MARKET_STATE_OPEN":
            continue
        if b.get("open_interest", 0) < cfg["min_oi"]:
            continue
        for side in ("YES", "NO"):
            quotes = side_quotes(b, side)
            reason = book_failure(quotes, max_spread=max_spread, min_depth=cfg["min_depth"])
            if reason is None and not passes_favorite(quotes, threshold):
                reason = "below_mid"
            if reason:
                rejected[reason] = rejected.get(reason, 0) + 1
            evaluated.append(_row(m, side, quotes, hours, b, reason))

    tradeable = [e for e in evaluated if not e.get("book_reject")]
    # Not annualized yield. Prefer brackets a model can price, then tighter books.
    tradeable.sort(key=lambda e: (
        0 if str(e["slug"]).startswith("tc-temp-") else 1,
        e.get("spread") if e.get("spread") is not None else 1,
        -(e.get("market_mid") or 0),
    ))
    return {
        "scanned_at": moment.isoformat(),
        "markets_scanned": scanned,
        "candidates_within_window": in_window,
        "skipped_categories": sorted(cfg["skip_categories"]),
        "skipped_by_category": skipped_cat,
        "skipped_slug": skipped_slug,
        "skipped_too_soon": skipped_soon,
        "skip_slug_regex": cfg["skip_pattern"],
        "max_spread": max_spread,
        "min_hours": min_hours,
        "price_threshold_on": "mid",
        "shortlisted": len(shortlist),
        "bbo_calls": calls,
        "book_rejects": rejected,
        "gimme_evaluated": evaluated,
        "gimme_bets": tradeable[:40],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true")
    ap.add_argument("--max-bbo", type=int, default=MAX_BBO_CALLS)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        data = scan(max_bbo_calls=args.max_bbo)
        status = "success" if data["gimme_bets"] or data["candidates_within_window"] == 0 else "partial"
        env = create_envelope("polymarket_us", data, status=status)
    except Exception as e:
        logger.exception("US scanner failed")
        env = create_envelope("polymarket_us", {"gimme_bets": []}, status="error", error=str(e))
    path = save_envelope(env, "polymarket_us.json")
    d = env["data"]
    logger.info("US scanner: %d markets scanned, %d in window, %d shortlisted, %d BBO calls, %d gimme candidates -> %s",
                d.get("markets_scanned", 0), d.get("candidates_within_window", 0), d.get("shortlisted", 0),
                d.get("bbo_calls", 0), len(d.get("gimme_bets", [])), path)
    if args.print:
        for g in d.get("gimme_bets", [])[:25]:
            print(f"mid {g.get('market_mid') or 0:.3f}  bid {g.get('market_bid') or 0:.3f}  "
                  f"ask {g.get('market_ask') or 0:.3f}  spr {g.get('spread') or 0:.3f}  "
                  f"{g['side']:<3}  {g.get('hours_to_expiry', 0):>6.1f}h  "
                  f"[{g['category']}] {g['question'][:60]}  ({g['slug']})")


if __name__ == "__main__":
    main()
