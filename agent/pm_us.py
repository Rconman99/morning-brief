"""Polymarket US (polymarket.us) execution venue — official `polymarket-us` SDK.

Why this exists
---------------
Polymarket.com (the Polygon CLOB) blocks US users from logging in or trading,
so the bot can't be run against it from the US. Polymarket US is Polymarket's
separate, CFTC-regulated exchange with a retail API: USD balances, no crypto
wallet, Ed25519 API keys created at https://polymarket.us/developer.

Environment variables (in /opt/morning-brief/.env):
- POLYMARKET_VENUE=us            route live trading here (default: legacy polygon CLOB)
- POLYMARKET_US_KEY_ID           API key id from the developer portal
- POLYMARKET_US_SECRET_KEY       secret shown once at creation

Market model differences vs polymarket.com
- Each US market is one outcome (`title`) of an event (`question`), e.g.
  question="World Series Champion", title="Chicago White Sox". YES/NO on that
  outcome are "long"/"short": buy YES = ORDER_INTENT_BUY_LONG, buy NO =
  ORDER_INTENT_BUY_SHORT, sell YES = ORDER_INTENT_SELL_LONG, sell NO = ORDER_INTENT_SELL_SHORT.
- Prices are USD strings, tick size per market (`orderPriceMinTickSize`, usually 0.001).
- Quantities are whole shares.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import logging
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger(__name__)

_cached_client = None
_public_client = None
_market_cache: dict[str, dict] = {}


def venue() -> str:
    """'us' when the bot is configured to trade on Polymarket US, else 'polygon'."""
    return os.environ.get("POLYMARKET_VENUE", "").strip().lower() or "polygon"


def is_us() -> bool:
    return venue() == "us"


def has_keys() -> bool:
    return bool(os.environ.get("POLYMARKET_US_KEY_ID", "").strip()
                and os.environ.get("POLYMARKET_US_SECRET_KEY", "").strip())


def get_public_client():
    """Unauthenticated client (markets, books, search).

    max_retries=0: the gateway throttles well below the documented 20 req/s and
    the SDK's instant retries just burn the budget; callers pace themselves and
    use `_call_paced` for 429 backoff.
    """
    global _public_client
    if _public_client is None:
        from polymarket_us import PolymarketUS
        _public_client = PolymarketUS(max_retries=0)
    return _public_client


_last_public_call = 0.0
PUBLIC_MIN_INTERVAL = float(os.environ.get("POLYMARKET_US_MIN_INTERVAL", "0.35"))  # seconds between public calls


def _call_paced(fn, *args, retries: int = 4, **kwargs):
    """Call a public-gateway function with pacing and exponential backoff on 429."""
    import time
    global _last_public_call
    delay = 3.0
    for attempt in range(retries + 1):
        wait = PUBLIC_MIN_INTERVAL - (time.monotonic() - _last_public_call)
        if wait > 0:
            time.sleep(wait)
        try:
            _last_public_call = time.monotonic()
            return fn(*args, **kwargs)
        except Exception as e:
            code = getattr(getattr(e, "response", None), "status_code", None) or getattr(e, "status_code", None)
            if (code == 429 or "429" in str(e)) and attempt < retries:
                logger.debug("US gateway 429 — backing off %.0fs", delay)
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            raise


def get_client():
    """Authenticated client (orders, positions, balances) or None if keys missing."""
    global _cached_client
    if _cached_client is not None:
        return _cached_client
    if not has_keys():
        return None
    try:
        from polymarket_us import PolymarketUS
    except ImportError:
        logger.error("polymarket-us SDK not installed — run: .venv/bin/pip install polymarket-us")
        return None
    try:
        _cached_client = PolymarketUS(
            key_id=os.environ["POLYMARKET_US_KEY_ID"].strip(),
            secret_key=os.environ["POLYMARKET_US_SECRET_KEY"].strip(),
        )
    except Exception as e:
        logger.error("Failed to create Polymarket US client: %s", e)
        return None
    return _cached_client


# ---------------------------------------------------------------- helpers ---

def _amt(x) -> float:
    """Amount dict {'value': '0.55', 'currency': 'USD'} -> 0.55 (0.0 if missing)."""
    if isinstance(x, dict):
        x = x.get("value")
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _usd(x: float) -> dict:
    return {"value": f"{x:.4f}".rstrip("0").rstrip(".") if x else "0", "currency": "USD"}


def round_to_tick(price: float, tick: float) -> float:
    """Round a price onto the market's tick grid (half-up), clamped to (tick, 1-tick)."""
    t = Decimal(str(tick or 0.001))
    p = (Decimal(str(price)) / t).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * t
    p = max(t, min(Decimal("1") - t, p))
    return float(p)


def days_until(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        end = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (end - datetime.now(timezone.utc)).days
    except ValueError:
        return None


# ---------------------------------------------------------------- markets ---

def list_markets(params: dict) -> list[dict]:
    """One page of /v1/markets (paced). Caches each market by slug."""
    r = _call_paced(get_public_client().markets.list, params)
    ms = (r.get("markets", []) or []) if isinstance(r, dict) else []
    for m in ms:
        if m.get("slug"):
            _market_cache[m["slug"]] = m
    return ms


def side_prices(m: dict) -> tuple[float, float]:
    """(yes_price, no_price) from a market's `marketSides` quotes (0.0 if absent)."""
    yes = no = 0.0
    for s in m.get("marketSides", []) or []:
        p = _amt(s.get("price"))
        if s.get("long"):
            yes = p
        else:
            no = p
    return yes, no


def get_market(slug: str) -> dict:
    """Market detail by slug (cached per process). {} if not found."""
    if slug in _market_cache:
        return _market_cache[slug]
    try:
        r = _call_paced(get_public_client().markets.retrieve_by_slug, slug)
        m = r.get("market", r) if isinstance(r, dict) else {}
        if m:
            _market_cache[slug] = m
        return m or {}
    except Exception as e:
        logger.debug("US market lookup failed for %s: %s", slug, e)
        return {}


def get_bbo(slug: str) -> dict:
    """Normalized top of book for a market slug.

    Returns {state, yes_ask, yes_bid, no_ask, last, open_interest, shares_traded,
             ask_shares, bid_shares} with floats; {} on failure.
    """
    try:
        r = _call_paced(get_public_client().markets.bbo, slug)
        d = r.get("marketData", r) if isinstance(r, dict) else {}
    except Exception as e:
        logger.debug("US bbo failed for %s: %s", slug, e)
        return {}
    if not d:
        return {}
    yes_ask = _amt(d.get("longQuote")) or _amt(d.get("bestAsk"))
    yes_bid = _amt(d.get("bestBid"))
    no_ask = _amt(d.get("shortQuote")) or (round(1 - yes_bid, 4) if yes_bid else 0.0)
    return {
        "state": d.get("state", ""),
        "yes_ask": yes_ask,
        "yes_bid": yes_bid,
        "no_ask": no_ask,
        "last": _amt(d.get("lastTradePx")),
        "open_interest": float(d.get("openInterest") or 0),
        "shares_traded": float(d.get("sharesTraded") or 0),
        "ask_shares": float(d.get("askShares") or 0),
        "bid_shares": float(d.get("bidShares") or 0),
    }


_WORD = re.compile(r"[a-z0-9]+")


def _tokens(s: str) -> set:
    return set(_WORD.findall((s or "").lower())) - {"the", "a", "an", "of", "in", "on", "to", "will", "be", "by", "vs", "and", "or"}


def find_market(question: str, outcome_hint: str = "", min_score: float = 0.6) -> dict:
    """Best-effort match of a free-text question (e.g. a polymarket.com market) to a
    US market. Returns the market dict with an added 'match_score', or {}.

    Only used for proposals that didn't originate from the US scanner; the US
    scanner emits US slugs directly, which is the reliable path.
    """
    q = (question or "").strip()
    if not q:
        return {}
    try:
        res = _call_paced(get_public_client().search.query, {"query": q[:120], "limit": 10, "status": "active"})
    except Exception as e:
        logger.debug("US search failed for %r: %s", q[:60], e)
        return {}
    want = _tokens(q) | _tokens(outcome_hint)
    best, best_score = {}, 0.0
    for ev in res.get("events", []) or []:
        for m in ev.get("markets", []) or []:
            if m.get("closed") or not m.get("active", True):
                continue
            have = _tokens(ev.get("title", "")) | _tokens(m.get("question", "")) | _tokens(m.get("title", ""))
            if not have or not want:
                continue
            score = len(want & have) / len(want)
            if score > best_score:
                best, best_score = dict(m), score
    if best and best_score >= min_score:
        best["match_score"] = round(best_score, 2)
        return best
    return {}


# ---------------------------------------------------------------- account ---

def get_balance() -> float:
    """Buying power in USD (0.0 if unauthenticated or on error)."""
    c = get_client()
    if not c:
        return 0.0
    try:
        bals = c.account.balances().get("balances", [])
        if not bals:
            return 0.0
        b = bals[0]
        return float(b.get("buyingPower") or b.get("currentBalance") or 0.0)
    except Exception as e:
        logger.warning("US balance lookup failed: %s", e)
        return 0.0


def list_open_orders() -> list[dict]:
    """Open orders normalized to the shape the tracker/dashboard print."""
    c = get_client()
    if not c:
        return []
    try:
        out = []
        for o in c.orders.list().get("orders", []) or []:
            qty = float(o.get("quantity") or 0)
            out.append({
                "id": o.get("id", ""),
                "slug": o.get("marketSlug", ""),
                "side": "BUY" if o.get("side", "").endswith("BUY") else "SELL",
                "intent": o.get("intent", ""),
                "price": _amt(o.get("price")),
                "original_size": qty,
                "size": qty,
                "size_matched": float(o.get("cumQuantity") or 0),
                "status": o.get("state", ""),
                "title": (o.get("marketMetadata") or {}).get("title", ""),
            })
        return out
    except Exception as e:
        logger.warning("US open-orders lookup failed: %s", e)
        return []


def load_positions() -> list[dict]:
    """Active positions normalized to the dict shape agent.run/close_positions use:
    asset (=slug), size, curPrice (best bid for YES), title, currentValue, venue='us'."""
    c = get_client()
    if not c:
        return []
    out = []
    try:
        cursor = None
        for _ in range(20):
            params = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            r = c.portfolio.positions(params)
            for slug, p in (r.get("positions") or {}).items():
                net = float(p.get("netPositionDecimal") or p.get("netPosition") or 0)
                if net == 0 or p.get("expired"):
                    continue
                meta = p.get("marketMetadata") or {}
                # net > 0 = long (YES) shares, net < 0 = short (NO) shares
                outcome = "yes" if net > 0 else "no"
                size = abs(net)
                cost = _amt(p.get("cost")) or _amt(p.get("baseCost"))
                out.append({
                    "venue": "us",
                    "asset": slug,
                    "slug": slug,
                    "outcome": outcome,
                    "size": size,
                    "available": abs(float(p.get("qtyAvailableDecimal") or p.get("qtyAvailable") or net)),
                    "avgPrice": _amt(p.get("avgPx")),
                    "initialValue": abs(cost),
                    "currentValue": abs(_amt(p.get("cashValue"))),
                    "cashPnl": _amt(p.get("realized")),
                    "title": f"{meta.get('title', '')} — {meta.get('outcome', '')}".strip(" —"),
                    "negativeRisk": False,
                })
            cursor = r.get("nextCursor")
            if r.get("eof", True) or not cursor:
                break
    except Exception as e:
        logger.warning("US positions lookup failed: %s", e)
        return out
    # Attach the live bid for what we hold, so auto-exit can compare against AUTO_EXIT_PRICE
    for p in out:
        b = get_bbo(p["slug"])
        if not b:
            p["curPrice"] = 0.0
            continue
        if p["outcome"] == "yes":
            p["curPrice"] = b.get("yes_bid", 0.0)
        else:
            # NO bid = 1 - YES ask (someone selling us YES is buying our NO)
            p["curPrice"] = round(1.0 - b["yes_ask"], 4) if b.get("yes_ask") else 0.0
        if p["currentValue"] == 0 and p["curPrice"]:
            p["currentValue"] = round(p["curPrice"] * p["size"], 2)
    return out


# ---------------------------------------------------------------- orders ----

INTENT = {("BUY", "yes"): "ORDER_INTENT_BUY_LONG", ("SELL", "yes"): "ORDER_INTENT_SELL_LONG",
          ("BUY", "no"): "ORDER_INTENT_BUY_SHORT", ("SELL", "no"): "ORDER_INTENT_SELL_SHORT"}


def build_order(slug: str, side: str, outcome: str, price: float, qty: float,
                post_only: bool = True) -> dict:
    """CreateOrderParams for a GTC limit order. qty rounded down to whole shares."""
    m = get_market(slug)
    tick = float(m.get("orderPriceMinTickSize") or 0.001)
    intent = INTENT[(side.upper(), (outcome or "yes").lower())]
    return {
        "marketSlug": slug,
        "intent": intent,
        "type": "ORDER_TYPE_LIMIT",
        "price": _usd(round_to_tick(price, tick)),
        "quantity": int(qty),
        "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
        "participateDontInitiate": bool(post_only),
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
    }


def place_limit(slug: str, side: str, outcome: str, price: float, qty: float,
                post_only: bool = True) -> dict:
    """Place a GTC limit order. Returns {'ok': bool, 'order_id', 'state', 'error', 'request'}."""
    c = get_client()
    if not c:
        return {"ok": False, "error": "No Polymarket US client (keys missing)"}
    params = build_order(slug, side, outcome, price, qty, post_only)
    if params["quantity"] < 1:
        return {"ok": False, "error": "quantity rounds to 0 shares", "request": params}
    try:
        r = c.orders.create(params)
    except Exception as e:
        return {"ok": False, "error": str(e), "request": params}
    execs = r.get("executions") or []
    state = ""
    reject = ""
    for ex in execs:
        if ex.get("type") == "EXECUTION_TYPE_REJECTED":
            reject = ex.get("orderRejectReason") or ex.get("text") or "rejected"
        state = (ex.get("order") or {}).get("state", state) or state
    if reject:
        return {"ok": False, "order_id": r.get("id", ""), "state": state, "error": reject, "request": params}
    return {"ok": True, "order_id": r.get("id", ""), "state": state or "ORDER_STATE_NEW",
            "filled": sum(float(ex.get("lastShares") or 0) for ex in execs if ex.get("type", "").endswith("FILL")),
            "request": params}


def preview_limit(slug: str, side: str, outcome: str, price: float, qty: float) -> dict:
    """Ask the exchange to validate an order WITHOUT placing it (auth + risk + sizing check)."""
    c = get_client()
    if not c:
        return {"ok": False, "error": "No Polymarket US client (keys missing)"}
    params = build_order(slug, side, outcome, price, qty, post_only=True)
    try:
        r = c.orders.preview({"request": params})
        return {"ok": True, "preview": r.get("order", r), "request": params}
    except Exception as e:
        return {"ok": False, "error": str(e), "request": params}


def cancel(order_id: str, slug: str) -> bool:
    c = get_client()
    if not c:
        return False
    try:
        c.orders.cancel(order_id, {"marketSlug": slug})
        return True
    except Exception as e:
        logger.warning("US cancel failed for %s: %s", order_id, e)
        return False
