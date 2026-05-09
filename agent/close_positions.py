"""Close Polymarket positions that have run to near-resolution.

Use when the 80%/95% exposure cap is saturated and you want to free capacity
for new trades. Lists positions at >= threshold and optionally places SELL
limit orders at 1-2 ticks below the current bid.

Usage:
  .venv/bin/python agent/close_positions.py --list                   # Show candidates
  .venv/bin/python agent/close_positions.py --threshold 0.99         # Filter tighter
  .venv/bin/python agent/close_positions.py --close --threshold 0.99 # Place sells (LIVE)
  .venv/bin/python agent/close_positions.py --dry-run --close        # Paper/preview

Safety:
  - Limit orders only. Never market orders.
  - Sells at max(bid, target_price - 0.02) so we don't give up too much.
  - Prompts for confirmation before sending live orders.
"""
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import logging

from agent.executor import get_client, get_mode
from agent.tracker import load_positions

logger = logging.getLogger(__name__)


def list_candidates(positions, threshold):
    candidates = [p for p in positions if p.get("curPrice", 0) >= threshold]
    print(f"\nPositions at or above {threshold:.0%}:\n")
    if not candidates:
        print("  (none)")
        return candidates
    for p in candidates:
        title = p.get("title", "")[:55]
        side = p.get("outcome", "?")
        shares = p.get("size", 0)
        cur = p.get("curPrice", 0)
        print(f"  {title:<57} {side:<4} {shares:>6.1f}sh  "
              f"@ {cur:.4f}  = ${cur*shares:>6.2f}")
    total = sum(p.get("curPrice", 0) * p.get("size", 0) for p in candidates)
    print(f"\n  Total cash freed if all closed: ~${total:.2f}")
    return candidates


def close_position(client, position, dry_run=False):
    """Place a SELL limit order one cent below current price.

    Position dict comes from the Polymarket data-api: keys `asset` (token_id),
    `size` (shares), `curPrice`, `title`. The 0.90 floor prevents fat-finger
    sells on positions that crashed (e.g., a NO bet now showing big losses).
    """
    from py_clob_client.clob_types import OrderArgs, OrderType

    asset = position["asset"]
    shares = position.get("size", 0)
    cur_price = position.get("curPrice", 0)
    target = max(0.90, cur_price - 0.01)  # one tick below, never below $0.90

    print(f"\n→ SELL {shares:.1f} @ {target:.4f}  ({position.get('title', '')[:60]})")
    if dry_run:
        print("  [dry-run — no order sent]")
        return {"status": "dry_run"}

    try:
        order_args = OrderArgs(
            token_id=asset,
            price=target,
            size=shares,
            side="SELL",
        )
        signed = client.create_order(order_args)
        resp = client.post_order(signed, OrderType.GTC)
        print(f"  ✓ {resp}")
        return {"status": "submitted", "response": resp}
    except Exception as e:
        print(f"  ✗ {e}")
        return {"status": "error", "error": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.99,
                    help="Price threshold (default 0.99)")
    ap.add_argument("--list", action="store_true", help="List candidates only")
    ap.add_argument("--close", action="store_true", help="Actually place SELL orders")
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview orders without sending")
    ap.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = ap.parse_args()

    positions = load_positions()
    candidates = list_candidates(positions, args.threshold)

    if args.list or not args.close:
        return

    if not candidates:
        return

    mode = get_mode()
    print(f"\nMode: {mode}  |  Dry run: {args.dry_run}")

    if not args.yes and not args.dry_run:
        confirm = input(f"\nPlace SELL orders for {len(candidates)} positions? [y/N] ")
        if confirm.lower() != "y":
            print("Aborted.")
            return

    client = None if args.dry_run else get_client()
    for p in candidates:
        close_position(client, p, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
