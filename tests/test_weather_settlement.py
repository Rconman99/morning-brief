"""LST climate day, inclusive brackets, station rules, and the city skip."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from agent.weather_settlement_check import (
    CITIES,
    assemble_report,
    brackets_tile,
    climate_day,
    climate_window_utc,
    contains_high,
    evaluate_group,
    parse_bracket,
    parse_bracket_slug,
    run_from_inputs,
    skip_reasons,
    station_matches_rules,
    table_bad_codes,
)
from agent.weather_us import STATIONS, parse_bracket as weather_parse_bracket


def _book(city, day, winner, rules, high_titles=None):
    titles = high_titles or ["61 or below", "62 to 63", "64 to 65", "66 or above"]
    rows = []
    for title in titles:
        rows.append({
            "slug": f"tc-temp-{city}high-{day}-" + title.replace(" ", ""),
            "city": city,
            "date": day,
            "title": title,
            "settlement": 1.0 if title == winner else 0.0,
            "rules": rules,
        })
    return rows


KMIA_RULES = (
    "This market resolves on the NWS Daily Climate Report for "
    "Miami International Airport (KMIA). The day is local standard time."
)


class TestLocalStandardTime:
    def test_july_hour_stays_on_the_previous_climate_date(self):
        # 04:30 UTC is 00:30 EDT (already July 15 on the civil clock) and 23:30 EST (still July 14).
        moment = datetime(2026, 7, 15, 4, 30, tzinfo=timezone.utc)
        assert climate_day(moment, -5) == date(2026, 7, 14)
        civil = moment.astimezone(ZoneInfo("America/New_York")).date()
        assert civil == date(2026, 7, 15)
        assert climate_day(moment, -5) != civil

    def test_climate_window_uses_standard_midnight_not_dst(self):
        start, end = climate_window_utc(date(2026, 7, 15), -5)
        assert start == datetime(2026, 7, 15, 5, 0, tzinfo=timezone.utc)
        assert end == datetime(2026, 7, 16, 5, 0, tzinfo=timezone.utc)
        # An observation at 04:30 UTC is outside this climate day.
        assert not (start <= datetime(2026, 7, 15, 4, 30, tzinfo=timezone.utc) < end)

    def test_winter_midnight_matches_standard_time(self):
        assert climate_day(datetime(2026, 1, 15, 4, 59, tzinfo=timezone.utc), -5) == date(2026, 1, 14)
        assert climate_day(datetime(2026, 1, 15, 5, 0, tzinfo=timezone.utc), -5) == date(2026, 1, 15)

    def test_station_offsets_are_standard_time(self):
        assert CITIES["mia"]["lst_offset_hours"] == -5
        assert CITIES["nyc"]["icao"] == "KNYC"
        assert CITIES["nyc"]["lst_offset_hours"] == -5
        assert CITIES["mdw"]["icao"] == "KMDW"
        assert CITIES["mdw"]["lst_offset_hours"] == -6
        assert CITIES["sfo"]["icao"] == "KSFO"
        assert CITIES["lax"]["icao"] == "KLAX"
        assert CITIES["sfo"]["lst_offset_hours"] == -8
        assert CITIES["lax"]["lst_offset_hours"] == -8
        assert table_bad_codes(STATIONS) == set()
        for code, (icao, _lat, _lon, off) in STATIONS.items():
            assert icao == CITIES[code]["icao"]
            assert off == CITIES[code]["lst_offset_hours"]


class TestBracketEdges:
    def test_titles_match_the_weather_model_parser(self):
        for title in ("62 to 63", "61 or below", "70 or above", "not a bracket", ""):
            assert parse_bracket(title) == weather_parse_bracket(title)

    def test_inclusive_whole_degrees(self):
        mid = parse_bracket("62 to 63")
        below = parse_bracket("61 or below")
        above = parse_bracket("70 or above")
        assert contains_high(mid, 62) and contains_high(mid, 63)
        assert not contains_high(mid, 61) and not contains_high(mid, 64)
        assert contains_high(below, 61) and contains_high(below, 40)
        assert not contains_high(below, 62)
        assert contains_high(above, 70) and contains_high(above, 100)
        assert not contains_high(above, 69)

    def test_slug_two_degree_label_is_inclusive(self):
        # gte93lt94 is the "93 to 94" market, not the half-open interval [93, 94).
        bracket = parse_bracket_slug("tc-temp-miahigh-2026-10-11-gte93lt94f")
        assert bracket == (93.0, 94.0)
        assert contains_high(bracket, 93) and contains_high(bracket, 94)
        assert not contains_high(bracket, 92) and not contains_high(bracket, 95)

    def test_full_book_picks_one_bracket(self):
        book = _book("mia", "2026-07-01", "62 to 63", KMIA_RULES)
        ranges = [parse_bracket(m["title"]) for m in book]
        assert brackets_tile(ranges)
        row = evaluate_group(book, 63)
        assert row["match"] is True
        assert row["counted"] is True
        assert row["reason"] == "ok"
        assert row["cli_high"] == 63
        edge = evaluate_group(book, 61)
        # Winner in the fixture is "62 to 63", so a high of 61 is a real miss.
        assert edge["match"] is False
        assert edge["computed_title"] == "61 or below"
        assert edge["reason"] == "bracket"

    def test_incomplete_book_is_not_a_mismatch(self):
        partial = _book("mia", "2026-07-01", "62 to 63", KMIA_RULES, high_titles=["62 to 63", "64 to 65"])
        row = evaluate_group(partial, 63)
        assert row["counted"] is False
        assert row["reason"] == "incomplete"
        report = assemble_report([row], source="live")
        assert report["cities"]["mia"]["n"] == 0
        assert report["skip_cities"] == []


class TestStationRules:
    def test_each_city_matches_its_station_and_rejects_the_neighbor(self):
        assert station_matches_rules(KMIA_RULES, "mia") is True
        assert station_matches_rules("Central Park observatory (KNYC).", "nyc") is True
        assert station_matches_rules("Chicago Midway Airport (KMDW).", "mdw") is True
        assert station_matches_rules("San Francisco International Airport (KSFO).", "sfo") is True
        assert station_matches_rules("Los Angeles International Airport (KLAX).", "lax") is True
        assert station_matches_rules("Settles using LaGuardia (KLGA).", "nyc") is False
        assert station_matches_rules("O'Hare (KORD) daily climate report.", "mdw") is False
        assert station_matches_rules(KMIA_RULES, "nyc") is False

    def test_naming_the_right_station_wins_over_a_rejected_alternative(self):
        text = "Resolves to Central Park (KNYC), not LaGuardia (KLGA) or JFK."
        assert station_matches_rules(text, "nyc") is True

    def test_missing_station_is_unknown_not_a_failure(self):
        assert station_matches_rules("", "mia") is None
        assert station_matches_rules("Resolves on the NWS Daily Climate Report.", "lax") is None

    def test_rules_mismatch_fails_an_otherwise_correct_bracket(self):
        book = _book("nyc", "2026-07-01", "62 to 63", "LaGuardia (KLGA) climate report.")
        row = evaluate_group(book, 63)
        assert row["rules"] == "mismatch"
        assert row["match"] is False
        assert row["counted"] is True
        assert row["reason"] == "rules"


class TestSkipFlag:
    def test_default_skips_only_a_live_backtest_mismatch(self, monkeypatch):
        monkeypatch.delenv("WX_SKIP_MISMATCH_CITIES", raising=False)
        monkeypatch.delenv("WX_SKIP_CITIES", raising=False)
        live = {"source": "live", "skip_cities": ["lax"]}
        assert skip_reasons(report=live, load=False) == {
            "lax": "settlement backtest reported a mismatch",
        }
        synthetic = {"source": "synthetic", "skip_cities": ["lax"]}
        assert skip_reasons(report=synthetic, load=False) == {}
        assert skip_reasons(report=None, load=False) == {}

    def test_flag_off_ignores_the_file_but_explicit_list_still_skips(self, monkeypatch):
        monkeypatch.setenv("WX_SKIP_MISMATCH_CITIES", "0")
        monkeypatch.setenv("WX_SKIP_CITIES", "nyc")
        live = {"source": "live", "skip_cities": ["lax"]}
        reasons = skip_reasons(report=live, load=False)
        assert "lax" not in reasons
        assert reasons["nyc"] == "listed in WX_SKIP_CITIES"

    def test_bad_station_table_always_skips(self, monkeypatch):
        monkeypatch.delenv("WX_SKIP_CITIES", raising=False)
        monkeypatch.delenv("WX_SKIP_MISMATCH_CITIES", raising=False)
        drifted = dict(STATIONS)
        drifted["nyc"] = ("KLGA", 40.77, -73.87, -4)  # wrong station and DST offset
        reasons = skip_reasons(stations=drifted, report=None, load=False)
        assert "nyc" in reasons

    def test_backtest_report_from_inputs_lists_the_bad_city(self):
        good = _book("mia", "2026-07-01", "62 to 63", KMIA_RULES)
        bad = _book("lax", "2026-07-01", "64 to 65", "Los Angeles International Airport (KLAX).")
        highs = {"KMIA": {"2026-07-01": 63}, "KLAX": {"2026-07-01": 63}}
        report = assemble_report(run_from_inputs(good + bad, highs), source="live")
        assert report["cities"]["mia"]["match_rate"] == 1.0
        assert report["cities"]["lax"]["match_rate"] == 0.0
        assert report["skip_cities"] == ["lax"]


class TestBacktestScriptAndPricingSkip:
    def test_network_failure_runs_synthetic_check_without_writing(self, tmp_path, monkeypatch):
        import agent.weather_settlement_backtest as bt
        out = tmp_path / "report.json"

        def boom(*a, **k):
            raise OSError("network down")

        monkeypatch.setattr(bt, "fetch_settled_temperature_markets", boom)
        code = bt.main(["--out", str(out)])
        assert code == 0
        assert not out.exists()

    def test_fixture_file_writes_a_live_style_report(self, tmp_path):
        import agent.weather_settlement_backtest as bt
        payload = {
            "markets": _book("mia", "2026-07-01", "66 or above", KMIA_RULES),
            "highs": {"KMIA": {"2026-07-01": 70}},
        }
        fixture = tmp_path / "fix.json"
        fixture.write_text(json.dumps(payload))
        out = tmp_path / "report.json"
        assert bt.main(["--fixture", str(fixture), "--out", str(out)]) == 0
        report = json.loads(out.read_text())
        assert report["source"] == "fixture"
        assert report["cities"]["mia"]["match_rate"] == 1.0
        assert report["skip_cities"] == []

    def test_price_markets_skips_a_flagged_city_before_the_forecast(self, monkeypatch):
        import agent.weather_us as wx
        monkeypatch.setenv("WX_ENSEMBLE", "0")
        monkeypatch.delenv("WX_SKIP_CITIES", raising=False)
        monkeypatch.delenv("WX_SKIP_MISMATCH_CITIES", raising=False)
        monkeypatch.setattr(
            "agent.weather_settlement_check.load_backtest_report",
            lambda path=None: {"source": "live", "skip_cities": ["lax"]},
        )
        called = []

        def fake_forecast(lat, lon, off):
            called.append((round(lat, 2), round(lon, 2)))
            return {}

        monkeypatch.setattr(wx, "current_forecast", fake_forecast)
        markets = [
            {"slug": "tc-temp-laxhigh-2026-10-12-gte69lt70f", "title": "69 to 70",
             "endDate": "2026-10-13T00:00:00Z"},
            {"slug": "tc-temp-miahigh-2026-10-12-gte90lt91f", "title": "90 to 91",
             "endDate": "2026-10-13T00:00:00Z"},
        ]

        def fake_list(params):
            return markets if params.get("offset", 0) == 0 else []

        monkeypatch.setattr("agent.pm_us.list_markets", fake_list)
        wx.price_markets({"cities": {}})
        lats = [c[0] for c in called]
        assert 25.79 in lats  # KMIA was priced
        assert all(abs(lat - 33.94) > 0.05 for lat, _lon in called)  # KLAX was not

    def test_rules_mismatch_logs_but_does_not_skip_unless_asked(self, monkeypatch):
        import agent.weather_us as wx
        monkeypatch.setenv("WX_ENSEMBLE", "0")
        monkeypatch.delenv("WX_SKIP_ON_RULES_MISMATCH", raising=False)
        monkeypatch.setattr(
            "agent.weather_settlement_check.load_backtest_report",
            lambda path=None: None,
        )
        called = []
        monkeypatch.setattr(wx, "current_forecast", lambda lat, lon, off: called.append(lat) or {})
        markets = [{
            "slug": "tc-temp-nychigh-2026-10-12-gte70lt71f",
            "title": "70 to 71",
            "rules": "Settles at LaGuardia (KLGA).",
            "endDate": "2026-10-13T00:00:00Z",
        }]
        monkeypatch.setattr("agent.pm_us.list_markets", lambda params: markets if params.get("offset", 0) == 0 else [])
        wx.price_markets({"cities": {}})
        assert called  # default: still forecast, only log
        called.clear()
        monkeypatch.setenv("WX_SKIP_ON_RULES_MISMATCH", "1")
        wx.price_markets({"cities": {}})
        assert called == []


class TestRiskCapsUnchanged:
    def test_humility_correlation_and_loss_pause_still_in_place(self):
        import inspect
        import agent.config as config
        import agent.weather_us as wx
        assert "MAX_EDGE" in inspect.getsource(wx.price_markets)
        assert config.MAX_DAILY_LOSS_PCT == 0.05
        assert config.MAX_WEEKLY_LOSS_PCT == 0.10
        assert config.MAKER_ONLY is True
        assert config.MAX_POSITIONS_PER_GROUP == int(os.environ.get("MAX_POSITIONS_PER_GROUP", "1"))
