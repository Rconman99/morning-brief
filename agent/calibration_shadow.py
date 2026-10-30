"""Summarize the watch-only calibration log.

Shadow mode does not change which bets are placed. It writes one JSON line per
weather_us or gimme_bets candidate to agent/calibration_shadow.jsonl. This
script counts, over a date range, how many of those lines would have been
skipped or resized, how that compares with bets actually placed, and the
settled P&L of the placed bets the calibrated rule would have skipped.

It does not place orders and it does not refresh settlements from the network.
A bet with no settlement yet is counted as unsettled and left out of the P&L.

    .venv/bin/python agent/calibration_shadow.py
    .venv/bin/python agent/calibration_shadow.py --from 2026-10-15 --to 2026-10-29
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.ledger import CACHE, TRADE_LOG, score
from agent.prob_calibration import SHADOW_LOG
from agent.proposal_log import eastern_day

_STRATEGIES = ("weather_us", "gimme_bets")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _day(ts: str) -> str | None:
    if not ts:
        return None
    try:
        return eastern_day(str(ts))
    except (TypeError, ValueError):
        text = str(ts)
        return text[:10] if len(text) >= 10 else None


def _in_range(day: str | None, start: str | None, end: str | None) -> bool:
    if day is None:
        return False
    if start and day < start:
        return False
    if end and day > end:
        return False
    return True


def _load_cache(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"settlements": {}, "fills": {}}
    if not isinstance(payload, dict):
        return {"settlements": {}, "fills": {}}
    payload.setdefault("settlements", {})
    payload.setdefault("fills", {})
    return payload


def summarize_shadow(start: str | None = None, end: str | None = None, *,
                     shadow_path: Path | None = None, trades_path: Path | None = None,
                     cache_path: Path | None = None) -> dict:
    """Counts and settled P&L for one inclusive US/Eastern date window.

    ``start`` and ``end`` are ``YYYY-MM-DD``. Omit either side to leave it open.
    P&L uses the ledger's settlement cache only. Unsettled bets are counted
    separately and do not contribute a number.
    """
    shadow_rows = [
        row for row in _read_jsonl(Path(shadow_path) if shadow_path else SHADOW_LOG)
        if row.get("strategy") in _STRATEGIES and _in_range(_day(row.get("timestamp") or ""), start, end)
    ]
    trades = [
        row for row in _read_jsonl(Path(trades_path) if trades_path else TRADE_LOG)
        if row.get("strategy") in _STRATEGIES
        and str(row.get("side") or "").upper() == "BUY"
        and row.get("status") in ("paper_filled", "submitted")
        and _in_range(_day(row.get("timestamp") or ""), start, end)
    ]
    would_skip_days = {}
    for row in shadow_rows:
        if not row.get("decision_kept") or not row.get("would_skip"):
            continue
        key = (
            row.get("strategy"),
            row.get("slug") or "",
            str(row.get("side") or "").lower(),
            _day(row.get("timestamp") or ""),
        )
        would_skip_days[key] = True

    cache = _load_cache(Path(cache_path) if cache_path else CACHE)
    scored_by = {}
    for row in score(trades, cache):
        ident = (
            row.get("timestamp") or "",
            row.get("strategy"),
            row.get("slug") or "",
            str(row.get("outcome") or "").lower(),
        )
        scored_by[ident] = row

    settled_n = 0
    unsettled_n = 0
    pnl = 0.0
    for trade in trades:
        key = (
            trade.get("strategy"),
            trade.get("slug") or "",
            str(trade.get("token_hint") or "yes").lower(),
            _day(trade.get("timestamp") or ""),
        )
        if key not in would_skip_days:
            continue
        hit = scored_by.get((
            trade.get("timestamp") or "",
            trade.get("strategy"),
            trade.get("slug") or "",
            str(trade.get("token_hint") or "yes").lower(),
        ))
        if not hit or not hit.get("resolved"):
            unsettled_n += 1
            continue
        settled_n += 1
        pnl += float(hit.get("pnl") or 0.0)

    kept = [row for row in shadow_rows if row.get("decision_kept")]
    return {
        "start": start,
        "end": end,
        "shadow_rows": len(shadow_rows),
        "would_skip": sum(1 for row in shadow_rows if row.get("would_skip")),
        "would_resize": sum(1 for row in shadow_rows if row.get("would_resize")),
        "decisions_kept": len(kept),
        "kept_would_skip": sum(1 for row in kept if row.get("would_skip")),
        "actual_bets": len(trades),
        "settled_would_skip_bets": settled_n,
        "unsettled_would_skip_bets": unsettled_n,
        "pnl_would_skip": round(pnl, 4),
    }


def format_summary(summary: dict) -> str:
    start = summary.get("start") or "beginning"
    end = summary.get("end") or "latest"
    pnl = summary.get("pnl_would_skip") or 0.0
    return "\n".join([
        f"Calibration shadow {start} .. {end}",
        f"candidates logged: {summary.get('shadow_rows', 0)}",
        f"would have been skipped: {summary.get('would_skip', 0)}",
        f"would have been resized: {summary.get('would_resize', 0)}",
        f"decisions the bot kept: {summary.get('decisions_kept', 0)}",
        f"kept bets the calibrated rule would have skipped: {summary.get('kept_would_skip', 0)}",
        f"actual bets placed in this window: {summary.get('actual_bets', 0)}",
        f"settled bets that would have been skipped: {summary.get('settled_would_skip_bets', 0)}",
        f"P&L of those settled bets: ${pnl:.2f}",
        f"unsettled bets that would have been skipped: {summary.get('unsettled_would_skip_bets', 0)}",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize calibration shadow logs")
    parser.add_argument("--from", dest="start", default=None, help="First US/Eastern date, YYYY-MM-DD")
    parser.add_argument("--to", dest="end", default=None, help="Last US/Eastern date, YYYY-MM-DD")
    args = parser.parse_args(argv)
    print(format_summary(summarize_shadow(args.start, args.end)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
