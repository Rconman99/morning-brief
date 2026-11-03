"""Gimme paper trades record the same review fields weather trades already carry.

Logging only. The bet (slug, side, price, size) is fixed by the book and the
model. Old trade rows that omit the new fields still parse.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
from datetime import datetime, timezone

import pytest

from agent.loss_review import review_losses


LONG = "tc-temp-laxhigh-2026-10-24-gte70lt72f"
SHORT = "tc-temp-miahigh-2026-10-20-gte90lt92f"
PRICED_AT = "2026-11-03T12:00:00+00:00"
DECISION_KEYS = (
    "slug", "side", "token_hint", "price", "size_shares", "size_usd",
    "edge", "ev_per_dollar", "model_prob", "market_bid", "market_ask",
    "market_mid", "spread", "depth_shares", "hours_to_expiry",
)


def _book(slug, *, bid, ask, hours, category, liquidity=None):
    row = {
        "venue": "us",
        "slug": slug,
        "side": "YES",
        "question": slug,
        "category": category,
        "market_bid": bid,
        "market_ask": ask,
        "market_mid": round((bid + ask) / 2, 4),
        "spread": round(ask - bid, 4),
        "depth_shares": 500,
        "hours_to_expiry": hours,
        "days_to_expiry": hours / 24,
        "price": round((bid + ask) / 2, 4),
        "risks": [],
        "neg_risk": False,
        "group_size": 0,
        "group_title": "",
    }
    if liquidity is not None:
        row["liquidity"] = liquidity
    return row


def _pin_calibration(monkeypatch):
    monkeypatch.setenv("POLYMARKET_VENUE", "us")
    monkeypatch.setenv("CALIBRATION_MODE", "shadow")
    monkeypatch.delenv("US_GIMME_SKIP_SLUG_REGEX", raising=False)
    monkeypatch.delenv("US_GIMME_MIN_EDGE", raising=False)
    monkeypatch.delenv("POLYMARKET_LIVE", raising=False)
    monkeypatch.delenv("POLYMARKET_US_KEY_ID", raising=False)
    monkeypatch.delenv("POLYMARKET_US_SECRET_KEY", raising=False)
    monkeypatch.setattr(
        "agent.prob_calibration.current_fit",
        lambda rows=None: {
            "enabled": True, "n": 500, "model_weight": 1.0, "prior_n": 0.0,
            "platt": None, "bins": None, "platt_min_n": 10000, "bin_min_n": 10000,
        },
    )


def _scan(monkeypatch, liquidity=4321.5):
    from agent import strategies

    items = [
        _book(SHORT, bid=0.93, ask=0.95, hours=8, category="climate"),
        _book(LONG, bid=0.91, ask=0.93, hours=120, category="climate", liquidity=liquidity),
        _book("cpceth-above-day-2026-10-20-4000", bid=0.94, ask=0.96, hours=24, category="crypto"),
        _book("will-the-bill-pass-2026", bid=0.93, ask=0.95, hours=48, category="politics"),
        _book("tc-temp-nyhigh-2026-10-19-gte60lt62f", bid=0.93, ask=0.95, hours=2, category="climate"),
    ]

    def fake_load(name, max_age_min=None):
        if "weather" in name:
            return {
                "priced_at": PRICED_AT,
                "rows": [
                    {"slug": SHORT, "p_yes": 0.985, "prob_method": "emos"},
                    {"slug": LONG, "p_yes": 0.99, "prob_method": "emos"},
                    {"slug": "tc-temp-nyhigh-2026-10-19-gte60lt62f", "p_yes": 0.99, "prob_method": "emos"},
                ],
            }
        if "polymarket_us" in name:
            return {"gimme_evaluated": items}
        return {}

    monkeypatch.setattr(strategies, "_load_signal", fake_load)
    return strategies.scan_gimme_bets({})


def _decisions(proposals):
    return [{key: row.get(key) for key in DECISION_KEYS} for row in proposals]


def _expected_size(pay, max_pos=10.0, min_shares=5):
    raw_return = (1.0 - pay) / pay * 100
    size_usd = min(max_pos, max_pos * (raw_return / 10.0))
    shares = size_usd / pay
    if shares < min_shares:
        shares = min_shares
        size_usd = shares * pay
    return round(shares, 1), round(size_usd, 2)


def test_same_book_keeps_the_same_bets_and_new_paper_trades_record_review_fields(monkeypatch, tmp_path):
    from agent import executor

    _pin_calibration(monkeypatch)
    first = _scan(monkeypatch)
    second = _scan(monkeypatch)
    assert [row["slug"] for row in first] == [LONG, SHORT]
    assert _decisions(first) == _decisions(second)

    pay = 0.93
    shares, size_usd = _expected_size(pay)
    taken = first[0]
    assert taken["side"] == "BUY"
    assert taken["token_hint"] == "yes"
    assert taken["price"] == pytest.approx(pay)
    assert taken["size_shares"] == pytest.approx(shares)
    assert taken["size_usd"] == pytest.approx(size_usd)
    assert taken["model_prob"] == pytest.approx(0.99)
    assert taken["market_mid"] == pytest.approx(0.92)
    assert taken["hours_to_expiry"] == pytest.approx(120)

    from agent import strategies
    reasons = {row["slug"]: row["reject_reason"] for row in strategies.scan_gimme_bets.last_evaluations}
    assert reasons["cpceth-above-day-2026-10-20-4000"] == "skipped_slug"
    assert reasons["will-the-bill-pass-2026"] == "no_model"
    assert reasons["tc-temp-nyhigh-2026-10-19-gte60lt62f"] == "too_soon"

    before = json.loads(json.dumps(_decisions(first)))
    log = tmp_path / "paper_trades.jsonl"
    monkeypatch.setattr(executor, "PAPER_TRADE_LOG", log)
    monkeypatch.setattr("agent.pm_us.get_market", lambda slug: {"orderPriceMinTickSize": "0.01"})
    monkeypatch.setattr("agent.pm_us.get_bbo", lambda slug: {
        "state": "MARKET_STATE_OPEN", "yes_bid": 0.91, "yes_ask": 0.93, "no_ask": 0.09,
        "ask_shares": 500, "bid_shares": 500,
    })
    monkeypatch.setattr("agent.pm_us.place_limit", lambda *a, **k: {"ok": True})

    rec = executor.place_limit_order(
        token_id="", side=taken["side"], price=taken["price"], size=taken["size_shares"],
        market_question=taken["question"], strategy=taken["strategy"], reason=taken["reason"],
        slug=taken["slug"], token_hint=taken["token_hint"], category=taken["category"],
        review=taken,
    )
    assert _decisions(first) == before
    assert rec["status"] == "paper_filled"
    assert rec["price"] == pytest.approx(taken["price"])
    assert rec["size"] == int(taken["size_shares"])
    assert rec["slug"] == taken["slug"]
    assert rec["strategy"] == "gimme_bets"

    stored = json.loads(log.read_text().splitlines()[0])
    assert stored["model_prob"] == pytest.approx(0.99)
    assert stored["market_prob"] == pytest.approx(0.92)
    assert stored["market_bid"] == pytest.approx(0.91)
    assert stored["market_ask"] == pytest.approx(0.93)
    assert stored["market_mid"] == pytest.approx(0.92)
    assert stored["spread"] == pytest.approx(0.02)
    assert stored["depth_shares"] == pytest.approx(500)
    assert stored["liquidity"] == pytest.approx(4321.5)
    assert stored["hours_to_expiry"] == pytest.approx(120)
    assert stored["priced_at"] == PRICED_AT
    logged_at = datetime.fromisoformat(stored["timestamp"])
    priced = datetime.fromisoformat(PRICED_AT)
    age = (logged_at - priced).total_seconds() / 3600.0
    assert stored["forecast_age_hours"] == pytest.approx(age, abs=1e-3)
    assert "liquidity_usd" not in stored

    # loss_review reads the trade itself. No proposal row is required.
    cache = tmp_path / "ledger_cache.json"
    cache.write_text(json.dumps({"settlements": {LONG: 0}, "fills": {}}), encoding="utf-8")
    proposals = tmp_path / "proposals.jsonl"
    proposals.write_text("", encoding="utf-8")
    day = logged_at.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).date().isoformat()
    report = review_losses(day, day, trades_path=log, cache_path=cache, proposals_path=proposals)
    assert report["losses"][0]["strategy"] == "gimme"
    assert report["losses"][0]["cause"] == "stale_info"
    assert report["losses"][0]["model_prob"] == pytest.approx(0.99)
    assert report["losses"][0]["market_prob"] == pytest.approx(0.92)
    assert report["strategies"]["gimme"]["brier"]["recent"]["n"] == 1

    # The sibling bet has no liquidity figure. The log omits it instead of inventing one.
    short = first[1]
    executor.place_limit_order(
        token_id="", side=short["side"], price=short["price"], size=short["size_shares"],
        market_question=short["question"], strategy=short["strategy"], reason=short["reason"],
        slug=short["slug"], token_hint=short["token_hint"], category=short["category"],
        review=short,
    )
    short_row = json.loads(log.read_text().splitlines()[1])
    assert "liquidity" not in short_row
    assert short_row["model_prob"] == pytest.approx(0.985)
    assert short_row["priced_at"] == PRICED_AT


def test_weather_paper_trades_do_not_gain_gimme_review_fields(monkeypatch, tmp_path):
    from agent import executor

    monkeypatch.setenv("POLYMARKET_VENUE", "us")
    monkeypatch.delenv("POLYMARKET_LIVE", raising=False)
    monkeypatch.delenv("POLYMARKET_US_KEY_ID", raising=False)
    monkeypatch.delenv("POLYMARKET_US_SECRET_KEY", raising=False)
    log = tmp_path / "paper_trades.jsonl"
    monkeypatch.setattr(executor, "PAPER_TRADE_LOG", log)
    monkeypatch.setattr("agent.pm_us.get_market", lambda slug: {"orderPriceMinTickSize": "0.01"})
    monkeypatch.setattr("agent.pm_us.get_bbo", lambda slug: {
        "state": "MARKET_STATE_OPEN", "yes_bid": 0.70, "yes_ask": 0.74, "no_ask": 0.30,
    })
    rec = executor.place_limit_order(
        token_id="", side="BUY", price=0.74, size=5,
        market_question="Miami high", strategy="weather_us", reason="unit",
        slug=SHORT, token_hint="no", category="climate",
        review={"model_prob": 0.9, "market_prob": 0.72, "priced_at": PRICED_AT},
    )
    assert rec["status"] == "paper_filled"
    assert "model_prob" not in rec
    stored = json.loads(log.read_text().splitlines()[0])
    assert "model_prob" not in stored
    assert "priced_at" not in stored


def test_old_gimme_trade_without_review_fields_still_parses(tmp_path):
    from agent.ledger import score

    # Same-day slug, so a missing forecast age cannot be inferred from the title.
    same_day = "tc-temp-laxhigh-2026-10-20-gte70lt72f"
    legacy = {
        "timestamp": "2026-10-20T15:00:00-04:00",
        "strategy": "gimme_bets",
        "slug": same_day,
        "token_hint": "yes",
        "side": "BUY",
        "status": "paper_filled",
        "venue": "us",
        "price": 0.93,
        "fill_price": 0.93,
        "size": 8,
        "role": "maker",
        "cost_usd": 7.44,
    }
    path = tmp_path / "paper_trades.jsonl"
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    again = json.loads(path.read_text().splitlines()[0])
    assert "model_prob" not in again
    scored = score([again], {"settlements": {same_day: 0}, "fills": {}})
    assert scored[0]["strategy"] == "gimme_bets"
    assert scored[0]["resolved"] is True

    cache = tmp_path / "ledger_cache.json"
    cache.write_text(json.dumps({"settlements": {same_day: 0}, "fills": {}}), encoding="utf-8")
    proposals = tmp_path / "proposals.jsonl"
    proposals.write_text("", encoding="utf-8")
    report = review_losses(
        "2026-10-01", "2026-10-31",
        trades_path=path, cache_path=cache, proposals_path=proposals,
    )
    assert report["losses"][0]["cause"] == "unknown"
    assert report["losses"][0]["model_prob"] is None
    assert report["strategies"]["gimme"]["brier"]["recent"]["n"] == 0
    assert report["strategies"]["gimme"]["brier"]["earlier"]["n"] == 0


def test_run_agent_writes_the_review_fields_without_changing_the_bet(monkeypatch, tmp_path):
    from agent import executor, run

    _pin_calibration(monkeypatch)
    proposals = _scan(monkeypatch, liquidity=2500)
    taken = proposals[0]
    decision = {key: taken[key] for key in DECISION_KEYS}

    monkeypatch.setattr("agent.local_signals.generate_trade_reasoning", lambda proposal: proposal.get("reason", ""))
    monkeypatch.setattr(run, "should_evaluate", lambda: False)
    monkeypatch.setattr(run, "init_paper_bankroll", lambda bankroll: None)
    monkeypatch.setattr(run, "load_agent_config", lambda: {"gimme_bets": {}})
    monkeypatch.setattr(run, "_log_cycle_proposals", lambda *a, **k: None)
    monkeypatch.setattr(run, "print_summary", lambda *a, **k: None)
    monkeypatch.setattr(run, "run_all_strategies", lambda params: [dict(taken)])
    run.run_all_strategies.last_gimme_evaluations = []
    monkeypatch.setattr(run, "filter_proposals", lambda props, bankroll: props)
    log = tmp_path / "paper_trades.jsonl"
    monkeypatch.setattr(executor, "PAPER_TRADE_LOG", log)
    monkeypatch.setattr("agent.pm_us.get_market", lambda slug: {"orderPriceMinTickSize": "0.01"})
    monkeypatch.setattr("agent.pm_us.get_bbo", lambda slug: {
        "state": "MARKET_STATE_OPEN", "yes_bid": 0.91, "yes_ask": 0.93, "no_ask": 0.09,
        "ask_shares": 500, "bid_shares": 500,
    })
    monkeypatch.setattr("agent.pm_us.place_limit", lambda *a, **k: (_ for _ in ()).throw(AssertionError("live send")))

    run.run_agent(bankroll=95, dry_run=False)
    assert {key: taken[key] for key in DECISION_KEYS} == decision
    stored = json.loads(log.read_text().splitlines()[0])
    assert stored["slug"] == taken["slug"]
    assert stored["price"] == pytest.approx(taken["price"])
    assert stored["size"] == int(taken["size_shares"])
    assert stored["model_prob"] == pytest.approx(taken["model_prob"])
    assert stored["market_prob"] == pytest.approx(taken["market_mid"])
    assert stored["liquidity"] == pytest.approx(2500)
    assert stored["priced_at"] == PRICED_AT
    assert stored["forecast_age_hours"] > 0
    assert stored["hours_to_expiry"] == pytest.approx(120)
    assert stored["spread"] == pytest.approx(taken["spread"])
    assert stored["depth_shares"] == pytest.approx(taken["depth_shares"])
