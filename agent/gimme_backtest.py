"""Replay logged gimme proposals under the new rules. Offline. No orders.

Reads proposals.jsonl and ledger settlements when those files exist. A rule is
applied only when the logged fields can support it. A missing bid is unknown,
not proof the book was one-sided — older rows never stored market_bid.

    .venv/bin/python agent/gimme_backtest.py
    .venv/bin/python agent/gimme_backtest.py --proposals agent/proposals.jsonl --settlements agent/ledger_cache.json
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json

from agent.gimme_rules import replay_decision


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path or not path.exists():
        return rows
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def load_settlements(path: Path | None) -> dict:
    if not path or not path.exists():
        return {}
    try:
        blob = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    st = blob.get("settlements", blob) if isinstance(blob, dict) else {}
    if not isinstance(st, dict):
        return {}
    return st


def gimme_rows(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        if row.get("record") not in (None, "proposal", "gimme_candidate"):
            continue
        if row.get("strategy") != "gimme_bets":
            continue
        out.append(row)
    return out


def _won(row: dict, settlements: dict):
    slug = row.get("slug") or ""
    if slug not in settlements:
        return None
    try:
        yes = float(settlements[slug])
    except (TypeError, ValueError):
        return None
    side = str(row.get("side") or "").lower()
    if side not in ("yes", "no"):
        return None
    return yes >= 0.5 if side == "yes" else yes < 0.5


def _hit_rate(flags: list[bool | None]) -> dict:
    known = [f for f in flags if f is not None]
    wins = sum(1 for f in known if f)
    return {
        "n": len(flags),
        "settled": len(known),
        "wins": wins,
        "hit_rate": (wins / len(known)) if known else None,
    }


def summarize(rows: list[dict], settlements: dict | None = None) -> dict:
    """How many historical gimme rows the new rules would keep, and the hit rate.

    ``would_keep`` is only True when every rule is decidable from the row.
    """
    settlements = settlements or {}
    gimmes = gimme_rows(rows)
    kept_hits = []
    reject_hits = []
    inconclusive = 0
    missing_counts = {}
    crypto_hits = []
    book_tested = []
    edge_tested = []
    for row in gimmes:
        decision = replay_decision(row)
        won = _won(row, settlements)
        for name in decision["missing"]:
            missing_counts[name] = missing_counts.get(name, 0) + 1
        if decision["would_keep"] is True:
            kept_hits.append(won)
        elif decision["would_keep"] is False:
            reject_hits.append(won)
        else:
            inconclusive += 1
        if decision["checks"].get("crypto_slug_skip") is False:
            crypto_hits.append(won)
        if decision["checks"].get("two_sided") is not None and decision["checks"].get("spread") is not None:
            passed_book = (
                decision["checks"]["two_sided"]
                and decision["checks"]["spread"]
                and decision["checks"].get("mid_threshold") is True
            )
            if passed_book:
                book_tested.append(won)
        if decision["checks"].get("own_edge") is True:
            edge_tested.append(won)
    return {
        "gimme_rows": len(gimmes),
        "baseline_settled": _hit_rate([_won(r, settlements) for r in gimmes]),
        "would_keep": _hit_rate(kept_hits),
        "proven_reject": _hit_rate(reject_hits),
        "inconclusive": inconclusive,
        "missing_fields": missing_counts,
        "crypto_slug_rejected": _hit_rate(crypto_hits),
        "passed_book_where_logged": _hit_rate(book_tested),
        "passed_own_edge_where_logged": _hit_rate(edge_tested),
        "cannot_test": [
            "Two-sided book, spread, and the midpoint threshold need market_bid and market_ask. Older gimme rows left market_bid empty, so those rules are inconclusive there — an empty bid is not treated as a zero bid.",
            "Own-edge needs model_prob plus a price. Gimme rows logged before a model existed cannot validate the edge rule; the live gate would now reject them for having no model.",
            "Minimum time-to-resolution needs hours_to_expiry or end_date. A date inside the slug is not a clock time, so it is not used.",
            "The combined crypto cap is a portfolio limit. This replay does not rebuild the order book of open trades, so it does not say whether ETH/SOL/XRP would have been blocked together.",
        ],
    }


def _fmt_rate(block: dict) -> str:
    rate = block.get("hit_rate")
    rate_s = f"{rate * 100:.1f}%" if rate is not None else "n/a"
    return f"n={block.get('n', 0)} settled={block.get('settled', 0)} wins={block.get('wins', 0)} hit={rate_s}"


def render(summary: dict) -> str:
    lines = [
        "Gimme replay (offline, no orders)",
        f"gimme rows: {summary['gimme_rows']}",
        f"baseline (all settled gimmes): {_fmt_rate(summary['baseline_settled'])}",
        f"new rules would keep:          {_fmt_rate(summary['would_keep'])}",
        f"new rules proven reject:       {_fmt_rate(summary['proven_reject'])}",
        f"inconclusive:                  {summary['inconclusive']}",
        f"crypto slug rejected:          {_fmt_rate(summary['crypto_slug_rejected'])}",
        f"passed book where quotes exist:{_fmt_rate(summary['passed_book_where_logged'])}",
        f"passed own-edge where logged:  {_fmt_rate(summary['passed_own_edge_where_logged'])}",
        "missing fields (rows where that rule could not be decided): "
        + (", ".join(f"{k}={v}" for k, v in sorted(summary["missing_fields"].items())) or "none"),
        "Cannot test from this log:",
    ]
    lines.extend(f"- {item}" for item in summary["cannot_test"])
    return "\n".join(lines)


def default_proposal_path() -> Path | None:
    for rel in ("agent/proposals.jsonl", "data/proposals.jsonl"):
        path = PROJECT_ROOT / rel
        if path.exists():
            return path
    return None


def default_settlement_path() -> Path | None:
    for rel in ("agent/ledger_cache.json", "data/ledger_cache.json"):
        path = PROJECT_ROOT / rel
        if path.exists():
            return path
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay historical gimme rows under the new rules")
    ap.add_argument("--proposals", default="")
    ap.add_argument("--settlements", default="")
    args = ap.parse_args()
    prop_path = Path(args.proposals) if args.proposals else default_proposal_path()
    set_path = Path(args.settlements) if args.settlements else default_settlement_path()
    if prop_path is None or not prop_path.exists():
        print("No proposals.jsonl found (looked in agent/ and data/). Nothing to replay.")
        print("The book, edge, and time rules cannot be tested until those fields are logged.")
        return 0
    rows = load_jsonl(prop_path)
    settlements = load_settlements(set_path) if set_path else {}
    print(f"proposals: {prop_path}")
    print(f"settlements: {set_path if set_path and set_path.exists() else 'none'}")
    print(render(summarize(rows, settlements)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
