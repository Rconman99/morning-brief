"""Streaming JSONL matches the in-memory loaders, and stays small on a big file.

The in-memory path is the one that was OOM-killed: read the whole proposal
log, then score it. These tests keep that path as the reference and compare
the streaming readers to it. No network and no .env.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import subprocess
import sys
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from agent.calibration_shadow import _summarize_rows, summarize_shadow
from agent.challenger_gate import FIXTURES, iter_fixtures, load_fixtures
from agent.loss_review import _review_loaded, review_losses
from agent.prob_calibration import (
    _FIT_CACHE,
    _SHADOW_STATS,
    _settled_sample_from_score,
    append_shadow,
    current_fit,
    fit_calibration,
    load_settled_rows,
    log_shadow_cycle_summary,
)
from agent.proposal_log import (
    eastern_day,
    score_proposal_records,
    score_proposal_records_streaming,
)

SLUG = "tc-temp-miahigh-2026-10-11-gte93lt94f"


def _legacy_rows(path: Path) -> list:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _write_jsonl(path: Path, rows: list) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _proposal_fixture() -> list[dict]:
    """Messy log: file order is not time order, ties, a bad line's neighbor, a blob."""
    blob = "X" * 2000
    return [
        {
            "record": "proposal",
            "timestamp": "2026-10-11T15:00:00+00:00",
            "strategy": "weather_us",
            "slug": SLUG,
            "side": "yes",
            "model_prob": 0.10,
            "market_ask": 0.40,
            "market_bid": 0.40,
            "market_prob": 0.40,
            "approved": False,
            "blob": blob,
            "prob_method": "later-file-row",
        },
        {
            "record": "proposal",
            "timestamp": "2026-10-11T14:00:00+00:00",
            "strategy": "weather_us",
            "slug": SLUG,
            "side": "yes",
            "group": "miahigh|2026-10-11",
            "model_prob": 0.80,
            "market_ask": 0.60,
            "market_bid": 0.58,
            "market_prob": 0.59,
            "approved": True,
            "prob_method": "emos",
            "ensemble_mean": 93.0,
            "blob": blob,
        },
        {
            "record": "quote",
            "timestamp": "2026-10-11T14:00:00+00:00",
            "slug": SLUG,
            "side": "yes",
            "market_ask": 0.99,
            "market_bid": 0.99,
        },
        {
            "record": "quote",
            "timestamp": "2026-10-11T16:00:00+00:00",
            "slug": SLUG,
            "side": "yes",
            "market_ask": 0.20,
            "market_bid": 0.20,
        },
        {
            "record": "quote",
            "timestamp": "2026-10-11T16:00:00+00:00",
            "slug": SLUG,
            "side": "yes",
            "market_ask": 0.80,
            "market_bid": 0.80,
        },
        {
            "record": "proposal",
            "timestamp": "2026-10-11T15:00:00+00:00",
            "strategy": "weather_us",
            "slug": SLUG,
            "side": "no",
            "model_prob": 0.75,
            "market_ask": 0.70,
            "market_bid": 0.70,
            "market_prob": 0.70,
        },
        {
            "record": "gimme_candidate",
            "timestamp": "2026-10-11T17:00:00+00:00",
            "slug": SLUG,
            "side": "no",
            "market_prob": 0.55,
        },
        {
            "record": "proposal",
            "timestamp": "2026-10-11T16:00:00+00:00",
            "strategy": "noise",
            "slug": "unsettled-slug",
            "side": "yes",
            "model_prob": 0.99,
            "market_prob": 0.50,
            "market_ask": 0.50,
            "blob": blob,
        },
    ]


def test_streaming_score_matches_the_in_memory_score(tmp_path):
    path = tmp_path / "proposals.jsonl"
    rows = _proposal_fixture()
    text = "".join(json.dumps(row) + "\n" for row in rows)
    path.write_text("\n" + text + "\nnot json\n\n", encoding="utf-8")
    settlements = {SLUG: 1.0}
    legacy = score_proposal_records(_legacy_rows(path), settlements)
    streamed = score_proposal_records_streaming(path, settlements)
    assert streamed == legacy
    yes = next(row for row in streamed if row["side"] == "yes")
    no = next(row for row in streamed if row["side"] == "no")
    # Earliest timestamp wins the entry, not the first line in the file.
    assert yes["model_prob"] == pytest.approx(0.80)
    assert yes["market_prob"] == pytest.approx(0.59)
    assert yes["n_logged"] == 2
    assert yes["prob_method"] == "emos"
    # Same timestamp: the higher price is the close. 0.20 and 0.80 at 16:00.
    assert yes["close_price"] == pytest.approx(0.80)
    assert yes["clv"] == pytest.approx(0.80 - 0.59)
    # A same-timestamp quote is not a close, and a later market_prob-only row is.
    assert no["close_price"] == pytest.approx(0.55)
    assert no["outcome"] == pytest.approx(0.0)
    assert all("blob" not in row for row in streamed)


def test_loss_review_streaming_matches_full_rows(tmp_path):
    from agent.loss_review import _load_cache

    trade = {
        "timestamp": "2026-10-20T15:00:00-04:00",
        "strategy": "weather_us",
        "slug": SLUG,
        "token_hint": "yes",
        "side": "BUY",
        "status": "paper_filled",
        "venue": "us",
        "price": 0.62,
        "fill_price": 0.62,
        "size": 10,
        "role": "maker",
        "model_prob": 0.50,
        "market_prob": 0.50,
        "blob": "T" * 500,
    }
    proposal = {
        "record": "proposal",
        "timestamp": "2026-10-20T14:00:00-04:00",
        "strategy": "weather_us",
        "slug": SLUG,
        "side": "yes",
        "model_prob": 0.90,
        "market_prob": 0.50,
        "spread": 0.08,
        "blob": "P" * 500,
    }
    later = {
        "record": "quote",
        "timestamp": "2026-10-20T18:00:00-04:00",
        "slug": SLUG,
        "side": "yes",
        "market_prob": 0.40,
        "market_ask": 0.40,
        "market_bid": 0.40,
    }
    noise = {
        "record": "proposal",
        "timestamp": "2026-10-20T12:00:00-04:00",
        "strategy": "weather_us",
        "slug": "other",
        "side": "yes",
        "model_prob": 0.99,
        "blob": "N" * 500,
    }
    trades_path = tmp_path / "trades.jsonl"
    proposals_path = tmp_path / "proposals.jsonl"
    cache_path = tmp_path / "cache.json"
    _write_jsonl(trades_path, [trade])
    _write_jsonl(proposals_path, [noise, proposal, later])
    cache_path.write_text(json.dumps({"settlements": {SLUG: 0}, "fills": {}}), encoding="utf-8")

    streamed = review_losses(
        "2026-10-01", "2026-10-31",
        trades_path=trades_path, cache_path=cache_path, proposals_path=proposals_path,
    )
    full_trades = _legacy_rows(trades_path)
    full_proposals = _legacy_rows(proposals_path)
    reference = _review_loaded(
        "2026-10-01", "2026-10-31", full_trades, full_proposals, _load_cache(cache_path), 14,
    )
    assert streamed == reference
    assert streamed["losses"][0]["cause"] == "thin_market"
    assert streamed["loss_count"] == 1


def test_shadow_summary_streaming_matches_full_rows(tmp_path):
    shadow = {
        "timestamp": "2026-10-20T15:00:00-04:00",
        "strategy": "weather_us",
        "slug": SLUG,
        "side": "yes",
        "raw_prob": 0.9,
        "calibrated_prob": 0.6,
        "would_skip": True,
        "would_resize": False,
        "decision_kept": True,
        "blob": "S" * 800,
    }
    resized = dict(shadow, would_skip=False, would_resize=True, decision_kept=True, slug=SLUG + "-b")
    out_of_range = dict(shadow, timestamp="2026-01-01T15:00:00-05:00")
    noise = dict(shadow, strategy="stage", blob="Z" * 800)
    trade = {
        "timestamp": "2026-10-20T15:00:00-04:00",
        "strategy": "weather_us",
        "slug": SLUG,
        "token_hint": "yes",
        "side": "BUY",
        "status": "paper_filled",
        "price": 0.40,
        "fill_price": 0.40,
        "size": 10,
        "role": "maker",
        "venue": "us",
        "blob": "trade-blob",
    }
    shadow_path = tmp_path / "shadow.jsonl"
    trades_path = tmp_path / "trades.jsonl"
    cache_path = tmp_path / "cache.json"
    _write_jsonl(shadow_path, [noise, out_of_range, shadow, resized])
    _write_jsonl(trades_path, [trade])
    cache_path.write_text(json.dumps({"settlements": {SLUG: 1}, "fills": {}}), encoding="utf-8")

    streamed = summarize_shadow(
        "2026-10-01", "2026-10-31",
        shadow_path=shadow_path, trades_path=trades_path, cache_path=cache_path,
    )
    from agent.calibration_shadow import _load_cache
    reference = _summarize_rows(
        _legacy_rows(shadow_path), _legacy_rows(trades_path), _load_cache(cache_path),
        "2026-10-01", "2026-10-31",
    )
    assert streamed == reference
    assert streamed["would_skip"] == 1
    assert streamed["would_resize"] == 1
    assert streamed["shadow_rows"] == 2  # out-of-range and noise dropped
    assert streamed["settled_would_skip_bets"] == 1
    assert streamed["pnl_would_skip"] > 0


def test_jsonl_fixtures_match_the_frozen_document_without_extra_fields(tmp_path):
    raw = json.loads(FIXTURES.read_text(encoding="utf-8"))["rows"]
    path = tmp_path / "fixtures.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for row in raw:
            copied = dict(row)
            copied["blob"] = "B" * 1000
            handle.write(json.dumps(copied) + "\n")
    streamed = list(iter_fixtures(path))
    assert streamed == load_fixtures()
    assert streamed == load_fixtures(path)
    assert all("blob" not in row for row in streamed)
    assert streamed[0]["trade"]["slug"] == raw[0]["trade"]["slug"]


def test_settled_sample_and_fit_match_the_in_memory_path(tmp_path, monkeypatch):
    path = tmp_path / "proposals.jsonl"
    cache = tmp_path / "ledger_cache.json"
    _write_jsonl(path, _proposal_fixture())
    cache.write_text(json.dumps({"settlements": {SLUG: 1.0}, "fills": {}}), encoding="utf-8")
    monkeypatch.setattr("agent.proposal_log.PROPOSAL_LOG", path)
    monkeypatch.setattr("agent.ledger.CACHE", cache)
    _FIT_CACHE["key"] = None
    _FIT_CACHE["fit"] = None

    records = _legacy_rows(path)
    asks = {}
    for row in records:
        if not isinstance(row, dict) or row.get("record", "proposal") != "proposal":
            continue
        try:
            day = eastern_day(row.get("timestamp") or "")
        except (TypeError, ValueError):
            continue
        key = (row.get("strategy") or "unknown", row.get("slug"), row.get("side"), day)
        if key not in asks and row.get("market_ask") is not None:
            asks[key] = row.get("market_ask")
    expected = _settled_sample_from_score(score_proposal_records(records, {SLUG: 1.0}), asks)
    assert load_settled_rows() == expected
    # File-order first ask is 0.40, from the later-timestamp line that appears first.
    yes = next(row for row in expected if row["side"] == "yes")
    assert yes["ask"] == pytest.approx(0.40)

    calls = {"n": 0}
    import agent.proposal_log as proposal_log
    real = proposal_log.stream_score_proposal_file

    def wrapped(log_path, settlements):
        calls["n"] += 1
        return real(log_path, settlements)

    monkeypatch.setattr(proposal_log, "stream_score_proposal_file", wrapped)
    first = current_fit()
    second = current_fit()
    assert calls["n"] == 1
    assert second["n"] == first["n"]
    assert second["model_weight"] == first["model_weight"]
    assert first["n"] == len(expected)
    fresh = fit_calibration(expected)
    assert first["model_weight"] == fresh["model_weight"]
    assert first["n"] == fresh["n"]

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "record": "proposal",
            "timestamp": "2026-10-12T18:00:00+00:00",
            "strategy": "gimme_bets",
            "slug": SLUG,
            "side": "yes",
            "model_prob": 0.70,
            "market_prob": 0.40,
            "market_ask": 0.45,
        }) + "\n")
    third = current_fit()
    assert calls["n"] == 2
    assert third["n"] == first["n"] + 1


def test_shadow_detail_is_debug_and_the_cycle_logs_one_summary(caplog):
    _SHADOW_STATS["rows"] = 0
    _SHADOW_STATS["would_skip"] = 0
    _SHADOW_STATS["would_resize"] = 0
    _SHADOW_STATS["kept"] = 0
    caplog.set_level(logging.DEBUG)
    append_shadow({
        "strategy": "weather_us",
        "slug": SLUG,
        "side": "yes",
        "raw_prob": 0.9,
        "calibrated_prob": 0.6,
        "market_prob": 0.55,
        "ask": 0.7,
        "would_skip": True,
        "would_resize": False,
        "decision_kept": True,
        "size_shares": 5,
        "calibrated_size_shares": 5,
    })
    from agent.prob_calibration import SHADOW_LOG
    stored = json.loads(SHADOW_LOG.read_text(encoding="utf-8").splitlines()[-1])
    assert stored["would_skip"] is True
    assert stored["mode"] == "shadow"
    assert stored["raw_prob"] == pytest.approx(0.9)
    info = [r for r in caplog.records if r.levelno == logging.INFO and "calibration shadow" in r.getMessage()]
    debug = [r for r in caplog.records if r.levelno == logging.DEBUG and r.getMessage().startswith("calibration shadow ")]
    assert info == []
    assert len(debug) == 1
    log_shadow_cycle_summary()
    summaries = [
        r.getMessage() for r in caplog.records
        if r.levelno == logging.INFO and r.getMessage().startswith("calibration shadow summary")
    ]
    assert summaries == ["calibration shadow summary: logged 1, would_skip 1, would_resize 0, decisions_kept 1"]


def _archive():
    spec = importlib.util.spec_from_file_location(
        "archive_proposals", PROJECT_ROOT / "deploy" / "archive_proposals.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_archive_script_is_dry_run_until_apply(tmp_path):
    module = _archive()
    path = tmp_path / "proposals.jsonl"
    old = {"record": "proposal", "timestamp": "2026-10-01T15:00:00+00:00", "slug": "old", "side": "yes"}
    new = {"record": "proposal", "timestamp": "2026-11-01T15:00:00+00:00", "slug": "new", "side": "yes"}
    blank_day = {"record": "proposal", "slug": "nodate", "side": "yes"}
    _write_jsonl(path, [old, new, blank_day])
    path.write_text(path.read_text(encoding="utf-8") + "not-json\n", encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    dry = module.archive_proposals(path, 10, apply=False, today="2026-11-09")
    assert path.read_text(encoding="utf-8") == before
    assert dry["applied"] is False
    assert dry["moved"] == 1
    assert dry["kept"] == 2
    assert dry["unparsed"] == 1
    assert dry["archive"] is None

    done = module.archive_proposals(path, 10, apply=True, today="2026-11-09", archive_dir=tmp_path / "archive")
    assert done["applied"] is True
    assert done["archive"]
    live = path.read_text(encoding="utf-8").splitlines()
    assert len(live) == 3
    assert all("old" not in line for line in live)
    import gzip
    archived = gzip.open(done["archive"], "rt", encoding="utf-8").read().splitlines()
    assert len(archived) == 1
    assert json.loads(archived[0])["slug"] == "old"


def _rss_script() -> str:
    return textwrap.dedent(
        """
        import json, sys
        from pathlib import Path
        sys.path.insert(0, sys.argv[1])
        root = Path(sys.argv[2])
        mode = sys.argv[3]
        proposals = root / "proposals.jsonl"
        trades = root / "trades.jsonl"
        cache = root / "cache.json"

        def peak_kb():
            # VmHWM is this process's peak after exec. getrusage ru_maxrss
            # is not reset by exec, so a big parent pytest makes it lie.
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("VmHWM:"):
                    return int(line.split()[1])
            raise RuntimeError("VmHWM missing")

        if mode == "full":
            text = proposals.read_text(encoding="utf-8")
            rows = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            from agent.proposal_log import score_proposal_records
            scored = score_proposal_records(rows, {"keep-slug": 1.0})
        else:
            from agent.proposal_log import score_proposal_records_streaming
            from agent.loss_review import review_losses
            from agent.calibration_shadow import summarize_shadow
            scored = score_proposal_records_streaming(proposals, {"keep-slug": 1.0})
            review_losses("2020-01-01", "2030-01-01", trades_path=trades, cache_path=cache, proposals_path=proposals)
            summarize_shadow("2020-01-01", "2030-01-01", shadow_path=proposals, trades_path=trades, cache_path=cache)
        print(len(scored))
        print(peak_kb())
        """
    )


def test_streaming_rss_on_a_60mb_file_stays_under_150mb(tmp_path):
    proposals = tmp_path / "proposals.jsonl"
    blob = "B" * 512
    filler = json.dumps({
        "record": "proposal",
        "strategy": "noise",
        "slug": "noise-slug",
        "side": "yes",
        "timestamp": "2026-06-01T15:00:00+00:00",
        "model_prob": 0.5,
        "market_prob": 0.4,
        "market_ask": 0.4,
        "blob": blob,
    }) + "\n"
    header = json.dumps({
        "record": "proposal",
        "strategy": "weather_us",
        "slug": "keep-slug",
        "side": "yes",
        "timestamp": "2026-10-11T14:00:00+00:00",
        "model_prob": 0.8,
        "market_prob": 0.6,
        "market_ask": 0.62,
        "market_bid": 0.58,
    }) + "\n"
    quote = json.dumps({
        "record": "quote",
        "slug": "keep-slug",
        "side": "yes",
        "timestamp": "2026-10-11T18:00:00+00:00",
        "market_ask": 0.70,
        "market_bid": 0.66,
    }) + "\n"
    target = 60 * 1024 * 1024
    with proposals.open("w", encoding="utf-8") as handle:
        handle.write(header)
        handle.write(quote)
        written = len(header) + len(quote)
        while written < target:
            handle.write(filler)
            written += len(filler)
    trades = tmp_path / "trades.jsonl"
    trades.write_text(json.dumps({
        "timestamp": "2026-10-11T15:00:00-04:00",
        "strategy": "weather_us",
        "slug": "keep-slug",
        "token_hint": "yes",
        "side": "BUY",
        "status": "paper_filled",
        "venue": "us",
        "price": 0.6,
        "fill_price": 0.6,
        "size": 5,
        "role": "maker",
    }) + "\n", encoding="utf-8")
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"settlements": {"keep-slug": 1}, "fills": {}}), encoding="utf-8")

    script = tmp_path / "rss.py"
    script.write_text(_rss_script(), encoding="utf-8")

    def run(mode: str) -> tuple[int, int]:
        proc = subprocess.run(
            [sys.executable, str(script), str(PROJECT_ROOT), str(tmp_path), mode],
            check=True, capture_output=True, text=True,
        )
        n_line, rss_line = proc.stdout.strip().splitlines()[-2:]
        return int(n_line), int(rss_line)

    full_n, full_kb = run("full")
    stream_n, stream_kb = run("stream")
    assert full_n == stream_n == 1
    assert stream_kb < 150 * 1024
    assert stream_kb * 2 < full_kb
    # Leave the measured peaks where a failure log can show them.
    print(f"rss_kb full={full_kb} stream={stream_kb} file_bytes={proposals.stat().st_size}")
