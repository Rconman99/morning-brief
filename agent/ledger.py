"""Trade ledger: scores every trade against the exchange's real settlement.

Replaces the old evaluator assumption that every >90¢ paper trade "wins".
A trade only counts once its market has settled on Polymarket US.

For each BUY trade (paper or live, venue=us):
    payout/share = settlement (YES)  or  1 - settlement (NO)
    pnl          = shares * (payout - fill_price) + rebate_or_fee
Fees follow the Polymarket US schedule (2026-10-07):
    taker fee    = 0.0695 * C * p * (1-p)      (paid)
    maker rebate = 0.0125 * C * p * (1-p)      (received)

Paper maker orders are assumed filled at our bid (optimistic). To keep that
honest, every paper trade is also scored as if it had been a taker buy at the
ask seen at entry ("taker bound"). Real edge lies between the two numbers.

Usage:
    .venv/bin/python agent/ledger.py          # refresh settlements, print summary
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
import math
import os
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

TRADE_LOG = PROJECT_ROOT / "agent" / "paper_trades.jsonl"
CACHE = PROJECT_ROOT / "agent" / "ledger_cache.json"
TAKER_THETA = 0.0695
MAKER_THETA = 0.0125


def fee(shares: float, price: float, role: str) -> float:
    """Signed fee impact on P&L (negative = cost, positive = rebate)."""
    f = shares * price * (1 - price)
    return round(MAKER_THETA * f, 4) if role == "maker" else round(-TAKER_THETA * f, 4)


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"settlements": {}, "fills": {}}


def _save_cache(c: dict) -> None:
    try:
        CACHE.write_text(json.dumps(c, indent=1))
    except OSError:
        pass


def load_trades() -> list[dict]:
    out = []
    if not TRADE_LOG.exists():
        return out
    for line in TRADE_LOG.read_text().splitlines():
        try:
            t = json.loads(line)
        except json.JSONDecodeError:
            continue
        if t.get("venue") != "us":
            continue
        if t.get("status") not in ("paper_filled", "submitted"):
            continue
        if str(t.get("side", "")).upper() != "BUY":
            continue
        out.append(t)
    return out


def refresh(trades: list[dict], max_lookups: int = 60) -> dict:
    """Fetch settlements (and live fills) for unresolved trades; cached on disk."""
    from agent import pm_us
    cache = _load_cache()
    st = cache.setdefault("settlements", {})
    fills = cache.setdefault("fills", {})
    lookups = 0
    for t in trades:
        slug = t.get("slug", "")
        if not slug or slug in st or lookups >= max_lookups:
            continue
        s = pm_us.get_settlement(slug)
        lookups += 1
        if s is not None:
            st[slug] = s
    # Live orders: learn how much actually filled and at what price.
    if pm_us.has_keys():
        for t in trades:
            oid = t.get("order_id", "")
            if t.get("status") != "submitted" or not oid or fills.get(oid, {}).get("final"):
                continue
            info = pm_us.get_order_fill(oid)
            if info:
                fills[oid] = info
    _save_cache(cache)
    return cache


def score(trades: list[dict], cache: dict) -> list[dict]:
    """Return one scored row per trade (resolved or open)."""
    st = cache.get("settlements", {})
    fills = cache.get("fills", {})
    rows = []
    for t in trades:
        slug = t.get("slug", "")
        outcome = (t.get("token_hint") or "yes").lower()
        role = t.get("role", "maker")
        price = float(t.get("fill_price") or t.get("price") or 0)
        shares = float(t.get("size") or 0)
        paper = t.get("status") == "paper_filled"
        if not paper:
            f = fills.get(t.get("order_id", ""), {})
            shares = float(f.get("filled", 0))
            if f.get("avg_price"):
                price = float(f["avg_price"])
            if shares <= 0:
                continue  # live order never filled: no position, nothing to score
        row = {
            "timestamp": t.get("timestamp", ""),
            "strategy": t.get("strategy", "unknown"),
            "category": t.get("category", "other"),
            "slug": slug,
            "outcome": outcome,
            "paper": paper,
            "shares": shares,
            "price": price,
            "cost": round(shares * price, 4),
            "resolved": slug in st,
        }
        if slug in st:
            payout = float(st[slug]) if outcome == "yes" else 1.0 - float(st[slug])
            row["won"] = payout >= 0.5
            row["pnl"] = round(shares * (payout - price) + fee(shares, price, role), 4)
            ask = float(t.get("ask_at_entry") or 0)
            if paper and ask:
                row["pnl_taker_bound"] = round(shares * (payout - ask) + fee(shares, ask, "taker"), 4)
            else:
                row["pnl_taker_bound"] = row["pnl"]
        rows.append(row)
    return rows


def summarize(rows: list[dict]) -> dict:
    """Per-strategy realized stats + overall equity curve."""
    by = defaultdict(list)
    for r in rows:
        by[r["strategy"]].append(r)
    out = {}
    for strat, rs in by.items():
        res = sorted([r for r in rs if r["resolved"]], key=lambda r: r["timestamp"])
        open_ = [r for r in rs if not r["resolved"]]
        cost = sum(r["cost"] for r in res)
        pnl = sum(r["pnl"] for r in res)
        pnl_t = sum(r["pnl_taker_bound"] for r in res)
        rets = [r["pnl"] / r["cost"] for r in res if r["cost"] > 0]
        if len(rets) >= 2:
            m = sum(rets) / len(rets)
            sd = math.sqrt(sum((x - m) ** 2 for x in rets) / (len(rets) - 1)) or 1e-6
            sharpe = m / sd
        else:
            sharpe = 0.0
        eq = peak = dd = 0.0
        for r in res:
            eq += r["pnl"]
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
        wins = sum(1 for r in res if r.get("won"))
        avg_price = (sum(r["price"] for r in res) / len(res)) if res else 0
        out[strat] = {
            "resolved": len(res),
            "open": len(open_),
            "open_cost": round(sum(r["cost"] for r in open_), 2),
            "wins": wins,
            "losses": len(res) - wins,
            "win_rate": round(wins / len(res), 3) if res else 0.0,
            "breakeven_win_rate": round(avg_price, 3),
            "deployed": round(cost, 2),
            "pnl": round(pnl, 2),
            "pnl_taker_bound": round(pnl_t, 2),
            "roi": round(pnl / cost, 4) if cost else 0.0,
            "roi_taker_bound": round(pnl_t / cost, 4) if cost else 0.0,
            "sharpe": round(sharpe, 3),
            "max_drawdown": round(dd, 2),
            "paper_share": round(sum(1 for r in res if r["paper"]) / len(res), 2) if res else 1.0,
        }
    return out


def build(refresh_settlements: bool = True) -> tuple[list[dict], dict]:
    trades = load_trades()
    cache = refresh(trades) if refresh_settlements else _load_cache()
    rows = score(trades, cache)
    return rows, summarize(rows)


def main():
    logging.basicConfig(level=logging.WARNING)
    rows, summary = build()
    print(f"{'strategy':<16}{'resolved':>9}{'open':>6}{'win%':>7}{'BE%':>6}{'P&L':>9}{'P&L(taker)':>12}{'ROI':>8}{'maxDD':>8}")
    for s, v in summary.items():
        print(f"{s:<16}{v['resolved']:>9}{v['open']:>6}{v['win_rate']*100:>6.0f}%{v['breakeven_win_rate']*100:>5.0f}%"
              f"{v['pnl']:>9.2f}{v['pnl_taker_bound']:>12.2f}{v['roi']*100:>7.1f}%{v['max_drawdown']:>8.2f}")
    if not summary:
        print("(no Polymarket US trades yet)")


if __name__ == "__main__":
    main()
