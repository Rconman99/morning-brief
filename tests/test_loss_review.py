"""Synthetic settled paper trades for the analysis-only loss review.

No network. No .env. The reviewer must not rewrite the trade log, the
settlement cache, or the proposal log.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from agent.loss_review import (
    CAUSES,
    HOLDOUT_DAYS,
    LATE_MOVE,
    OVERCONFIDENT_GAP,
    STALE_EVENT_DAYS,
    STALE_HOURS,
    THIN_DEPTH_SHARES,
    THIN_LIQUIDITY,
    THIN_SPREAD,
    format_summary,
    main,
    review_losses,
)


SLUG = "tc-temp-miahigh-2026-10-20-gte90lt92f"


def _trade(**overrides):
    row = {
        "timestamp": "2026-10-20T15:00:00-04:00",
        "strategy": "weather_us",
        "slug": SLUG,
        "token_hint": "yes",
        "side": "BUY",
        "status": "paper_filled",
        "price": 0.62,
        "fill_price": 0.62,
        "size": 10,
        "role": "maker",
        "venue": "us",
    }
    row.update(overrides)
    return row


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _cache(settlements: dict) -> dict:
    return {"settlements": settlements, "fills": {}}


def _paths(tmp_path: Path, trades, settlements, proposals=None):
    trades_path = tmp_path / "paper_trades.jsonl"
    cache_path = tmp_path / "ledger_cache.json"
    proposals_path = tmp_path / "proposals.jsonl"
    _write_jsonl(trades_path, trades)
    cache_path.write_text(json.dumps(_cache(settlements)), encoding="utf-8")
    _write_jsonl(proposals_path, proposals or [])
    return trades_path, cache_path, proposals_path


def _review(tmp_path, trades, settlements, proposals=None, start="2026-10-01", end="2026-10-31"):
    trades_path, cache_path, proposals_path = _paths(tmp_path, trades, settlements, proposals)
    before = {
        "trades": trades_path.read_text(encoding="utf-8"),
        "cache": cache_path.read_text(encoding="utf-8"),
        "proposals": proposals_path.read_text(encoding="utf-8"),
    }
    report = review_losses(
        start, end,
        trades_path=trades_path,
        cache_path=cache_path,
        proposals_path=proposals_path,
    )
    assert trades_path.read_text(encoding="utf-8") == before["trades"]
    assert cache_path.read_text(encoding="utf-8") == before["cache"]
    assert proposals_path.read_text(encoding="utf-8") == before["proposals"]
    return report


def _loss(report, strategy="weather_us"):
    rows = [row for row in report["losses"] if row["strategy"] == strategy]
    assert len(rows) == 1
    return rows[0]


def test_overconfident_when_model_is_far_above_the_market_and_wrong(tmp_path):
    report = _review(tmp_path, [
        _trade(model_prob=0.88, market_prob=0.88 - OVERCONFIDENT_GAP),
    ], {SLUG: 0})
    assert _loss(report)["cause"] == "overconfident"
    assert report["strategies"]["weather_us"]["causes"]["overconfident"] == 1
    assert report["top_cause"] == "overconfident"


def test_small_gap_is_not_overconfident(tmp_path):
    report = _review(tmp_path, [
        _trade(model_prob=0.70, market_prob=0.70 - OVERCONFIDENT_GAP + 0.01),
    ], {SLUG: 0})
    assert _loss(report)["cause"] == "unknown"


def test_model_below_market_is_not_overconfident(tmp_path):
    report = _review(tmp_path, [
        _trade(model_prob=0.40, market_prob=0.80),
    ], {SLUG: 0})
    assert _loss(report)["cause"] == "unknown"


def test_stale_forecast_age(tmp_path):
    report = _review(tmp_path, [
        _trade(forecast_age_hours=STALE_HOURS, model_prob=0.60, market_prob=0.58),
    ], {SLUG: 0})
    assert _loss(report)["cause"] == "stale_info"


def test_stale_hours_to_expiry(tmp_path):
    report = _review(tmp_path, [
        _trade(hours_to_expiry=STALE_HOURS),
    ], {SLUG: 0})
    assert _loss(report)["cause"] == "stale_info"


def test_stale_when_settlement_timestamp_is_long_after_entry(tmp_path):
    report = _review(tmp_path, [
        _trade(timestamp="2026-10-20T10:00:00-04:00"),
    ], {SLUG: {"settlement": 0, "settled_at": "2026-10-22T10:00:00-04:00"}})
    assert _loss(report)["cause"] == "stale_info"


def test_slug_event_two_days_after_entry_is_stale(tmp_path):
    slug = "tc-temp-miahigh-2026-10-22-gte90lt92f"
    report = _review(tmp_path, [
        _trade(timestamp="2026-10-20T15:00:00-04:00", slug=slug),
    ], {slug: 0})
    assert _loss(report)["cause"] == "stale_info"
    assert STALE_EVENT_DAYS == 2


def test_same_day_slug_is_not_stale(tmp_path):
    report = _review(tmp_path, [_trade()], {SLUG: 0})
    assert _loss(report)["cause"] == "unknown"


def test_wide_spread_is_thin_market(tmp_path):
    report = _review(tmp_path, [
        _trade(spread=THIN_SPREAD),
    ], {SLUG: 0})
    assert _loss(report)["cause"] == "thin_market"


def test_low_depth_or_liquidity_is_thin_market_only_when_recorded(tmp_path):
    thin_depth = _review(tmp_path, [
        _trade(depth_shares=THIN_DEPTH_SHARES - 1),
    ], {SLUG: 0})
    assert _loss(thin_depth)["cause"] == "thin_market"

    nested = tmp_path / "nested"
    nested.mkdir()
    thin_liq = _review(nested, [
        _trade(liquidity=THIN_LIQUIDITY - 1),
    ], {SLUG: 0})
    assert _loss(thin_liq)["cause"] == "thin_market"

    blank = tmp_path / "blank"
    blank.mkdir()
    missing = _review(blank, [_trade()], {SLUG: 0})
    assert _loss(missing)["cause"] == "unknown"


def test_late_move_uses_a_later_quote_not_the_settlement(tmp_path):
    quote = {
        "record": "quote",
        "timestamp": "2026-10-20T18:00:00-04:00",
        "slug": SLUG,
        "side": "yes",
        "market_ask": 0.55,
        "market_bid": 0.55,
    }
    moved = _review(tmp_path, [
        _trade(market_prob=0.55 + LATE_MOVE),
    ], {SLUG: 0}, proposals=[quote])
    assert _loss(moved)["cause"] == "late_move"

    quiet = tmp_path / "quiet"
    quiet.mkdir()
    held = _review(quiet, [
        _trade(market_prob=0.70),
    ], {SLUG: 0}, proposals=[{**quote, "market_ask": 0.69, "market_bid": 0.69}])
    assert _loss(held)["cause"] == "unknown"


def test_overconfident_wins_when_several_heuristics_match(tmp_path):
    report = _review(tmp_path, [
        _trade(
            model_prob=0.90,
            market_prob=0.50,
            forecast_age_hours=48,
            spread=0.20,
            hours_to_expiry=72,
        ),
    ], {SLUG: 0}, proposals=[{
        "record": "quote",
        "timestamp": "2026-10-20T20:00:00-04:00",
        "slug": SLUG,
        "side": "yes",
        "market_ask": 0.20,
        "market_bid": 0.20,
    }])
    assert _loss(report)["cause"] == "overconfident"


def test_wins_and_unsettled_and_live_rows_are_not_tagged(tmp_path):
    other = "tc-temp-miahigh-2026-10-21-gte90lt92f"
    report = _review(tmp_path, [
        _trade(model_prob=0.95, market_prob=0.50),
        _trade(slug=other, timestamp="2026-10-21T15:00:00-04:00", model_prob=0.95, market_prob=0.40),
        _trade(slug="live-slug", status="submitted", model_prob=0.95, market_prob=0.40),
        _trade(slug="sell-slug", side="SELL"),
        _trade(slug="polygon-slug", venue="polygon", model_prob=0.95, market_prob=0.40),
    ], {SLUG: 1})
    assert report["wins"] == 1
    assert report["losses"] == []
    assert report["loss_count"] == 0
    assert report["unsettled_ignored"] == 1
    assert report["strategies"]["weather_us"]["top_cause"] is None


def test_unsettled_paper_trade_is_counted_and_not_tagged(tmp_path):
    report = _review(tmp_path, [
        _trade(model_prob=0.95, market_prob=0.40),
    ], {})
    assert report["settled"] == 0
    assert report["unsettled_ignored"] == 1
    assert report["losses"] == []
    assert report["top_cause"] is None


def test_date_window_uses_eastern_days(tmp_path):
    inside = _trade(timestamp="2026-10-15T23:30:00-04:00")
    outside = _trade(
        timestamp="2026-10-16T04:30:00Z",
        slug="tc-temp-miahigh-2026-10-15-gte90lt92f",
    )
    report = _review(
        tmp_path, [inside, outside],
        {SLUG: 0, outside["slug"]: 0},
        start="2026-10-15", end="2026-10-15",
    )
    assert report["settled"] == 1
    assert report["loss_count"] == 1


def test_brier_splits_last_14_days_from_earlier(tmp_path):
    early_slug = "tc-temp-miahigh-2026-10-01-gte90lt92f"
    recent_slug = "tc-temp-miahigh-2026-10-20-gte90lt92f"
    report = _review(tmp_path, [
        _trade(
            timestamp="2026-10-01T12:00:00-04:00",
            slug=early_slug,
            model_prob=0.0,
            market_prob=1.0,
        ),
        _trade(
            timestamp="2026-10-20T12:00:00-04:00",
            slug=recent_slug,
            model_prob=1.0,
            market_prob=0.0,
        ),
    ], {early_slug: 1, recent_slug: 0}, start="2026-10-01", end="2026-10-31")
    weather = report["strategies"]["weather_us"]["brier"]
    assert report["recent_start"] == "2026-10-18"
    assert HOLDOUT_DAYS == 14
    assert weather["earlier"]["n"] == 1
    assert weather["earlier"]["brier_model"] == pytest.approx(1.0)
    assert weather["earlier"]["brier_market"] == pytest.approx(0.0)
    assert weather["recent"]["n"] == 1
    assert weather["recent"]["brier_model"] == pytest.approx(1.0)
    assert weather["recent"]["brier_market"] == pytest.approx(0.0)
    assert weather["recent"]["beats_market"] is False


def test_gimme_alias_and_stage_bucket(tmp_path):
    report = _review(tmp_path, [
        _trade(strategy="gimme_bets", model_prob=0.50, market_prob=0.48),
        _trade(strategy="stage", slug="stage-market", timestamp="2026-10-21T12:00:00-04:00"),
    ], {SLUG: 0, "stage-market": 0})
    assert report["strategies"]["gimme"]["loss_count"] == 1
    assert report["strategies"]["gimme"]["causes"]["unknown"] == 1
    assert "gimme_bets" not in report["strategies"]
    assert report["strategies"]["stage"]["loss_count"] == 1
    assert report["strategies"]["weather_us"]["loss_count"] == 0
    assert list(report["strategies"])[:3] == ["weather_us", "gimme", "stage"]


def test_top_cause_tie_follows_heuristic_priority(tmp_path):
    second = "tc-temp-miahigh-2026-10-21-gte90lt92f"
    report = _review(tmp_path, [
        _trade(model_prob=0.90, market_prob=0.50),
        _trade(slug=second, timestamp="2026-10-21T12:00:00-04:00", forecast_age_hours=48),
    ], {SLUG: 0, second: 0})
    assert report["strategies"]["weather_us"]["causes"]["overconfident"] == 1
    assert report["strategies"]["weather_us"]["causes"]["stale_info"] == 1
    assert report["top_cause"] == "overconfident"
    assert report["cause_priority"] == list(CAUSES)


def test_proposal_log_supplies_probabilities(tmp_path):
    proposal = {
        "record": "proposal",
        "timestamp": "2026-10-20T14:00:00-04:00",
        "strategy": "weather_us",
        "slug": SLUG,
        "side": "yes",
        "model_prob": 0.91,
        "market_prob": 0.60,
    }
    report = _review(tmp_path, [_trade()], {SLUG: 0}, proposals=[proposal])
    loss = _loss(report)
    assert loss["cause"] == "overconfident"
    assert loss["model_prob"] == pytest.approx(0.91)
    assert loss["market_prob"] == pytest.approx(0.60)


def test_no_side_loss_uses_the_side_we_bought(tmp_path):
    report = _review(tmp_path, [
        _trade(token_hint="no", model_prob=0.90, market_prob=0.55),
    ], {SLUG: 1})
    assert _loss(report)["cause"] == "overconfident"
    assert _loss(report)["side"] == "no"


def test_malformed_line_is_skipped(tmp_path):
    trades_path, cache_path, proposals_path = _paths(tmp_path, [_trade()], {SLUG: 0})
    trades_path.write_text("not-json\n" + trades_path.read_text(encoding="utf-8"), encoding="utf-8")
    report = review_losses(
        "2026-10-01", "2026-10-31",
        trades_path=trades_path, cache_path=cache_path, proposals_path=proposals_path,
    )
    assert report["loss_count"] == 1


def test_text_summary_names_causes_and_brier(tmp_path):
    report = _review(tmp_path, [
        _trade(model_prob=0.90, market_prob=0.50),
    ], {SLUG: 0})
    text = format_summary(report)
    assert "Loss review 2026-10-01 through 2026-10-31" in text
    assert "weather_us" in text
    assert "gimme" in text
    assert "stage" in text
    assert "overconfident" in text
    assert "Top failure cause: overconfident" in text
    assert "Brier" in text
    assert "last 14 days" in text
    assert "does not place orders" in text
    assert ".env" not in text


def test_cli_json_and_save(tmp_path, capsys, monkeypatch):
    trades_path, cache_path, proposals_path = _paths(tmp_path, [
        _trade(model_prob=0.90, market_prob=0.40),
    ], {SLUG: 0})
    reviews = tmp_path / "reviews"
    monkeypatch.setattr("agent.loss_review.REVIEWS_DIR", reviews)
    code = main([
        "--from", "2026-10-01",
        "--to", "2026-10-31",
        "--json",
        "--save",
        "--trades", str(trades_path),
        "--cache", str(cache_path),
        "--proposals", str(proposals_path),
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["top_cause"] == "overconfident"
    written = reviews / "loss_review_2026-10-01_2026-10-31.json"
    assert json.loads(written.read_text(encoding="utf-8"))["top_cause"] == "overconfident"
    assert not list(PROJECT_ROOT.joinpath("agent", "reviews").glob("loss_review_*"))


def test_cli_text_does_not_write(tmp_path, capsys):
    trades_path, cache_path, proposals_path = _paths(tmp_path, [_trade()], {SLUG: 0})
    code = main([
        "--from", "2026-10-01",
        "--to", "2026-10-31",
        "--trades", str(trades_path),
        "--cache", str(cache_path),
        "--proposals", str(proposals_path),
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "unknown" in out
    assert "Top failure cause: unknown" in out
    assert list(tmp_path.glob("loss_review_*")) == []


def test_cli_rejects_bad_dates():
    with pytest.raises(SystemExit) as exc:
        main(["--from", "10/01/2026", "--to", "2026-10-31"])
    assert exc.value.code != 0


def test_module_does_not_read_env_files():
    source = (PROJECT_ROOT / "agent" / "loss_review.py").read_text(encoding="utf-8")
    assert ".env" not in source
    assert "dotenv" not in source
    assert "POLYMARKET_LIVE" not in source
    assert "POLYMARKET_PAPER" not in source
