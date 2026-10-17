"""Polymarket US live trading is opt-in. get_mode() is the only switch.

Matrix:
  no keys                         -> paper
  keys only                       -> paper
  keys + POLYMARKET_LIVE=1        -> live
  keys + LIVE=1 + PAPER=1         -> paper
  LIVE=1 without keys             -> paper
  non-us venue                    -> unchanged (private key, not POLYMARKET_LIVE)
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import logging

import pytest

from agent import executor
from agent.run import announce_mode


_ENV_KEYS = (
    "POLYMARKET_VENUE",
    "POLYMARKET_US_KEY_ID",
    "POLYMARKET_US_SECRET_KEY",
    "POLYMARKET_LIVE",
    "POLYMARKET_PAPER",
    "POLYMARKET_PRIVATE_KEY",
)


def _clear_mode_env(monkeypatch):
    for name in _ENV_KEYS:
        monkeypatch.delenv(name, raising=False)


def _us(monkeypatch, *, keys=False, live=None, paper=None):
    _clear_mode_env(monkeypatch)
    monkeypatch.setenv("POLYMARKET_VENUE", "us")
    if keys:
        monkeypatch.setenv("POLYMARKET_US_KEY_ID", "test-key-id")
        monkeypatch.setenv("POLYMARKET_US_SECRET_KEY", "test-secret")
    if live is not None:
        monkeypatch.setenv("POLYMARKET_LIVE", live)
    if paper is not None:
        monkeypatch.setenv("POLYMARKET_PAPER", paper)


@pytest.mark.parametrize(
    "live,paper,keys,expected,note",
    [
        (None, None, False, "paper", False),
        (None, None, True, "paper", True),
        ("1", None, True, "live", False),
        ("true", None, True, "live", False),
        ("yes", None, True, "live", False),
        ("YES", None, True, "live", False),
        (" True ", None, True, "live", False),
        ("1", "1", True, "paper", False),
        ("true", "TRUE", True, "paper", False),
        ("1", "yes", True, "paper", False),
        ("1", None, False, "paper", False),
        ("yes", None, False, "paper", False),
        ("0", None, True, "paper", True),
        ("false", None, True, "paper", True),
        (None, "1", True, "paper", True),
        ("1", "0", True, "live", False),
    ],
)
def test_us_mode_matrix(monkeypatch, live, paper, keys, expected, note):
    _us(monkeypatch, keys=keys, live=live, paper=paper)
    assert executor.get_mode() == expected
    assert executor.keys_without_live_opt_in() is note


def test_one_us_key_is_not_enough(monkeypatch):
    _us(monkeypatch, keys=False, live="1")
    monkeypatch.setenv("POLYMARKET_US_KEY_ID", "test-key-id")
    assert executor.get_mode() == "paper"
    assert executor.keys_without_live_opt_in() is False


def test_non_us_venue_unchanged(monkeypatch):
    """Polygon still goes live from POLYMARKET_PRIVATE_KEY alone."""
    _clear_mode_env(monkeypatch)
    assert executor.get_mode() == "paper"

    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0x" + "ab" * 16)
    assert executor.get_mode() == "live"

    # POLYMARKET_LIVE is not required, and setting it does not change polygon.
    monkeypatch.delenv("POLYMARKET_LIVE", raising=False)
    assert executor.get_mode() == "live"
    monkeypatch.setenv("POLYMARKET_LIVE", "1")
    assert executor.get_mode() == "live"

    # US keys on the polygon venue do not flip the US rule on.
    monkeypatch.setenv("POLYMARKET_US_KEY_ID", "test-key-id")
    monkeypatch.setenv("POLYMARKET_US_SECRET_KEY", "test-secret")
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    assert executor.get_mode() == "paper"

    monkeypatch.setenv("POLYMARKET_VENUE", "polygon")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "x" * 32)
    monkeypatch.delenv("POLYMARKET_LIVE", raising=False)
    assert executor.get_mode() == "live"
    monkeypatch.setenv("POLYMARKET_PAPER", "1")
    assert executor.get_mode() == "paper"
    assert executor.keys_without_live_opt_in() is False


def test_short_polygon_key_stays_paper(monkeypatch):
    _clear_mode_env(monkeypatch)
    monkeypatch.setenv("POLYMARKET_VENUE", "polygon")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "short")
    monkeypatch.setenv("POLYMARKET_LIVE", "1")
    assert executor.get_mode() == "paper"


def test_startup_note_when_keys_without_live(monkeypatch, caplog):
    _us(monkeypatch, keys=True)
    caplog.set_level(logging.INFO)
    assert announce_mode() == "paper"
    assert "=== Polymarket Agent [PAPER mode] ===" in caplog.text
    assert "US API keys are present but live trading is disabled until POLYMARKET_LIVE=1" in caplog.text


def test_startup_live_has_no_opt_in_note(monkeypatch, caplog):
    _us(monkeypatch, keys=True, live="1")
    caplog.set_level(logging.INFO)
    assert announce_mode() == "live"
    assert "=== Polymarket Agent [LIVE mode] ===" in caplog.text
    assert "disabled until POLYMARKET_LIVE=1" not in caplog.text


def test_startup_paper_override_does_not_claim_live_is_unset(monkeypatch, caplog):
    _us(monkeypatch, keys=True, live="1", paper="1")
    caplog.set_level(logging.INFO)
    assert announce_mode() == "paper"
    assert "=== Polymarket Agent [PAPER mode] ===" in caplog.text
    assert "disabled until POLYMARKET_LIVE=1" not in caplog.text


def test_risk_gate_stays_on_paper_log_until_opt_in(monkeypatch):
    """Weekly loss pause must keep counting paper rows when keys exist but live is off."""
    from agent.risk_gate import _live_us

    _us(monkeypatch, keys=True)
    assert _live_us() is False
    monkeypatch.setenv("POLYMARKET_LIVE", "1")
    assert _live_us() is True
    monkeypatch.setenv("POLYMARKET_PAPER", "1")
    assert _live_us() is False


def test_keys_only_logs_paper_trade_and_does_not_send(monkeypatch, tmp_path):
    _us(monkeypatch, keys=True)
    log = tmp_path / "paper_trades.jsonl"
    monkeypatch.setattr(executor, "PAPER_TRADE_LOG", log)
    monkeypatch.setattr("agent.pm_us.get_market", lambda slug: {"orderPriceMinTickSize": "0.01"})
    monkeypatch.setattr(
        "agent.pm_us.get_bbo",
        lambda slug: {
            "state": "MARKET_STATE_OPEN",
            "yes_bid": 0.90,
            "yes_ask": 0.93,
            "no_ask": 0.10,
        },
    )
    sent = []
    monkeypatch.setattr("agent.pm_us.place_limit", lambda *a, **k: sent.append(True) or {"ok": True})

    rec = executor.place_limit_order(
        token_id="",
        side="BUY",
        price=0.90,
        size=10,
        market_question="Test market",
        strategy="gimme_bets",
        reason="unit",
        slug="tc-temp-miahigh-2026-10-11-gte93lt94f",
        token_hint="yes",
    )
    assert rec["status"] == "paper_filled"
    assert rec["mode"] == "paper"
    assert sent == []
    assert "paper_filled" in log.read_text()


def test_direct_order_and_cancel_refuse_without_opt_in(monkeypatch):
    _us(monkeypatch, keys=True)
    from agent import pm_us

    res = pm_us.place_limit("some-slug", "BUY", "yes", 0.5, 1)
    assert res["ok"] is False
    assert "POLYMARKET_LIVE" in res["error"]
    assert pm_us.cancel("order-id", "some-slug") is False
    assert pm_us.cancel_stale_orders() == 0


def test_paper_cycle_does_not_read_equity_or_cancel(monkeypatch, tmp_path):
    """Keys present, POLYMARKET_LIVE unset: no balance gate and no stale-order cancel."""
    from agent import run

    _us(monkeypatch, keys=True)
    monkeypatch.setattr(run, "PROJECT_ROOT", tmp_path)
    (tmp_path / "agent").mkdir()
    monkeypatch.setattr(run, "init_paper_bankroll", lambda amount=1000.0: None)
    monkeypatch.setattr(run, "should_evaluate", lambda: False)
    monkeypatch.setattr(run, "load_agent_config", lambda: {})
    monkeypatch.setattr(run, "run_all_strategies", lambda params: [])
    monkeypatch.setattr("agent.tracker.load_env", lambda: None)
    monkeypatch.setattr("agent.pm_client.get_wallet_address", lambda: "")

    calls = []
    monkeypatch.setattr("agent.pm_us.account_equity", lambda: calls.append("equity") or 100.0)
    monkeypatch.setattr("agent.pm_us.cancel_stale_orders", lambda *a, **k: calls.append("cancel") or 0)
    monkeypatch.setattr("agent.pm_us.get_client", lambda: calls.append("client") or None)

    result = run.run_agent(bankroll=95)
    assert result == {"proposals": 0, "approved": 0, "executed": 0}
    assert calls == []
