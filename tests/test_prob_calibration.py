"""Settled-trade probability calibration: small samples trust the market.

The edge test sees the calibrated probability. The raw model probability is
what gets logged. Calibration cannot make a probability more extreme, and it
cannot raise edge. The humility cap still sees the pre-calibration edge.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from agent.gimme_rules import passes_own_edge, taker_fee_per_share
from agent.prob_calibration import (
    calibrate_side,
    clamp_side_probability,
    confidence_scale,
    current_fit,
    edge_probability,
    filter_effect,
    fit_calibration,
    load_settled_rows,
    resize_shares,
    shrinkage_weight,
)
from agent.weather_ensemble import decide_from_yes


def _fit(**kwargs):
    base = {
        "enabled": True,
        "n": 0,
        "model_weight": 0.0,
        "prior_n": 80.0,
        "platt": None,
        "bins": None,
        "platt_min_n": 80,
        "bin_min_n": 200,
    }
    base.update(kwargs)
    return base


def _trusted():
    return _fit(n=500, model_weight=1.0, prior_n=0.0, platt_min_n=10000, bin_min_n=10000)


class TestShrinkage:
    def test_empty_sample_weight_is_zero(self):
        weight, n = shrinkage_weight([], prior=80)
        assert n == 0
        assert weight == 0.0

    def test_small_sample_stays_near_the_market_even_if_the_model_was_perfect(self):
        rows = [
            {"model_prob": 0.90, "market_prob": 0.50, "outcome": 1.0}
            for _ in range(5)
        ]
        weight, n = shrinkage_weight(rows, prior=80)
        assert n == 5
        # Unshrunk weight would be 1 (model gap points the right way). The prior
        # keeps 5 trades at 5/85.
        assert weight == pytest.approx(5 / 85, abs=1e-9)
        assert weight < 0.1

    def test_a_model_worse_than_the_market_gets_no_weight(self):
        # Model says 88%, market says 55%, the event misses. Weight must not go negative.
        rows = [
            {"model_prob": 0.88, "market_prob": 0.55, "outcome": 0.0}
            for _ in range(60)
        ]
        weight, n = shrinkage_weight(rows, prior=80)
        assert n == 60
        assert weight == 0.0

    def test_platt_and_bins_stay_off_until_the_sample_is_large_enough(self):
        rows = []
        for i in range(60):
            rows.append({
                "model_prob": 0.80 + (i % 5) * 0.03,
                "market_prob": 0.55,
                "outcome": 1.0 if i % 2 == 0 else 0.0,
            })
        fit = fit_calibration(rows, prior=80, platt_min=80, bins_min=200, bin_count=20)
        assert fit["n"] == 60
        assert fit["platt"] is None
        assert fit["bins"] is None
        assert fit["model_weight"] < 60 / (60 + 80) + 1e-9

    def test_platt_turns_on_after_its_floor_and_cannot_outrun_the_raw_probability(self):
        rows = []
        for i in range(90):
            # Overconfident: model 0.90, reality is a coin flip, market sits at 0.50.
            rows.append({
                "model_prob": 0.90,
                "market_prob": 0.50,
                "outcome": 1.0 if i < 45 else 0.0,
            })
        fit = fit_calibration(rows, prior=80, platt_min=80, bins_min=200)
        assert fit["n"] == 90
        assert fit["platt"] is not None
        assert 0.0 <= fit["platt"]["b"] <= 1.0
        out = calibrate_side(0.90, 0.50, fit)
        assert out["calibrated_prob"] <= 0.90 + 1e-9
        assert abs(out["calibrated_prob"] - 0.5) <= abs(0.90 - 0.5) + 1e-9


class TestClamp:
    def test_small_n_falls_back_to_the_market_when_that_is_less_extreme(self):
        out = calibrate_side(0.88, 0.60, _fit())
        assert out["method"] == "market_shrink"
        assert out["calibrated_prob"] == pytest.approx(0.60)
        assert out["model_weight"] == 0.0

    def test_a_more_extreme_market_cannot_raise_confidence(self):
        out = calibrate_side(0.88, 0.95, _fit())
        assert out["calibrated_prob"] == pytest.approx(0.88)
        assert "clamp" in out["method"]
        assert abs(out["calibrated_prob"] - 0.5) <= abs(0.88 - 0.5) + 1e-12

    def test_never_more_extreme_than_raw_across_a_grid(self):
        for raw in (0.05, 0.20, 0.50, 0.80, 0.88, 0.97):
            for market in (0.02, 0.40, 0.60, 0.90, 0.99):
                out = calibrate_side(raw, market, _fit(model_weight=0.35, n=40))
                assert abs(out["calibrated_prob"] - 0.5) <= abs(raw - 0.5) + 1e-9
                # Edge of this side is monotonic in the probability. It must not rise.
                if raw >= 0.5:
                    assert out["calibrated_prob"] <= raw + 1e-9
                else:
                    assert out["calibrated_prob"] >= raw - 1e-9

    def test_missing_market_keeps_the_raw_probability(self):
        out = calibrate_side(0.91, None, _fit())
        assert out["calibrated_prob"] == pytest.approx(0.91)
        assert out["method"] == "raw_no_market"

    def test_disabled_flag_returns_the_raw_probability(self, monkeypatch):
        monkeypatch.setenv("PROB_CAL_ENABLED", "0")
        out = calibrate_side(0.91, 0.40)
        assert out["method"] == "disabled"
        assert out["calibrated_prob"] == pytest.approx(0.91)

    def test_clamp_interval_matches_the_helper(self):
        assert clamp_side_probability(0.88, 1.0) == pytest.approx(0.88)
        assert clamp_side_probability(0.88, 0.10) == pytest.approx(0.12)
        assert edge_probability(0.87, 0.99, 0.90) == pytest.approx(0.87)


class TestEdgeFilter:
    def test_humility_still_rejects_before_calibration_can_shrink_the_edge(self):
        skipped = decide_from_yes(
            0.99, "yes", 0.50, 0.50, 0.50,
            min_prob=0.80, min_edge=0.04, max_edge=0.25, weight=0.40,
        )
        assert skipped is not None
        assert skipped["_skip"] == "humility"
        assert skipped["edge"] > 0.25

    def test_empty_history_uses_the_market_and_fails_a_wide_model_edge(self, monkeypatch):
        monkeypatch.setattr("agent.prob_calibration.current_fit", lambda rows=None: _fit())
        # Raw 0.90 clears ask 0.80 + fee + 4¢. The market mid is 0.85, which does not.
        taken = decide_from_yes(
            0.90, "yes", 0.80, 0.90, 0.15,
            min_prob=0.80, min_edge=0.04, max_edge=0.25, weight=0.40,
        )
        assert taken is None
        fee = taker_fee_per_share(0.80)
        assert passes_own_edge(0.90, 0.80, 0.04) is True
        assert passes_own_edge(0.85, 0.80, 0.04) is False
        assert 0.85 - 0.80 - fee < 0.04

    def test_weather_strategy_skips_when_the_calibrated_edge_fails(self, monkeypatch):
        import agent.strategies as strategies
        monkeypatch.setenv("POLYMARKET_VENUE", "us")
        monkeypatch.setenv("WX_ENSEMBLE", "1")
        monkeypatch.setattr("agent.prob_calibration.current_fit", lambda rows=None: _fit())
        proposal = {
            "question": "Highest temperature in Miami — 66 or above",
            "slug": "tc-temp-miahigh-2026-10-12-66orabove",
            "side": "YES",
            "price": 0.80,
            "ask": 0.80,
            "model_prob": 0.90,
            "edge": 0.06,
            "lead": 0,
            "forecast": 72.0,
            "prob_method": "emos",
            "p_yes": 0.90,
            "yes_ask": 0.80,
            "yes_bid": 0.90,
            "no_ask": 0.15,
        }
        monkeypatch.setattr(strategies, "_load_signal", lambda *a, **k: {"proposals": [proposal]})
        assert strategies.scan_weather_us({"bankroll": 100, "max_position_pct": 0.05, "min_shares": 5}) == []

    def test_gimme_edge_filter_uses_the_calibrated_probability(self, monkeypatch):
        import agent.strategies as strategies
        monkeypatch.setenv("POLYMARKET_VENUE", "us")
        monkeypatch.setattr("agent.prob_calibration.current_fit", lambda rows=None: _fit())
        slug = "tc-temp-miahigh-2026-10-20-gte90lt92f"
        book = {
            "venue": "us",
            "slug": slug,
            "side": "YES",
            "question": slug,
            "category": "climate",
            "market_bid": 0.91,
            "market_ask": 0.93,
            "market_mid": 0.92,
            "spread": 0.02,
            "depth_shares": 500,
            "hours_to_expiry": 24,
            "days_to_expiry": 1,
            "price": 0.92,
            "risks": [],
            "neg_risk": False,
            "group_size": 0,
            "group_title": "",
        }

        def fake_load(name, max_age_min=None):
            if "weather" in name:
                return {"rows": [{"slug": slug, "p_yes": 0.99, "prob_method": "emos"}]}
            if "polymarket_us" in name:
                return {"gimme_evaluated": [book]}
            return {}

        monkeypatch.setattr(strategies, "_load_signal", fake_load)
        assert strategies.scan_gimme_bets({"bankroll": 95, "max_position_pct": 0.05, "min_shares": 5}) == []
        reasons = {row["slug"]: row["reject_reason"] for row in strategies.scan_gimme_bets.last_evaluations}
        assert reasons[slug] == "model_edge"
        audit = strategies.scan_gimme_bets.last_evaluations[0]
        # The log still scores the raw model. The edge test saw the market mid.
        assert audit["model_prob"] == pytest.approx(0.99)
        assert audit["calibrated_prob"] == pytest.approx(0.92)
        assert passes_own_edge(0.99, 0.93, 0.03) is True
        assert passes_own_edge(audit["calibrated_prob"], 0.93, 0.03) is False

    def test_partial_weight_keeps_the_bet_and_shrinks_size_above_the_floor(self, monkeypatch):
        import agent.strategies as strategies
        monkeypatch.setenv("POLYMARKET_VENUE", "us")
        # 0.70 of the way to a 0.99 model, from a 0.92 mid, still clears 3¢ after the fee.
        monkeypatch.setattr(
            "agent.prob_calibration.current_fit",
            lambda rows=None: _fit(n=200, model_weight=0.70, prior_n=80),
        )
        slug = "tc-temp-miahigh-2026-10-20-gte90lt92f"
        book = {
            "venue": "us",
            "slug": slug,
            "side": "YES",
            "question": slug,
            "category": "climate",
            "market_bid": 0.91,
            "market_ask": 0.93,
            "market_mid": 0.92,
            "spread": 0.02,
            "depth_shares": 5000,
            "hours_to_expiry": 24,
            "days_to_expiry": 1,
            "price": 0.92,
            "risks": [],
            "neg_risk": False,
            "group_size": 0,
            "group_title": "",
        }

        def fake_load(name, max_age_min=None):
            if "weather" in name:
                return {"rows": [{"slug": slug, "p_yes": 0.99, "prob_method": "emos"}]}
            if "polymarket_us" in name:
                return {"gimme_evaluated": [book]}
            return {}

        monkeypatch.setattr(strategies, "_load_signal", fake_load)
        proposals = strategies.scan_gimme_bets({
            "bankroll": 10000, "max_position_pct": 0.05, "min_shares": 5,
        })
        assert len(proposals) == 1
        row = proposals[0]
        assert row["model_prob"] == pytest.approx(0.99)
        assert row["calibrated_prob"] == pytest.approx(0.70 * 0.99 + 0.30 * 0.92)
        assert row["calibrated_prob"] < row["model_prob"]
        assert passes_own_edge(row["calibrated_prob"], 0.93, 0.03) is True
        # Same book sized off the raw quote, before the confidence scale.
        pay = 0.93
        max_pos = 10000 * 0.05
        raw_return = (1.0 - pay) / pay * 100
        unscaled = min(max_pos, max_pos * (raw_return / 10.0)) / pay
        assert row["size_shares"] < unscaled
        assert row["size_shares"] >= 5
        assert confidence_scale(0.99, row["calibrated_prob"]) < 1.0

    def test_share_floor_is_not_cut_on_a_small_bankroll(self):
        assert resize_shares(5, 5, 0.5) == 5
        assert resize_shares(100, 5, 0.5) == pytest.approx(50)
        assert resize_shares(100, 5, 1.2) == pytest.approx(100)


class TestHistoryAndReplay:
    def test_missing_logs_load_as_an_empty_sample(self, monkeypatch, tmp_path):
        monkeypatch.setattr("agent.proposal_log.PROPOSAL_LOG", tmp_path / "missing.jsonl")
        monkeypatch.setattr("agent.ledger.CACHE", tmp_path / "missing_cache.json")
        assert load_settled_rows() == []
        fit = current_fit()
        assert fit["n"] == 0
        assert fit["model_weight"] == 0.0

    def test_synthetic_overconfident_book_is_skipped_not_resized(self):
        """Illustration, not a count from the server logs (those files are gitignored).

        Sixty settled rows, model stuck at 88%, market at 55%, ask at 80¢.
        With the default prior the weight is ~0, so every row that the raw
        model would have bought fails the calibrated edge test.
        """
        rows = []
        for i in range(60):
            rows.append({
                "model_prob": 0.88,
                "market_prob": 0.55,
                "ask": 0.80,
                "outcome": 1.0 if i < 32 else 0.0,  # about 53% hits
            })
        effect = filter_effect(rows, min_edge=0.04, min_prob=0.80, fit=fit_calibration(rows, prior=80))
        assert effect["considered"] == 60
        assert effect["skipped"] == 60
        assert effect["resized"] == 0
        assert effect["unchanged"] == 0
        assert effect["model_weight"] == 0.0
