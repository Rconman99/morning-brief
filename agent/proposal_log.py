"""Append-only log of every strategy proposal, and the math that scores it.

Trading P&L on a handful of fills is mostly noise. Recording every proposal
(traded or not) and scoring it once the market settles gives a Brier score,
log loss, and closing-line value against the market's own price.

One row per strategy + market + side per cycle. Scoring uses the first proposal
of the US/Eastern day and the last quote seen on a later cycle, before settlement.
This module does not place orders or change a strategy's size or price.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROPOSAL_LOG = PROJECT_ROOT / "agent" / "proposals.jsonl"
_EASTERN = ZoneInfo("America/New_York")
_LOGLOSS_EPS = 1e-6


def brier_score(prob: float, outcome: float) -> float:
    """Mean squared error of one probability. Lower is better. Range 0..1."""
    return (float(prob) - float(outcome)) ** 2


def log_loss(prob: float, outcome: float, eps: float = _LOGLOSS_EPS) -> float:
    """Binary log loss. `prob` is clipped so a 0/1 forecast cannot explode."""
    p = min(max(float(prob), eps), 1.0 - eps)
    y = float(outcome)
    return -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))


def closing_line_value(close_price: float, proposal_price: float) -> float:
    """CLV = price of the held side near the close, minus the price at proposal.

    Positive means the market moved toward the side we liked after we proposed it.
    """
    return float(close_price) - float(proposal_price)


def implied_prob(ask, bid) -> float | None:
    """Market probability of the held side: mid when both quotes exist, else the one we have."""
    prices = []
    for raw in (ask, bid):
        if raw is None:
            continue
        try:
            p = float(raw)
        except (TypeError, ValueError):
            continue
        if 0 < p < 1:
            prices.append(p)
    if len(prices) == 2:
        return (prices[0] + prices[1]) / 2.0
    if len(prices) == 1:
        return prices[0]
    return None


def held_side(proposal: dict) -> str:
    """YES/NO outcome the proposal would buy. Order side is usually just BUY."""
    for key in ("token_hint", "outcome", "side"):
        val = str(proposal.get(key) or "").strip().lower()
        if val in ("yes", "no"):
            return val
    return ""


def _edge(proposal: dict):
    if isinstance(proposal.get("edge"), (int, float)):
        return float(proposal["edge"])
    raw = proposal.get("edge_pct")
    if not isinstance(raw, (int, float)):
        return None
    raw = float(raw)
    # Strategies store edge_pct in percentage points (4.0 means 4 cents).
    return raw / 100.0 if abs(raw) > 1.5 else raw


def _model_prob(proposal: dict):
    raw = proposal.get("model_prob", proposal.get("estimated_prob"))
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return None
    p = float(raw)
    if 0.0 <= p <= 1.0:
        return p
    return None


_FORECAST_FLOATS = (
    "ensemble_mean", "ensemble_spread", "emos_bias", "emos_spread",
    "obs_floor", "trade_prob", "p_yes", "model_weight",
)
_FORECAST_INTS = ("n_members", "n_models")
_FORECAST_STRS = ("prob_method", "prob_fallback", "ensemble_models")


def forecast_fields(proposal: dict) -> dict:
    """Ensemble / EMOS diagnostics copied onto a proposal-log row.

    model_prob itself is the unblended side probability the scorer uses.
    trade_prob is the log-odds blend the edge test used, when that path ran.
    Missing fields are omitted so older proposals stay unchanged.
    """
    if not isinstance(proposal, dict):
        return {}
    out = {}
    for key in _FORECAST_STRS:
        val = proposal.get(key)
        if isinstance(val, str) and val:
            out[key] = val
    for key in _FORECAST_FLOATS:
        val = proposal.get(key)
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        out[key] = float(val)
    for key in _FORECAST_INTS:
        val = proposal.get(key)
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        out[key] = int(val)
    if isinstance(proposal.get("obs_floor_applied"), bool):
        out["obs_floor_applied"] = proposal["obs_floor_applied"]
    return out


def _pos_price(raw):
    if raw is None:
        return None
    try:
        p = float(raw)
    except (TypeError, ValueError):
        return None
    if 0 < p < 1:
        return p
    return None


def _group_key(slug: str) -> str:
    try:
        from agent.pm_us import group_key
        return group_key(slug)
    except Exception:
        return slug


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def eastern_day(ts: str) -> str:
    """US/Eastern calendar date of a proposal. The server itself may be in UTC."""
    return _parse_ts(ts).astimezone(_EASTERN).date().isoformat()


def quotes_from_weather_payload(data: dict) -> list[dict]:
    """YES/NO quotes from a weather_us envelope, so a market we stop proposing still has a close."""
    out = []
    for row in (data or {}).get("rows") or []:
        slug = row.get("slug") or ""
        if not slug:
            continue
        yes_ask, yes_bid = _pos_price(row.get("yes_ask")), _pos_price(row.get("yes_bid"))
        no_ask, no_bid = _pos_price(row.get("no_ask")), _pos_price(row.get("no_bid"))
        if yes_ask or yes_bid:
            out.append({"slug": slug, "side": "yes", "market_ask": yes_ask, "market_bid": yes_bid})
        if no_ask or no_bid:
            out.append({"slug": slug, "side": "no", "market_ask": no_ask, "market_bid": no_bid})
    return out


def quotes_from_weather_envelope(env: dict, max_age_min: float = 30, now: datetime | None = None) -> list[dict]:
    """Quotes from a weather_us envelope. Ignore a stale file so yesterday's book cannot fake CLV."""
    if not env or env.get("status") not in ("success", "partial"):
        return []
    if max_age_min is not None:
        try:
            gen = datetime.fromisoformat(env.get("generated_at"))
        except (TypeError, ValueError):
            return []
        if gen.tzinfo is None:
            gen = gen.replace(tzinfo=timezone.utc)
        moment = now or datetime.now(gen.tzinfo)
        if (moment - gen).total_seconds() / 60 > max_age_min:
            return []
    return quotes_from_weather_payload(env.get("data") or {})


def load_weather_quotes() -> list[dict]:
    try:
        from lib.data_envelope import load_envelope
        env = load_envelope("weather_us.json")
    except Exception:
        return []
    return quotes_from_weather_envelope(env)


def load_records(path: Path | None = None) -> list[dict]:
    p = Path(path) if path else PROPOSAL_LOG
    if not p.exists():
        return []
    rows = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _append(path: Path, rows: list[dict]) -> int:
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    return len(rows)


def log_cycle(proposals: list[dict], executed: list[dict] | None = None,
              quotes: list[dict] | None = None, now: datetime | None = None,
              path: Path | None = None) -> int:
    """Append this cycle's proposals and any later-useful quotes.

    Dedupe within the cycle: first proposal wins for a strategy+slug+side.
    Quote rows are written only for slug+side pairs we have proposed (this
    cycle or earlier) and that this cycle did not already log as a proposal,
    so the close price can update after the strategy goes quiet.
    """
    path = Path(path) if path else PROPOSAL_LOG
    moment = now or datetime.now().astimezone()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    stamp = moment.isoformat()
    if quotes is None:
        quotes = load_weather_quotes()

    executed_keys = set()
    for ex in executed or []:
        side = held_side(ex)
        executed_keys.add((ex.get("strategy") or "", ex.get("slug") or "", side))

    quote_idx = {}
    for q in quotes or []:
        side = str(q.get("side") or "").lower()
        slug = q.get("slug") or ""
        if slug and side in ("yes", "no"):
            quote_idx[(slug, side)] = q

    seen = set()
    proposal_rows = []
    proposed_pairs = set()
    for raw in proposals or []:
        slug = raw.get("slug") or ""
        side = held_side(raw)
        strategy = raw.get("strategy") or "unknown"
        if not slug or side not in ("yes", "no"):
            continue
        key = (strategy, slug, side)
        if key in seen:
            continue
        seen.add(key)
        proposed_pairs.add((slug, side))
        q = quote_idx.get((slug, side), {})
        ask = _pos_price(raw.get("market_ask"))
        bid = _pos_price(raw.get("market_bid"))
        if ask is None:
            ask = _pos_price(q.get("market_ask"))
        if bid is None:
            bid = _pos_price(q.get("market_bid"))
        # A gimme proposal's limit/price is the ask it saw, when no book quote was copied over.
        if ask is None:
            ask = _pos_price(raw.get("ask") if raw.get("ask") is not None else raw.get("price"))
        check = raw.get("risk_check") or {}
        approved = bool(check.get("approved"))
        row = {
            "record": "proposal",
            "timestamp": stamp,
            "strategy": strategy,
            "slug": slug,
            "market_id": raw.get("market_id") or slug,
            "group": raw.get("group") or _group_key(slug),
            "side": side,
            "model_prob": _model_prob(raw),
            "market_ask": ask,
            "market_bid": bid,
            "market_prob": implied_prob(ask, bid),
            "edge": _edge(raw),
            "approved": approved,
            "reject_reason": None if approved else (check.get("reason") or "not approved"),
            "executed": key in executed_keys,
        }
        row.update(forecast_fields(raw))
        proposal_rows.append(row)

    known = {(r.get("slug"), r.get("side")) for r in load_records(path)
             if r.get("record", "proposal") == "proposal"}
    known |= proposed_pairs
    quote_rows = []
    seen_q = set()
    for q in quotes or []:
        slug = q.get("slug") or ""
        side = str(q.get("side") or "").lower()
        pair = (slug, side)
        if pair not in known or pair in proposed_pairs or pair in seen_q:
            continue
        ask, bid = _pos_price(q.get("market_ask")), _pos_price(q.get("market_bid"))
        if implied_prob(ask, bid) is None:
            continue
        seen_q.add(pair)
        quote_rows.append({
            "record": "quote",
            "timestamp": stamp,
            "slug": slug,
            "side": side,
            "market_ask": ask,
            "market_bid": bid,
        })
    return _append(path, proposal_rows + quote_rows)


def calibration_buckets(rows: list[dict], buckets: int = 10) -> list[dict]:
    """Ten equal bins of model probability: count, mean forecast, realized frequency."""
    out = []
    for i in range(buckets):
        lo = i / buckets
        hi = (i + 1) / buckets
        if i == buckets - 1:
            members = [r for r in rows if r.get("model_prob") is not None and lo <= float(r["model_prob"]) <= hi]
        else:
            members = [r for r in rows if r.get("model_prob") is not None and lo <= float(r["model_prob"]) < hi]
        n = len(members)
        out.append({
            "lo": lo,
            "hi": hi,
            "n": n,
            "mean_pred": (sum(float(r["model_prob"]) for r in members) / n) if n else None,
            "realized": (sum(float(r["outcome"]) for r in members) / n) if n else None,
        })
    return out


def score_proposal_records(records: list[dict], settlements: dict) -> list[dict]:
    """One scored row per strategy+slug+side+Eastern day, once YES settlement is known.

    Entry is the first proposal that day. Close is the last quote (or later
    proposal) for that slug+side with a strictly later timestamp — the last
    price we saw while the market was still being quoted. No later quote means
    CLV is null, not zero.
    """
    proposals = [r for r in records if r.get("record", "proposal") == "proposal" and r.get("slug") and r.get("side")]
    grouped: dict[tuple, list] = {}
    for row in proposals:
        try:
            day = eastern_day(row.get("timestamp") or "")
        except (TypeError, ValueError):
            continue
        key = (row.get("strategy") or "unknown", row["slug"], row["side"], day)
        grouped.setdefault(key, []).append(row)

    scored = []
    for (strategy, slug, side, day), group in grouped.items():
        if slug not in settlements:
            continue
        group.sort(key=lambda r: r.get("timestamp") or "")
        first = group[0]
        model = first.get("model_prob")
        market = first.get("market_prob")
        if market is None:
            market = implied_prob(first.get("market_ask"), first.get("market_bid"))
        try:
            yes = float(settlements[slug])
        except (TypeError, ValueError):
            continue
        outcome = yes if side == "yes" else 1.0 - yes
        entry = market
        later = []
        first_ts = first.get("timestamp") or ""
        for row in records:
            if row.get("slug") != slug or row.get("side") != side:
                continue
            if (row.get("timestamp") or "") <= first_ts:
                continue
            px = implied_prob(row.get("market_ask"), row.get("market_bid"))
            if px is None and row.get("market_prob") is not None:
                px = _pos_price(row.get("market_prob"))
            if px is None:
                continue
            later.append((row.get("timestamp") or "", px))
        later.sort()
        close = later[-1][1] if later else None
        clv = closing_line_value(close, entry) if close is not None and entry is not None else None
        item = {
            "strategy": strategy,
            "slug": slug,
            "side": side,
            "day": day,
            "group": first.get("group"),
            "model_prob": model,
            "market_prob": market,
            "outcome": outcome,
            "entry_price": entry,
            "close_price": close,
            "clv": clv,
            "approved": first.get("approved"),
            "n_logged": len(group),
        }
        if model is not None:
            item["brier_model"] = brier_score(model, outcome)
            item["logloss_model"] = log_loss(model, outcome)
        if market is not None:
            item["brier_market"] = brier_score(market, outcome)
            item["logloss_market"] = log_loss(market, outcome)
        item.update(forecast_fields(first))
        scored.append(item)
    scored.sort(key=lambda r: (r["day"], r["strategy"], r["slug"], r["side"]))
    return scored


def summarize_model_vs_market(scored: list[dict]) -> dict:
    """Per strategy, only rows that have both a model probability and a market probability."""
    by: dict[str, list] = {}
    for row in scored:
        if row.get("model_prob") is None or row.get("market_prob") is None:
            continue
        if "brier_model" not in row or "brier_market" not in row:
            continue
        by.setdefault(row["strategy"], []).append(row)
    summary = {}
    for strategy, rows in by.items():
        n = len(rows)
        clvs = [r["clv"] for r in rows if r.get("clv") is not None]
        brier_model = sum(r["brier_model"] for r in rows) / n
        brier_market = sum(r["brier_market"] for r in rows) / n
        summary[strategy] = {
            "n": n,
            "brier_model": brier_model,
            "brier_market": brier_market,
            "logloss_model": sum(r["logloss_model"] for r in rows) / n,
            "logloss_market": sum(r["logloss_market"] for r in rows) / n,
            "mean_clv": (sum(clvs) / len(clvs)) if clvs else None,
            "n_clv": len(clvs),
            "beats_market": brier_model < brier_market,
            "calibration": calibration_buckets(rows),
        }
    return summary


def evidence_line(summary: dict | None) -> str:
    """One informational sentence. Not a capital-ladder gate."""
    rows = [(k, v) for k, v in (summary or {}).items() if v.get("n")]
    n = sum(v["n"] for _, v in rows)
    if not n:
        return "Proposal evidence: no scored proposals yet."
    brier_model = sum(v["brier_model"] * v["n"] for _, v in rows) / n
    brier_market = sum(v["brier_market"] * v["n"] for _, v in rows) / n
    clv_n = sum(v.get("n_clv") or 0 for _, v in rows)
    if clv_n:
        clv = sum((v["mean_clv"] or 0) * v["n_clv"] for _, v in rows) / clv_n
        clv_s = f"{clv * 100:+.1f}¢"
    else:
        clv_s = "n/a"
    verb = "beats" if brier_model < brier_market else "does not beat"
    return (
        f"Proposal evidence: model {verb} market on N={n}, "
        f"Brier {brier_model:.3f} vs {brier_market:.3f}, CLV {clv_s}"
    )


def format_model_section(summary: dict | None, brief: bool = False) -> list[str]:
    """Text block for the scoreboard. `brief` drops empty calibration bins (Telegram)."""
    lines = ["MODEL VS MARKET (informational — does not move the capital ladder)",
             evidence_line(summary)]
    if not summary:
        return lines
    for strategy, row in sorted(summary.items()):
        clv = row.get("mean_clv")
        clv_s = f"{clv * 100:+.1f}¢" if clv is not None else "n/a"
        lines.append(
            f"{strategy}: N={row['n']} Brier {row['brier_model']:.3f} vs {row['brier_market']:.3f} "
            f"logloss {row['logloss_model']:.3f} vs {row['logloss_market']:.3f} CLV {clv_s}"
        )
        bins = []
        for b in row.get("calibration") or []:
            if brief and not b["n"]:
                continue
            if not b["n"]:
                bins.append(f"{b['lo']:.1f}:n=0")
            else:
                bins.append(
                    f"{b['lo']:.1f}:n={b['n']} pred {b['mean_pred']:.2f} hit {b['realized']:.2f}"
                )
        if bins:
            lines.append("  calibration " + " | ".join(bins))
    return lines
