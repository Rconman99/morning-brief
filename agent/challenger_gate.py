"""Champion/challenger gate for paper-bot probabilities. Eval only.

Replays the frozen rows in ``tests/fixtures/real_misses/`` through the current
probability path and one candidate. It does not place orders, resize, change a
gate, the loss pause, paper/live, or calibration.

The champion path is the probability the paper bot scores today:

- ``weather_us``: the recorded raw side probability (the unblended number the
  proposal log scores). The morning forecast is not in the fixture, so this
  does not refetch EMOS or the ensemble.
- ``gimme``: ``agent.gimme_rules.model_prob_for``. Crypto slugs have no model,
  so the five frozen gimme losses stay unscored until a model is registered.

The candidate is a module (``pkg.mod:side_probability``) or a JSON config.
Neither function is given the outcome. Brier uses the side that was bought.

FAIL when any of these is true:

1. Candidate Brier on the fixed scored set is worse than the champion.
2. On a strategy where the champion already beats the market, the candidate
   is worse than that market benchmark.
3. ``weather_us`` Brier or win rate regresses at all. Win rate is how often
   the probability is on the correct side of 0.5. Under the champion that is
   the frozen 12W/13, because every weather probability is above 0.5.
4. Any frozen real miss gets more confident in the wrong direction. A higher
   probability on the side that lost is more confident. A miss the champion
   does not score (no model) is more confident when the candidate says that
   side is likelier than 0.5.

    .venv/bin/python agent/challenger_gate.py --candidate champion
    .venv/bin/python agent/challenger_gate.py --candidate pkg.mod:side_probability
    .venv/bin/python agent/challenger_gate.py --candidate candidate.json
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.gimme_rules import model_prob_for
from agent.loss_review import canonical_strategy
from agent.proposal_log import brier_score

FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "real_misses" / "fixtures.json"
EPS = 1e-12
_STRATEGY_ORDER = ("weather_us", "gimme", "stage")


def _prob(raw):
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if value != value or not 0.0 <= value <= 1.0:
        return None
    return value


def replay_inputs(fixture: dict) -> dict:
    """What a probability path is allowed to see. No outcome and no P&L."""
    trade = fixture.get("trade") if isinstance(fixture.get("trade"), dict) else {}
    return {
        "strategy": canonical_strategy(fixture.get("strategy") or trade.get("strategy")),
        "side": fixture.get("side") or trade.get("token_hint") or trade.get("side"),
        "model_prob": fixture.get("model_prob"),
        "market_prob": fixture.get("market_prob"),
        "model_prob_source": fixture.get("model_prob_source"),
        "market_prob_source": fixture.get("market_prob_source"),
        "trade": dict(trade),
    }


def champion_probability(fixture: dict) -> float | None:
    """Current scored probability for one frozen row.

    Weather returns the recorded raw side probability. Gimme asks
    ``model_prob_for`` and returns None when that slug has no model.
    """
    strategy = canonical_strategy(fixture.get("strategy"))
    trade = fixture.get("trade") if isinstance(fixture.get("trade"), dict) else {}
    if strategy == "gimme":
        slug = str(trade.get("slug") or fixture.get("slug") or "")
        side = str(fixture.get("side") or trade.get("token_hint") or "")
        p_yes = fixture.get("p_yes", trade.get("p_yes"))
        prob, _reason = model_prob_for(slug, side, p_yes)
        return _prob(prob)
    return _prob(fixture.get("model_prob"))


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def candidate_from_config(config: dict):
    """Build a candidate from a small JSON config. Identity is the champion."""
    if not isinstance(config, dict):
        raise ValueError("candidate config must be a JSON object")
    mode = str(config.get("mode") or "identity").strip().lower()
    name = str(config.get("name") or mode)
    selected = config.get("strategies")
    if selected is not None:
        selected = {canonical_strategy(item) for item in selected}
    target = float(config.get("target", 1.0))
    fraction = float(config.get("fraction", 1.0))
    factor = float(config.get("factor", 1.0))
    on_missing = config.get("on_missing")
    constant = config.get("prob")

    def side_probability(fixture: dict):
        base = champion_probability(fixture)
        strategy = canonical_strategy(fixture.get("strategy"))
        if selected is not None and strategy not in selected:
            return base
        if mode == "identity":
            return base
        if mode == "toward":
            if base is None:
                return None if on_missing is None else _clamp(float(on_missing))
            return _clamp(base + fraction * (target - base))
        if mode == "scale":
            if base is None:
                return None
            return _clamp(base * factor)
        if mode == "constant":
            if constant is None:
                raise ValueError("constant mode requires prob")
            return _clamp(float(constant))
        raise ValueError(f"unknown candidate mode {mode!r}")

    side_probability.__name__ = name
    return side_probability, name


def load_candidate(spec: str):
    """Resolve ``--candidate`` to ``(callable, name)``.

    ``champion`` and ``identity`` replay the current path. A ``.json`` file is
    a config. Anything else is ``module`` or ``module:function`` exporting
    ``side_probability``.
    """
    text = str(spec or "").strip()
    if not text:
        raise ValueError("candidate is required")
    if text in {"champion", "identity"}:
        return champion_probability, "champion"
    path = Path(text)
    if not path.is_file():
        rooted = PROJECT_ROOT / text
        if rooted.is_file():
            path = rooted
    if path.is_file() and path.suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return candidate_from_config(payload)
    module_name, sep, attr = text.partition(":")
    if not sep:
        attr = "side_probability"
    module = importlib.import_module(module_name)
    fn = getattr(module, attr, None)
    if not callable(fn):
        raise ValueError(f"{text} has no callable {attr}")
    return fn, text


def load_fixtures(path: Path | None = None) -> list[dict]:
    payload = json.loads(Path(path or FIXTURES).read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("fixtures must be a list or an object with rows")
    return rows


def _call_prob(fn, view: dict, label: str) -> float | None:
    value = fn(view)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} probability must be a number or null, got {value!r}")
    number = float(value)
    if number != number or not 0.0 <= number <= 1.0:
        raise ValueError(f"{label} probability {number} is outside [0, 1]")
    return number


def _mean(pairs: list[tuple[float, float]]) -> float | None:
    if not pairs:
        return None
    return sum(brier_score(prob, outcome) for prob, outcome in pairs) / len(pairs)


def _hit(prob: float, outcome: float) -> bool:
    return (prob >= 0.5) == (outcome >= 0.5)


def _rate(hits: int, n: int) -> float | None:
    if not n:
        return None
    return hits / n


def _slug(fixture: dict) -> str:
    trade = fixture.get("trade") if isinstance(fixture.get("trade"), dict) else {}
    return str(trade.get("slug") or fixture.get("slug") or "")


def _ordered(names: list[str]) -> list[str]:
    first = [name for name in _STRATEGY_ORDER if name in names]
    rest = sorted(name for name in names if name not in first)
    return first + rest


def _fmt_prob(value) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def _fmt_brier(value) -> str:
    if value is None:
        return "n/a"
    return f"{value:.6f}"


def _fmt_rate(hits: int, n: int) -> str:
    if not n:
        return "n/a"
    return f"{hits}/{n}"


def evaluate(candidate, fixtures: list[dict] | None = None, candidate_name: str = "candidate") -> dict:
    """Replay fixtures. ``candidate`` is ``side_probability(view) -> float | None``."""
    rows = load_fixtures() if fixtures is None else list(fixtures)
    replayed = []
    for fixture in rows:
        view = replay_inputs(fixture)
        champion = _call_prob(champion_probability, view, "champion")
        proposed = _call_prob(candidate, view, candidate_name)
        outcome = fixture.get("outcome")
        outcome_f = None if outcome is None else float(outcome)
        market = _prob(fixture.get("market_prob"))
        replayed.append({
            "strategy": view["strategy"],
            "slug": _slug(fixture),
            "side": str(view.get("side") or ""),
            "outcome": outcome_f,
            "miss": bool(fixture.get("miss")),
            "cause": fixture.get("cause"),
            "pnl": fixture.get("pnl"),
            "market_prob": market,
            "champion_prob": champion,
            "candidate_prob": proposed,
        })

    def brier_rows(group: list[dict]) -> list[dict]:
        return [
            row for row in group
            if row["champion_prob"] is not None
            and row["candidate_prob"] is not None
            and row["market_prob"] is not None
            and row["outcome"] is not None
        ]

    def block(group: list[dict]) -> dict:
        scored = brier_rows(group)
        champion_pairs = [(row["champion_prob"], row["outcome"]) for row in scored]
        candidate_pairs = [(row["candidate_prob"], row["outcome"]) for row in scored]
        market_pairs = [(row["market_prob"], row["outcome"]) for row in scored]
        champion_brier = _mean(champion_pairs)
        candidate_brier = _mean(candidate_pairs)
        market_brier = _mean(market_pairs)
        champ_calls = [row for row in group if row["champion_prob"] is not None and row["outcome"] is not None]
        cand_calls = [row for row in group if row["candidate_prob"] is not None and row["outcome"] is not None]
        champ_hits = sum(1 for row in champ_calls if _hit(row["champion_prob"], row["outcome"]))
        cand_hits = sum(1 for row in cand_calls if _hit(row["candidate_prob"], row["outcome"]))
        wins = sum(1 for row in group if row["outcome"] is not None and row["outcome"] >= 0.5)
        losses = sum(1 for row in group if row["outcome"] is not None and row["outcome"] < 0.5)
        return {
            "n": len(scored),
            "rows": len(group),
            "brier_champion": champion_brier,
            "brier_candidate": candidate_brier,
            "brier_market": market_brier,
            "champion_beats_market": (
                None if champion_brier is None or market_brier is None else champion_brier < market_brier
            ),
            "candidate_beats_market": (
                None if candidate_brier is None or market_brier is None else candidate_brier < market_brier
            ),
            "outcome_wins": wins,
            "outcome_losses": losses,
            "hits_champion": champ_hits,
            "calls_champion": len(champ_calls),
            "hits_candidate": cand_hits,
            "calls_candidate": len(cand_calls),
            "win_rate_champion": _rate(champ_hits, len(champ_calls)),
            "win_rate_candidate": _rate(cand_hits, len(cand_calls)),
        }

    by_name: dict[str, list] = {}
    for row in replayed:
        by_name.setdefault(row["strategy"], []).append(row)
    strategies = {name: block(by_name[name]) for name in _ordered(list(by_name))}
    overall = block(replayed)

    reasons = []
    for row in replayed:
        if row["champion_prob"] is not None and row["candidate_prob"] is None:
            reasons.append(
                f"{row['strategy']} {row['slug']}: candidate returned no probability "
                "for a row the champion scored"
            )
    if overall["brier_champion"] is not None and overall["brier_candidate"] is not None:
        if overall["brier_candidate"] > overall["brier_champion"] + EPS:
            reasons.append(
                "candidate Brier "
                f"{_fmt_brier(overall['brier_candidate'])} is worse than champion "
                f"{_fmt_brier(overall['brier_champion'])} on the fixed set"
            )
    for name, info in strategies.items():
        if info["champion_beats_market"] and info["brier_candidate"] is not None and info["brier_market"] is not None:
            if info["brier_candidate"] > info["brier_market"] + EPS:
                reasons.append(
                    f"{name}: candidate Brier {_fmt_brier(info['brier_candidate'])} is worse than "
                    f"the market benchmark {_fmt_brier(info['brier_market'])}, and the champion "
                    "already beats that benchmark"
                )
    weather = strategies.get("weather_us")
    if weather and weather["brier_champion"] is not None and weather["brier_candidate"] is not None:
        if weather["brier_candidate"] > weather["brier_champion"] + EPS:
            reasons.append(
                "weather_us Brier regressed from "
                f"{_fmt_brier(weather['brier_champion'])} to {_fmt_brier(weather['brier_candidate'])}"
            )
    if weather and weather["win_rate_champion"] is not None and weather["win_rate_candidate"] is not None:
        if weather["win_rate_candidate"] + EPS < weather["win_rate_champion"]:
            reasons.append(
                "weather_us win rate regressed from "
                f"{_fmt_rate(weather['hits_champion'], weather['calls_champion'])} to "
                f"{_fmt_rate(weather['hits_candidate'], weather['calls_candidate'])}"
            )

    misses = []
    for row in replayed:
        if not row["miss"]:
            continue
        champion = row["champion_prob"]
        proposed = row["candidate_prob"]
        outcome = row["outcome"]
        if proposed is None or outcome is None or outcome >= 0.5:
            more = False
        elif champion is None:
            more = proposed > 0.5 + EPS
        else:
            more = proposed > champion + EPS
        item = {
            "strategy": row["strategy"],
            "slug": row["slug"],
            "side": row["side"],
            "cause": row["cause"],
            "outcome": outcome,
            "pnl": row["pnl"],
            "market_prob": row["market_prob"],
            "champion_prob": champion,
            "candidate_prob": proposed,
            "more_confident_wrong": more,
            "brier_before": None if champion is None or outcome is None else brier_score(champion, outcome),
            "brier_after": None if proposed is None or outcome is None else brier_score(proposed, outcome),
        }
        misses.append(item)
        if more:
            reasons.append(
                f"miss {row['strategy']} {row['slug']} got more confident in the wrong direction "
                f"({_fmt_prob(champion)} -> {_fmt_prob(proposed)})"
            )

    return {
        "verdict": "FAIL" if reasons else "PASS",
        "reasons": reasons,
        "candidate": candidate_name,
        "n_fixtures": len(replayed),
        "n_misses": len(misses),
        "n_weather": sum(1 for row in replayed if row["strategy"] == "weather_us"),
        "overall": overall,
        "strategies": strategies,
        "misses": misses,
    }


def format_report(report: dict) -> str:
    """Plain-text gate report for a PR description. No orders."""
    lines = [
        f"Challenger gate: {report['verdict']}",
        f"Candidate: {report.get('candidate')}",
        (
            f"Fixed fixtures: {report.get('n_fixtures', 0)} "
            f"(real misses {report.get('n_misses', 0)}, "
            f"settled weather_us {report.get('n_weather', 0)})."
        ),
        (
            "This gate does not place orders and does not change sizing, gates, "
            "the loss pause, paper/live, or calibration."
        ),
        "",
        "Brier is the mean squared error of the bought side. Lower is better.",
        "The market benchmark is the frozen market probability of that side.",
        "",
    ]
    overall = report.get("overall") or {}
    lines.append(
        "Fixed set: "
        f"n={overall.get('n', 0)} "
        f"champion {_fmt_brier(overall.get('brier_champion'))} "
        f"candidate {_fmt_brier(overall.get('brier_candidate'))} "
        f"market {_fmt_brier(overall.get('brier_market'))}."
    )
    lines.append("")
    for name, info in (report.get("strategies") or {}).items():
        record = f"{info.get('outcome_wins', 0)}W/{info.get('outcome_losses', 0)}L"
        lines.append(
            f"{name}: scored n={info.get('n', 0)} of {info.get('rows', 0)} rows, outcomes {record}."
        )
        lines.append(
            "  Brier champion "
            f"{_fmt_brier(info.get('brier_champion'))} "
            f"candidate {_fmt_brier(info.get('brier_candidate'))} "
            f"market {_fmt_brier(info.get('brier_market'))}."
        )
        lines.append(
            "  Champion beats market: "
            f"{_yes_no(info.get('champion_beats_market'))}. "
            "Candidate beats market: "
            f"{_yes_no(info.get('candidate_beats_market'))}."
        )
        lines.append(
            "  Win rate champion "
            f"{_fmt_rate(info.get('hits_champion', 0), info.get('calls_champion', 0))} "
            "candidate "
            f"{_fmt_rate(info.get('hits_candidate', 0), info.get('calls_candidate', 0))}."
        )
        lines.append("")
    lines.append("Real misses, champion probability then candidate:")
    if not report.get("misses"):
        lines.append("  none")
    for miss in report.get("misses") or []:
        flag = "MORE CONFIDENT WRONG" if miss.get("more_confident_wrong") else "not more confident"
        lines.append(
            f"  {miss['strategy']} {miss['side']} {miss['slug']} "
            f"cause={miss.get('cause') or 'n/a'} outcome={miss.get('outcome')} "
            f"pnl={miss.get('pnl')} "
            f"before={_fmt_prob(miss.get('champion_prob'))} "
            f"after={_fmt_prob(miss.get('candidate_prob'))} "
            f"market={_fmt_prob(miss.get('market_prob'))} "
            f"brier {_fmt_brier(miss.get('brier_before'))} -> {_fmt_brier(miss.get('brier_after'))} "
            f"{flag}."
        )
    lines.append("")
    if report.get("reasons"):
        lines.append("FAIL reasons:")
        for reason in report["reasons"]:
            lines.append(f"  - {reason}")
    else:
        lines.append("FAIL reasons: none.")
    weather = (report.get("strategies") or {}).get("weather_us") or {}
    if weather.get("calls_champion"):
        lines.append(
            "Weather win rate counts a call as right when the probability is on the "
            "correct side of 0.5. Champion weather win rate is "
            f"{_fmt_rate(weather.get('hits_champion', 0), weather.get('calls_champion', 0))} "
            f"on frozen outcomes {weather.get('outcome_wins', 0)}W/{weather.get('outcome_losses', 0)}L."
        )
    lines.append(
        "Promotion still needs this verdict to be PASS and Ryan's approval. "
        "A PASS here does not by itself change a live or paper bet."
    )
    return "\n".join(lines)


def _yes_no(value) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "n/a"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay frozen paper rows through the champion and one candidate. Eval only.",
    )
    parser.add_argument(
        "--candidate", required=True,
        help="champion, a JSON config path, or module:function (default attr side_probability)",
    )
    parser.add_argument("--fixtures", type=Path, default=None, help="Fixture JSON (default tests/fixtures/real_misses/)")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON")
    args = parser.parse_args(argv)
    try:
        fn, name = load_candidate(args.candidate)
        fixtures = load_fixtures(args.fixtures) if args.fixtures else None
        report = evaluate(fn, fixtures=fixtures, candidate_name=name)
    except (OSError, ValueError, json.JSONDecodeError, ModuleNotFoundError, AttributeError) as exc:
        print(f"challenger gate could not run: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(report, indent=2) if args.json else format_report(report)
    print(text)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
