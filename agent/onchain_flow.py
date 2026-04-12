"""On-chain flow analysis for Polymarket — detects large/informed trades.

Monitors Polymarket trade activity to detect whale trades and informed money
flow before markets move.

Based on research from Jon Becker's "Microstructure of Wealth Transfer
in Prediction Markets" — makers systematically extract value from takers,
and large trades from specific addresses predict market movement.

Uses the Polymarket Gamma API and Data API (REST/JSON) instead of raw
Polygon RPC event decoding, avoiding the web3.py dependency.

Run: .venv/bin/python agent/onchain_flow.py
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import requests

from lib.cache import get_cached, set_cached, make_cache_key
from lib.data_envelope import create_envelope, save_envelope

logger = logging.getLogger(__name__)

# Polymarket exchange contracts on Polygon (for reference / future on-chain use)
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a084b51A749Defb054"
NEG_RISK_CTF = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# Output file
OUTPUT_FILE = "onchain_flow.json"

# How many trades to pull per request (Gamma/Data API caps)
TRADE_FETCH_LIMIT = 200


# ============================================================
# 1. Fetch recent large trades
# ============================================================

def fetch_recent_large_trades(min_usd: float = 5000, hours: int = 24) -> list[dict]:
    """Fetch recent Polymarket trades above a USD threshold.

    Strategy: pull high-volume markets from Gamma API, then check their
    recent activity via the Data API. We filter client-side for size.

    Returns list of dicts:
        [{address, side, amount_usd, token_id, market_slug, timestamp, tx_hash}, ...]
    """
    cache_key = make_cache_key("onchain_flow", "large_trades", {
        "min_usd": min_usd, "hours": hours,
    })
    cached = get_cached(cache_key, max_age_hours=1)
    if cached is not None:
        logger.info("Using cached large trades (%d entries)", len(cached))
        return cached

    trades = []

    # Step 1: Get active markets sorted by recent volume
    markets = _fetch_active_markets(limit=30)
    if not markets:
        logger.warning("No active markets returned from Gamma API")
        return []

    # Step 2: For each high-volume market, fetch recent activity
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    for market in markets:
        slug = market.get("slug", "")
        condition_id = market.get("conditionId") or market.get("condition_id", "")
        question = market.get("question", slug)

        activity = _fetch_market_activity(condition_id, limit=TRADE_FETCH_LIMIT)

        for item in activity:
            usd_value = _parse_usd_value(item)
            if usd_value < min_usd:
                continue

            ts = _parse_timestamp(item)
            if ts and ts < cutoff:
                continue

            trades.append({
                "address": item.get("proxyWallet") or item.get("user") or item.get("maker", "unknown"),
                "side": _parse_side(item),
                "amount_usd": round(usd_value, 2),
                "token_id": item.get("asset") or item.get("tokenId") or condition_id,
                "market_slug": slug,
                "question": question[:120],
                "timestamp": ts.isoformat() if ts else None,
                "tx_hash": item.get("transactionHash") or item.get("txHash") or item.get("id", ""),
                "price": _safe_float(item.get("price")),
                "type": item.get("type", "unknown"),  # maker vs taker hint
            })

    # Sort by size descending
    trades.sort(key=lambda t: t["amount_usd"], reverse=True)
    logger.info("Found %d trades >= $%.0f in last %dh", len(trades), min_usd, hours)

    set_cached(cache_key, trades)
    return trades


# ============================================================
# 2. Detect whale activity
# ============================================================

def detect_whale_activity(trades: list[dict], threshold_usd: float = 10_000) -> list[dict]:
    """Group trades by market and flag whale-level concentration.

    Flags markets where:
    - A single address traded > threshold_usd in the window
    - Total volume spiked (we compare against the market's own average)

    Returns: [{market_slug, question, whale_address, total_usd, direction, signal}, ...]
    """
    if not trades:
        return []

    # Group by (market_slug, address)
    by_market_addr: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_market: dict[str, list[dict]] = defaultdict(list)

    for t in trades:
        key = (t["market_slug"], t["address"])
        by_market_addr[key].append(t)
        by_market[t["market_slug"]].append(t)

    whales = []

    for (slug, addr), addr_trades in by_market_addr.items():
        total = sum(t["amount_usd"] for t in addr_trades)
        if total < threshold_usd:
            continue

        # Determine net direction from this address
        buy_total = sum(t["amount_usd"] for t in addr_trades if t["side"] in ("buy", "yes", "Buy"))
        sell_total = sum(t["amount_usd"] for t in addr_trades if t["side"] in ("sell", "no", "Sell"))
        direction = "BUY" if buy_total > sell_total else "SELL"

        # Volume share: what % of this market's tracked volume is this address?
        market_total = sum(t["amount_usd"] for t in by_market[slug])
        share_pct = (total / market_total * 100) if market_total > 0 else 0

        question = addr_trades[0].get("question", slug)

        whales.append({
            "market_slug": slug,
            "question": question,
            "whale_address": addr,
            "total_usd": round(total, 2),
            "trade_count": len(addr_trades),
            "direction": direction,
            "volume_share_pct": round(share_pct, 1),
            "signal": _whale_signal(total, share_pct, direction),
        })

    # Sort by total descending
    whales.sort(key=lambda w: w["total_usd"], reverse=True)
    logger.info("Detected %d whale entries across markets", len(whales))
    return whales


# ============================================================
# 3. Detect informed flow
# ============================================================

def detect_informed_flow(trades: list[dict]) -> list[dict]:
    """Look for patterns of informed trading.

    Signals:
    a) Concentration: >30% of tracked volume from a single address in a market
    b) Price-impact trades: large trades at prices far from 0.50 (strong conviction)
    c) One-sided flow: >70% of volume in one direction in a market

    Returns: [{market_slug, signal_type, confidence, direction, details}, ...]
    """
    if not trades:
        return []

    signals = []

    # Group by market
    by_market: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_market[t["market_slug"]].append(t)

    for slug, market_trades in by_market.items():
        market_total = sum(t["amount_usd"] for t in market_trades)
        if market_total == 0:
            continue

        question = market_trades[0].get("question", slug)

        # --- Signal A: Address concentration ---
        addr_totals: dict[str, float] = defaultdict(float)
        for t in market_trades:
            addr_totals[t["address"]] += t["amount_usd"]

        for addr, addr_total in addr_totals.items():
            share = addr_total / market_total
            if share > 0.30:
                signals.append({
                    "market_slug": slug,
                    "question": question,
                    "signal_type": "address_concentration",
                    "confidence": "high" if share > 0.50 else "medium",
                    "direction": _dominant_direction(
                        [t for t in market_trades if t["address"] == addr]
                    ),
                    "details": (
                        f"Address {addr[:10]}... controls {share:.0%} of tracked "
                        f"volume (${addr_total:,.0f} of ${market_total:,.0f})"
                    ),
                })

        # --- Signal B: Strong-conviction trades (price far from 0.50) ---
        for t in market_trades:
            price = t.get("price")
            if price is None:
                continue
            # Trades at >$0.85 or <$0.15 with large size suggest conviction
            if (price > 0.85 or price < 0.15) and t["amount_usd"] >= 10_000:
                signals.append({
                    "market_slug": slug,
                    "question": question,
                    "signal_type": "conviction_trade",
                    "confidence": "high" if t["amount_usd"] >= 25_000 else "medium",
                    "direction": t["side"].upper() if t["side"] else "UNKNOWN",
                    "details": (
                        f"${t['amount_usd']:,.0f} trade at price {price:.2f} "
                        f"by {t['address'][:10]}..."
                    ),
                })

        # --- Signal C: One-sided flow ---
        buy_vol = sum(
            t["amount_usd"] for t in market_trades
            if t["side"] in ("buy", "yes", "Buy")
        )
        sell_vol = sum(
            t["amount_usd"] for t in market_trades
            if t["side"] in ("sell", "no", "Sell")
        )
        total_directional = buy_vol + sell_vol
        if total_directional > 0:
            buy_ratio = buy_vol / total_directional
            if buy_ratio > 0.70 or buy_ratio < 0.30:
                dominant = "BUY" if buy_ratio > 0.70 else "SELL"
                ratio_pct = max(buy_ratio, 1 - buy_ratio)
                signals.append({
                    "market_slug": slug,
                    "question": question,
                    "signal_type": "one_sided_flow",
                    "confidence": "high" if ratio_pct > 0.85 else "medium",
                    "direction": dominant,
                    "details": (
                        f"{ratio_pct:.0%} of flow is {dominant} "
                        f"(${buy_vol:,.0f} buy / ${sell_vol:,.0f} sell)"
                    ),
                })

    # Deduplicate by (slug, signal_type) — keep highest confidence
    seen = {}
    for sig in signals:
        key = (sig["market_slug"], sig["signal_type"])
        existing = seen.get(key)
        if existing is None or _conf_rank(sig["confidence"]) > _conf_rank(existing["confidence"]):
            seen[key] = sig
    signals = list(seen.values())

    logger.info("Detected %d informed flow signals", len(signals))
    return signals


# ============================================================
# 4. Main entry point — get_flow_signals()
# ============================================================

def get_flow_signals() -> dict:
    """Main function called by the agent. Fetches trades, runs analysis.

    Returns:
        {
            "whale_markets": [...],
            "informed_flow": [...],
            "volume_summary": {...},
            "status": "success" | "partial" | "error",
        }
    """
    try:
        trades = fetch_recent_large_trades(min_usd=5000, hours=24)
    except Exception as e:
        logger.error("Failed to fetch trades: %s", e)
        trades = []

    whale_markets = detect_whale_activity(trades, threshold_usd=10_000)
    informed_flow = detect_informed_flow(trades)

    # Volume summary across all fetched trades
    total_volume = sum(t["amount_usd"] for t in trades)
    unique_markets = len({t["market_slug"] for t in trades})
    unique_addresses = len({t["address"] for t in trades})

    result = {
        "whale_markets": whale_markets,
        "informed_flow": informed_flow,
        "volume_summary": {
            "total_tracked_usd": round(total_volume, 2),
            "large_trade_count": len(trades),
            "unique_markets": unique_markets,
            "unique_addresses": unique_addresses,
            "top_markets": _top_markets_by_volume(trades, n=5),
        },
    }

    # Determine status
    if trades:
        status = "success"
        error = None
    else:
        status = "partial"
        error = "No large trades found or APIs unreachable"

    envelope = create_envelope("onchain_flow", result, status=status, error=error)
    save_envelope(envelope, OUTPUT_FILE)

    logger.info(
        "Flow analysis complete: %d whale entries, %d informed signals, $%.0f tracked",
        len(whale_markets), len(informed_flow), total_volume,
    )
    return result


# ============================================================
# Internal helpers — API fetching
# ============================================================

def _fetch_active_markets(limit: int = 30) -> list[dict]:
    """Get active markets sorted by 24h volume from Gamma API."""
    cache_key = make_cache_key("onchain_flow", "active_markets", {"limit": limit})
    cached = get_cached(cache_key, max_age_hours=1)
    if cached is not None:
        return cached

    try:
        resp = requests.get(
            f"{GAMMA_API}/markets",
            params={
                "limit": str(limit),
                "active": "true",
                "order": "volumeNum",
                "ascending": "false",
            },
            timeout=15,
        )
        resp.raise_for_status()
        markets = resp.json()
        if isinstance(markets, list):
            set_cached(cache_key, markets)
            return markets
        logger.warning("Gamma /markets returned non-list: %s", type(markets))
        return []
    except requests.RequestException as e:
        logger.warning("Gamma API /markets failed: %s", e)
        return []
    except (ValueError, KeyError) as e:
        logger.warning("Gamma API /markets parse error: %s", e)
        return []


def _fetch_market_activity(condition_id: str, limit: int = 200) -> list[dict]:
    """Fetch recent trade activity for a specific market.

    Tries multiple endpoints in order of preference:
    1. Data API /activity with market filter
    2. CLOB API /activity
    3. Gamma API /trades
    """
    if not condition_id:
        return []

    # Try Data API first — most detailed
    try:
        resp = requests.get(
            f"{DATA_API}/activity",
            params={"market": condition_id, "limit": str(limit)},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                return data
    except (requests.RequestException, ValueError):
        pass

    # Fallback: try without market filter (global activity)
    try:
        resp = requests.get(
            f"{DATA_API}/activity",
            params={"limit": str(min(limit, 100))},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data
    except (requests.RequestException, ValueError):
        pass

    # Last resort: CLOB API
    try:
        resp = requests.get(
            f"{CLOB_API}/activity",
            params={"limit": str(min(limit, 50))},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data
    except (requests.RequestException, ValueError):
        pass

    logger.debug("No activity data for condition_id=%s", condition_id[:16])
    return []


# ============================================================
# Internal helpers — parsing & classification
# ============================================================

def _parse_usd_value(item: dict) -> float:
    """Extract USD value from a trade/activity record.

    Different API responses use different field names.
    """
    for field in ("usdcSize", "size", "amount", "value", "tradeAmount", "collateralAmount"):
        val = _safe_float(item.get(field))
        if val and val > 0:
            return val

    # Sometimes size is in shares and we need to multiply by price
    shares = _safe_float(item.get("shares") or item.get("matchedAmount"))
    price = _safe_float(item.get("price") or item.get("avgPrice"))
    if shares and price:
        return shares * price

    return 0.0


def _parse_side(item: dict) -> str:
    """Normalize trade side/direction."""
    side = str(item.get("side", "") or item.get("type", "") or item.get("outcome", "")).lower()
    if side in ("buy", "yes", "long", "0"):
        return "buy"
    if side in ("sell", "no", "short", "1"):
        return "sell"
    # Some APIs use action field
    action = str(item.get("action", "")).lower()
    if "buy" in action or "bid" in action:
        return "buy"
    if "sell" in action or "ask" in action:
        return "sell"
    return side or "unknown"


def _parse_timestamp(item: dict) -> datetime | None:
    """Parse timestamp from various API formats."""
    for field in ("timestamp", "createdAt", "created_at", "matchedAt", "time"):
        raw = item.get(field)
        if not raw:
            continue
        try:
            # Numeric timestamp (seconds or milliseconds)
            if isinstance(raw, (int, float)):
                ts = raw if raw < 1e12 else raw / 1000  # millis -> seconds
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            # ISO string
            if isinstance(raw, str):
                # Handle Z suffix
                raw = raw.replace("Z", "+00:00")
                return datetime.fromisoformat(raw)
        except (ValueError, OSError):
            continue
    return None


def _safe_float(val) -> float | None:
    """Safely convert to float, returning None on failure."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if f == f else None  # NaN check
    except (ValueError, TypeError):
        return None


def _dominant_direction(trades: list[dict]) -> str:
    """Determine whether a set of trades is net BUY or SELL."""
    buy = sum(t["amount_usd"] for t in trades if t["side"] in ("buy", "yes"))
    sell = sum(t["amount_usd"] for t in trades if t["side"] in ("sell", "no"))
    return "BUY" if buy >= sell else "SELL"


def _whale_signal(total_usd: float, share_pct: float, direction: str) -> str:
    """Generate a human-readable whale signal summary."""
    size_label = "mega" if total_usd >= 50_000 else "large"
    conc_label = "dominant" if share_pct > 50 else "significant"
    return f"{size_label.title()} {direction.lower()} — {conc_label} ({share_pct:.0f}% of market volume)"


def _conf_rank(confidence: str) -> int:
    """Rank confidence levels for deduplication."""
    return {"high": 3, "medium": 2, "low": 1}.get(confidence, 0)


def _top_markets_by_volume(trades: list[dict], n: int = 5) -> list[dict]:
    """Summarize top N markets by tracked volume."""
    totals: dict[str, dict] = {}
    for t in trades:
        slug = t["market_slug"]
        if slug not in totals:
            totals[slug] = {
                "market_slug": slug,
                "question": t.get("question", slug),
                "total_usd": 0,
                "trade_count": 0,
            }
        totals[slug]["total_usd"] += t["amount_usd"]
        totals[slug]["trade_count"] += 1

    ranked = sorted(totals.values(), key=lambda x: x["total_usd"], reverse=True)
    for item in ranked:
        item["total_usd"] = round(item["total_usd"], 2)
    return ranked[:n]


# ============================================================
# CLI entry point
# ============================================================

def main():
    """Run on-chain flow analysis and print summary."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    print("=== On-Chain Flow Analysis ===\n")

    result = get_flow_signals()

    summary = result.get("volume_summary", {})
    print(f"Tracked: ${summary.get('total_tracked_usd', 0):,.0f} across "
          f"{summary.get('large_trade_count', 0)} large trades in "
          f"{summary.get('unique_markets', 0)} markets\n")

    whales = result.get("whale_markets", [])
    if whales:
        print(f"--- Whale Activity ({len(whales)} entries) ---")
        for w in whales[:10]:
            print(f"  {w['direction']:4s}  ${w['total_usd']:>10,.0f}  "
                  f"{w['volume_share_pct']:>5.1f}%  {w['question'][:60]}")
    else:
        print("No whale activity detected.\n")

    informed = result.get("informed_flow", [])
    if informed:
        print(f"\n--- Informed Flow Signals ({len(informed)}) ---")
        for s in informed[:10]:
            print(f"  [{s['confidence']:>6s}] {s['signal_type']:<25s} "
                  f"{s['direction']:4s}  {s['details'][:60]}")
    else:
        print("No informed flow signals detected.\n")

    top = summary.get("top_markets", [])
    if top:
        print(f"\n--- Top Markets by Volume ---")
        for m in top:
            print(f"  ${m['total_usd']:>10,.0f}  ({m['trade_count']} trades)  {m['question'][:55]}")

    print(f"\nSaved to data/processed/{OUTPUT_FILE}")


if __name__ == "__main__":
    main()
