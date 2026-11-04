"""Champion/challenger gate on the frozen real paper rows.

No network. The gate must not be used to change orders, size, or calibration.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from agent.challenger_gate import (
    champion_probability,
    evaluate,
    load_fixtures,
    main,
)


WEATHER_LOSS = "tc-temp-sfohigh-2026-10-27-gte66lt67f"
GIMME_LOSSES = {
    "cpc-btc-above-day-2026-10-10-84250",
    "cpc-btc-above-day-2026-10-10-85250",
    "cpc-btc-above-day-2026-10-10-81250",
    "cpcxrp-xrp-above-day-2026-10-17-1p25",
    "cpcxrp-xrp-range-hr-2026-10-18-0100z-1p36",
}
LAX_WINS = {
    "tc-temp-laxhigh-2026-10-29-gte74lt75f",
    "tc-temp-laxhigh-2026-11-02-gte75lt76f",
}


def test_frozen_fixtures_are_the_real_misses_and_settled_weather():
    rows = load_fixtures()
    misses = [row for row in rows if row["miss"]]
    weather = [row for row in rows if row["strategy"] == "weather_us"]
    gimme = [row for row in misses if row["strategy"] == "gimme"]
    assert len(rows) == 18
    assert len(misses) == 6
    assert len(gimme) == 5
    assert {row["trade"]["slug"] for row in gimme} == GIMME_LOSSES
    assert all(row["cause"] == "unknown" for row in gimme)
    assert all(row["model_prob"] is None for row in gimme)
    assert len(weather) == 13
    assert sum(row["outcome"] == 1 for row in weather) == 12
    assert sum(row["outcome"] == 0 for row in weather) == 1
    loss = next(row for row in weather if row["miss"])
    assert loss["trade"]["slug"] == WEATHER_LOSS
    assert loss["cause"] == "late_move"
    assert loss["model_prob"] == pytest.approx(0.8823)
    assert loss["market_prob"] == pytest.approx(0.75)
    assert loss["pnl"] == pytest.approx(-4.4256)
    slugs = {row["trade"]["slug"] for row in rows}
    assert "tc-temp-sfohigh-2026-11-03-gte67lt68f" not in slugs
    assert "tc-temp-miahigh-2026-11-04-gte80lt81f" not in slugs
    assert "0x" not in json.dumps(rows)


def test_champion_vs_itself_passes():
    report = evaluate(champion_probability, candidate_name="champion")
    weather = report["strategies"]["weather_us"]
    assert report["verdict"] == "PASS"
    assert report["reasons"] == []
    assert report["n_fixtures"] == 18
    assert report["n_misses"] == 6
    assert report["n_weather"] == 13
    assert report["overall"]["n"] == 13
    assert weather["n"] == 13
    assert weather["outcome_wins"] == 12
    assert weather["outcome_losses"] == 1
    assert weather["hits_champion"] == 12
    assert weather["calls_champion"] == 13
    assert weather["win_rate_candidate"] == pytest.approx(weather["win_rate_champion"])
    assert weather["brier_candidate"] == pytest.approx(weather["brier_champion"])
    assert weather["champion_beats_market"] is True
    assert weather["brier_champion"] == pytest.approx(0.06638871461538462)
    assert weather["brier_market"] == pytest.approx(0.11177692307692308)
    assert report["strategies"]["gimme"]["n"] == 0
    assert all(miss["more_confident_wrong"] is False for miss in report["misses"])
    loss = next(miss for miss in report["misses"] if miss["slug"] == WEATHER_LOSS)
    assert loss["champion_prob"] == pytest.approx(0.8823)
    assert loss["candidate_prob"] == pytest.approx(0.8823)


def test_overconfident_candidate_fails():
    def side_probability(view):
        prob = champion_probability(view)
        if prob is None:
            return None
        return min(1.0, prob + (1.0 - prob))

    report = evaluate(side_probability, candidate_name="overconfident")
    text = "\n".join(report["reasons"])
    weather = report["strategies"]["weather_us"]
    loss = next(miss for miss in report["misses"] if miss["slug"] == WEATHER_LOSS)
    assert report["verdict"] == "FAIL"
    assert "worse than champion" in text
    assert "weather_us Brier regressed" in text
    assert "more confident in the wrong direction" in text
    assert WEATHER_LOSS in text
    assert weather["brier_candidate"] > weather["brier_champion"]
    assert loss["candidate_prob"] == pytest.approx(1.0)
    assert loss["more_confident_wrong"] is True
    assert weather["win_rate_candidate"] == pytest.approx(weather["win_rate_champion"])


def test_weather_regression_fails():
    """Win rate can fall while Brier improves. That still fails.

    Calling the San Francisco loss correctly helps Brier. Pushing two settled
    LAX wins under 0.5 costs less Brier than that help, and the weather hit
    rate drops from 12/13 to 11/13.
    """

    def side_probability(view):
        slug = (view.get("trade") or {}).get("slug")
        if slug == WEATHER_LOSS:
            return 0.0
        if slug in LAX_WINS:
            return 0.49
        return champion_probability(view)

    report = evaluate(side_probability, candidate_name="weather_regression")
    text = "\n".join(report["reasons"])
    weather = report["strategies"]["weather_us"]
    loss = next(miss for miss in report["misses"] if miss["slug"] == WEATHER_LOSS)
    assert report["verdict"] == "FAIL"
    assert "weather_us win rate regressed from 12/13 to 11/13" in text
    assert weather["brier_candidate"] < weather["brier_champion"]
    assert "worse than champion" not in text
    assert loss["more_confident_wrong"] is False
    assert weather["hits_candidate"] == 11
    assert weather["calls_candidate"] == 13


def test_worse_than_market_fails_when_champion_already_beats_it():
    rows = [
        {
            "strategy": "alpha",
            "side": "yes",
            "outcome": 1.0,
            "miss": False,
            "model_prob": 0.90,
            "market_prob": 0.70,
            "trade": {"slug": "alpha-win", "token_hint": "yes"},
        },
        {
            "strategy": "beta",
            "side": "yes",
            "outcome": 1.0,
            "miss": False,
            "model_prob": 0.10,
            "market_prob": 0.20,
            "trade": {"slug": "beta-1", "token_hint": "yes"},
        },
        {
            "strategy": "beta",
            "side": "yes",
            "outcome": 1.0,
            "miss": False,
            "model_prob": 0.10,
            "market_prob": 0.20,
            "trade": {"slug": "beta-2", "token_hint": "yes"},
        },
    ]

    def side_probability(view):
        if view["strategy"] == "alpha":
            return 0.40
        return 1.0

    report = evaluate(side_probability, fixtures=rows, candidate_name="market_slip")
    text = "\n".join(report["reasons"])
    assert report["verdict"] == "FAIL"
    assert "alpha:" in text
    assert "worse than the market benchmark" in text
    assert report["overall"]["brier_candidate"] < report["overall"]["brier_champion"]
    assert "worse than champion" not in text


def test_cli_champion_passes_and_config_fails(tmp_path, capsys):
    assert main(["--candidate", "champion"]) == 0
    passed = capsys.readouterr().out
    assert "Challenger gate: PASS" in passed
    assert "weather_us:" in passed
    assert "does not place orders" in passed

    config = tmp_path / "overconfident.json"
    config.write_text(json.dumps({
        "name": "overconfident",
        "mode": "toward",
        "target": 1,
        "fraction": 1,
    }), encoding="utf-8")
    assert main(["--candidate", str(config)]) == 1
    failed = capsys.readouterr().out
    assert "Challenger gate: FAIL" in failed
    assert WEATHER_LOSS in failed

    assert main(["--candidate", "agent.challenger_gate:champion_probability"]) == 0
