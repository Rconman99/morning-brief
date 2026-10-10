"""Strategy scoreboard + capital ladder.

Answers two questions every cycle, from REAL settled trades (agent/ledger.py):
  1. Which strategies are earning? (their weight, and so their share of the
     bankroll, moves via the evaluator: winners get more, losers less)
  2. Has the system earned more capital? It never moves money; it tells you
     when a step up (or down) the ladder is justified.

Capital ladder: $100 -> $250 -> $500 -> $1,000 -> $2,500 -> $5,000
  Step UP when, since the current stage began:
    - >= 50 settled trades
    - ROI after fees >= +1%  (paper: the conservative taker-bound ROI must also be > 0)
    - max drawdown < 15% of the stage bankroll
    - from $250 up, >= 80% of those settled trades must be LIVE fills (paper doesn't count)
  Step DOWN when: drawdown > 20% of the stage bankroll, or ROI < -3% over >= 30 settled trades.

Usage:
    .venv/bin/python agent/scoreboard.py              # print scoreboard
    .venv/bin/python agent/scoreboard.py --telegram   # also send the daily summary (once/day)
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import logging
import os
from datetime import datetime, timezone

LADDER = [100, 250, 500, 1000, 2500, 5000]
STATE = PROJECT_ROOT / "agent" / "capital_state.json"
MIN_TRADES_UP = 50
MIN_ROI_UP = 0.01
MAX_DD_UP = 0.15
LIVE_SHARE_UP = 0.80
DD_DOWN = 0.20
ROI_DOWN = -0.03
MIN_TRADES_DOWN = 30


def _load_state(bankroll: float) -> dict:
    try:
        st = json.loads(STATE.read_text())
    except (OSError, json.JSONDecodeError):
        st = {}
    if st.get("stage_bankroll") != bankroll:
        st = {"stage_bankroll": bankroll, "stage_start": datetime.now(timezone.utc).isoformat(),
              "last_telegram": st.get("last_telegram", "")}
        _save_state(st)
    return st


def _save_state(st: dict) -> None:
    try:
        STATE.write_text(json.dumps(st, indent=1))
    except OSError:
        pass


def evaluate_ladder(rows: list[dict], bankroll: float, stage_start: str) -> dict:
    """Decide UP / HOLD / DOWN for the capital ladder from settled trades in this stage."""
    stage = sorted([r for r in rows if r["resolved"] and r["timestamp"] >= stage_start[:19]],
                   key=lambda r: r["timestamp"])
    n = len(stage)
    cost = sum(r["cost"] for r in stage)
    pnl = sum(r["pnl"] for r in stage)
    pnl_t = sum(r["pnl_taker_bound"] for r in stage)
    roi = pnl / cost if cost else 0.0
    roi_t = pnl_t / cost if cost else 0.0
    eq = peak = dd = 0.0
    for r in stage:
        eq += r["pnl"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    live_share = (sum(1 for r in stage if not r["paper"]) / n) if n else 0.0
    paper_mostly = live_share < 0.5

    nxt = next((x for x in LADDER if x > bankroll), None)
    prev = max([x for x in LADDER if x < bankroll], default=None)
    checks = {
        f"settled trades >= {MIN_TRADES_UP}": n >= MIN_TRADES_UP,
        f"ROI >= {MIN_ROI_UP:.0%}": roi >= MIN_ROI_UP,
        f"max drawdown < {MAX_DD_UP:.0%} of bankroll": dd < MAX_DD_UP * bankroll,
    }
    if paper_mostly:
        checks["taker-bound ROI > 0 (paper honesty check)"] = roi_t > 0
    if bankroll >= 250:
        checks[f"live fills >= {LIVE_SHARE_UP:.0%}"] = live_share >= LIVE_SHARE_UP

    if dd > DD_DOWN * bankroll or (n >= MIN_TRADES_DOWN and roi < ROI_DOWN):
        verdict = "DOWN"
        advice = (f"Step DOWN to ${prev:,}: withdraw the difference and set POLYMARKET_BANKROLL={prev}."
                  if prev else "Pause live trading (POLYMARKET_PAPER=1) and review the losing strategy.")
    elif all(checks.values()) and nxt:
        verdict = "UP"
        advice = f"Earned a step UP to ${nxt:,}: deposit the difference, then set POLYMARKET_BANKROLL={nxt}."
    else:
        verdict = "HOLD"
        missing = [k for k, ok in checks.items() if not ok]
        advice = "Keep running. Still needed: " + "; ".join(missing) if missing else "At the top of the ladder."
    return {
        "verdict": verdict, "advice": advice, "bankroll": bankroll, "next": nxt,
        "stage_trades": n, "stage_pnl": round(pnl, 2), "stage_pnl_taker_bound": round(pnl_t, 2),
        "stage_roi": round(roi, 4), "stage_roi_taker_bound": round(roi_t, 4),
        "stage_max_drawdown": round(dd, 2), "live_share": round(live_share, 2), "checks": checks,
    }


def render(summary: dict, ladder: dict, weights: dict, budgets: dict) -> str:
    lines = [f"POLYMARKET AGENT SCOREBOARD — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
             f"Bankroll stage ${ladder['bankroll']:,.0f}  |  verdict: {ladder['verdict']}",
             ""]
    if summary:
        lines.append(f"{'strategy':<15}{'wt':>5}{'budget':>8}{'settled':>8}{'open':>5}{'win%':>6}{'BE%':>5}{'P&L':>8}{'taker':>8}{'ROI':>7}")
        for s, v in sorted(summary.items(), key=lambda kv: -kv[1]["pnl"]):
            lines.append(f"{s:<15}{weights.get(s, 1.0):>5.2f}{budgets.get(s, 0):>8.0f}{v['resolved']:>8}{v['open']:>5}"
                         f"{v['win_rate']*100:>5.0f}%{v['breakeven_win_rate']*100:>4.0f}%{v['pnl']:>8.2f}"
                         f"{v['pnl_taker_bound']:>8.2f}{v['roi']*100:>6.1f}%")
    else:
        lines.append("(no Polymarket US trades yet)")
    lines += ["",
              f"This stage: {ladder['stage_trades']} settled, P&L ${ladder['stage_pnl']:.2f} "
              f"(taker-bound ${ladder['stage_pnl_taker_bound']:.2f}), ROI {ladder['stage_roi']*100:.1f}%, "
              f"max DD ${ladder['stage_max_drawdown']:.2f}, live {ladder['live_share']*100:.0f}%",
              ladder["advice"]]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--telegram", action="store_true", help="Send the summary to Telegram (max once per day)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    from agent.ledger import build
    from agent.config import load_agent_config
    from agent.risk_gate import strategy_budgets

    bankroll = float(os.environ.get("POLYMARKET_BANKROLL", "95"))
    st = _load_state(bankroll)
    rows, summary = build(refresh_settlements=True)
    ladder = evaluate_ladder(rows, bankroll, st["stage_start"])
    params = load_agent_config()
    weights = {s: float((params.get(s) or {}).get("weight", 1.0)) for s in summary}
    budgets = strategy_budgets(bankroll, set(summary)) if summary else {}
    text = render(summary, ladder, weights, budgets)
    print(text)

    try:
        from lib.data_envelope import create_envelope, save_envelope
        save_envelope(create_envelope("scoreboard", {"strategies": summary, "ladder": ladder,
                                                     "weights": weights, "budgets": budgets}), "scoreboard.json")
    except Exception:
        pass

    if args.telegram:
        today = datetime.now(timezone.utc).date().isoformat()
        if st.get("last_telegram") != today and datetime.now(timezone.utc).hour >= 15:  # ~8am PT
            try:
                from lib.notify import send_telegram
                if send_telegram("<pre>" + text.replace("&", "&amp;").replace("<", "&lt;") + "</pre>"):
                    st["last_telegram"] = today
                    _save_state(st)
            except Exception:
                pass


if __name__ == "__main__":
    main()
