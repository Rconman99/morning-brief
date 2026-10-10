"""Verify the bot's Polymarket US setup.

Usage (from /opt/morning-brief):
    .venv/bin/python agent/verify_us.py             # keys, balance, open orders, positions
    .venv/bin/python agent/verify_us.py --preview   # also ask the exchange to validate a
                                                    # 1-share order (nothing is placed)

Exit code 0 = ready to trade live; 1 = auth/validation problem; 2 = config problem.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import os


def load_env():
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="Validate a 1-share order server-side (not placed)")
    ap.add_argument("--slug", default="", help="Market slug for the preview (default: best gimme candidate or a liquid market)")
    args = ap.parse_args()

    load_env()
    from agent import pm_us

    print("POLYMARKET_VENUE:  ", pm_us.venue(), "" if pm_us.is_us() else "  <-- set POLYMARKET_VENUE=us in .env")
    print("US API keys:       ", "present" if pm_us.has_keys() else "MISSING (POLYMARKET_US_KEY_ID / POLYMARKET_US_SECRET_KEY)")
    if not pm_us.has_keys():
        return 2

    client = pm_us.get_client()
    if not client:
        print("FAIL: could not build client"); return 1

    try:
        bals = client.account.balances().get("balances", [])
    except Exception as e:
        print(f"FAIL: balances call rejected — {e}")
        print("      (bad key/secret, key revoked, or account not approved for API trading)")
        return 1
    b = bals[0] if bals else {}
    print(f"balance:            ${float(b.get('currentBalance') or 0):.2f}   buying power: ${float(b.get('buyingPower') or 0):.2f}   "
          f"open orders: ${float(b.get('openOrders') or 0):.2f}   unsettled: ${float(b.get('unsettledFunds') or 0):.2f}")

    orders = pm_us.list_open_orders()
    print("open orders:       ", len(orders))
    for o in orders[:10]:
        print(f"   {o['side']} {o['original_size']:.0f} @ {o['price']:.3f} [{o['status'][12:]}] {o['slug']}  {o['title'][:40]}")
    positions = pm_us.load_positions()
    print("positions:         ", len(positions))
    for p in positions[:10]:
        print(f"   {p['outcome'].upper():<3} {p['size']:.0f} sh  avg {p['avgPrice']:.3f}  bid {p['curPrice']:.3f}  {p['slug']}  {p['title'][:40]}")

    if not args.preview:
        print("\nOK — auth works. Add --preview to validate order sizing/risk server-side.")
        return 0

    slug = args.slug
    if not slug:
        sig = PROJECT_ROOT / "data" / "processed" / "polymarket_us.json"
        if sig.exists():
            try:
                g = json.loads(sig.read_text()).get("data", {}).get("gimme_bets", [])
                if g:
                    slug = g[0]["slug"]
            except Exception:
                pass
    if not slug:
        ms = pm_us.list_markets({"limit": 20, "active": True, "closed": False})
        slug = next((m["slug"] for m in ms if m.get("slug")), "")
    if not slug:
        print("No market available for the preview."); return 2

    bbo = pm_us.get_bbo(slug)
    price = max(0.01, round((bbo.get("yes_bid") or 0.05) * 0.5, 3))  # a bid far below market: valid, would never fill
    print(f"\npreview market:     {slug}  (state {bbo.get('state','?')}, yes bid {bbo.get('yes_bid',0):.3f})")
    res = pm_us.preview_limit(slug, "BUY", "yes", price, 1)
    if res.get("ok"):
        o = res.get("preview") or {}
        print(f"preview YES ACCEPTED: state={o.get('state')} price={o.get('price')} qty={o.get('quantity')} intent={o.get('intent')}")
        # NO side: a NO bid far below market (NO at 0.02 => wire YES price 0.98? no — NO at
        # (1 - yes_ask) - margin). We bid NO at half its current bid so it would never fill,
        # and confirm the exchange echoes the YES-terms wire price we expect.
        no_bid = max(0.01, round((1 - (bbo.get("yes_ask") or 0.95)) * 0.5, 3))
        res_no = pm_us.preview_limit(slug, "BUY", "no", no_bid, 1)
        if not res_no.get("ok"):
            print(f"preview NO REJECTED: {res_no.get('error')}  request={json.dumps(res_no.get('request'))}")
            return 1
        on = res_no.get("preview") or {}
        sent = res_no["request"]["price"]["value"]
        print(f"preview NO  ACCEPTED: NO bid {no_bid} sent as YES-terms {sent}; exchange echoed price={on.get('price')} intent={on.get('intent')}")
        print("\nOK — the exchange validates YES and NO orders from this key. The bot can trade live.")
        return 0
    print(f"preview REJECTED:   {res.get('error')}")
    print(f"request was:        {json.dumps(res.get('request'))}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
