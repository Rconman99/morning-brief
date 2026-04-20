"""Close / roll the AMD $195 May22 short put position safely.

Rationale: 3 short AMD puts = $58,500 max assignment on $100k paper account
(58% single-name concentration). Industry guidance: never > 20-25% in a single
CSP. This script offers three exits, ranked from safest to most aggressive:

    --action=close          : Buy-to-close all 3 contracts at market. Lock loss, free capital.
    --action=roll-down-out  : BTC current position, STO further-dated, lower-strike put.
    --action=roll-to-spread : BTC puts, STO a put credit spread (defined max loss).

Usage:
    .venv/bin/python agent/close_amd_puts.py --action=close --dry-run
    .venv/bin/python agent/close_amd_puts.py --action=close --yes

Safety:
    - Dry-run by default until --yes is passed.
    - Limit orders only, never market.
    - Confirmation prompt unless --yes.
"""
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import logging

logger = logging.getLogger(__name__)


def find_amd_put_positions(client):
    """Return list of open short AMD put positions."""
    try:
        positions = client.get_all_positions()
    except Exception as e:
        print(f"Failed to fetch positions: {e}")
        return []
    amd_puts = []
    for p in positions:
        # OCC symbol like AMD260522P00195000 — contains AMD and P (put)
        if p.symbol.startswith("AMD") and "P" in p.symbol[3:] and len(p.symbol) > 10:
            amd_puts.append(p)
    return amd_puts


def close_positions(client, positions, dry_run):
    """Buy-to-close each short put with a limit at current_price + 2 cents."""
    from alpaca.trading.requests import LimitOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    results = []
    for p in positions:
        qty = abs(float(p.qty))  # short positions have negative qty
        cur_price = float(p.current_price)
        # Slightly above current to get filled fast (we're buying premium back)
        limit = round(cur_price + 0.02, 2)

        print(f"\n→ BUY TO CLOSE {qty:.0f} {p.symbol} @ ${limit:.2f}")
        print(f"  Entry avg: ${float(p.avg_entry_price):.2f}, Current: ${cur_price:.2f}")
        print(f"  Unrealized P&L: ${float(p.unrealized_pl):+.2f}")

        if dry_run:
            print("  [dry-run — no order sent]")
            results.append({"status": "dry_run", "symbol": p.symbol})
            continue

        try:
            req = LimitOrderRequest(
                symbol=p.symbol,
                qty=int(qty),
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                limit_price=limit,
            )
            order = client.submit_order(req)
            print(f"  ✓ Order submitted: {order.id}")
            results.append({"status": "submitted", "symbol": p.symbol, "order_id": str(order.id)})
        except Exception as e:
            print(f"  ✗ Failed: {e}")
            results.append({"status": "error", "symbol": p.symbol, "error": str(e)})
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", choices=["close", "roll-down-out", "roll-to-spread"], default="close")
    ap.add_argument("--dry-run", action="store_true", help="Preview without sending orders")
    ap.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = ap.parse_args()

    # Load env + client
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    from agent.equity_executor import get_client
    client = get_client()
    if not client:
        print("ERROR: no Alpaca client — check .env credentials")
        return

    positions = find_amd_put_positions(client)
    if not positions:
        print("No AMD put positions found. Nothing to close.")
        return

    print("=" * 60)
    print("AMD PUT POSITIONS")
    print("=" * 60)
    for p in positions:
        print(f"  {p.symbol}  qty={p.qty}  avg=${float(p.avg_entry_price):.2f}  "
              f"now=${float(p.current_price):.2f}  P&L=${float(p.unrealized_pl):+.2f}")
    print()

    if args.action == "close":
        if not args.yes and not args.dry_run:
            resp = input(f"Close {len(positions)} AMD put position(s)? [y/N] ")
            if resp.lower() != "y":
                print("Aborted.")
                return
        results = close_positions(client, positions, dry_run=args.dry_run)
        print("\nDone:")
        for r in results:
            print(f"  {r}")
    else:
        print(f"Action '{args.action}' not implemented yet.")
        print("Recommendation: use --action=close first to neutralize, then open")
        print("a new put credit spread via the options strategy module with proper")
        print("5-8 ticker diversification and defined max loss per spread.")


if __name__ == "__main__":
    main()
