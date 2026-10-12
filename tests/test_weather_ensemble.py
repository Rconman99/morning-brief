"""Ensemble daily highs, EMOS, observed-high floor, and proposal-log fields.

HTTP is mocked. Nothing here calls Open-Meteo or api.weather.gov.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import math
import random
from datetime import datetime, timedelta, timezone

import pytest
import requests

from agent.weather_ensemble import (
    apply_observed_floor,
    blend_log_odds,
    build_bucket_distribution,
    crps_gaussian,
    decide_from_yes,
    ensemble_enabled,
    fetch_ensemble_summary,
    fit_emos,
    gaussian_bracket_probs,
    member_daily_highs,
    parse_member_series,
    reapply_weather_gate,
)
from agent.proposal_log import log_cycle, score_proposal_records


BRACKETS = [(-1e6, 61.0), (62.0, 63.0), (64.0, 65.0), (66.0, 1e6)]


def _hours(start: datetime, n: int) -> list[str]:
    return [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(n)]


class TestClimateDayHighs:
    def test_july_utc_hour_stays_on_the_previous_standard_day(self):
        # 04:00 UTC on 15 Jul is 00:00 EDT and 23:00 EST on 14 Jul.
        stray = "2026-07-15T04:00"
        start = datetime(2026, 7, 15, 5, 0, tzinfo=timezone.utc)
        times = [stray] + _hours(start, 20)
        hot = [120.0] + [70.0] * 20
        cool = [50.0] + [68.0] * 20
        highs = member_daily_highs(times, [hot, cool], offset_h=-5, min_hours=18)
        assert highs["2026-07-15"] == [70.0, 68.0]
        # One stray hour is not a daily high, so the 14th is absent.
        assert "2026-07-14" not in highs
        tagged = member_daily_highs(times, [hot, cool], offset_h=-5, min_hours=1)
        assert tagged["2026-07-14"] == [120.0, 50.0]
        assert 120.0 not in tagged["2026-07-15"]

    def test_multi_model_member_names_are_separate_series(self):
        times = _hours(datetime(2026, 1, 15, 5, 0, tzinfo=timezone.utc), 20)
        hourly = {"time": times}
        hourly["temperature_2m_ncep_gefs_seamless"] = [60.0] * 20
        hourly["temperature_2m_member01_ncep_gefs_seamless"] = [61.0] * 20
        parsed_times, series = parse_member_series(hourly)
        assert parsed_times == times
        assert len(series) == 2
        highs = member_daily_highs(parsed_times, series, -5)
        assert highs["2026-01-15"] == [60.0, 61.0]


class TestBracketProbabilities:
    def test_integer_bracket_contains_its_edges_and_tiles(self):
        probs = gaussian_bracket_probs(63.0, 0.4, BRACKETS)
        assert sum(probs) == pytest.approx(1.0, abs=1e-6)
        assert probs[1] > 0.8  # 62 to 63, both ends inclusive
        assert probs[0] < 0.01  # 61 or below stops at 61
        assert probs[1] > probs[2]  # 64 to 65 is only the right-hand tail

    def test_open_tail_takes_the_rest_of_a_hot_forecast(self):
        probs = gaussian_bracket_probs(80.0, 1.0, BRACKETS)
        assert probs[-1] == pytest.approx(1.0, abs=1e-3)
        assert probs[0] == pytest.approx(0.0, abs=1e-6)


class TestEmos:
    def test_crps_at_the_mean_matches_the_closed_form(self):
        # σ (2φ(0) - 1/√π) ≈ 0.2337
        assert crps_gaussian(0.0, 0.0, 1.0) == pytest.approx(0.233695, abs=1e-4)
        assert crps_gaussian(0.0, 3.0, 1.0) > crps_gaussian(0.0, 0.0, 1.0)

    def test_fit_recovers_bias_and_refuses_a_short_window(self):
        rng = random.Random(7)
        residuals = [2.0 + rng.gauss(0, 1.5) for _ in range(60)]
        fit = fit_emos(residuals, min_n=40)
        assert fit is not None
        mean = sum(residuals) / len(residuals)
        assert fit["bias"] == pytest.approx(mean, abs=0.35)
        assert fit["n"] == 60
        assert fit["spread"] > 0.5
        assert fit_emos(residuals[:12], min_n=40) is None
        assert fit_emos([], min_n=40) is None

    def test_short_history_keeps_the_raw_ensemble(self):
        stats = {"mean": 70.0, "spread": 2.0, "n_members": 31, "n_models": 1, "models": ["gfs_seamless"]}
        raw = build_bucket_distribution(stats, BRACKETS, residuals=[1.0] * 10, lead=1,
                                        observed_f=None, sigma_floor=1.8)
        assert raw["prob_method"] == "ensemble_raw"
        assert raw["prob_fallback"] == "short_history"
        assert raw["emos_bias"] is None
        assert sum(raw["probs"]) == pytest.approx(1.0)
        fitted = build_bucket_distribution(stats, BRACKETS, residuals=[2.0] * 50, lead=1,
                                           observed_f=None, sigma_floor=1.8)
        assert fitted["prob_method"] == "emos"
        assert fitted["emos_bias"] == pytest.approx(2.0, abs=0.26)
        assert fitted["calibrated_mean"] == pytest.approx(72.0, abs=0.3)
        assert fitted["prob_fallback"] is None

    def test_garbage_payload_returns_none(self):
        assert build_bucket_distribution({"mean": "nope"}, BRACKETS, None, 1, None, 1.2) is None
        assert build_bucket_distribution(None, BRACKETS, None, 1, None, 1.2) is None


class TestObservedFloor:
    def test_zeros_brackets_entirely_below_the_reading_and_renormalizes(self):
        probs = [0.25, 0.25, 0.25, 0.25]
        out, applied = apply_observed_floor(probs, BRACKETS, observed_f=70.0, margin_f=1.0)
        assert applied is True
        assert out == pytest.approx([0.0, 0.0, 0.0, 1.0])

    def test_keeps_a_bracket_that_still_contains_the_high(self):
        out, applied = apply_observed_floor([0.2, 0.3, 0.3, 0.2], BRACKETS, observed_f=63.0, margin_f=0.0)
        assert applied is True
        assert out[1] > 0  # 62 to 63 includes 63
        assert out[0] == 0.0  # 61 or below is entirely below 63
        assert sum(out) == pytest.approx(1.0)

    def test_one_degree_margin_keeps_the_neighbor_bucket(self):
        # 64°F observed, 1°F margin → cutoff 63. "62 to 63" is not entirely below.
        kept, applied = apply_observed_floor([0.1, 0.2, 0.3, 0.4], BRACKETS, 64.0, margin_f=1.0)
        assert applied is True
        assert kept[1] > 0
        strict, _ = apply_observed_floor([0.1, 0.2, 0.3, 0.4], BRACKETS, 64.0, margin_f=0.0)
        assert strict[1] == 0.0

    def test_does_not_wipe_the_distribution_when_every_bracket_loses(self):
        original = [0.4, 0.6]
        out, applied = apply_observed_floor(original, [(60.0, 61.0), (62.0, 63.0)], 90.0, margin_f=0.0)
        assert applied is False
        assert out == original

    def test_no_observation_leaves_probabilities_alone(self):
        probs = [0.2, 0.8]
        out, applied = apply_observed_floor(probs, [(60.0, 61.0), (62.0, 1e6)], None)
        assert applied is False
        assert out == probs


class TestBlendAndEdge:
    def test_log_odds_blend_sits_between_model_and_market(self):
        blended = blend_log_odds(0.9, 0.6, 0.40)
        assert 0.6 < blended < 0.9
        assert blended == pytest.approx(0.754, abs=0.01)
        assert blend_log_odds(0.9, None, 0.40) == pytest.approx(0.9)
        assert blend_log_odds(0.9, 0.6, 1.0) == pytest.approx(0.9)

    def test_humility_cap_still_rejects_a_wide_edge(self):
        # Model 99% blended toward a 50¢ market is still far above a 50¢ ask.
        skipped = decide_from_yes(
            0.99, "yes", 0.50, 0.50, 0.50,
            min_prob=0.80, min_edge=0.04, max_edge=0.25, weight=0.40,
        )
        assert skipped is not None
        assert skipped["_skip"] == "humility"
        assert skipped["edge"] > 0.25

    def test_accepts_a_modest_edge_and_scores_the_unblended_prob(self):
        taken = decide_from_yes(
            0.90, "yes", 0.80, 0.90, 0.15,
            min_prob=0.80, min_edge=0.04, max_edge=0.25, weight=0.40,
        )
        assert taken is not None
        assert "_skip" not in taken
        assert taken["model_prob"] == pytest.approx(0.90)
        assert taken["trade_prob"] < 0.90  # pulled toward the 0.85 mid
        assert taken["trade_prob"] > 0.80
        assert taken["edge"] >= 0.04
        assert taken["edge"] <= 0.25
        assert taken["price"] <= 0.80

    def test_flag_off_and_unknown_values(self, monkeypatch):
        monkeypatch.setenv("WX_ENSEMBLE", "0")
        assert ensemble_enabled() is False
        monkeypatch.setenv("WX_ENSEMBLE", "off")
        assert ensemble_enabled() is False
        monkeypatch.setenv("WX_ENSEMBLE", "1")
        assert ensemble_enabled() is True
        legacy = {"side": "YES", "model_prob": 0.9, "price": 0.8, "edge": 0.05}
        assert reapply_weather_gate(legacy) is legacy
        monkeypatch.setenv("WX_ENSEMBLE", "0")
        assert reapply_weather_gate({"prob_method": "emos", "p_yes": 0.9, "side": "YES"})["p_yes"] == 0.9


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


class TestEnsembleHttp:
    def test_connection_error_returns_none_and_sends_a_user_agent(self, monkeypatch):
        monkeypatch.setattr("agent.weather_ensemble.time.sleep", lambda *a, **k: None)
        seen = []

        def boom(url, params=None, headers=None, timeout=None):
            seen.append(headers)
            raise requests.ConnectionError("down")

        assert fetch_ensemble_summary(25.79, -80.32, -5, get=boom) is None
        assert seen
        assert "User-Agent" in seen[0]
        assert "weather" in seen[0]["User-Agent"].lower()

    def test_one_model_500_does_not_drop_the_other(self, monkeypatch):
        monkeypatch.setattr("agent.weather_ensemble.time.sleep", lambda *a, **k: None)
        times = _hours(datetime(2026, 1, 15, 5, 0, tzinfo=timezone.utc), 20)
        hourly = {"time": times, "temperature_2m": [70.0] * 20}
        for i in range(1, 10):
            hourly[f"temperature_2m_member{i:02d}"] = [70.0 + i * 0.1] * 20

        def fake_get(url, params=None, headers=None, timeout=None):
            assert "ensemble-api.open-meteo.com" in url
            assert params["temperature_unit"] == "fahrenheit"
            assert params["timezone"] == "GMT"
            if params["models"] == "gfs_seamless":
                return _Resp(200, {"hourly": hourly})
            return _Resp(500, {"error": True})

        summary = fetch_ensemble_summary(25.79, -80.32, -5, get=fake_get)
        assert summary is not None
        day = summary["2026-01-15"]
        assert day["n_models"] == 1
        assert day["models"] == ["gfs_seamless"]
        assert day["n_members"] >= 8
        assert day["mean"] == pytest.approx(70.4, abs=0.3)

    def test_nws_observation_uses_celsius_and_the_climate_window(self, monkeypatch):
        import agent.weather_us as wx
        seen = []

        def fake_get(url, params=None, headers=None, timeout=None):
            seen.append((url, headers))
            assert url.startswith("https://api.weather.gov/stations/KMIA/observations")
            return _Resp(200, {"features": [
                {"properties": {
                    "timestamp": "2026-01-15T18:00:00Z",
                    "temperature": {"value": 20.0, "unitCode": "wmoUnit:degC"},
                    "maxTemperatureLast24Hours": {"value": 40.0, "unitCode": "wmoUnit:degC"},
                }},
                {"properties": {
                    "timestamp": "2026-01-15T15:00:00Z",
                    "temperature": {"value": 10.0, "unitCode": "wmoUnit:degC"},
                }},
                {"properties": {
                    # Previous climate day (13:00 EST on the 14th). Must not raise the floor.
                    "timestamp": "2026-01-14T18:00:00Z",
                    "temperature": {"value": 35.0, "unitCode": "wmoUnit:degC"},
                }},
                {"properties": {
                    "timestamp": "2026-01-15T21:00:00Z",
                    "temperature": {"value": 70.0, "unitCode": "wmoUnit:degF"},
                }},
            ]})

        monkeypatch.setattr(wx.requests, "get", fake_get)
        high = wx.observed_max_so_far("KMIA", -5, "2026-01-15")
        # 20°C = 68°F, 10°C = 50°F, explicit 70°F. The 35°C reading is yesterday.
        assert high == pytest.approx(70.0)
        assert "User-Agent" in seen[0][1]
        assert seen[0][1]["User-Agent"]


def _tiled_markets(day: str):
    titles = ["61 or below", "62 to 63", "64 to 65", "66 or above"]
    rows = []
    for title in titles:
        rows.append({
            "slug": f"tc-temp-miahigh-{day}-" + title.replace(" ", ""),
            "title": title,
            "question": "Highest temperature in Miami",
            "endDate": day + "T00:00:00Z",
            "marketSides": [
                {"long": True, "price": {"value": "0.40", "currency": "USD"}},
                {"long": False, "price": {"value": "0.60", "currency": "USD"}},
            ],
        })
    return rows


class TestPriceMarketsUsesEnsemble:
    def _common(self, monkeypatch):
        import agent.weather_us as wx
        monkeypatch.setenv("WX_ENSEMBLE", "1")
        monkeypatch.setenv("WX_MODEL_WEIGHT", "0.40")
        monkeypatch.setenv("WX_EMOS_MIN_DAYS", "40")
        monkeypatch.setenv("WX_OBS_FLOOR_MARGIN", "1")
        monkeypatch.delenv("WX_SKIP_CITIES", raising=False)
        monkeypatch.setattr(
            "agent.weather_settlement_check.load_backtest_report",
            lambda path=None: None,
        )
        day = datetime.now(timezone(timedelta(hours=-5))).date().isoformat()
        markets = _tiled_markets(day)

        def fake_list(params):
            return markets if params.get("offset", 0) == 0 else []

        monkeypatch.setattr("agent.pm_us.list_markets", fake_list)
        monkeypatch.setattr("agent.pm_us.get_bbo", lambda slug: {
            "state": "MARKET_STATE_OPEN",
            "yes_ask": 0.40, "yes_bid": 0.38, "no_ask": 0.62,
            "ask_shares": 20, "bid_shares": 20,
        })
        return wx, day

    def test_emos_floor_and_no_crash_when_the_blend_is_not_needed(self, monkeypatch):
        wx, day = self._common(monkeypatch)

        def fail_blend(*a, **k):
            raise AssertionError("deterministic blend should not run when the ensemble priced the day")

        monkeypatch.setattr(wx, "current_forecast", fail_blend)
        monkeypatch.setattr(wx, "fetch_ensemble_summary", lambda lat, lon, off: {
            day: {"mean": 64.0, "spread": 1.5, "n_members": 82, "n_models": 2,
                  "models": ["gfs_seamless", "ecmwf_ifs025"]},
        })
        monkeypatch.setattr(wx, "observed_max_so_far", lambda *a, **k: 70.0)
        cal = {"cities": {"mia": {"1": {"residuals": [2.0] * 50, "bias": 2.0, "sd": 0.0, "n": 50}}}}
        data = wx.price_markets(cal)
        by_title = {r["bracket"]: r for r in data["rows"]}
        assert by_title["61 or below"]["p_yes"] == 0.0
        assert by_title["62 to 63"]["p_yes"] == 0.0
        assert by_title["64 to 65"]["p_yes"] == 0.0
        assert by_title["66 or above"]["p_yes"] == pytest.approx(1.0)
        top = by_title["66 or above"]
        assert top["prob_method"] == "emos"
        assert top["obs_floor_applied"] is True
        assert top["emos_bias"] == pytest.approx(2.0, abs=0.26)
        assert top["ensemble_mean"] == pytest.approx(64.0)
        assert top["n_members"] == 82
        assert "gfs_seamless" in top["ensemble_models"]

    def test_http_failure_falls_back_to_the_residual_kernel(self, monkeypatch):
        wx, day = self._common(monkeypatch)

        def boom(*a, **k):
            raise requests.ConnectionError("ensemble down")

        monkeypatch.setattr(wx, "fetch_ensemble_summary", boom)
        monkeypatch.setattr(wx, "current_forecast", lambda lat, lon, off: {day: (70.0, 1.5, 71.0)})
        monkeypatch.setattr(wx, "observed_max_so_far", lambda *a, **k: None)
        cal = {"cities": {"mia": {"1": {"residuals": [0.5] * 20, "bias": 0.5, "sd": 1.5, "n": 20}}}}
        data = wx.price_markets(cal)
        assert data["rows"]
        assert {r["prob_method"] for r in data["rows"]} == {"residual_kernel"}
        assert {r["prob_fallback"] for r in data["rows"]} == {"ensemble_unavailable"}

    def test_flag_off_does_not_call_the_ensemble(self, monkeypatch):
        wx, day = self._common(monkeypatch)
        monkeypatch.setenv("WX_ENSEMBLE", "0")

        def boom(*a, **k):
            raise AssertionError("ensemble fetch while WX_ENSEMBLE=0")

        monkeypatch.setattr(wx, "fetch_ensemble_summary", boom)
        called = []
        monkeypatch.setattr(wx, "current_forecast", lambda lat, lon, off: called.append(lat) or {})
        wx.price_markets({"cities": {}})
        assert called  # residual path still requests the deterministic blend


class TestStrategyAndLog:
    def test_strategy_keeps_the_raw_probability_for_scoring(self, monkeypatch):
        import agent.strategies as strategies
        monkeypatch.setenv("POLYMARKET_VENUE", "us")
        monkeypatch.setenv("WX_ENSEMBLE", "1")
        monkeypatch.setenv("WX_MODEL_WEIGHT", "0.40")
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
            "prob_fallback": None,
            "p_yes": 0.90,
            "yes_ask": 0.80,
            "yes_bid": 0.90,
            "no_ask": 0.15,
            "ensemble_mean": 70.0,
            "ensemble_spread": 1.6,
            "emos_bias": 1.5,
            "emos_spread": 1.9,
            "obs_floor": 68.0,
            "obs_floor_applied": True,
            "model_weight": 0.40,
        }
        monkeypatch.setattr(strategies, "_load_signal", lambda *a, **k: {"proposals": [proposal]})
        out = strategies.scan_weather_us({"bankroll": 100, "max_position_pct": 0.05, "min_shares": 5})
        assert len(out) == 1
        assert out[0]["model_prob"] == pytest.approx(0.90)
        assert out[0]["trade_prob"] < 0.90
        assert out[0]["prob_method"] == "emos"
        assert out[0]["ensemble_mean"] == pytest.approx(70.0)
        assert out[0]["obs_floor_applied"] is True
        assert "emos" in out[0]["reason"]

    def test_proposal_log_records_method_and_scores_the_model(self, tmp_path):
        slug = "tc-temp-miahigh-2026-10-12-66orabove"
        prop = {
            "strategy": "weather_us",
            "slug": slug,
            "side": "BUY",
            "token_hint": "yes",
            "model_prob": 0.86,
            "trade_prob": 0.80,
            "market_ask": 0.70,
            "market_bid": 0.66,
            "edge_pct": 6.0,
            "prob_method": "emos",
            "prob_fallback": "short_history",
            "ensemble_mean": 81.2,
            "ensemble_spread": 2.1,
            "emos_bias": 1.4,
            "emos_spread": 1.9,
            "obs_floor": 79.0,
            "obs_floor_applied": True,
            "risk_check": {"approved": False, "reason": "paper"},
        }
        path = tmp_path / "proposals.jsonl"
        now = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)
        log_cycle([prop], quotes=[], now=now, path=path)
        row = json.loads(path.read_text().splitlines()[0])
        assert row["model_prob"] == pytest.approx(0.86)
        assert row["trade_prob"] == pytest.approx(0.80)
        assert row["prob_method"] == "emos"
        assert row["prob_fallback"] == "short_history"
        assert row["ensemble_mean"] == pytest.approx(81.2)
        assert row["ensemble_spread"] == pytest.approx(2.1)
        assert row["emos_bias"] == pytest.approx(1.4)
        assert row["obs_floor"] == pytest.approx(79.0)
        assert row["obs_floor_applied"] is True
        scored = score_proposal_records([row], {slug: 1.0})
        assert scored[0]["model_prob"] == pytest.approx(0.86)
        assert scored[0]["prob_method"] == "emos"
        assert scored[0]["brier_model"] == pytest.approx((0.86 - 1) ** 2)
        assert math.isfinite(scored[0]["logloss_model"])
