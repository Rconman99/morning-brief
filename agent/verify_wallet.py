"""Verify the bot can trade on Polymarket CLOB V2.

Usage (from /opt/morning-brief):
    .venv/bin/python agent/verify_wallet.py            # status only
    .venv/bin/python agent/verify_wallet.py --canary   # also place + cancel a tiny order

Status: prints signer, resolved account wallet + type, collateral balance,
open orders. Nothing is sent.

--canary: places a 5-share BUY limit at $0.01 (max cost $0.05) on a liquid
market, confirms the CLOB *accepted* it (the thing that has been failing since
June 28), then cancels it immediately. It should never fill at that price, so
the only cost is a couple of cents of risk for a few seconds.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import os
import time


def load_env():
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--canary", action="store_true", help="Place and cancel a tiny test order")
    ap.add_argument("--slug", default="", help="Market slug for the canary (default: auto-pick)")
    args = ap.parse_args()

    load_env()
    from agent.pm_client import (
        get_client, get_signer_address, get_wallet_address,
        get_collateral_balance, list_open_orders,
    )

    print("signer (EOA):     ", get_signer_address() or "(no key)")
    print("POLYMARKET_WALLET:", os.environ.get("POLYMARKET_WALLET", "") or "(unset → SDK default)")

    client = get_client()
    if not client:
        print("\nFAIL: could not create client (see log above).")
        return 2

    print("account wallet:   ", client.wallet)
    print("wallet type:      ", client.wallet_type, "(V2 wants DEPOSIT_WALLET)")
    print("collateral (pUSD):", f"${get_collateral_balance():.2f}")
    orders = list_open_orders()
    print("open orders:      ", len(orders))
    for o in orders[:10]:
        print(f"   {o['side']} {o['original_size']:.1f} @ {o['price']:.2f} [{o['status']}] {o['id'][:12]}…")

    if not args.canary:
        print("\nOK — status only. Add --canary to test order acceptance.")
        return 0

    # --- canary order -------------------------------------------------------
    slug = args.slug
    if not slug:
        # Pick the highest-volume open binary market.
        try:
            page = client.list_markets(closed=False, order="volumeNum", ascending=False, page_size=20).first_page()
            for m in page.items:
                if m.slug and m.outcomes and m.outcomes.yes and (m.outcomes.yes.position_id or m.outcomes.yes.token_id):
                    slug = m.slug
                    break
        except Exception as e:
            print(f"\nCould not auto-pick a market ({e}); pass --slug <market-slug>.")
            return 2
    print(f"\ncanary market:     {slug}")

    from agent.pm_client import resolve_order_asset
    ids = resolve_order_asset(slug)
    if not ids:
        print("FAIL: could not resolve asset ids for that slug.")
        return 2
    asset = ids["yes"]
    print(f"canary asset:      {asset[:16]}… (market version {ids.get('version')})")

    try:
        resp = client.place_limit_order(asset_id=asset, price=0.01, size=5, side="BUY")
    except Exception as e:
        print(f"\nFAIL: order raised: {e}")
        return 1

    if not getattr(resp, "ok", False):
        print(f"\nFAIL: CLOB rejected the order: {getattr(resp, 'code', '')} {getattr(resp, 'message', resp)}")
        return 1

    print(f"ACCEPTED: order {resp.order_id} status={resp.status}")
    time.sleep(1)
    try:
        cres = client.cancel_order(order_id=resp.order_id)
        print(f"cancelled: {cres}")
    except Exception as e:
        print(f"WARNING: cancel failed ({e}) — cancel it manually on polymarket.com; it's a $0.05 bid at 1¢.")
        return 1

    print("\nOK — the CLOB accepts orders from this wallet. The bot is unblocked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
