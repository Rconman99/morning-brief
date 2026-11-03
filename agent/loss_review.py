"""Tag settled paper losses and compare model vs market Brier.

Read-only. This script does not place orders, refresh settlements, or change
sizing, gates, or calibration. It reads the trade log, the settlement cache,
and the proposal log, then prints a summary.

Cause tags (first match wins):

1. overconfident — on a losing side, model_prob is at least OVERCONFIDENT_GAP
   (0.15) above market_prob. The model was farther from the market and wrong.
2. stale_info — forecast_age_hours or hours_to_expiry is at least STALE_HOURS
   (36), or a recorded settlement timestamp is at least that many hours after
   entry, or the date embedded in the slug is at least STALE_EVENT_DAYS (2)
   calendar days after the entry day.
3. thin_market — only when the field was recorded: spread >= THIN_SPREAD
   (0.06), or depth_shares < THIN_DEPTH_SHARES (50), or liquidity <
   THIN_LIQUIDITY (2000).
4. late_move — a later quote of the same side is at least LATE_MOVE (0.05)
   below the entry market price. The settlement price itself is not a quote.
5. unknown — none of the above.

Brier uses every settled paper trade in the window that has both
probabilities (wins and losses). The recent slice is the last HOLDOUT_DAYS
(14) US/Eastern days ending on --to. Earlier is the rest of the window.

    .venv/bin/python agent/loss_review.py --from YYYY-MM-DD --to YYYY-MM-DD
    .venv/bin/python agent/loss_review.py --from YYYY-MM-DD --to YYYY-MM-DD --json
    .venv/bin/python agent/loss_review.py --from YYYY-MM-DD --to YYYY-MM-DD --save
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.ledger import CACHE, TRADE_LOG, score
from agent.proposal_log import PROPOSAL_LOG, brier_score, eastern_day, implied_prob

CAUSES = ("overconfident", "stale_info", "thin_market", "late_move", "unknown")
CANONICAL = ("weather_us", "gimme", "stage")
OVERCONFIDENT_GAP = 0.15
STALE_HOURS = 36.0
STALE_EVENT_DAYS = 2
THIN_SPREAD = 0.06
THIN_DEPTH_SHARES = 50.0
THIN_LIQUIDITY = 2000.0
LATE_MOVE = 0.05
HOLDOUT_DAYS = 14
REVIEWS_DIR = PROJECT_ROOT / "agent" / "reviews"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_EPS = 1e-9


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


def _num(raw):
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


def _prob(raw):
    value = _num(raw)
    if value is None or not (0.0 <= value <= 1.0):
        return None
    return value


def _price(raw):
    value = _num(raw)
    if value is None or not (0.0 < value < 1.0):
        return None
    return value


def _day(ts) -> str | None:
    if not ts:
        return None
    try:
        return eastern_day(str(ts))
    except (TypeError, ValueError):
        text = str(ts)
        return text[:10] if len(text) >= 10 and text[4:5] == "-" else None


def _parse_ts(ts: str) -> datetime:
    moment = datetime.fromisoformat(str(ts))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _in_range(day: str | None, start: str, end: str) -> bool:
    if day is None:
        return False
    return start <= day <= end


def canonical_strategy(name: str | None) -> str:
    """Roll gimme_bets up to gimme. Keep weather_us and stage as named."""
    key = str(name or "unknown").strip().lower()
    if key in ("gimme", "gimme_bets"):
        return "gimme"
    return key or "unknown"


def _paper_buy(row: dict) -> bool:
    if str(row.get("side") or "").upper() != "BUY":
        return False
    if row.get("status") != "paper_filled":
        return False
    venue = row.get("venue")
    return venue in (None, "", "us")


def _settlement_tables(cache: dict) -> tuple[dict, dict]:
    prices = {}
    times = {}
    for slug, val in (cache.get("settlements") or {}).items():
        if isinstance(val, dict):
            raw = val.get("settlement", val.get("yes"))
            stamp = val.get("settled_at") or val.get("settlement_ts") or val.get("settlement_time")
            if stamp:
                times[slug] = str(stamp)
        else:
            raw = val
        try:
            prices[slug] = float(raw)
        except (TypeError, ValueError):
            continue
    return prices, times


def _market_prob(row: dict):
    if not isinstance(row, dict):
        return None
    for key in ("market_prob", "market_mid"):
        value = _prob(row.get(key))
        if value is not None:
            return value
    return implied_prob(row.get("market_ask"), row.get("market_bid"))


def _side(row: dict) -> str:
    for key in ("token_hint", "outcome", "side"):
        val = str(row.get(key) or "").strip().lower()
        if val in ("yes", "no"):
            return val
    return "yes"


def _match_proposal(trade: dict, proposals: list[dict]) -> dict | None:
    slug = trade.get("slug") or ""
    side = _side(trade)
    stamp = str(trade.get("timestamp") or "")
    strategy = canonical_strategy(trade.get("strategy"))
    candidates = []
    for row in proposals:
        if row.get("record", "proposal") != "proposal":
            continue
        if (row.get("slug") or "") != slug or _side(row) != side:
            continue
        if canonical_strategy(row.get("strategy")) != strategy:
            continue
        candidates.append(row)
    if not candidates:
        return None
    before = [row for row in candidates if str(row.get("timestamp") or "") <= stamp]
    pool = before or candidates
    pool.sort(key=lambda row: str(row.get("timestamp") or ""))
    return pool[-1] if before else pool[0]


def _later_price(trade: dict, records: list[dict]):
    slug = trade.get("slug") or ""
    side = _side(trade)
    stamp = str(trade.get("timestamp") or "")
    later = []
    for row in records:
        if (row.get("slug") or "") != slug or _side(row) != side:
            continue
        if str(row.get("timestamp") or "") <= stamp:
            continue
        price = _market_prob(row)
        if price is None:
            continue
        later.append((str(row.get("timestamp") or ""), price))
    if not later:
        return None
    later.sort()
    return later[-1][1]


def _event_date(slug: str) -> date | None:
    match = _DATE.search(slug or "")
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(0))
    except ValueError:
        return None


def _hours_between(start, end) -> float | None:
    if not start or not end:
        return None
    try:
        return (_parse_ts(end) - _parse_ts(start)).total_seconds() / 3600.0
    except (TypeError, ValueError):
        return None


def _first_num(*values):
    for value in values:
        number = _num(value)
        if number is not None:
            return number
    return None


def _features(trade: dict, proposal: dict | None, records: list[dict], settled_at: str | None) -> dict:
    proposal = proposal or {}
    model = _prob(trade.get("model_prob"))
    if model is None:
        model = _prob(proposal.get("model_prob", proposal.get("estimated_prob")))
    market = _market_prob(trade)
    if market is None:
        market = _market_prob(proposal)
    return {
        "model_prob": model,
        "market_prob": market,
        "spread": _first_num(trade.get("spread"), proposal.get("spread")),
        "depth_shares": _first_num(trade.get("depth_shares"), proposal.get("depth_shares")),
        "liquidity": _first_num(
            trade.get("liquidity"), proposal.get("liquidity"),
            trade.get("liquidity_usd"), proposal.get("liquidity_usd"),
        ),
        "forecast_age_hours": _first_num(trade.get("forecast_age_hours"), proposal.get("forecast_age_hours")),
        "hours_to_expiry": _first_num(trade.get("hours_to_expiry"), proposal.get("hours_to_expiry")),
        "settled_at": trade.get("settled_at") or proposal.get("settled_at") or settled_at,
        "later_price": _later_price(trade, records),
    }


def _overconfident(features: dict) -> bool:
    model = features.get("model_prob")
    market = features.get("market_prob")
    if model is None or market is None:
        return False
    return (model - market) + _EPS >= OVERCONFIDENT_GAP


def _stale(trade: dict, features: dict) -> bool:
    age = features.get("forecast_age_hours")
    if age is not None and age + _EPS >= STALE_HOURS:
        return True
    hours = features.get("hours_to_expiry")
    if hours is not None and hours + _EPS >= STALE_HOURS:
        return True
    gap = _hours_between(trade.get("timestamp"), features.get("settled_at"))
    if gap is not None and gap + _EPS >= STALE_HOURS:
        return True
    entry = _day(trade.get("timestamp"))
    event = _event_date(str(trade.get("slug") or ""))
    if entry and event:
        try:
            if (event - date.fromisoformat(entry)).days >= STALE_EVENT_DAYS:
                return True
        except ValueError:
            return False
    return False


def _thin(features: dict) -> bool:
    spread = features.get("spread")
    if spread is not None and spread + _EPS >= THIN_SPREAD:
        return True
    depth = features.get("depth_shares")
    if depth is not None and depth < THIN_DEPTH_SHARES:
        return True
    liquidity = features.get("liquidity")
    if liquidity is not None and liquidity < THIN_LIQUIDITY:
        return True
    return False


def _late(features: dict) -> bool:
    entry = features.get("market_prob")
    later = features.get("later_price")
    if entry is None or later is None:
        return False
    return (entry - later) + _EPS >= LATE_MOVE


def tag_loss(trade: dict, features: dict) -> str:
    """One cause. Earlier rules win when a loss matches more than one."""
    if _overconfident(features):
        return "overconfident"
    if _stale(trade, features):
        return "stale_info"
    if _thin(features):
        return "thin_market"
    if _late(features):
        return "late_move"
    return "unknown"


def _empty_causes() -> dict:
    return {cause: 0 for cause in CAUSES}


def _top_cause(counts: dict) -> str | None:
    best = None
    best_n = 0
    for cause in CAUSES:
        count = int(counts.get(cause) or 0)
        if count > best_n:
            best = cause
            best_n = count
    return best


def _brier_block(rows: list[dict]) -> dict:
    scored = [
        row for row in rows
        if row.get("model_prob") is not None and row.get("market_prob") is not None
    ]
    if not scored:
        return {"n": 0, "brier_model": None, "brier_market": None, "beats_market": None}
    model = sum(brier_score(row["model_prob"], row["outcome"]) for row in scored) / len(scored)
    market = sum(brier_score(row["market_prob"], row["outcome"]) for row in scored) / len(scored)
    return {
        "n": len(scored),
        "brier_model": model,
        "brier_market": market,
        "beats_market": model < market,
    }


def _recent_start(start: str, end: str, holdout_days: int) -> str:
    recent = date.fromisoformat(end) - timedelta(days=max(holdout_days, 1) - 1)
    recent_s = recent.isoformat()
    return start if recent_s < start else recent_s


def review_losses(start: str, end: str, *,
                  trades_path: Path | None = None,
                  cache_path: Path | None = None,
                  proposals_path: Path | None = None,
                  holdout_days: int = HOLDOUT_DAYS) -> dict:
    """Settled paper losses in an inclusive US/Eastern window, plus Brier slices."""
    date.fromisoformat(start)
    date.fromisoformat(end)
    trades = [
        row for row in _read_jsonl(Path(trades_path) if trades_path else TRADE_LOG)
        if _paper_buy(row) and _in_range(_day(row.get("timestamp")), start, end)
    ]
    cache = _load_cache(Path(cache_path) if cache_path else CACHE)
    prices, settled_at = _settlement_tables(cache)
    records = _read_jsonl(Path(proposals_path) if proposals_path else PROPOSAL_LOG)
    scored = score(trades, {"settlements": prices, "fills": {}})

    recent = _recent_start(start, end, holdout_days)
    earlier_end_d = date.fromisoformat(recent) - timedelta(days=1)
    earlier_end = earlier_end_d.isoformat() if earlier_end_d.isoformat() >= start else None

    enriched = []
    for trade, row in zip(trades, scored):
        proposal = _match_proposal(trade, records)
        features = _features(trade, proposal, records, settled_at.get(trade.get("slug") or ""))
        side = _side(trade)
        yes = prices.get(trade.get("slug") or "")
        outcome = None
        if yes is not None:
            outcome = yes if side == "yes" else 1.0 - yes
        enriched.append({
            "trade": trade,
            "row": row,
            "features": features,
            "strategy": canonical_strategy(trade.get("strategy")),
            "side": side,
            "outcome": outcome,
        })

    losses = []
    wins = 0
    unsettled = 0
    settled_rows = []
    for item in enriched:
        row = item["row"]
        if not row.get("resolved"):
            unsettled += 1
            continue
        features = item["features"]
        settled_rows.append({
            "strategy": item["strategy"],
            "day": _day(item["trade"].get("timestamp")),
            "model_prob": features.get("model_prob"),
            "market_prob": features.get("market_prob"),
            "outcome": item["outcome"] if item["outcome"] is not None else (1.0 if row.get("won") else 0.0),
        })
        if row.get("won"):
            wins += 1
            continue
        cause = tag_loss(item["trade"], features)
        losses.append({
            "timestamp": item["trade"].get("timestamp"),
            "strategy": item["strategy"],
            "slug": item["trade"].get("slug"),
            "side": item["side"],
            "cause": cause,
            "model_prob": features.get("model_prob"),
            "market_prob": features.get("market_prob"),
            "pnl": row.get("pnl"),
            "day": _day(item["trade"].get("timestamp")),
        })

    names = list(CANONICAL)
    for loss in losses:
        if loss["strategy"] not in names:
            names.append(loss["strategy"])
    for row in settled_rows:
        if row["strategy"] not in names:
            names.append(row["strategy"])

    strategies = {}
    for name in names:
        group = [loss for loss in losses if loss["strategy"] == name]
        counts = _empty_causes()
        for loss in group:
            counts[loss["cause"]] = counts.get(loss["cause"], 0) + 1
        mine = [row for row in settled_rows if row["strategy"] == name]
        strategies[name] = {
            "loss_count": len(group),
            "causes": counts,
            "top_cause": _top_cause(counts),
            "brier": {
                "recent": _brier_block([row for row in mine if row.get("day") and row["day"] >= recent]),
                "earlier": _brier_block([row for row in mine if row.get("day") and row["day"] < recent]),
            },
        }

    cause_counts = _empty_causes()
    for loss in losses:
        cause_counts[loss["cause"]] += 1

    return {
        "start": start,
        "end": end,
        "holdout_days": holdout_days,
        "recent_start": recent,
        "earlier_end": earlier_end,
        "settled": wins + len(losses),
        "wins": wins,
        "loss_count": len(losses),
        "unsettled_ignored": unsettled,
        "top_cause": _top_cause(cause_counts),
        "cause_counts": cause_counts,
        "cause_priority": list(CAUSES),
        "strategies": strategies,
        "losses": [{key: loss[key] for key in (
            "timestamp", "strategy", "slug", "side", "cause", "model_prob", "market_prob", "pnl",
        )} for loss in losses],
        "heuristics": {
            "priority": list(CAUSES),
            "overconfident_gap": OVERCONFIDENT_GAP,
            "stale_hours": STALE_HOURS,
            "stale_event_days": STALE_EVENT_DAYS,
            "thin_spread": THIN_SPREAD,
            "thin_depth_shares": THIN_DEPTH_SHARES,
            "thin_liquidity": THIN_LIQUIDITY,
            "late_move": LATE_MOVE,
            "holdout_days": holdout_days,
        },
    }


def _top_line(counts: dict, top: str | None, total: int) -> str:
    if not top:
        return "Top failure cause: none."
    count = int(counts.get(top) or 0)
    tied = [cause for cause in CAUSES if int(counts.get(cause) or 0) == count and cause != top]
    base = f"Top failure cause: {top} ({count} of {total} losses"
    if tied:
        return base + f", tied with {', '.join(tied)}; the earlier rule in the priority list is reported)."
    return base + ")."


def _brier_sentence(label: str, block: dict) -> str:
    if not block.get("n"):
        return f"{label}: no scored trades."
    return (
        f"{label}: model {block['brier_model']:.4f}, "
        f"market {block['brier_market']:.4f}, n={block['n']}."
    )


def format_summary(report: dict) -> str:
    """Plain-English summary. No orders, no gate changes."""
    lines = [
        f"Loss review {report.get('start')} through {report.get('end')} (US/Eastern).",
        (
            f"Settled paper trades: {report.get('settled', 0)}. "
            f"Wins: {report.get('wins', 0)}. "
            f"Losses: {report.get('loss_count', 0)}. "
            f"Unsettled in window, not tagged: {report.get('unsettled_ignored', 0)}."
        ),
        "",
    ]
    recent_label = (
        f"Model vs market Brier, last 14 days "
        f"({report.get('recent_start')} through {report.get('end')})"
    )
    if report.get("earlier_end"):
        earlier_label = (
            f"Model vs market Brier, earlier "
            f"({report.get('start')} through {report.get('earlier_end')})"
        )
    else:
        earlier_label = "Model vs market Brier, earlier"
    for name, block in (report.get("strategies") or {}).items():
        top = block.get("top_cause") or "none"
        count = int(block.get("loss_count") or 0)
        noun = "loss" if count == 1 else "losses"
        lines.append(f"{name}: {count} {noun}. Top cause: {top}.")
        for cause in CAUSES:
            lines.append(f"  {cause}: {block.get('causes', {}).get(cause, 0)}")
        brier = block.get("brier") or {}
        lines.append("  " + _brier_sentence(recent_label, brier.get("recent") or {"n": 0}))
        lines.append("  " + _brier_sentence(earlier_label, brier.get("earlier") or {"n": 0}))
        lines.append("")
    top = report.get("top_cause")
    lines.append(_top_line(report.get("cause_counts") or {}, top, int(report.get("loss_count") or 0)))
    lines.append(
        "This report does not place orders and does not change sizing or gates."
    )
    lines.append(
        "Cause priority: overconfident, then stale_info (forecast age or entry "
        f"at least {STALE_HOURS:.0f}h before settlement, or a slug date "
        f"{STALE_EVENT_DAYS}+ days out), then thin_market (spread, depth, or "
        "liquidity only when recorded), then late_move (a later quote, not the "
        "settlement), else unknown."
    )
    return "\n".join(lines)


def _valid_day(value: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD") from exc
    return value


def _write_review(report: dict, text: str, as_json: bool) -> Path:
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "json" if as_json else "txt"
    path = REVIEWS_DIR / f"loss_review_{report['start']}_{report['end']}.{suffix}"
    payload = text if as_json else text
    if as_json:
        payload = json.dumps(report, indent=2) + "\n"
    path.write_text(payload, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review settled paper losses. Read-only.")
    parser.add_argument("--from", dest="start", required=True, type=_valid_day,
                        help="First US/Eastern date, YYYY-MM-DD")
    parser.add_argument("--to", dest="end", required=True, type=_valid_day,
                        help="Last US/Eastern date, YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text")
    parser.add_argument("--save", action="store_true",
                        help="Also write a copy under agent/reviews/")
    parser.add_argument("--trades", type=Path, default=None, help="Trade log (default paper_trades.jsonl)")
    parser.add_argument("--cache", type=Path, default=None, help="Settlement cache (default ledger_cache.json)")
    parser.add_argument("--proposals", type=Path, default=None, help="Proposal log (default proposals.jsonl)")
    args = parser.parse_args(argv)
    report = review_losses(
        args.start, args.end,
        trades_path=args.trades,
        cache_path=args.cache,
        proposals_path=args.proposals,
    )
    text = json.dumps(report, indent=2) if args.json else format_summary(report)
    if args.save:
        _write_review(report, text, args.json)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
