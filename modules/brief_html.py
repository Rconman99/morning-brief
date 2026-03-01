"""Morning Brief HTML Dashboard: generates a styled, self-contained HTML report."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import datetime

from lib.data_envelope import load_envelope
from modules.morning_brief import determine_verdict, get_eastern_date
from modules.brief_learning import (
    KNOWLEDGE_BASE, LEARNING_CSS, LEARNING_JS,
    label as ll, section_title as st,
    build_data_snapshot, resolve_quiz_tokens, build_concepts_json,
    build_panel_html, build_tour_html, build_learning_path_html,
    build_how_to_use_html,
)

logger = logging.getLogger(__name__)

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "data" / "outputs"


def setup_logging():
    log_dir = PROJECT_ROOT / "data" / "outputs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(log_dir / "run.log", mode="a"),
            ],
        )


CSS = """
:root {
    --bg-primary: #0d1117;
    --bg-card: #161b22;
    --bg-card-hover: #1c2333;
    --border: #30363d;
    --text-primary: #e6edf3;
    --text-secondary: #8b949e;
    --text-muted: #484f58;
    --green: #3fb950;
    --green-bg: rgba(63,185,80,0.12);
    --red: #f85149;
    --red-bg: rgba(248,81,73,0.12);
    --yellow: #d29922;
    --yellow-bg: rgba(210,153,34,0.12);
    --blue: #58a6ff;
    --blue-bg: rgba(88,166,255,0.12);
    --gray: #8b949e;
    --gray-bg: rgba(139,148,158,0.12);
    --accent: #58a6ff;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    background: var(--bg-primary);
    color: var(--text-primary);
    line-height: 1.6;
    padding: 0;
}
.container { max-width: 1200px; margin: 0 auto; padding: 24px; }

/* Hero Banner */
.hero {
    background: linear-gradient(135deg, #161b22 0%, #1a2332 50%, #161b22 100%);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 32px;
    margin-bottom: 24px;
    text-align: center;
}
.hero h1 {
    font-size: 1.5rem;
    font-weight: 600;
    color: var(--text-secondary);
    margin-bottom: 8px;
    letter-spacing: 2px;
    text-transform: uppercase;
}
.hero .date { font-size: 1.1rem; color: var(--text-muted); margin-bottom: 16px; }
.hero .pnl {
    font-size: 3rem;
    font-weight: 700;
    margin: 12px 0 20px;
    font-variant-numeric: tabular-nums;
}
.pnl-positive { color: var(--green); }
.pnl-negative { color: var(--red); }
.pnl-neutral { color: var(--text-secondary); }
.status-pills { display: flex; justify-content: center; gap: 8px; flex-wrap: wrap; }
.pill {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 4px 12px; border-radius: 16px; font-size: 0.8rem;
    background: var(--bg-card); border: 1px solid var(--border);
}
.pill-success { border-color: var(--green); color: var(--green); }
.pill-partial { border-color: var(--yellow); color: var(--yellow); }
.pill-error { border-color: var(--red); color: var(--red); }
.pill-missing { border-color: var(--text-muted); color: var(--text-muted); }

/* Section headers */
.section { margin-bottom: 24px; }
.section-title {
    font-size: 1.1rem; font-weight: 600; color: var(--text-secondary);
    text-transform: uppercase; letter-spacing: 1.5px;
    padding-bottom: 8px; margin-bottom: 16px;
    border-bottom: 1px solid var(--border);
}

/* Card grid */
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
.card {
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: 8px; padding: 20px;
    transition: background 0.15s;
}
.card:hover { background: var(--bg-card-hover); }
.card-header {
    font-size: 1rem; font-weight: 600; margin-bottom: 12px;
    display: flex; align-items: center; gap: 8px;
}
.card-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 4px 0; font-size: 0.9rem;
}
.card-label { color: var(--text-secondary); }
.card-value { font-weight: 500; font-variant-numeric: tabular-nums; }

/* Verdict grid */
.verdict-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
.verdict-card {
    border-radius: 8px; padding: 16px;
    border-left: 4px solid transparent;
}
.verdict-BUY { background: var(--green-bg); border-left-color: var(--green); }
.verdict-SELL { background: var(--red-bg); border-left-color: var(--red); }
.verdict-AVOID { background: var(--yellow-bg); border-left-color: var(--yellow); }
.verdict-HOLD { background: var(--gray-bg); border-left-color: var(--gray); }
.verdict-REVIEW { background: var(--blue-bg); border-left-color: var(--blue); }
.verdict-ticker { font-size: 1.1rem; font-weight: 700; }
.verdict-label {
    display: inline-block; font-size: 0.75rem; font-weight: 700;
    padding: 2px 8px; border-radius: 4px; margin-left: 8px;
    text-transform: uppercase; letter-spacing: 0.5px;
}
.verdict-label-BUY { background: var(--green); color: #0d1117; }
.verdict-label-SELL { background: var(--red); color: #0d1117; }
.verdict-label-AVOID { background: var(--yellow); color: #0d1117; }
.verdict-label-HOLD { background: var(--gray); color: #0d1117; }
.verdict-label-REVIEW { background: var(--blue); color: #0d1117; }
.verdict-reason { font-size: 0.85rem; color: var(--text-secondary); margin-top: 6px; }

/* Portfolio table */
.table-wrap { overflow-x: auto; }
table {
    width: 100%; border-collapse: collapse;
    background: var(--bg-card); border-radius: 8px;
    overflow: hidden;
}
th {
    text-align: left; padding: 12px 16px; font-size: 0.8rem;
    text-transform: uppercase; letter-spacing: 1px;
    color: var(--text-secondary); background: #1c2128;
    border-bottom: 1px solid var(--border);
}
td {
    padding: 10px 16px; font-size: 0.9rem;
    border-bottom: 1px solid var(--border);
    font-variant-numeric: tabular-nums;
}
tr:last-child td { border-bottom: none; }
tr:hover td { background: var(--bg-card-hover); }

/* Correlation badges */
.corr-high {
    display: inline-block; padding: 4px 10px; border-radius: 6px;
    background: var(--red-bg); color: var(--red);
    font-size: 0.85rem; font-weight: 600;
    margin: 3px;
}

/* Journal stats */
.stat-row { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }
.stat-box {
    flex: 1; min-width: 120px; text-align: center;
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px;
}
.stat-number { font-size: 1.8rem; font-weight: 700; }
.stat-label { font-size: 0.8rem; color: var(--text-secondary); margin-top: 4px; }

/* Patterns / checklist */
.alert-box {
    background: var(--yellow-bg); border: 1px solid var(--yellow);
    border-radius: 8px; padding: 12px 16px; margin-bottom: 8px;
    font-size: 0.9rem;
}
.checklist-item {
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: 8px; padding: 12px 16px; margin-bottom: 8px;
    font-size: 0.9rem;
}

/* Earnings */
.tone-bar {
    height: 8px; border-radius: 4px; background: var(--border);
    position: relative; margin: 8px 0;
}
.tone-fill {
    height: 100%; border-radius: 4px;
    position: absolute; top: 0;
}
.risk-list { list-style: none; padding: 0; margin-top: 8px; }
.risk-list li {
    font-size: 0.85rem; color: var(--text-secondary);
    padding: 4px 0 4px 16px; position: relative;
}
.risk-list li::before {
    content: '\26A0 '; position: absolute; left: 0; color: var(--yellow);
}

/* Footer */
.footer {
    text-align: center; padding: 24px; margin-top: 32px;
    border-top: 1px solid var(--border);
    font-size: 0.8rem; color: var(--text-muted);
}

/* Options compact cards */
.options-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
.pmcc-row { display: flex; justify-content: space-between; font-size: 0.85rem; padding: 3px 0; }
.pmcc-label { color: var(--text-secondary); }
.pmcc-value { font-weight: 500; }
.tag-skipped {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    background: var(--gray-bg); color: var(--gray); font-size: 0.8rem;
}

/* Unavailable placeholder */
.unavailable {
    color: var(--text-muted); font-style: italic;
    padding: 24px; text-align: center;
    background: var(--bg-card); border-radius: 8px;
    border: 1px dashed var(--border);
}
"""


def _esc(text) -> str:
    """HTML-escape a string."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _pnl_color(val) -> str:
    if val is None:
        return "var(--text-secondary)"
    return "var(--green)" if val >= 0 else "var(--red)"


def _pnl_class(val) -> str:
    if val is None:
        return "pnl-neutral"
    return "pnl-positive" if val >= 0 else "pnl-negative"


def _status_pill(name: str, status: str) -> str:
    cls = f"pill pill-{status}"
    icons = {"success": "&#10003;", "partial": "&#9888;", "error": "&#10007;", "missing": "&#9633;"}
    icon = icons.get(status, "?")
    return f'<span class="{cls}">{icon} {_esc(name)}</span>'


def _fmt_price(val) -> str:
    if val is None:
        return "N/A"
    return f"${val:,.2f}"


def _fmt_pnl(val) -> str:
    if val is None:
        return "N/A"
    return f"${val:+,.2f}"


def _fmt_delta(val) -> str:
    if val is None:
        return "N/A"
    return f"{val:.2f}\u0394"


def generate_html(processed_dir: Path = None, outputs_dir: Path = None) -> str:
    """Generate a self-contained HTML dashboard from all module outputs."""
    pdir = processed_dir or PROCESSED_DIR

    # Load envelopes — temporarily redirect data_envelope's PROCESSED_DIR
    from lib import data_envelope
    original_dir = data_envelope.PROCESSED_DIR
    data_envelope.PROCESSED_DIR = pdir

    journal = load_envelope("journal.json")
    earnings = load_envelope("earnings_tone.json")
    valuation = load_envelope("valuation.json")
    portfolio = load_envelope("portfolio.json")
    technical = load_envelope("technical_signals.json")
    sentiment = load_envelope("news_sentiment.json")
    options = load_envelope("options.json")
    opportunities = load_envelope("opportunities.json")
    risk_dashboard = load_envelope("risk_dashboard.json")
    scorecard = load_envelope("scorecard.json")
    trade_memory = load_envelope("trade_memory.json")
    insider = load_envelope("insider_tracker.json")
    position_sizer = load_envelope("position_sizer.json")

    data_envelope.PROCESSED_DIR = original_dir

    date_str = get_eastern_date()
    modules = {
        "Journal": journal, "Earnings": earnings, "Valuation": valuation,
        "Portfolio": portfolio, "Technical": technical, "Sentiment": sentiment,
        "Options": options, "Scanner": opportunities,
        "Risk": risk_dashboard, "Scorecard": scorecard,
        "Memory": trade_memory, "Insider": insider, "Sizer": position_sizer,
    }

    # Total P&L
    total_pnl = portfolio["data"].get("total_pnl")

    # Build verdicts
    all_tickers = set()
    for comp in valuation["data"].get("comparisons", []):
        all_tickers.add(comp.get("stock_a", ""))
        all_tickers.add(comp.get("stock_b", ""))
    for h in portfolio["data"].get("holdings", []):
        all_tickers.add(h.get("ticker", ""))
    for t in technical["data"].get("results", []):
        all_tickers.add(t.get("ticker", ""))
    for s in sentiment["data"].get("results", []):
        all_tickers.add(s.get("ticker", ""))
    all_tickers.discard("")

    risk_data = risk_dashboard["data"]

    insider_data = insider.get("data", {})
    scenario_data = valuation["data"].get("scenarios", [])

    verdicts = {}
    for ticker in sorted(all_tickers):
        v, reason = determine_verdict(
            ticker, valuation["data"], earnings["data"], portfolio["data"],
            technical["data"], sentiment["data"], risk_data,
            insider_data, scenario_data)
        verdicts[ticker] = (v, reason)

    # ── Build Learning Data ──
    snapshot = build_data_snapshot(
        portfolio["data"], technical["data"], earnings["data"], risk_data)
    resolved_kb = resolve_quiz_tokens(KNOWLEDGE_BASE, snapshot)
    concepts_json = build_concepts_json(resolved_kb)

    # ── Build HTML ──
    parts = [f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Morning Brief &mdash; {_esc(date_str)}</title>
<style>{CSS}
{LEARNING_CSS}</style>
<script>window.__LEARN_CONCEPTS={concepts_json};</script>
</head>
<body>
<div class="container">
"""]

    # Hero banner
    pnl_display = _fmt_pnl(total_pnl) if total_pnl is not None else "N/A"
    pnl_cls = _pnl_class(total_pnl)
    pills = " ".join(_status_pill(name, env["status"]) for name, env in modules.items())

    parts.append(f"""
<div class="hero">
    <h1>Morning Brief</h1>
    <div class="date">{_esc(date_str)}</div>
    <div class="pnl {pnl_cls}">{pnl_display}</div>
    <div style="font-size:0.85rem;color:var(--text-secondary);margin-bottom:16px">Total Portfolio P&amp;L</div>
    <div class="status-pills">{pills}</div>
</div>
""")

    # ── Learning Path + How to Use ──
    parts.append(build_learning_path_html())
    parts.append(build_how_to_use_html())

    # ── Risk Dashboard Section ──
    if risk_dashboard["status"] in ("success", "partial"):
        rd = risk_data
        regime = rd.get("regime", "UNKNOWN")
        regime_note = rd.get("regime_note", "")
        regime_colors = {
            "DANGER": ("var(--red)", "var(--red-bg)"),
            "CAUTION": ("var(--yellow)", "var(--yellow-bg)"),
            "NORMAL": ("var(--green)", "var(--green-bg)"),
            "LOW_RISK": ("var(--green)", "var(--green-bg)"),
        }
        r_color, r_bg = regime_colors.get(regime, ("var(--text-secondary)", "var(--bg-card)"))

        parts.append(f"""
<div class="section">
    {st("Risk Dashboard", extra_html=f' &mdash; <span style="color:{r_color}">{_esc(regime)}</span>')}
    <div style="background:{r_bg};border:1px solid {r_color};border-radius:8px;padding:14px 18px;margin-bottom:16px;font-size:0.95rem;color:{r_color}">{_esc(regime_note)}</div>
""")

        vix_val = rd.get("vix")
        tny_val = rd.get("ten_year_yield")
        parts.append('<div class="stat-row">')
        parts.append(f'<div class="stat-box"><div class="stat-number" style="color:{r_color}">{rd.get("total_risk", 0):.1f}</div><div class="stat-label">Risk Score</div></div>')
        if vix_val is not None:
            vix_color = "var(--red)" if vix_val > 30 else "var(--yellow)" if vix_val > 20 else "var(--green)"
            parts.append(f'<div class="stat-box"><div class="stat-number" style="color:{vix_color}">{vix_val:.1f}</div><div class="stat-label">VIX</div></div>')
        if tny_val is not None:
            parts.append(f'<div class="stat-box"><div class="stat-number">{tny_val:.2f}%</div><div class="stat-label">10Y Yield</div></div>')
        parts.append('</div>')

        risk_level_colors = {"high": "var(--red)", "medium": "var(--yellow)", "low": "var(--green)"}
        risk_level_bg = {"high": "var(--red-bg)", "medium": "var(--yellow-bg)", "low": "var(--green-bg)"}

        for horizon_name, horizon_risks in [("NOW (Today)", rd.get("now_risks", [])),
                                              ("SHORT (This Week)", rd.get("short_risks", [])),
                                              ("LONG (This Month+)", rd.get("long_risks", []))]:
            if horizon_risks:
                parts.append(f'<div style="font-size:0.9rem;font-weight:600;color:var(--text-secondary);margin:12px 0 6px;text-transform:uppercase">{horizon_name}</div>')
                for r in horizon_risks:
                    rc = risk_level_colors.get(r["level"], "var(--text-secondary)")
                    rb = risk_level_bg.get(r["level"], "var(--bg-card)")
                    parts.append(f'<div style="background:{rb};border-left:3px solid {rc};border-radius:4px;padding:8px 12px;margin-bottom:4px;font-size:0.85rem">{_esc(r["detail"])}</div>')

        parts.append('</div>')

    # ── Trading Windows Section ──
    from modules.morning_brief import get_trading_windows
    windows = get_trading_windows()
    if windows:
        parts.append(f'<div class="section">{st("Trading Windows")}')
        for w in windows:
            if w["is_open"]:
                bg = "var(--green-bg)"
                border = "var(--green)"
                icon = "&#9679;"
                label = f"OPEN — closes {_esc(w['close'])}"
            else:
                bg = "var(--red-bg)"
                border = "var(--red)"
                icon = "&#9679;"
                label = f"CLOSED — opens {_esc(w['open'])}"
            parts.append(f"""
<div style="background:{bg};border:1px solid {border};border-radius:8px;padding:14px 18px;margin-bottom:8px;display:flex;align-items:center;gap:12px">
    <span style="color:{border};font-size:1.2rem">{icon}</span>
    <div>
        <span style="font-weight:700;font-size:1rem">{_esc(w['ticker'])}</span>
        <span style="color:{border};font-weight:600;margin-left:8px">{label}</span>
        <div style="font-size:0.8rem;color:var(--text-secondary);margin-top:2px">{_esc(w['note'])}</div>
    </div>
</div>""")
        parts.append('</div>')

    # ── Verdicts Section ──
    parts.append(f'<div class="section">{st("Watchlist Verdicts")}')
    parts.append('<div class="verdict-grid">')
    for ticker in sorted(verdicts.keys()):
        v, reason = verdicts[ticker]
        parts.append(f"""
<div class="verdict-card verdict-{v}">
    <div><span class="verdict-ticker">{_esc(ticker)}</span>
    <span class="verdict-label verdict-label-{v}">{v}</span></div>
    <div class="verdict-reason">{_esc(reason)}</div>
</div>""")
    parts.append('</div></div>')

    # ── Position Sizing Section ──
    parts.append(f'<div class="section">{st("Position Sizing")}')
    if position_sizer["status"] in ("success", "partial"):
        ps_data = position_sizer["data"]
        positions = [p for p in ps_data.get("positions", []) if p.get("recommended_shares", 0) > 0]
        pv = ps_data.get("portfolio_value", 0)
        rpt = ps_data.get("risk_per_trade_pct", 0)
        parts.append(f'<div style="font-size:0.85rem;color:var(--text-muted);margin-bottom:12px">Portfolio: ${pv:,.0f} | Risk/trade: {rpt:.0%}</div>')
        if positions:
            parts.append('<div class="table-wrap"><table>')
            parts.append('<thead><tr><th>Ticker</th><th>Shares</th><th>Value</th><th>Stop</th><th>Risk/Share</th><th>% of Portfolio</th></tr></thead><tbody>')
            for p in positions:
                parts.append(f'<tr><td style="font-weight:600">{_esc(p["ticker"])}</td><td>{p["recommended_shares"]}</td><td>{_fmt_price(p["recommended_value"])}</td><td>{_fmt_price(p["stop_loss"])}</td><td>{_fmt_price(p["risk_per_share"])}</td><td>{p["portfolio_pct"]:.1%}</td></tr>')
            parts.append('</tbody></table></div>')
        else:
            parts.append('<div class="unavailable">No position sizing recommendations</div>')
    else:
        parts.append('<div class="unavailable">Position sizing data unavailable</div>')
    parts.append('</div>')

    # ── Opportunity Scanner Section ──
    parts.append(f'<div class="section">{st("Opportunity Scanner")}')
    if opportunities["status"] in ("success", "partial"):
        opp_data = opportunities["data"]
        parts.append(f'<div style="font-size:0.85rem;color:var(--text-muted);margin-bottom:12px">Scanned {opp_data.get("universe_size", 0)} tickers outside your watchlist</div>')

        opps = opp_data.get("opportunities", [])
        if opps:
            parts.append('<div class="card-grid">')
            for o in opps:
                comp = o.get("composite_score", 0)
                parts.append(f"""
<div class="card" style="border-left:3px solid var(--green)">
    <div class="card-header">{_esc(o['ticker'])}</div>
    <div class="card-row">{ll("Composite")}
        <span class="card-value" style="color:var(--green)">{comp:+.1f}</span></div>
    <div class="card-row">{ll("RSI")}
        <span class="card-value">{o.get('rsi_14', 'N/A')}</span></div>
    <div class="card-row">{ll("MACD")}
        <span class="card-value">{_esc(o.get('macd_signal', 'N/A'))}</span></div>
    <div class="card-row">{ll("Volume")}
        <span class="card-value">{o.get('volume_ratio', 'N/A')}x</span></div>
    <div class="card-row">{ll("PE")}
        <span class="card-value">{o.get('pe_ttm', 'N/A')}</span></div>
    <div style="margin-top:8px;font-size:0.85rem;color:var(--text-secondary)">{_esc(o.get('reason', ''))}</div>
    <div style="font-size:0.8rem;color:var(--yellow);margin-top:4px">{_esc(o.get('risk_note', ''))}</div>
</div>""")
            parts.append('</div>')
        else:
            parts.append('<div class="unavailable">No opportunities passed the filter criteria</div>')

        sector = opp_data.get("sector_read", {})
        if sector:
            sector_names = {"XLK": "Tech", "XLF": "Financials", "XLE": "Energy",
                           "XLV": "Health Care", "XLI": "Industrials", "ARKK": "Innovation"}
            parts.append('<div style="margin-top:16px"><div style="font-size:0.9rem;color:var(--text-secondary);margin-bottom:8px;font-weight:600">Sector Pulse</div>')
            for etf, data in sector.items():
                name = sector_names.get(etf, etf)
                trend_color = "var(--green)" if data["trend"] == "bullish" else "var(--red)" if data["trend"] == "bearish" else "var(--yellow)"
                parts.append(f'<div style="display:inline-block;background:var(--bg-card);border:1px solid var(--border);border-radius:6px;padding:6px 12px;margin:3px;font-size:0.85rem">{_esc(etf)} ({_esc(name)}): <span style="color:{trend_color}">{data["change_1d"]:+.1f}% / {data["change_5d"]:+.1f}%</span></div>')
            parts.append('</div>')
    else:
        parts.append('<div class="unavailable">Opportunity scanner data unavailable</div>')
    parts.append('</div>')

    # ── Trade Memory Section ──
    parts.append(f'<div class="section">{st("Trade Memory", extra_html=" &mdash; Pattern Matching")}')
    if trade_memory["status"] in ("success", "partial"):
        mem_results = trade_memory["data"].get("results", [])
        notable = [r for r in mem_results if r.get("match_result", {}).get("matches", 0) > 0]
        if notable:
            parts.append('<div class="card-grid">')
            for r in notable:
                match = r["match_result"]
                confidence = match.get("confidence", "")
                if confidence == "high_win":
                    border_color = "var(--green)"
                    bg_color = "var(--green-bg)"
                elif confidence == "high_loss":
                    border_color = "var(--red)"
                    bg_color = "var(--red-bg)"
                else:
                    border_color = "var(--yellow)"
                    bg_color = "var(--yellow-bg)"
                wr = match.get("win_rate")
                wr_str = f"{wr:.0%}" if wr is not None else "N/A"
                parts.append(f"""
<div class="card" style="border-left:3px solid {border_color}">
    <div class="card-header">{_esc(r['ticker'])}</div>
    <div class="card-row">{ll("Matches")}
        <span class="card-value">{match.get('matches', 0)}</span></div>
    <div class="card-row">{ll("Win Rate")}
        <span class="card-value" style="color:{border_color}">{wr_str}</span></div>
    <div class="card-row">{ll("Record")}
        <span class="card-value">{match.get('wins', 0)}W / {match.get('losses', 0)}L</span></div>
    <div style="margin-top:8px;font-size:0.85rem;color:var(--text-secondary)">{_esc(r.get('signal', ''))}</div>
</div>""")
            parts.append('</div>')
        else:
            parts.append('<div class="unavailable">No matching historical patterns found</div>')
    else:
        parts.append('<div class="unavailable">Trade memory data unavailable</div>')
    parts.append('</div>')

    # ── Insider Activity Section ──
    parts.append(f'<div class="section">{st("Insider Activity (SEC Form 4)")}')
    if insider["status"] in ("success", "partial"):
        insider_results = insider["data"].get("results", [])
        active = [r for r in insider_results if r.get("transaction_count", 0) > 0]
        if active:
            parts.append('<div class="table-wrap"><table>')
            parts.append('<thead><tr><th>Ticker</th><th>Filings</th><th>Signal</th><th>Cluster Buy</th><th>Detail</th></tr></thead><tbody>')
            for r in active:
                cluster_icon = '<span style="color:var(--green);font-weight:700">YES</span>' if r.get("cluster_buy") else '<span style="color:var(--text-muted)">No</span>'
                signal_color = "var(--green)" if r.get("signal") == "active" else "var(--text-secondary)"
                parts.append(f'<tr><td style="font-weight:600">{_esc(r["ticker"])}</td><td>{r["transaction_count"]}</td><td style="color:{signal_color}">{_esc(r.get("signal", ""))}</td><td>{cluster_icon}</td><td style="font-size:0.85rem">{_esc(r.get("detail", ""))}</td></tr>')
            parts.append('</tbody></table></div>')
        else:
            parts.append('<div class="unavailable">No recent insider activity detected</div>')
    else:
        parts.append('<div class="unavailable">Insider tracking data unavailable</div>')
    parts.append('</div>')

    # ── Scenario Valuations Section ──
    scenarios = valuation["data"].get("scenarios", [])
    parts.append(f'<div class="section">{st("Scenario Valuations (Bull / Base / Bear)")}')
    if scenarios:
        parts.append('<div class="card-grid">')
        for s in scenarios:
            rr = s.get("risk_reward")
            rr_str = f"{rr:.1f}x" if rr else "N/A"
            rr_color = "var(--green)" if rr and rr >= 1.5 else "var(--yellow)" if rr else "var(--text-muted)"
            parts.append(f"""
<div class="card">
    <div class="card-header">{_esc(s['ticker'])} <span style="font-size:0.8rem;color:var(--text-muted);font-weight:400">(${s['current_price']:,.2f})</span></div>
    <div class="card-row"><span class="card-label learnable" data-concept="scenario_valuation" style="color:var(--green)">Bull</span>
        <span class="card-value" style="color:var(--green)">${s['bull_price']:,.2f} ({s['bull_upside']:+.1%})</span></div>
    <div class="card-row">{ll("Base")}
        <span class="card-value">${s['base_price']:,.2f} ({s['base_upside']:+.1%})</span></div>
    <div class="card-row"><span class="card-label learnable" data-concept="scenario_valuation" style="color:var(--red)">Bear</span>
        <span class="card-value" style="color:var(--red)">${s['bear_price']:,.2f} ({s['bear_downside']:+.1%})</span></div>
    <div class="card-row">{ll("R/R Ratio")}
        <span class="card-value" style="color:{rr_color}">{rr_str}</span></div>
</div>""")
        parts.append('</div>')
    else:
        parts.append('<div class="unavailable">No scenario valuation data available</div>')
    parts.append('</div>')

    # ── Scorecard Section ──
    parts.append(f'<div class="section">{st("Verdict Scorecard")}')
    if scorecard["status"] in ("success", "partial"):
        sc = scorecard["data"]
        summary = sc.get("summary", {})
        total_scored = summary.get("total_scored", 0)
        win_rate = summary.get("win_rate", 0)
        evaluated = sc.get("evaluated_verdicts", 0)

        wr_display = f"{win_rate:.0%}" if total_scored > 0 else "N/A"
        wr_color = "var(--green)" if win_rate >= 0.5 else "var(--red)" if total_scored > 0 else "var(--text-secondary)"

        parts.append(f"""
<div class="stat-row">
    <div class="stat-box">
        <div class="stat-number">{evaluated}</div>
        <div class="stat-label">Verdicts Evaluated</div>
    </div>
    <div class="stat-box">
        <div class="stat-number" style="color:{wr_color}">{wr_display}</div>
        <div class="stat-label">Win Rate</div>
    </div>
    <div class="stat-box">
        <div class="stat-number" style="color:var(--green)">{summary.get('total_wins', 0)}</div>
        <div class="stat-label">Wins</div>
    </div>
    <div class="stat-box">
        <div class="stat-number" style="color:var(--red)">{summary.get('total_losses', 0)}</div>
        <div class="stat-label">Losses</div>
    </div>
</div>""")

        by_verdict = summary.get("by_verdict", {})
        if by_verdict and total_scored > 0:
            parts.append('<div class="card-grid">')
            for v, stats in by_verdict.items():
                if v == "REVIEW":
                    parts.append(f"""
<div class="card">
    <div class="card-header"><span class="verdict-label verdict-label-{v}">{v}</span></div>
    <div class="card-row">{ll("Tracked")}
        <span class="card-value">{stats.get('tracked', 0)}</span></div>
    <div class="card-row">{ll("Status")}
        <span class="card-value" style="color:var(--text-muted)">Unscored</span></div>
</div>""")
                elif stats.get("scored", 0) > 0:
                    v_wr = stats.get("win_rate", 0)
                    v_color = "var(--green)" if v_wr >= 0.5 else "var(--red)"
                    parts.append(f"""
<div class="card">
    <div class="card-header"><span class="verdict-label verdict-label-{v}">{v}</span></div>
    <div class="card-row">{ll("Win Rate")}
        <span class="card-value" style="color:{v_color}">{v_wr:.0%}</span></div>
    <div class="card-row">{ll("Record")}
        <span class="card-value">{stats.get('wins', 0)}W / {stats.get('losses', 0)}L</span></div>
</div>""")
            parts.append('</div>')
        elif total_scored == 0:
            parts.append('<div class="unavailable">No evaluation windows have elapsed yet — check back in 5 days</div>')
    else:
        parts.append('<div class="unavailable">No scorecard data yet — verdicts will be evaluated after first run</div>')
    parts.append('</div>')

    # ── Portfolio Section ──
    parts.append(f'<div class="section">{st("Portfolio Holdings")}')
    if portfolio["status"] in ("success", "partial"):
        holdings = portfolio["data"].get("holdings", [])
        parts.append('<div class="table-wrap"><table>')
        parts.append("""<thead><tr>
            <th>Ticker</th><th>Shares</th><th>Cost Basis</th>
            <th>Price</th><th>P&amp;L</th><th>ATR</th>
            <th>Trail Stop</th><th>Locked Profit</th>
        </tr></thead><tbody>""")
        for h in holdings:
            pnl = h.get("pnl")
            lp = h.get("locked_profit")
            parts.append(f"""<tr>
                <td style="font-weight:600">{_esc(h['ticker'])}</td>
                <td>{h.get('shares', 'N/A')}</td>
                <td>{_fmt_price(h.get('cost_basis'))}</td>
                <td>{_fmt_price(h.get('current_price'))}</td>
                <td style="color:{_pnl_color(pnl)};font-weight:600">{_fmt_pnl(pnl)}</td>
                <td>{_fmt_price(h.get('atr'))}</td>
                <td>{_fmt_price(h.get('trailing_stop'))}</td>
                <td style="color:{_pnl_color(lp)}">{_fmt_pnl(lp)}</td>
            </tr>""")
        parts.append("</tbody></table></div>")

        # High correlations only (>= 0.80)
        corr = portfolio["data"].get("correlations", {})
        high_corrs = []
        for pair, val in corr.items():
            if isinstance(val, dict):
                c = val.get("correlation")
                if c is not None and abs(c) >= 0.80:
                    high_corrs.append((pair.replace("_", " / "), c))
            elif isinstance(val, (int, float)) and abs(val) >= 0.80:
                high_corrs.append((pair.replace("_", " / "), val))

        if high_corrs:
            parts.append('<div style="margin-top:16px">')
            parts.append('<div style="font-size:0.9rem;color:var(--text-secondary);margin-bottom:8px;font-weight:600">High Correlations (&ge; 0.80)</div>')
            for pair, c in sorted(high_corrs, key=lambda x: abs(x[1]), reverse=True):
                parts.append(f'<span class="corr-high">{_esc(pair)}: {c:.2f}</span>')
            parts.append('</div>')
    else:
        parts.append('<div class="unavailable">Portfolio data unavailable</div>')
    parts.append('</div>')

    # ── Journal Section ──
    parts.append(f'<div class="section">{st("Trade Journal")}')
    if journal["status"] in ("success", "partial"):
        jd = journal["data"]
        wr = jd.get("win_rate")
        wr_display = f"{wr:.0%}" if wr is not None else "N/A"
        wr_color = "var(--green)" if wr and wr >= 0.5 else "var(--red)"

        parts.append(f"""
<div class="stat-row">
    <div class="stat-box">
        <div class="stat-number">{jd.get('total_trades', 'N/A')}</div>
        <div class="stat-label">Total Trades</div>
    </div>
    <div class="stat-box">
        <div class="stat-number" style="color:{wr_color}">{wr_display}</div>
        <div class="stat-label">Win Rate</div>
    </div>
    <div class="stat-box">
        <div class="stat-number" style="color:var(--green)">{jd.get('winners', 'N/A')}</div>
        <div class="stat-label">Winners</div>
    </div>
    <div class="stat-box">
        <div class="stat-number" style="color:var(--red)">{jd.get('losers', 'N/A')}</div>
        <div class="stat-label">Losers</div>
    </div>
</div>""")

        patterns = jd.get("patterns", [])
        if patterns:
            for p in patterns:
                parts.append(f'<div class="alert-box">{_esc(p.get("detail", str(p)))}</div>')

        checklist = jd.get("checklist", [])
        if checklist:
            for rule in checklist:
                parts.append(f'<div class="checklist-item">{_esc(rule)}</div>')
    else:
        parts.append('<div class="unavailable">Journal data unavailable</div>')
    parts.append('</div>')

    # ── Earnings Section ──
    parts.append(f'<div class="section">{st("Earnings Tone Analysis")}')
    if earnings["status"] in ("success", "partial"):
        results = earnings["data"].get("results", [])
        if results:
            parts.append('<div class="card-grid">')
            for r in results:
                tone = r.get("tone_score", 0)
                conf = r.get("confidence_score", 0)
                # Tone bar: map -5..+5 to 0%..100%
                tone_pct = max(0, min(100, (tone + 5) * 10))
                tone_color = "var(--green)" if tone > 0 else "var(--red)" if tone < -1 else "var(--yellow)"

                ticker_display = r['ticker']
                if ticker_display.startswith("SAMPLE_"):
                    ticker_display = ticker_display.replace("SAMPLE_", "") + " (Sample)"

                parts.append(f"""
<div class="card">
    <div class="card-header">{_esc(ticker_display)}</div>
    <div class="card-row">{ll("Tone Score")}
        <span class="card-value" style="color:{tone_color}">{tone:+.1f}</span></div>
    <div class="tone-bar"><div class="tone-fill" style="width:{tone_pct}%;background:{tone_color}"></div></div>
    <div class="card-row">{ll("Confidence")}
        <span class="card-value">{conf:.2f}</span></div>
    <div class="card-row">{ll("Hedge / Definitive")}
        <span class="card-value">{r.get('hedge_count', 0)} / {r.get('definitive_count', 0)}</span></div>""")

                risks = r.get("risk_factors", [])
                if risks:
                    parts.append('<ul class="risk-list">')
                    for rf in risks[:5]:
                        parts.append(f'<li>{_esc(rf)}</li>')
                    parts.append('</ul>')

                summary = r.get("summary", "")
                if summary:
                    parts.append(f'<div style="margin-top:8px;font-size:0.85rem;color:var(--text-secondary)">{_esc(summary)}</div>')
                parts.append('</div>')
            parts.append('</div>')
        else:
            parts.append('<div class="unavailable">No earnings results</div>')
    else:
        parts.append('<div class="unavailable">Earnings data unavailable</div>')
    parts.append('</div>')

    # ── Valuation Section ──
    parts.append(f'<div class="section">{st("Valuation Comparisons")}')
    if valuation["status"] in ("success", "partial"):
        comps = valuation["data"].get("comparisons", [])
        if comps:
            parts.append('<div class="card-grid">')
            for comp in comps:
                cheaper = comp.get("cheaper", "inconclusive")
                cheaper_color = "var(--green)" if cheaper != "inconclusive" else "var(--text-muted)"
                parts.append(f"""
<div class="card">
    <div class="card-header">{_esc(comp['stock_a'])} vs {_esc(comp['stock_b'])}</div>
    <div class="card-row">{ll("Cheaper")}
        <span class="card-value" style="color:{cheaper_color}">{_esc(cheaper)}</span></div>
    <div class="card-row">{ll("Metrics")}
        <span class="card-value">{comp.get('a_wins', 0)}-{comp.get('b_wins', 0)} ({comp.get('comparable_metrics', 0)} comparable)</span></div>
    <div style="margin-top:8px;font-size:0.85rem;color:var(--text-secondary)">{_esc(comp.get('thesis', ''))}</div>
</div>""")
            parts.append('</div>')
        else:
            parts.append('<div class="unavailable">No valuation comparisons</div>')
    else:
        parts.append('<div class="unavailable">Valuation data unavailable</div>')
    parts.append('</div>')

    # ── Technical Signals Section ──
    parts.append(f'<div class="section">{st("Technical Signals")}')
    if technical["status"] in ("success", "partial"):
        tech_results = technical["data"].get("results", [])
        if tech_results:
            parts.append('<div class="card-grid">')
            for t in tech_results:
                comp_score = t.get("composite_score", 0)
                if comp_score > 2:
                    score_color = "var(--green)"
                elif comp_score < -2:
                    score_color = "var(--red)"
                else:
                    score_color = "var(--yellow)"

                rsi = t.get("rsi_14")
                rsi_color = "var(--red)" if rsi and rsi > 70 else "var(--green)" if rsi and rsi < 30 else "var(--text-primary)"

                parts.append(f"""
<div class="card">
    <div class="card-header">{_esc(t['ticker'])}</div>
    <div class="card-row">{ll("Composite")}
        <span class="card-value" style="color:{score_color}">{comp_score:+.1f}</span></div>
    <div class="card-row">{ll("RSI(14)")}
        <span class="card-value" style="color:{rsi_color}">{rsi if rsi else 'N/A'}</span></div>
    <div class="card-row">{ll("MACD")}
        <span class="card-value">{_esc(t.get('macd_signal', 'N/A'))}</span></div>
    <div class="card-row">{ll("Bollinger")}
        <span class="card-value">{_esc(t.get('bb_position', 'N/A'))}</span></div>
    <div class="card-row">{ll("SMA Trend")}
        <span class="card-value">{_esc(t.get('sma_trend', 'N/A'))}</span></div>
    <div class="card-row">{ll("Vol Ratio")}
        <span class="card-value">{t.get('volume_ratio', 'N/A')}</span></div>
    <div class="card-row">{ll("VWAP")}
        <span class="card-value">{f"${t['vwap']}" if t.get('vwap') else 'N/A'} <span style="color:var(--text-secondary)">({_esc(t.get('vwap_signal') or 'N/A')})</span></span></div>
    <div class="card-row">{ll("POC")}
        <span class="card-value">{f"${t['volume_profile']['poc']}" if t.get('volume_profile') else 'N/A'}</span></div>
</div>""")
            parts.append('</div>')
        else:
            parts.append('<div class="unavailable">No technical signals data</div>')
    else:
        parts.append('<div class="unavailable">Technical signals unavailable</div>')
    parts.append('</div>')

    # ── News Sentiment Section ──
    parts.append(f'<div class="section">{st("News Sentiment")}')
    if sentiment["status"] in ("success", "partial"):
        sent_results = sentiment["data"].get("results", [])
        if sent_results:
            parts.append('<div class="card-grid">')
            for s in sent_results:
                score = s.get("sentiment_score", 0)
                if score > 0.2:
                    sent_color = "var(--green)"
                    sent_bg = "var(--green-bg)"
                elif score < -0.2:
                    sent_color = "var(--red)"
                    sent_bg = "var(--red-bg)"
                else:
                    sent_color = "var(--yellow)"
                    sent_bg = "var(--yellow-bg)"

                parts.append(f"""
<div class="card" style="border-left:3px solid {sent_color}">
    <div class="card-header">{_esc(s['ticker'])}</div>
    <div class="card-row">{ll("Sentiment")}
        <span class="card-value" style="color:{sent_color}">{score:+.2f}</span></div>
    <div class="card-row">{ll("Articles")}
        <span class="card-value">{s.get('article_count', 0)}</span></div>""")
                headline = s.get("key_headline", "")
                if headline:
                    parts.append(f'<div style="margin-top:8px;font-size:0.85rem;color:var(--text-secondary)">{_esc(headline[:120])}</div>')
                summary = s.get("summary", "")
                if summary:
                    parts.append(f'<div style="font-size:0.8rem;color:var(--text-muted);margin-top:4px">{_esc(summary[:150])}</div>')
                parts.append('</div>')
            parts.append('</div>')
        else:
            parts.append('<div class="unavailable">No news sentiment data</div>')
    else:
        parts.append('<div class="unavailable">News sentiment unavailable</div>')
    parts.append('</div>')

    # ── Options Section ──
    parts.append(f'<div class="section">{st("Options Analysis")}')
    if options["status"] in ("success", "partial"):
        tickers = options["data"].get("tickers", [])
        if tickers:
            parts.append('<div class="options-grid">')
            for t in tickers:
                ticker = t["ticker"]
                if t.get("skipped"):
                    parts.append(f"""
<div class="card">
    <div class="card-header">{_esc(ticker)} <span class="tag-skipped">Skipped</span></div>
    <div style="font-size:0.85rem;color:var(--text-muted)">{_esc(t.get('reason', 'N/A'))}</div>
</div>""")
                    continue

                pmcc = t.get("pmcc", {})
                parts.append(f"""
<div class="card">
    <div class="card-header">{_esc(ticker)}</div>
    <div class="card-row">{ll("Max Pain")}
        <span class="card-value">{_fmt_price(t.get('max_pain'))}</span></div>
    <div class="card-row">{ll("Front Month")}
        <span class="card-value">{_esc(t.get('front_month_expiry', 'N/A'))}</span></div>""")

                # Top strikes
                top = t.get("top_strikes", [])
                if top:
                    strikes_str = ", ".join(f"${s['strike']} ({s['total_oi']:,})" for s in top)
                    parts.append(f'<div class="card-row">{ll("Top OI")}<span class="card-value" style="font-size:0.8rem">{_esc(strikes_str)}</span></div>')

                # PMCC details
                if pmcc:
                    parts.append('<div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">')
                    parts.append('<div style="font-size:0.8rem;color:var(--text-muted);margin-bottom:6px;font-weight:600">PMCC Setup</div>')
                    parts.append(f"""
    <div class="pmcc-row"><span class="pmcc-label">Long {_esc(pmcc.get('long_expiry',''))}</span>
        <span class="pmcc-value">${pmcc.get('long_strike',0)} @ {_fmt_price(pmcc.get('long_price'))} ({_fmt_delta(pmcc.get('long_delta'))})</span></div>
    <div class="pmcc-row"><span class="pmcc-label">Short {_esc(pmcc.get('short_expiry',''))}</span>
        <span class="pmcc-value">${pmcc.get('short_strike',0)} @ {_fmt_price(pmcc.get('short_price'))} ({_fmt_delta(pmcc.get('short_delta'))})</span></div>
    <div class="pmcc-row"><span class="pmcc-label">Net Debit</span>
        <span class="pmcc-value" style="color:var(--accent)">{_fmt_price(pmcc.get('net_debit'))}</span></div>""")
                    parts.append('</div>')
                parts.append('</div>')
            parts.append('</div>')
        else:
            parts.append('<div class="unavailable">No options data</div>')
    else:
        parts.append('<div class="unavailable">Options data unavailable</div>')
    parts.append('</div>')

    # Footer
    parts.append(f"""
<div class="footer">
    This is analysis, not financial advice. All trading decisions are yours.<br>
    Generated {_esc(datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z'))}
</div>
</div>
{build_panel_html()}
{build_tour_html()}
<script>{LEARNING_JS}</script>
</body>
</html>""")

    return "\n".join(parts)


def main():
    setup_logging()
    logger.info("=== Brief HTML Module ===")

    html = generate_html()

    odir = OUTPUTS_DIR
    odir.mkdir(parents=True, exist_ok=True)
    date_str = get_eastern_date()
    output_path = odir / f"morning_brief_{date_str}.html"
    output_path.write_text(html)
    logger.info("HTML brief written to %s", output_path)


if __name__ == "__main__":
    main()
