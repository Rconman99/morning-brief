"""Portfolio tracker — monitors live positions, P&L, and resolved markets.

Run: .venv/bin/python agent/tracker.py
Shows: open orders, active positions, resolved P&L, overall performance.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import os
import requests
from datetime import datetime


def load_env():
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def get_client():
    """py-clob-client-v2 client used by tracker.main()'s `client.get_open_orders()` call.

    Two-step init pattern required by v2: build an L1-only client to derive
    API creds, then a full L1 + L2 client for authenticated requests.
    """
    key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    if not key:
        return None
    from py_clob_client_v2 import ClobClient
    boot = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=137,
        key=key,
        signature_type=0,
    )
    creds = boot.create_or_derive_api_key()
    return ClobClient(
        host="https://clob.polymarket.com",
        chain_id=137,
        key=key,
        creds=creds,
        signature_type=0,
    )


def get_positions(wallet: str) -> list:
    resp = requests.get(
        "https://data-api.polymarket.com/positions",
        params={"user": wallet, "sizeThreshold": "0.01"},
        timeout=15,
    )
    return resp.json() if resp.status_code == 200 else []


def get_activity(wallet: str, limit: int = 50) -> list:
    resp = requests.get(
        "https://data-api.polymarket.com/activity",
        params={"user": wallet, "limit": str(limit)},
        timeout=15,
    )
    return resp.json() if resp.status_code == 200 else []


def load_positions() -> list:
    """Return ACTIVE positions for the wallet derived from POLYMARKET_PRIVATE_KEY.

    Imported by agent/close_positions.py and agent.run.auto_exit_winners — both
    expected this helper to exist. Filters to currentValue > 0 so already-redeemed
    positions don't get spurious SELL orders placed on them.
    """
    load_env()
    key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    if not key:
        return []
    from eth_account import Account
    wallet = Account.from_key(key).address
    positions = get_positions(wallet)
    return [p for p in positions if (p.get("currentValue") or 0) > 0]


def main():
    load_env()

    from eth_account import Account
    key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    acct = Account.from_key(key)
    wallet = acct.address

    print(f"\n{'='*60}")
    print(f"  POLYMARKET AGENT TRACKER — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Wallet: {wallet}")
    print(f"{'='*60}\n")

    # Open orders via CLOB
    client = get_client()
    if client:
        orders = client.get_open_orders()
        if isinstance(orders, list) and orders:
            print(f"OPEN ORDERS ({len(orders)})")
            for o in orders:
                if isinstance(o, dict):
                    print(f"  {o.get('side','?')} {o.get('original_size', o.get('size','?'))} shares @ {o.get('price','?')} | {o.get('status','?')}")
            print()

    # Positions via Data API
    positions = get_positions(wallet)
    if not positions:
        print("No positions found.\n")
        return

    active = [p for p in positions if (p.get("currentValue") or 0) > 0]
    resolved = [p for p in positions if (p.get("currentValue") or 0) == 0]

    # Active positions
    if active:
        print(f"ACTIVE POSITIONS ({len(active)})")
        print(f"{'Market':<45} {'Side':<5} {'Shares':>7} {'Avg':>6} {'Now':>6} {'Value':>8} {'P&L':>8} {'P&L%':>7}")
        print("-" * 93)
        total_value = 0
        total_cost = 0
        for p in sorted(active, key=lambda x: x.get("currentValue", 0), reverse=True):
            title = p.get("title", "")[:44]
            outcome = p.get("outcome", "?")[:4]
            size = p.get("size", 0)
            avg = p.get("avgPrice", 0)
            cur = p.get("curPrice", 0)
            value = p.get("currentValue", 0)
            cost = p.get("initialValue", 0)
            pnl = p.get("cashPnl", 0)
            pnl_pct = p.get("percentPnl", 0)
            total_value += value
            total_cost += cost
            icon = "+" if pnl >= 0 else ""
            print(f"  {title:<43} {outcome:<5} {size:>7.1f} {avg:>6.3f} {cur:>6.3f} ${value:>7.2f} {icon}${pnl:>6.2f} {pnl_pct:>+6.1f}%")
        print("-" * 93)
        total_pnl = total_value - total_cost
        print(f"  {'TOTAL':<43} {'':5} {'':>7} {'':>6} {'':>6} ${total_value:>7.2f} {'+'if total_pnl>=0 else ''}${total_pnl:>6.2f}")
        print()

    # Resolved positions (wins/losses)
    wins = [p for p in resolved if (p.get("cashPnl") or 0) > 0]
    losses = [p for p in resolved if (p.get("cashPnl") or 0) < 0]
    breakeven = [p for p in resolved if (p.get("cashPnl") or 0) == 0]

    if resolved:
        total_resolved_pnl = sum(p.get("cashPnl", 0) for p in resolved)
        win_rate = len(wins) / len(resolved) * 100 if resolved else 0
        print(f"RESOLVED ({len(resolved)} total: {len(wins)}W / {len(losses)}L / {len(breakeven)}BE)")
        print(f"  Win rate: {win_rate:.0f}%")
        print(f"  Resolved P&L: ${'+'if total_resolved_pnl>=0 else ''}{total_resolved_pnl:.2f}")
        if wins:
            print(f"  Biggest win: ${max(p.get('cashPnl',0) for p in wins):.2f}")
        if losses:
            print(f"  Biggest loss: ${min(p.get('cashPnl',0) for p in losses):.2f}")
        print()

    # Recent activity
    activity = get_activity(wallet, limit=10)
    if activity:
        print(f"RECENT TRADES ({len(activity)})")
        for a in activity[:5]:
            ts = datetime.fromtimestamp(a.get("timestamp", 0)).strftime("%m/%d %H:%M")
            side = a.get("side", "?")
            size = a.get("size", 0)
            price = a.get("price", 0)
            usdc = a.get("usdcSize", 0)
            title = a.get("title", "")[:40]
            outcome = a.get("outcome", "")
            print(f"  [{ts}] {side} {outcome} — {title} | {size:.0f} @ {price:.3f} (${usdc:.2f})")
        print()

    # Overall summary
    all_pnl = sum(p.get("cashPnl", 0) for p in positions)
    all_invested = sum(p.get("initialValue", 0) for p in positions)
    all_current = sum(p.get("currentValue", 0) for p in positions)
    all_realized = sum(p.get("realizedPnl", 0) for p in positions)

    print(f"{'='*60}")
    print(f"  OVERALL: {len(positions)} positions | ${all_invested:.2f} invested")
    print(f"  Current value: ${all_current:.2f}")
    print(f"  Unrealized P&L: ${'+'if (all_current-all_invested)>=0 else ''}{all_current-all_invested:.2f}")
    print(f"  Realized P&L: ${'+'if all_realized>=0 else ''}{all_realized:.2f}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
