"""Polymarket US market scanner — builds `gimme_bets` from the US exchange's own books.

Writes data/processed/polymarket_us.json in the same envelope/shape the strategies
expect from modules/polymarket_scanner.py, so `agent.strategies.scan_gimme_bets`
works unchanged when POLYMARKET_VENUE=us. Each entry carries the US market slug
directly (no cross-venue matching needed at execution time).

Run (from /opt/morning-brief):
    .venv/bin/python agent/us_scanner.py            # scan + write envelope
    .venv/bin/python agent/us_scanner.py --print    # also print the top candidates

Budget: the public gateway throttles hard (429s well under the documented
20 req/s), so calls are paced (~3/s) and the list endpoint's own per-side quotes
(`marketSides[].price`) pre-filter candidates; a BBO call is made only for the
shortlist (to confirm the market is open and there is depth at the quote).
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import logging
import os
import time
from datetime import datetime, timezone

from lib.data_envelope import create_envelope, save_envelope

logger = logging.getLogger(__name__)

PRICE_THRESHOLD = float(os.environ.get("US_GIMME_PRICE_THRESHOLD", "0.92"))
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


def scan(max_bbo_calls: int = MAX_BBO_CALLS) -> dict:
    from agent.pm_us import get_bbo, days_until, side_prices
    now = datetime.now(timezone.utc)
    shortlist = []
    scanned = in_window = skipped_cat = 0
    for m in iter_open_markets(max_pages=MAX_PAGES):
        scanned += 1
        d = days_until(m.get("endDate"))
        if d is None or d < 0:
            continue
        if d > MAX_DAYS:
            break  # sorted by end_date asc — nothing later qualifies
        if m.get("closed") or not m.get("active", True):
            continue
        in_window += 1
        if (m.get("category") or "other").lower() in SKIP_CATEGORIES:
            skipped_cat += 1
            continue
        yes_q, no_q = side_prices(m)
        q = max(yes_q, no_q)
        if PRICE_THRESHOLD <= q < 1.0:
            # provisional yield from the list quote; the book confirms the top of the list
            yld = ((1.0 - q) / q * 100) * (365 / max(d, 1))
            shortlist.append((yld, m, d))

    # The list endpoint can repeat a market across pages; keep one per slug.
    seen = set()
    shortlist = [t for t in shortlist if not (t[1]["slug"] in seen or seen.add(t[1]["slug"]))]
    shortlist.sort(key=lambda t: t[0], reverse=True)
    results = []
    calls = 0
    for _, m, d in shortlist:
        if calls >= max_bbo_calls:
            logger.warning("US scanner: hit BBO budget (%d) with %d candidates left",
                           max_bbo_calls, len(shortlist) - calls)
            break
        b = get_bbo(m["slug"])
        calls += 1
        if not b or b.get("state") != "MARKET_STATE_OPEN":
            continue
        if b["open_interest"] < MIN_OPEN_INTEREST:
            continue
        question = m.get("question", "")
        outcome = m.get("title") or ""
        label = f"{question} — {outcome}" if outcome and outcome.lower() not in ("yes", "no") else question
        for side, price, depth in (("YES", b["yes_ask"], b["ask_shares"]), ("NO", b["no_ask"], b["bid_shares"])):
            if not price or price < PRICE_THRESHOLD or price >= 1.0:
                continue
            if depth < MIN_ASK_SHARES:
                continue
            profit = round(1.0 - price, 4)
            raw_ret = round(profit / price * 100, 2)
            risks = []
            spread = (b["yes_ask"] - b["yes_bid"]) if (b["yes_ask"] and b["yes_bid"]) else 0
            if spread > 0.04:
                risks.append("wide_spread")
            if depth * price < 500:
                risks.append("low_liquidity")
            if d <= 2:
                risks.append("imminent_resolution")
            if price >= 0.98:
                risks.append("likely_settled")
            results.append({
                "venue": "us",
                "question": label,
                "slug": m["slug"],                 # US market slug — executor uses this directly
                "us_slug": m["slug"],
                "event_question": question,
                "outcome": outcome,
                "side": side,
                "price": round(price, 4),
                "profit_per_share": profit,
                "raw_return_pct": raw_ret,
                "annualized_yield_pct": round(raw_ret * (365 / max(d, 1)), 1),
                "volume_24h": b["shares_traded"],   # cumulative shares traded (no 24h figure on the US API)
                "liquidity": round(depth * price, 2), # USD resting at the quote we'd hit
                "depth_shares": depth,
                "open_interest": b["open_interest"],
                "days_to_expiry": d,
                "end_date": m.get("endDate"),
                "risks": risks,
                "category": m.get("category", "other"),
                "market_type": m.get("marketType", ""),
                "tick": m.get("orderPriceMinTickSize", 0.001),
                "neg_risk": False,
                "group_size": 0,
                "group_title": "",
            })

    results.sort(key=lambda x: x.get("annualized_yield_pct") or 0, reverse=True)
    return {
        "scanned_at": now.isoformat(),
        "markets_scanned": scanned,
        "candidates_within_window": in_window,
        "skipped_categories": sorted(SKIP_CATEGORIES),
        "skipped_by_category": skipped_cat,
        "shortlisted": len(shortlist),
        "bbo_calls": calls,
        "gimme_bets": results[:40],
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
            print(f"{g['annualized_yield_pct']:>7.1f}%/yr  {g['side']:<3} @ {g['price']:.3f}  {g['days_to_expiry']:>3}d  "
                  f"OI {g['open_interest']:>9,.0f}  [{g['category']}] {g['question'][:70]}  ({g['slug']})")


if __name__ == "__main__":
    main()
