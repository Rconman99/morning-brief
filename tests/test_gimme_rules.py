"""Gimme bet rules: crypto skip, real book, own edge, EV rank, category, crypto cap."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
from datetime import datetime, timedelta, timezone

import pytest

from agent.gimme_rules import (
    DEFAULT_SKIP_SLUG_REGEX,
    assess_us_gimme,
    ev_per_dollar,
    is_crypto,
    model_prob_for,
    normalize_category,
    passes_own_edge,
    replay_decision,
    side_quotes,
    slug_is_skipped,
    taker_fee_per_share,
)
from agent.gimme_backtest import summarize


def test_default_skip_regex_catches_every_cpc_prefix():
    assert DEFAULT_SKIP_SLUG_REGEX == r"^cpc"
    for slug in (
        "cpc-btc-above-day-2026-10-18-84250",
        "cpceth-above-day-2026-10-18-4000",
        "cpcsol-above-day-2026-10-18-180",
        "cpcxrp-above-day-2026-10-18-2",
    ):
        assert slug_is_skipped(slug, DEFAULT_SKIP_SLUG_REGEX)
    assert not slug_is_skipped("tc-temp-miahigh-2026-10-18-gte93lt94f", DEFAULT_SKIP_SLUG_REGEX)
    assert not slug_is_skipped("tec-mlb-champ-2026-10-18-nyy", DEFAULT_SKIP_SLUG_REGEX)


def test_skip_regex_env_override_still_works(monkeypatch):
    monkeypatch.setenv("US_GIMME_SKIP_SLUG_REGEX", r"^cpc-")
    assert slug_is_skipped("cpc-btc-above-day-2026-10-18-84250")
    assert not slug_is_skipped("cpceth-above-day-2026-10-18-4000")
    monkeypatch.setenv("US_GIMME_SKIP_SLUG_REGEX", "")
    assert not slug_is_skipped("cpceth-above-day-2026-10-18-4000")
    assert not slug_is_skipped("cpcsol-above-day-2026-10-18-180")


def test_one_sided_ask_is_not_a_favorite_and_wide_spread_is_rejected():
    one_sided = side_quotes(
        {"yes_bid": 0, "yes_ask": 0.96, "no_ask_real": 0, "no_ask_synthetic": False,
         "ask_shares": 500, "bid_shares": 0},
        "YES",
    )
    assert one_sided["bid"] == 0
    decided = assess_us_gimme(
        {"side": "YES", "market_bid": 0, "market_ask": 0.96, "depth_shares": 500,
         "hours_to_expiry": 24},
        0.99,
    )
    assert decided["keep"] is False
    assert decided["reason"] == "one_sided_no_bid"

    wide = assess_us_gimme(
        {"side": "YES", "market_bid": 0.90, "market_ask": 0.97, "depth_shares": 500,
         "hours_to_expiry": 24, "spread": 0.07, "market_mid": 0.935},
        0.99, max_spread=0.04,
    )
    assert wide["reason"] == "wide_spread"

    # Ask is 0.93 (>= 0.92) but the midpoint is 0.915. The threshold uses the mid.
    below_mid = assess_us_gimme(
        {"side": "YES", "market_bid": 0.90, "market_ask": 0.93, "depth_shares": 500,
         "hours_to_expiry": 24},
        0.99, min_price=0.92, max_spread=0.04,
    )
    assert below_mid["reason"] == "below_mid"
    assert below_mid["mid"] == pytest.approx(0.915)

    ok = assess_us_gimme(
        {"side": "YES", "market_bid": 0.91, "market_ask": 0.93, "depth_shares": 400,
         "hours_to_expiry": 24},
        0.99, min_price=0.92, max_spread=0.04, min_edge=0.03,
    )
    assert ok["keep"] is True
    assert ok["mid"] == pytest.approx(0.92)
    assert ok["pay_price"] == pytest.approx(0.93)


def test_own_edge_includes_taker_fee_and_rejects_categories_without_a_model():
    price = 0.94
    fee = taker_fee_per_share(price)
    assert fee == pytest.approx(0.0695 * 0.94 * 0.06)
    margin = 0.03
    assert passes_own_edge(price + fee + margin - 0.001, price, margin) is False
    assert passes_own_edge(price + fee + margin, price, margin) is True

    weather_slug = "tc-temp-miahigh-2026-10-20-gte80lt82f"
    prob, source = model_prob_for(weather_slug, "YES", 0.99)
    assert source == "weather_model"
    assert prob == pytest.approx(0.99)
    no_prob, no_source = model_prob_for(weather_slug, "NO", 0.20)
    assert no_source == "weather_model"
    assert no_prob == pytest.approx(0.80)
    missing, why = model_prob_for(weather_slug, "NO", None)
    assert missing is None and why == "weather_missing"
    none, why = model_prob_for("will-the-bill-pass", "YES", None)
    assert none is None and why == "no_model"
    crypto, why = model_prob_for("cpceth-above-day-2026-10-20-4000", "YES", 0.99)
    assert crypto is None and why == "no_model"

    book = {"side": "YES", "market_bid": 0.92, "market_ask": price, "depth_shares": 400,
            "hours_to_expiry": 12}
    assert assess_us_gimme(book, None, model_reason="no_model")["reason"] == "no_model"
    assert assess_us_gimme(book, price + fee + 0.02, min_edge=0.03)["reason"] == "model_edge"
    assert assess_us_gimme(book, price + fee + 0.03, min_edge=0.03)["keep"] is True


def test_too_soon_is_rejected_and_rank_key_is_ev_per_dollar_not_yield():
    book = {"side": "YES", "market_bid": 0.92, "market_ask": 0.94, "depth_shares": 400}
    soon = assess_us_gimme({**book, "hours_to_expiry": 2}, 0.99, min_hours=6)
    assert soon["reason"] == "too_soon"
    later = assess_us_gimme({**book, "hours_to_expiry": 6}, 0.99, min_hours=6)
    assert later["keep"] is True

    short_ev = ev_per_dollar(0.985, 0.95)
    long_ev = ev_per_dollar(0.99, 0.93)
    assert long_ev > short_ev
    ranked = sorted(
        [
            {"ev_per_dollar": short_ev, "annualized_yield": 9000, "hours": 8},
            {"ev_per_dollar": long_ev, "annualized_yield": 40, "hours": 120},
        ],
        key=lambda row: row["ev_per_dollar"],
        reverse=True,
    )
    assert ranked[0]["hours"] == 120


def _us_book(slug, *, bid, ask, hours, category, side="YES"):
    return {
        "venue": "us",
        "slug": slug,
        "side": side,
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


def test_strategy_keeps_weather_with_edge_and_ranks_by_ev(monkeypatch):
    from agent import strategies

    monkeypatch.setenv("POLYMARKET_VENUE", "us")
    monkeypatch.delenv("US_GIMME_SKIP_SLUG_REGEX", raising=False)
    monkeypatch.delenv("US_GIMME_MIN_EDGE", raising=False)
    short = "tc-temp-miahigh-2026-10-20-gte90lt92f"
    long = "tc-temp-laxhigh-2026-10-24-gte70lt72f"
    crypto = "cpceth-above-day-2026-10-20-4000"
    politics = "will-the-bill-pass-2026"
    soon = "tc-temp-nyhigh-2026-10-19-gte60lt62f"
    items = [
        _us_book(short, bid=0.93, ask=0.95, hours=8, category="climate"),
        _us_book(long, bid=0.91, ask=0.93, hours=120, category="climate"),
        _us_book(crypto, bid=0.94, ask=0.96, hours=24, category="crypto"),
        _us_book(politics, bid=0.93, ask=0.95, hours=48, category="politics"),
        _us_book(soon, bid=0.93, ask=0.95, hours=2, category="climate"),
        _us_book("one-sided", bid=0.0, ask=0.97, hours=24, category="other"),
    ]
    # Fix the one-sided row: helper averaged 0 and 0.97 into a fake mid. Blank it.
    items[-1]["market_bid"] = 0
    items[-1]["market_mid"] = None
    items[-1]["spread"] = None

    def fake_load(name, max_age_min=None):
        if "weather" in name:
            return {"rows": [
                {"slug": short, "p_yes": 0.985, "prob_method": "emos"},
                {"slug": long, "p_yes": 0.99, "prob_method": "emos"},
                {"slug": soon, "p_yes": 0.99, "prob_method": "emos"},
            ]}
        if "polymarket_us" in name:
            return {"gimme_evaluated": items}
        return {}

    monkeypatch.setattr(strategies, "_load_signal", fake_load)
    # Full model weight: calibration does not change the PR #18 edge math.
    monkeypatch.setattr(
        "agent.prob_calibration.current_fit",
        lambda rows=None: {
            "enabled": True, "n": 500, "model_weight": 1.0, "prior_n": 0.0,
            "platt": None, "bins": None, "platt_min_n": 10000, "bin_min_n": 10000,
        },
    )
    proposals = strategies.scan_gimme_bets({})
    slugs = [p["slug"] for p in proposals]
    assert slugs == [long, short]
    assert proposals[0]["ev_per_dollar"] > proposals[1]["ev_per_dollar"]
    assert proposals[0]["category"] == "climate"
    assert proposals[0]["market_bid"] == pytest.approx(0.91)
    assert proposals[0]["market_ask"] == pytest.approx(0.93)
    assert proposals[0]["market_mid"] == pytest.approx(0.92)
    assert proposals[0]["model_prob"] == pytest.approx(0.99)
    reasons = {row["slug"]: row["reject_reason"] for row in strategies.scan_gimme_bets.last_evaluations}
    assert reasons[crypto] == "skipped_slug"
    assert reasons[politics] == "no_model"
    assert reasons[soon] == "too_soon"
    assert reasons["one-sided"] == "one_sided_no_bid"
    assert reasons[long] is None


def test_strategy_rejects_crypto_when_skip_regex_is_cleared(monkeypatch):
    from agent import strategies

    monkeypatch.setenv("POLYMARKET_VENUE", "us")
    monkeypatch.setenv("US_GIMME_SKIP_SLUG_REGEX", "")
    slug = "cpcsol-above-day-2026-10-20-180"

    def fake_load(name, max_age_min=None):
        if "polymarket_us" in name:
            return {"gimme_bets": [_us_book(slug, bid=0.94, ask=0.96, hours=24, category="crypto")]}
        return {}

    monkeypatch.setattr(strategies, "_load_signal", fake_load)
    assert strategies.scan_gimme_bets({}) == []
    assert strategies.scan_gimme_bets.last_evaluations[0]["reject_reason"] == "no_model"


def test_scanner_skips_cpc_slugs_and_requires_a_two_sided_mid(monkeypatch):
    from agent import us_scanner

    monkeypatch.delenv("US_GIMME_SKIP_SLUG_REGEX", raising=False)
    now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

    def end_in(hours):
        return (now + timedelta(hours=hours)).isoformat()

    def mkt(slug, category, hours, yes=0.93, no=0.08):
        return {
            "slug": slug,
            "category": category,
            "endDate": end_in(hours),
            "active": True,
            "closed": False,
            "question": slug,
            "title": "Yes",
            "marketSides": [
                {"long": True, "price": {"value": str(yes)}},
                {"long": False, "price": {"value": str(no)}},
            ],
        }

    markets = [
        mkt("cpceth-above-day-2026-10-19-4000", "crypto", 20),
        mkt("cpcsol-above-day-2026-10-19-180", "crypto", 20),
        mkt("cpcxrp-above-day-2026-10-19-2", "crypto", 20),
        mkt("cpc-btc-above-day-2026-10-19-84250", "crypto", 20),
        mkt("too-soon", "other", 1),
        mkt("one-sided-ask", "other", 20, yes=0.97),
        mkt("wide-book", "other", 20),
        mkt("ask-above-mid-below", "other", 20),
        mkt("tc-temp-miahigh-2026-10-19-gte80lt82f", "climate", 30),
        mkt("other-later", "politics", 12),
    ]
    books = {
        "one-sided-ask": {"state": "MARKET_STATE_OPEN", "yes_bid": 0, "yes_ask": 0.97,
                          "no_ask_real": 0, "no_ask_synthetic": False, "ask_shares": 800,
                          "bid_shares": 0, "open_interest": 0, "shares_traded": 10},
        "wide-book": {"state": "MARKET_STATE_OPEN", "yes_bid": 0.90, "yes_ask": 0.97,
                      "no_ask_real": 0.10, "no_ask_synthetic": False, "ask_shares": 800,
                      "bid_shares": 800, "open_interest": 0, "shares_traded": 10},
        "ask-above-mid-below": {"state": "MARKET_STATE_OPEN", "yes_bid": 0.90, "yes_ask": 0.93,
                                "no_ask_real": 0.10, "no_ask_synthetic": False, "ask_shares": 800,
                                "bid_shares": 800, "open_interest": 0, "shares_traded": 10},
        "tc-temp-miahigh-2026-10-19-gte80lt82f": {
            "state": "MARKET_STATE_OPEN", "yes_bid": 0.91, "yes_ask": 0.94,
            "no_ask_real": 0.09, "no_ask_synthetic": False, "ask_shares": 800,
            "bid_shares": 800, "open_interest": 0, "shares_traded": 10,
        },
        "other-later": {"state": "MARKET_STATE_OPEN", "yes_bid": 0.91, "yes_ask": 0.93,
                        "no_ask_real": 0.09, "no_ask_synthetic": False, "ask_shares": 800,
                        "bid_shares": 800, "open_interest": 0, "shares_traded": 10},
    }
    calls = []

    monkeypatch.setattr("agent.pm_us.list_markets", lambda params: markets)
    monkeypatch.setattr("agent.pm_us.get_bbo", lambda slug: calls.append(slug) or books.get(slug, {}))

    data = us_scanner.scan(max_bbo_calls=20, now=now)
    assert calls[0].startswith("tc-temp-")
    for slug in ("cpceth-above-day-2026-10-19-4000", "cpcsol-above-day-2026-10-19-180",
                 "cpcxrp-above-day-2026-10-19-2", "cpc-btc-above-day-2026-10-19-84250", "too-soon"):
        assert slug not in calls
    kept = {(g["slug"], g["side"]) for g in data["gimme_bets"]}
    assert ("tc-temp-miahigh-2026-10-19-gte80lt82f", "YES") in kept
    assert ("other-later", "YES") in kept
    assert all(not s.startswith("cpc") for s, _ in kept)
    assert "one-sided-ask" not in {s for s, _ in kept}
    assert "wide-book" not in {s for s, _ in kept}
    assert "ask-above-mid-below" not in {s for s, _ in kept}
    weather = next(g for g in data["gimme_bets"] if g["slug"].startswith("tc-temp-"))
    assert weather["market_mid"] == pytest.approx(0.925)
    assert weather["market_bid"] == pytest.approx(0.91)
    assert weather["market_ask"] == pytest.approx(0.94)
    assert weather["depth_shares"] == 800
    assert weather["price"] == weather["market_mid"]
    reasons = {(g["slug"], g["side"]): g["book_reject"] for g in data["gimme_evaluated"]}
    assert reasons[("one-sided-ask", "YES")] == "one_sided_no_bid"
    assert reasons[("wide-book", "YES")] == "wide_spread"
    assert reasons[("ask-above-mid-below", "YES")] == "below_mid"


def test_old_cpc_dash_override_lets_cpceth_through_the_scanner(monkeypatch):
    """The env override still works: ^cpc- does not hide cpceth-, the bug we're fixing."""
    from agent import us_scanner

    monkeypatch.setenv("US_GIMME_SKIP_SLUG_REGEX", r"^cpc-")
    now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    end = (now + timedelta(hours=20)).isoformat()
    markets = [{
        "slug": "cpceth-above-day-2026-10-19-4000",
        "category": "crypto",
        "endDate": end,
        "active": True,
        "closed": False,
        "question": "ETH",
        "title": "Yes",
        "marketSides": [
            {"long": True, "price": {"value": "0.94"}},
            {"long": False, "price": {"value": "0.08"}},
        ],
    }]
    monkeypatch.setattr("agent.pm_us.list_markets", lambda params: markets)
    monkeypatch.setattr("agent.pm_us.get_bbo", lambda slug: {
        "state": "MARKET_STATE_OPEN", "yes_bid": 0.92, "yes_ask": 0.94,
        "no_ask_real": 0.08, "ask_shares": 500, "bid_shares": 500,
        "open_interest": 0, "shares_traded": 1,
    })
    data = us_scanner.scan(now=now)
    assert any(g["slug"].startswith("cpceth-") for g in data["gimme_bets"])


def test_proposal_log_records_bid_ask_mid_and_depth(tmp_path):
    from agent.proposal_log import log_cycle

    path = tmp_path / "proposals.jsonl"
    now = datetime(2026, 10, 18, 16, 0, tzinfo=timezone.utc)
    kept = {
        "strategy": "gimme_bets",
        "venue": "us",
        "slug": "tc-temp-miahigh-2026-10-20-gte80lt82f",
        "side": "BUY",
        "token_hint": "yes",
        "price": 0.94,
        "market_bid": 0.91,
        "market_ask": 0.94,
        "market_mid": 0.925,
        "depth_shares": 800,
        "model_prob": 0.99,
        "edge_pct": 4.6,
        "category": "climate",
        "risk_check": {"approved": True, "reason": "ok"},
    }
    rejected = {
        "strategy": "gimme_bets",
        "slug": "one-sided",
        "side": "yes",
        "market_bid": 0,
        "market_ask": 0.97,
        "market_mid": None,
        "depth_shares": 100,
        "reject_reason": "one_sided_no_bid",
    }
    n = log_cycle([kept], quotes=[], now=now, path=path, candidates=[rejected, kept])
    assert n == 2
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    proposal = next(r for r in rows if r["record"] == "proposal")
    assert proposal["market_bid"] == pytest.approx(0.91)
    assert proposal["market_ask"] == pytest.approx(0.94)
    assert proposal["market_mid"] == pytest.approx(0.925)
    assert proposal["depth_shares"] == 800
    assert proposal["market_prob"] == pytest.approx(0.925)
    candidate = next(r for r in rows if r["record"] == "gimme_candidate")
    assert candidate["market_bid"] == 0
    assert candidate["reject_reason"] == "one_sided_no_bid"
    assert candidate["slug"] == "one-sided"


def test_paper_trade_record_stores_category(monkeypatch, tmp_path):
    from agent import executor

    monkeypatch.setenv("POLYMARKET_VENUE", "us")
    monkeypatch.delenv("POLYMARKET_LIVE", raising=False)
    monkeypatch.delenv("POLYMARKET_US_KEY_ID", raising=False)
    monkeypatch.delenv("POLYMARKET_US_SECRET_KEY", raising=False)
    log = tmp_path / "paper_trades.jsonl"
    monkeypatch.setattr(executor, "PAPER_TRADE_LOG", log)
    monkeypatch.setattr("agent.pm_us.get_market", lambda slug: {"orderPriceMinTickSize": "0.01"})
    monkeypatch.setattr("agent.pm_us.get_bbo", lambda slug: {
        "state": "MARKET_STATE_OPEN", "yes_bid": 0.90, "yes_ask": 0.93, "no_ask": 0.10,
    })
    sent = []
    monkeypatch.setattr("agent.pm_us.place_limit", lambda *a, **k: sent.append(True) or {"ok": True})

    rec = executor.place_limit_order(
        token_id="", side="BUY", price=0.93, size=5,
        market_question="Miami high", strategy="gimme_bets", reason="unit",
        slug="tc-temp-miahigh-2026-10-20-gte80lt82f", token_hint="yes",
        category="climate",
    )
    assert rec["status"] == "paper_filled"
    assert rec["mode"] == "paper"
    assert rec["category"] == "climate"
    assert sent == []
    stored = json.loads(log.read_text().splitlines()[0])
    assert stored["category"] == "climate"

    crypto = executor.place_limit_order(
        token_id="", side="BUY", price=0.93, size=5,
        market_question="ETH", strategy="gimme_bets", reason="unit",
        slug="cpcxrp-above-day-2026-10-20-2", token_hint="yes",
        category="",
    )
    assert crypto["category"] == "crypto"
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert rows[-1]["category"] == "crypto"
    assert is_crypto(rows[-1]["slug"], rows[-1]["category"])


def _crypto_proposal(slug, price=0.95):
    return {
        "strategy": "gimme_bets",
        "category": "crypto",
        "slug": slug,
        "side": "BUY",
        "price": price,
        "size_shares": 5,
        "size_usd": round(5 * price, 2),
        "edge_pct": 5,
        "conviction": 0.8,
        "volume_24h": 100000,
        "question": slug,
    }


def test_crypto_cap_blocks_a_second_coin_and_category_field_is_used(monkeypatch):
    from agent.risk_gate import check_proposal, filter_proposals, _calculate_exposure

    monkeypatch.setattr("agent.config.MAX_CRYPTO_EXPOSURE_PCT", 0.05)
    monkeypatch.setattr("agent.config.MAX_SINGLE_POSITION_PCT", 0.05)
    monkeypatch.setattr("agent.config.MAX_CATEGORY_EXPOSURE_PCT", 0.50)

    now = datetime.now().astimezone().isoformat()
    # No category key: the old log. Slug still counts toward the combined crypto cap,
    # and the per-category bucket falls back to the strategy name.
    legacy = {
        "timestamp": now,
        "status": "paper_filled",
        "cost_usd": 4.75,
        "strategy": "gimme_bets",
        "slug": "cpceth-above-day-2026-10-18-4000",
    }
    exposure = _calculate_exposure([legacy])
    assert exposure["crypto_deployed"] == pytest.approx(4.75)
    assert exposure["by_category"].get("gimme_bets") == pytest.approx(4.75)
    assert "crypto" not in exposure["by_category"]

    blocked = check_proposal(_crypto_proposal("cpcsol-above-day-2026-10-18-180"), 95, [legacy], exposure)
    assert blocked["approved"] is False
    assert "crypto" in blocked["reason"].lower()

    labeled = {
        "timestamp": now,
        "status": "paper_filled",
        "cost_usd": 10,
        "strategy": "gimme_bets",
        "category": "climate",
        "slug": "tc-temp-miahigh-2026-10-18-gte80lt82f",
    }
    labeled_exposure = _calculate_exposure([labeled])
    assert labeled_exposure["by_category"]["climate"] == pytest.approx(10)
    assert "gimme_bets" not in labeled_exposure["by_category"]
    assert labeled_exposure["crypto_deployed"] == 0

    # Same event group is still rejected by the correlation cap, before the crypto cap matters.
    same_group = {
        "timestamp": now,
        "status": "paper_filled",
        "cost_usd": 1.0,
        "strategy": "gimme_bets",
        "category": "crypto",
        "slug": "cpc-btc-above-day-2026-10-18-80000",
    }
    group_exposure = _calculate_exposure([same_group])
    second_btc = _crypto_proposal("cpc-btc-range-day-2026-10-18-82000", price=0.50)
    second_btc["size_usd"] = 2.0
    second_btc["size_shares"] = 4
    group_block = check_proposal(second_btc, 95, [same_group], group_exposure)
    assert group_block["approved"] is False
    assert "event group" in group_block["reason"]

    monkeypatch.setattr("agent.risk_gate.get_paper_trades", lambda: [])
    monkeypatch.setattr("agent.risk_gate.realized_loss_check", lambda bankroll: None)
    monkeypatch.setattr("agent.risk_gate._live_us", lambda: False)
    approved = filter_proposals([
        _crypto_proposal("cpceth-above-day-2026-10-18-4000"),
        _crypto_proposal("cpcxrp-above-day-2026-10-18-2"),
    ], 95)
    assert len(approved) == 1
    assert approved[0]["slug"].startswith("cpceth-")


def test_loss_pause_still_rejects_everything(monkeypatch):
    from agent.risk_gate import filter_proposals

    monkeypatch.setattr("agent.risk_gate.get_paper_trades", lambda: [])
    monkeypatch.setattr("agent.risk_gate._live_us", lambda: False)
    monkeypatch.setattr(
        "agent.risk_gate.realized_loss_check",
        lambda bankroll: "Weekly realized loss $20.00 > 10% of bankroll — paused",
    )
    approved = filter_proposals([_crypto_proposal("cpceth-above-day-2026-10-18-4000")], 95)
    assert approved == []


def test_replay_keeps_only_rows_the_log_can_support():
    weather = {
        "record": "proposal",
        "strategy": "gimme_bets",
        "slug": "tc-temp-miahigh-2026-10-20-gte80lt82f",
        "side": "yes",
        "market_bid": 0.91,
        "market_ask": 0.94,
        "market_mid": 0.925,
        "model_prob": 0.99,
        "hours_to_expiry": 20,
    }
    crypto = {
        "record": "proposal",
        "strategy": "gimme_bets",
        "slug": "cpceth-above-day-2026-10-18-4000",
        "side": "yes",
        "market_ask": 0.96,
        "model_prob": None,
    }
    blank_bid = {
        "record": "proposal",
        "strategy": "gimme_bets",
        "slug": "some-other-market",
        "side": "yes",
        "market_bid": None,
        "market_ask": 0.95,
        "model_prob": None,
    }
    settlements = {
        weather["slug"]: 1,
        crypto["slug"]: 0,
    }
    view = replay_decision(weather)
    assert view["would_keep"] is True
    crypto_view = replay_decision(crypto)
    assert crypto_view["would_keep"] is False
    assert crypto_view["checks"]["crypto_slug_skip"] is False
    assert crypto_view["checks"]["two_sided"] is None
    blank = replay_decision(blank_bid)
    assert blank["would_keep"] is None
    assert "two_sided" in blank["missing"]
    assert "own_edge" in blank["missing"]

    summary = summarize([weather, crypto, blank_bid], settlements)
    assert summary["would_keep"]["n"] == 1
    assert summary["would_keep"]["wins"] == 1
    assert summary["crypto_slug_rejected"]["settled"] == 1
    assert summary["crypto_slug_rejected"]["wins"] == 0
    assert summary["inconclusive"] == 1
    assert any("market_bid" in item for item in summary["cannot_test"])


def test_normalize_category_groups_crypto():
    assert normalize_category("Crypto", "cpceth-above-day-2026-10-18-1") == "crypto"
    assert normalize_category("", "cpcsol-above-day-2026-10-18-1") == "crypto"
    assert normalize_category("", "tc-temp-miahigh-2026-10-18-gte80lt82f") == "climate"
    assert normalize_category("climate", "tc-temp-miahigh-2026-10-18-gte80lt82f") == "climate"
