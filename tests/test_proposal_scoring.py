"""Proposal log, Brier / log loss / CLV, and the scoreboard evidence line."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import math
from datetime import datetime, timezone

import pytest

from agent.proposal_log import (
    brier_score,
    closing_line_value,
    evidence_line,
    format_model_section,
    implied_prob,
    log_cycle,
    log_loss,
    quotes_from_weather_envelope,
    score_proposal_records,
    summarize_model_vs_market,
)
from agent.scoreboard import evaluate_ladder, render


SLUG = "tc-temp-miahigh-2026-10-11-gte93lt94f"


def _proposal(model=0.86, side="no", strategy="weather_us", ask=0.80, bid=0.76, edge_pct=5.0,
              approved=False, reason="Already holding 1 position(s) in event group 'miahigh|2026-10-11'"):
    return {
        "strategy": strategy,
        "venue": "us",
        "slug": SLUG,
        "market_id": SLUG,
        "side": "BUY",
        "token_hint": side,
        "price": ask,
        "model_prob": model,
        "market_ask": ask,
        "market_bid": bid,
        "edge_pct": edge_pct,
        "question": "Miami high",
        "category": "climate",
        "risk_check": {"approved": approved, "reason": reason},
    }


class TestScoringMath:
    def test_brier(self):
        assert brier_score(0.8, 1) == pytest.approx(0.04)
        assert brier_score(0.8, 0) == pytest.approx(0.64)
        assert brier_score(1, 1) == pytest.approx(0.0)

    def test_log_loss_matches_definition_and_stays_finite(self):
        assert log_loss(0.8, 1) == pytest.approx(-math.log(0.8))
        assert log_loss(0.8, 0) == pytest.approx(-math.log(0.2))
        assert math.isfinite(log_loss(1, 1))
        assert math.isfinite(log_loss(0, 0))

    def test_clv_is_close_minus_proposal(self):
        assert closing_line_value(0.70, 0.55) == pytest.approx(0.15)
        assert closing_line_value(0.40, 0.55) == pytest.approx(-0.15)

    def test_implied_prob_uses_mid_then_the_single_quote(self):
        assert implied_prob(0.80, 0.76) == pytest.approx(0.78)
        assert implied_prob(0.80, None) == pytest.approx(0.80)
        assert implied_prob(0, 0) is None


class TestProposalLog:
    def test_one_row_per_market_side_per_cycle_keeps_the_first(self, tmp_path):
        path = tmp_path / "proposals.jsonl"
        now = datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)
        first = _proposal(model=0.86)
        second = _proposal(model=0.10)
        n = log_cycle([first, second], quotes=[], now=now, path=path)
        assert n == 1
        row = __import__("json").loads(path.read_text().splitlines()[0])
        assert row["record"] == "proposal"
        assert row["strategy"] == "weather_us"
        assert row["slug"] == SLUG
        assert row["market_id"] == SLUG
        assert row["group"] == "miahigh|2026-10-11"
        assert row["side"] == "no"
        assert row["model_prob"] == pytest.approx(0.86)
        assert row["market_ask"] == pytest.approx(0.80)
        assert row["market_bid"] == pytest.approx(0.76)
        assert row["market_prob"] == pytest.approx(0.78)
        assert row["edge"] == pytest.approx(0.05)
        assert row["approved"] is False
        assert "event group" in row["reject_reason"]
        assert row["executed"] is False

    def test_executed_flag_and_a_later_quote(self, tmp_path):
        path = tmp_path / "proposals.jsonl"
        t1 = datetime(2026, 10, 11, 16, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc)
        prop = _proposal(approved=True, reason="Approved")
        log_cycle([prop], executed=[{
            "strategy": "weather_us", "slug": SLUG, "token_hint": "no", "side": "BUY",
        }], quotes=[], now=t1, path=path)
        # Same market, not proposed this cycle: the quote is the pre-close update.
        n = log_cycle([], quotes=[{
            "slug": SLUG, "side": "no", "market_ask": 0.91, "market_bid": 0.89,
        }], now=t2, path=path)
        assert n == 1
        lines = path.read_text().splitlines()
        assert len(lines) == 2
        first = __import__("json").loads(lines[0])
        assert first["executed"] is True
        assert first["approved"] is True
        assert first["reject_reason"] is None

    def test_scoring_uses_first_proposal_and_last_later_quote(self):
        records = [
            {
                "record": "proposal", "timestamp": "2026-10-11T14:00:00+00:00",
                "strategy": "weather_us", "slug": SLUG, "side": "yes", "group": "miahigh|2026-10-11",
                "model_prob": 0.80, "market_ask": 0.60, "market_bid": 0.58, "market_prob": 0.59,
                "approved": True,
            },
            {
                "record": "proposal", "timestamp": "2026-10-11T18:00:00+00:00",
                "strategy": "weather_us", "slug": SLUG, "side": "yes",
                "model_prob": 0.40, "market_ask": 0.62, "market_bid": 0.60, "market_prob": 0.61,
            },
            {
                "record": "quote", "timestamp": "2026-10-12T15:00:00+00:00",
                "slug": SLUG, "side": "yes", "market_ask": 0.72, "market_bid": 0.68,
            },
        ]
        scored = score_proposal_records(records, {SLUG: 1.0})
        assert len(scored) == 1
        row = scored[0]
        assert row["model_prob"] == pytest.approx(0.80)  # first proposal, not the later 0.40
        assert row["outcome"] == pytest.approx(1.0)
        assert row["brier_model"] == pytest.approx(0.04)
        assert row["brier_market"] == pytest.approx((0.59 - 1) ** 2)
        assert row["logloss_model"] == pytest.approx(log_loss(0.80, 1))
        assert row["logloss_market"] == pytest.approx(log_loss(0.59, 1))
        assert row["close_price"] == pytest.approx(0.70)  # mid of the last quote
        assert row["clv"] == pytest.approx(0.70 - 0.59)
        assert row["n_logged"] == 2

    def test_no_side_outcome_and_missing_close_leaves_clv_blank(self):
        records = [{
            "record": "proposal", "timestamp": "2026-10-11T14:00:00+00:00",
            "strategy": "weather_us", "slug": SLUG, "side": "no",
            "model_prob": 0.75, "market_ask": 0.70, "market_bid": None, "market_prob": 0.70,
        }]
        # YES settled at 0, so the NO side won.
        scored = score_proposal_records(records, {SLUG: 0.0})
        assert scored[0]["outcome"] == pytest.approx(1.0)
        assert scored[0]["brier_model"] == pytest.approx((0.75 - 1) ** 2)
        assert scored[0]["clv"] is None
        assert scored[0]["close_price"] is None

    def test_unsettled_market_is_not_scored(self):
        records = [{
            "record": "proposal", "timestamp": "2026-10-11T14:00:00+00:00",
            "strategy": "weather_us", "slug": SLUG, "side": "yes",
            "model_prob": 0.8, "market_prob": 0.6, "market_ask": 0.6, "market_bid": 0.6,
        }]
        assert score_proposal_records(records, {}) == []

    def test_same_timestamp_quote_does_not_invent_clv(self):
        records = [
            {
                "record": "proposal", "timestamp": "2026-10-11T14:00:00+00:00",
                "strategy": "weather_us", "slug": SLUG, "side": "yes",
                "model_prob": 0.8, "market_ask": 0.55, "market_bid": 0.55, "market_prob": 0.55,
            },
            {
                "record": "quote", "timestamp": "2026-10-11T14:00:00+00:00",
                "slug": SLUG, "side": "yes", "market_ask": 0.90, "market_bid": 0.90,
            },
        ]
        scored = score_proposal_records(records, {SLUG: 1.0})
        assert scored[0]["clv"] is None

    def test_ten_bucket_summary_and_evidence_line(self):
        scored = score_proposal_records([
            {
                "record": "proposal", "timestamp": "2026-10-11T14:00:00+00:00",
                "strategy": "weather_us", "slug": SLUG, "side": "yes",
                "model_prob": 0.85, "market_ask": 0.60, "market_bid": 0.60, "market_prob": 0.60,
            },
            {
                "record": "quote", "timestamp": "2026-10-11T18:00:00+00:00",
                "slug": SLUG, "side": "yes", "market_ask": 0.66, "market_bid": 0.66,
            },
        ], {SLUG: 1.0})
        summary = summarize_model_vs_market(scored)
        cal = summary["weather_us"]["calibration"]
        assert len(cal) == 10
        assert cal[8]["n"] == 1  # 0.85 lands in [0.8, 0.9)
        assert cal[8]["mean_pred"] == pytest.approx(0.85)
        assert cal[8]["realized"] == pytest.approx(1.0)
        assert cal[0]["n"] == 0
        assert summary["weather_us"]["beats_market"] is True
        assert summary["weather_us"]["mean_clv"] == pytest.approx(0.06)
        line = evidence_line(summary)
        assert line.startswith("Proposal evidence: model beats market on N=1")
        assert "CLV +6.0¢" in line
        text = "\n".join(format_model_section(summary, brief=False))
        assert "calibration" in text
        assert "0.0:n=0" in text
        brief = "\n".join(format_model_section(summary, brief=True))
        assert "0.0:n=0" not in brief
        assert "0.8:n=1" in brief

    def test_model_that_loses_to_the_market_says_so(self):
        summary = summarize_model_vs_market([{
            "strategy": "weather_us", "model_prob": 0.2, "market_prob": 0.9,
            "outcome": 1.0, "brier_model": brier_score(0.2, 1), "brier_market": brier_score(0.9, 1),
            "logloss_model": log_loss(0.2, 1), "logloss_market": log_loss(0.9, 1), "clv": None,
        }])
        assert summary["weather_us"]["beats_market"] is False
        assert "does not beat" in evidence_line(summary)


class TestWeatherQuotesStayFresh:
    def test_stale_weather_file_does_not_supply_a_close(self):
        env = {
            "status": "success",
            "generated_at": "2026-10-01T12:00:00+00:00",
            "data": {"rows": [{"slug": SLUG, "yes_ask": 0.4, "yes_bid": 0.3, "no_ask": 0.7}]},
        }
        now = datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)
        assert quotes_from_weather_envelope(env, now=now) == []
        fresh = dict(env, generated_at="2026-10-11T17:40:00+00:00")
        quotes = quotes_from_weather_envelope(fresh, now=now)
        assert {q["side"] for q in quotes} == {"yes", "no"}


class TestScoreboardDoesNotGateOnProposals:
    def test_ladder_checks_ignore_proposal_evidence(self):
        ladder = evaluate_ladder([], 95, "2026-10-01T00:00:00+00:00")
        blob = __import__("json").dumps(ladder["checks"])
        assert "Proposal" not in blob
        assert ladder["verdict"] == "HOLD"
        text = render({}, ladder, {}, {}, model_vs_market={})
        assert "Proposal evidence: no scored proposals yet." in text
        assert ladder["advice"] in text
        assert "does not move the capital ladder" in text


class TestRunLoopLogsProposals:
    def test_run_agent_writes_rejected_proposals(self, tmp_path, monkeypatch):
        import agent.run as run
        path = tmp_path / "proposals.jsonl"
        monkeypatch.setattr("agent.proposal_log.PROPOSAL_LOG", path)
        monkeypatch.setattr("agent.proposal_log.load_weather_quotes", lambda: [])
        monkeypatch.setattr(run, "get_mode", lambda: "paper")
        monkeypatch.setattr(run, "init_paper_bankroll", lambda bankroll: None)
        monkeypatch.setattr(run, "should_evaluate", lambda: False)
        monkeypatch.setattr(run, "load_agent_config", lambda: {"weather_us": {}, "gimme_bets": {}})
        monkeypatch.setattr(run, "print_summary", lambda *a, **k: None)
        monkeypatch.setattr("agent.pm_client.get_wallet_address", lambda: "")
        prop = _proposal()
        monkeypatch.setattr(run, "run_all_strategies", lambda params: [prop])
        monkeypatch.setattr(run, "filter_proposals", lambda proposals, bankroll: [])
        run.run_agent(bankroll=95, dry_run=True)
        lines = path.read_text().splitlines()
        assert len(lines) == 1
        row = __import__("json").loads(lines[0])
        assert row["approved"] is False
        assert row["model_prob"] == pytest.approx(0.86)
        assert row["side"] == "no"
