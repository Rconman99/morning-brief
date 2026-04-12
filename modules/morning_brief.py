"""Morning Brief generator: aggregates all module outputs into a daily markdown report."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import datetime

from lib.data_envelope import load_envelope
from lib.api import yahoo_finance_price_history

logger = logging.getLogger(__name__)

# Allow override for testing
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "data" / "outputs"
SCORECARD_DIR = PROJECT_ROOT / "data" / "scorecard"


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


def get_eastern_date() -> str:
    """Get current date in US Eastern timezone."""
    try:
        from zoneinfo import ZoneInfo
        eastern = ZoneInfo("America/New_York")
        return datetime.now(eastern).strftime("%Y-%m-%d")
    except ImportError:
        from datetime import timezone, timedelta
        eastern = timezone(timedelta(hours=-5))
        return datetime.now(eastern).strftime("%Y-%m-%d")


def get_trading_windows(config_path: Path = None) -> list[dict]:
    """Check trading windows from portfolio.json, return status for each."""
    cfg = config_path or (PROJECT_ROOT / "config" / "portfolio.json")
    try:
        portfolio = json.loads(cfg.read_text())
    except Exception:
        return []

    today = datetime.now().strftime("%Y-%m-%d")
    windows = []
    for h in portfolio.get("holdings", []):
        tw = h.get("trading_window")
        if not tw:
            continue
        open_date = tw.get("open", "")
        close_date = tw.get("close", "")
        is_open = open_date <= today <= close_date
        windows.append({
            "ticker": h["ticker"],
            "open": open_date,
            "close": close_date,
            "note": tw.get("note", ""),
            "is_open": is_open,
        })
    return windows


def status_icon(status: str) -> str:
    icons = {"success": "✅", "partial": "⚠️", "error": "❌", "missing": "⬜"}
    return icons.get(status, "❓")


def determine_verdict(ticker: str, valuation_data: dict, earnings_data: dict,
                      portfolio_data: dict, technical_data: dict = None,
                      sentiment_data: dict = None,
                      risk_dashboard_data: dict = None,
                      insider_data: dict = None,
                      scenario_data: list = None) -> tuple[str, str]:
    """Determine BUY/SELL/AVOID/HOLD/REVIEW verdict using all data sources.

    Rules (evaluated in order):
    - SELL: trailing stop within 5% of price OR technical composite < -3
    - AVOID: news sentiment < -0.5 OR (technical composite < -2 AND earnings tone < -1)
    - DANGER GATE: if risk regime is DANGER, downgrade BUY to HOLD
    - BUY: technical composite > +2 AND sentiment >= 0 AND (cheaper on 2+ metrics OR not in comparison pair)
    - REVIEW: < 2 data sources available
    - HOLD: default
    """
    val_comparisons = valuation_data.get("comparisons", [])
    earnings_results = earnings_data.get("results", [])
    holdings = portfolio_data.get("holdings", [])
    tech_results = (technical_data or {}).get("results", [])
    sent_results = (sentiment_data or {}).get("results", [])

    # Get technical composite for this ticker
    composite = None
    for t in tech_results:
        if t.get("ticker") == ticker:
            composite = t.get("composite_score")
            break

    # Get news sentiment for this ticker
    sentiment = None
    for s in sent_results:
        if s.get("ticker") == ticker:
            sentiment = s.get("sentiment_score")
            break

    # Get earnings tone for this ticker
    tone_score = None
    for e in earnings_results:
        if e.get("ticker") == ticker or e.get("ticker") == f"SAMPLE_{ticker}":
            tone_score = e.get("tone_score")
            break

    # ── SELL ──
    # Trailing stop within 5% of current price (stop must be BELOW price)
    # OR price has already breached the stop (price < stop)
    for h in holdings:
        if h.get("ticker") == ticker:
            stop = h.get("trailing_stop")
            price = h.get("current_price")
            if stop and price and price > 0:
                if price < stop:
                    # Price has breached the trailing stop
                    return "SELL", f"Price ${price:.2f} has breached trailing stop ${stop:.2f} — stop triggered"
                elif (price - stop) / price < 0.05:
                    # Stop is within 5% below current price
                    return "SELL", f"Trailing stop ${stop:.2f} is within 5% of current price ${price:.2f}"

    # Technical composite < -3
    if composite is not None and composite < -3:
        return "SELL", f"Technical composite score {composite:.1f} is strongly bearish (< -3)"

    # ── AVOID ──
    # News sentiment < -0.5
    if sentiment is not None and sentiment < -0.5:
        return "AVOID", f"News sentiment is {sentiment:.2f} (< -0.5)"

    # Technical composite < -2 AND earnings tone < -1
    if composite is not None and composite < -2 and tone_score is not None and tone_score < -1:
        return "AVOID", f"Technical composite {composite:.1f} and earnings tone {tone_score:.1f} both negative"

    # Earnings tone <= -2 (legacy rule, still useful)
    if tone_score is not None and tone_score <= -2:
        return "AVOID", f"Earnings tone score is {tone_score} (≤ -2)"

    # More expensive on 4+ metrics
    for comp in val_comparisons:
        if comp.get("stock_a") == ticker and comp.get("cheaper") == comp.get("stock_b"):
            total_comparable = comp.get("comparable_metrics", 0)
            a_wins = comp.get("a_wins", 0)
            if total_comparable - a_wins >= 4:
                return "AVOID", f"More expensive than {comp['stock_b']} on 4+ metrics"
        elif comp.get("stock_b") == ticker and comp.get("cheaper") == comp.get("stock_a"):
            total_comparable = comp.get("comparable_metrics", 0)
            b_wins = comp.get("b_wins", 0)
            if total_comparable - b_wins >= 4:
                return "AVOID", f"More expensive than {comp['stock_a']} on 4+ metrics"

    # Insider cluster buy check
    insider_boost = False
    for ins in (insider_data or {}).get("results", []):
        if ins.get("ticker") == ticker and ins.get("cluster_buy"):
            insider_boost = True
            break

    # Scenario risk/reward check
    scenario_rr = None
    for sc in (scenario_data or []):
        if sc.get("ticker") == ticker:
            scenario_rr = sc.get("risk_reward")
            break

    # ── GATE: Risk regime blocks aggressive BUY ──
    regime = (risk_dashboard_data or {}).get("regime", "NORMAL")
    if regime == "DANGER":
        if composite is not None and composite > 2:
            return "HOLD", f"Technicals bullish ({composite:.1f}) but risk regime is DANGER — wait for conditions to improve"

    # ── BUY ──
    # Technical composite > +2 AND sentiment >= 0
    if composite is not None and composite > 2:
        if sentiment is None or sentiment >= 0:
            # Check if cheaper on 2+ metrics OR not in any comparison pair
            in_comparison = False
            for comp in val_comparisons:
                if comp.get("stock_a") == ticker or comp.get("stock_b") == ticker:
                    in_comparison = True
                    if comp.get("cheaper") == ticker:
                        wins = comp.get("a_wins", 0) if comp.get("stock_a") == ticker else comp.get("b_wins", 0)
                        if wins >= 2:
                            reason = f"Strong technicals (composite {composite:.1f}) with positive sentiment and cheaper on {wins} metrics"
                            if insider_boost:
                                reason += " + insider cluster buying"
                            if scenario_rr and scenario_rr >= 1.5:
                                reason += f" + favorable R/R ({scenario_rr:.1f}x)"
                            return "BUY", reason
            if not in_comparison:
                reason = f"Strong technicals (composite {composite:.1f}) with positive sentiment"
                if insider_boost:
                    reason += " + insider cluster buying"
                if scenario_rr and scenario_rr >= 1.5:
                    reason += f" + favorable R/R ({scenario_rr:.1f}x)"
                return "BUY", reason

    # Insider cluster buy with positive technicals (lower threshold)
    if insider_boost and composite is not None and composite > 1:
        if sentiment is None or sentiment >= -0.2:
            return "BUY", f"Insider cluster buying detected with positive technicals (composite {composite:.1f})"

    # ── REVIEW ──
    # Count available data sources
    sources = 0
    if len(val_comparisons) > 0:
        sources += 1
    if len(holdings) > 0:
        sources += 1
    if composite is not None:
        sources += 1
    if sentiment is not None:
        sources += 1
    if sources < 2:
        return "REVIEW", f"Only {sources} data source(s) available for this ticker"

    return "HOLD", "Default — no strong signal in either direction"


def _get_current_price(ticker: str) -> float | None:
    """Get most recent close price for a ticker."""
    df = yahoo_finance_price_history(ticker, period="5d")
    if df.empty:
        return None
    return float(df["Close"].iloc[-1])


def log_verdicts(processed_dir: Path = None, scorecard_dir: Path = None):
    """Collect today's verdicts and append to verdict_log.json with dedup."""
    pdir = processed_dir or PROCESSED_DIR
    sdir = scorecard_dir or SCORECARD_DIR
    sdir.mkdir(parents=True, exist_ok=True)

    from lib import data_envelope
    original_dir = data_envelope.PROCESSED_DIR
    data_envelope.PROCESSED_DIR = pdir

    valuation = load_envelope("valuation.json")
    earnings = load_envelope("earnings_tone.json")
    portfolio = load_envelope("portfolio.json")
    technical = load_envelope("technical_signals.json")
    sentiment = load_envelope("news_sentiment.json")
    risk_dashboard = load_envelope("risk_dashboard.json")
    insider = load_envelope("insider_tracker.json")

    data_envelope.PROCESSED_DIR = original_dir

    risk_data = risk_dashboard.get("data", {})
    insider_data = insider.get("data", {})
    scenario_data = valuation["data"].get("scenarios", [])

    # Collect all tickers
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

    if not all_tickers:
        logger.info("No tickers to log verdicts for")
        return

    date_str = get_eastern_date()

    # Get SPY price for benchmark
    spy_price = _get_current_price("SPY")

    # Load existing log
    log_path = sdir / "verdict_log.json"
    if log_path.exists():
        try:
            log_data = json.loads(log_path.read_text())
        except json.JSONDecodeError:
            log_data = {"entries": []}
    else:
        log_data = {"entries": []}

    # Dedup set: existing (date, ticker) pairs
    existing = {(e.get("date"), e.get("ticker")) for e in log_data.get("entries", [])}

    new_count = 0
    for ticker in sorted(all_tickers):
        if (date_str, ticker) in existing:
            continue
        verdict, reason = determine_verdict(
            ticker, valuation["data"], earnings["data"], portfolio["data"],
            technical["data"], sentiment["data"], risk_data,
            insider_data, scenario_data)
        price = _get_current_price(ticker)
        log_data["entries"].append({
            "date": date_str,
            "ticker": ticker,
            "verdict": verdict,
            "reason": reason,
            "price_at_verdict": price,
            "spy_price_at_verdict": spy_price,
        })
        new_count += 1

    # Atomic write: write to temp file then rename to prevent corruption
    tmp_path = log_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(log_data, indent=2))
    tmp_path.rename(log_path)
    logger.info("Logged %d new verdicts to %s", new_count, log_path)


def generate_brief(processed_dir: Path = None, outputs_dir: Path = None) -> str:
    """Generate the morning brief markdown from all module outputs."""
    pdir = processed_dir or PROCESSED_DIR
    odir = outputs_dir or OUTPUTS_DIR

    # Load all envelopes
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
    congress = load_envelope("congress_tracker.json")
    polymarket = load_envelope("polymarket.json")
    econ_calendar = load_envelope("economic_calendar.json")
    macro = load_envelope("macro_dashboard.json")
    sector_rot = load_envelope("sector_rotation.json")
    unusual_opts = load_envelope("unusual_options.json")
    social_sent = load_envelope("social_sentiment.json")
    last30days = load_envelope("last30days_research.json")
    social_intel = load_envelope("social_intelligence.json")
    dividends = load_envelope("dividend_tracker.json")
    corr_heatmap = load_envelope("correlation_heatmap.json")
    backtester = load_envelope("backtester.json")
    alerts = load_envelope("alert_system.json")

    data_envelope.PROCESSED_DIR = original_dir

    date_str = get_eastern_date()
    modules = {"Journal": journal, "Earnings": earnings, "Valuation": valuation,
               "Portfolio": portfolio, "Technical": technical, "Sentiment": sentiment,
               "Options": options, "Scanner": opportunities,
               "Risk": risk_dashboard, "Scorecard": scorecard,
               "Memory": trade_memory, "Insider": insider, "Sizer": position_sizer,
               "Congress": congress, "Polymarket": polymarket,
               "EconCalendar": econ_calendar, "Macro": macro, "SectorRotation": sector_rot,
               "UnusualOpts": unusual_opts, "SocialSent": social_sent,
               "Dividends": dividends, "CorrHeatmap": corr_heatmap,
               "Backtester": backtester, "Alerts": alerts,
               "Last30Days": last30days, "SocialIntel": social_intel}

    lines = [
        f"# Morning Brief — {date_str}",
        "",
        "## Module Status",
        "",
    ]
    for name, env in modules.items():
        lines.append(f"- {status_icon(env['status'])} **{name}**: {env['status']}")
    lines.append("")

    # Risk Dashboard — after Module Status, BEFORE everything else
    risk_data = risk_dashboard["data"]
    regime = risk_data.get("regime", "UNKNOWN")
    regime_note = risk_data.get("regime_note", "")
    lines.append(f"## Risk Dashboard — {regime}")
    if risk_dashboard["status"] in ("success", "partial"):
        lines.append(regime_note)
        lines.append("")

        risk_icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}

        now_risks = risk_data.get("now_risks", [])
        if now_risks:
            lines.append("### NOW (Today)")
            for r in now_risks:
                icon = risk_icons.get(r["level"], "⚪")
                lines.append(f"- {icon} {r['detail']}")
            lines.append("")

        short_risks = risk_data.get("short_risks", [])
        if short_risks:
            lines.append("### SHORT (This Week)")
            for r in short_risks:
                icon = risk_icons.get(r["level"], "⚪")
                lines.append(f"- {icon} {r['detail']}")
            lines.append("")

        long_risks = risk_data.get("long_risks", [])
        if long_risks:
            lines.append("### LONG (This Month+)")
            for r in long_risks:
                icon = risk_icons.get(r["level"], "⚪")
                lines.append(f"- {icon} {r['detail']}")
            lines.append("")
    else:
        lines.append("*Data unavailable*")
        lines.append("")

    # Economic Calendar
    if econ_calendar["status"] in ("success", "partial"):
        ec = econ_calendar["data"]
        ec_signal = ec.get("signal", "calm").upper()
        lines.append(f"## Economic Calendar — {ec_signal}")
        lines.append(f"*{ec.get('signal_detail', 'No significant events')}*")
        lines.append("")
        events = ec.get("upcoming_events", [])
        if events:
            lines.append("### This Week")
            for ev in events:
                impact_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(ev.get("impact", "low"), "⚪")
                lines.append(f"- {impact_icon} **{ev['name']}** ({ev.get('date', 'TBD')}) — {ev.get('description', '')}")
            lines.append("")
        fomc = ec.get("fomc_next", {})
        if fomc:
            lines.append(f"*Next FOMC: {fomc.get('date', '?')} ({fomc.get('days_until', '?')} days)*")
            lines.append("")
        indicators = ec.get("recent_indicators", {})
        if indicators:
            lines.append("### Key Indicators")
            for name, info in indicators.items():
                val = info.get("value", "N/A")
                dt = info.get("date", "")
                lines.append(f"- **{name}**: {val} ({dt})")
            lines.append("")
    else:
        lines.append("## Economic Calendar")
        lines.append("*Data unavailable*")
        lines.append("")

    # Macro Dashboard
    if macro["status"] in ("success", "partial"):
        md = macro["data"]
        macro_signal = md.get("signal", "mixed").upper()
        lines.append(f"## Macro Dashboard — {macro_signal}")
        lines.append(f"*{md.get('regime_summary', md.get('signal_detail', ''))}*")
        lines.append("")
        for key, ind in md.get("indicators", {}).items():
            val = ind.get("value", "N/A")
            c1d = ind.get("change_1d", 0)
            c5d = ind.get("change_5d", 0)
            trend = ind.get("trend", "flat")
            interp = ind.get("interpretation", "")
            trend_icon = "📈" if trend == "rising" else "📉" if trend == "falling" else "➡️"
            lines.append(f"- {trend_icon} **{key}**: {val} ({c1d:+.1f}% 1d, {c5d:+.1f}% 5d) — {interp}")
        lines.append("")
    else:
        lines.append("## Macro Dashboard")
        lines.append("*Data unavailable*")
        lines.append("")

    # Sector Rotation
    if sector_rot["status"] in ("success", "partial"):
        sr = sector_rot["data"]
        sr_signal = sr.get("signal", "stable").upper()
        rot_type = sr.get("rotation_type", "stable")
        lines.append(f"## Sector Rotation — {sr_signal}")
        lines.append(f"*{sr.get('signal_detail', 'No notable rotation')}*")
        lines.append("")
        leaders = sr.get("leaders_21d", [])
        if leaders:
            lines.append("### Leaders (21d)")
            for l in leaders[:3]:
                lines.append(f"- 🟢 **{l.get('etf', '?')}** ({l.get('sector', '')}) — {l.get('return_21d', 0):+.1f}% (RS: {l.get('relative_strength', 0):+.1f}%)")
            lines.append("")
        laggards = sr.get("laggards_21d", [])
        if laggards:
            lines.append("### Laggards (21d)")
            for l in laggards[:3]:
                lines.append(f"- 🔴 **{l.get('etf', '?')}** ({l.get('sector', '')}) — {l.get('return_21d', 0):+.1f}% (RS: {l.get('relative_strength', 0):+.1f}%)")
            lines.append("")
        breadth = sr.get("breadth", {})
        if breadth:
            pos = breadth.get("positive_sectors", 0)
            neg = breadth.get("negative_sectors", 0)
            lines.append(f"*Breadth: {pos} sectors positive, {neg} negative — {breadth.get('label', 'mixed')}*")
            lines.append("")
    else:
        lines.append("## Sector Rotation")
        lines.append("*Data unavailable*")
        lines.append("")

    # Trading Windows
    windows = get_trading_windows()
    if windows:
        lines.append("## Trading Windows")
        for w in windows:
            status = "OPEN" if w["is_open"] else "CLOSED"
            icon = "🟢" if w["is_open"] else "🔴"
            if w["is_open"]:
                lines.append(f"- {icon} **{w['ticker']}**: Window {status} (closes {w['close']})")
            else:
                lines.append(f"- {icon} **{w['ticker']}**: Window {status} (opens {w['open']})")
            if w["note"]:
                lines.append(f"  - {w['note']}")
        lines.append("")

    # Journal Section
    lines.append("## Trade Journal Summary")
    if journal["status"] in ("success", "partial"):
        jd = journal["data"]
        lines.append(f"- **Total Trades**: {jd.get('total_trades', 'N/A')}")
        wr = jd.get("win_rate")
        lines.append(f"- **Win Rate**: {wr:.1%}" if wr else "- **Win Rate**: N/A")
        patterns = jd.get("patterns", [])
        if patterns:
            lines.append("- **Patterns Detected**:")
            for p in patterns:
                lines.append(f"  - {p.get('detail', str(p))}")
        checklist = jd.get("checklist", [])
        if checklist:
            lines.append("- **Checklist Rules**:")
            for rule in checklist:
                lines.append(f"  - {rule}")
    else:
        lines.append("*Data unavailable*")
    lines.append("")

    # Earnings Section
    lines.append("## Earnings Tone Analysis")
    if earnings["status"] in ("success", "partial"):
        for r in earnings["data"].get("results", []):
            lines.append(f"### {r['ticker']}")
            lines.append(f"- **Tone Score**: {r['tone_score']} (scale: -5 to +5)")
            lines.append(f"- **Confidence**: {r['confidence_score']:.2f}")
            lines.append(f"- **Hedge/Definitive**: {r['hedge_count']} / {r['definitive_count']}")
            if r.get("risk_factors"):
                lines.append("- **Risk Factors**:")
                for rf in r["risk_factors"][:5]:
                    lines.append(f"  - {rf}")
            lines.append(f"- {r.get('summary', '')}")
            lines.append("")
    else:
        lines.append("*Data unavailable*")
    lines.append("")

    # Valuation Section
    lines.append("## Valuation Comparisons")
    if valuation["status"] in ("success", "partial"):
        for comp in valuation["data"].get("comparisons", []):
            lines.append(f"### {comp['stock_a']} vs {comp['stock_b']}")
            lines.append(f"- **Cheaper**: {comp['cheaper']}")
            lines.append(f"- {comp['thesis']}")
            lines.append("")
    else:
        lines.append("*Data unavailable*")
    lines.append("")

    # Portfolio Section
    lines.append("## Portfolio Overview")
    if portfolio["status"] in ("success", "partial"):
        pd_data = portfolio["data"]
        for h in pd_data.get("holdings", []):
            ticker = h["ticker"]
            lines.append(f"### {ticker}")
            price = h.get("current_price")
            lines.append(f"- **Price**: ${price:.2f}" if price else "- **Price**: N/A")
            pnl = h.get("pnl")
            lines.append(f"- **P&L**: ${pnl:+,.2f}" if pnl is not None else "- **P&L**: N/A")
            stop = h.get("trailing_stop")
            lines.append(f"- **Trailing Stop**: ${stop:.2f}" if stop else "- **Trailing Stop**: N/A")
            lp = h.get("locked_profit")
            if lp is not None:
                lines.append(f"- **Locked Profit**: ${lp:+,.2f}")
            lines.append("")

        corr = pd_data.get("correlations", {})
        if corr:
            lines.append("### Correlations")
            for pair, val in corr.items():
                if isinstance(val, dict):
                    c = val.get("correlation")
                    flag = val.get("flag", "")
                    if c is not None:
                        lines.append(f"- {pair}: {c:.2f}" + (" ⚠️ HIGH" if abs(c) >= 0.80 else ""))
                    else:
                        lines.append(f"- {pair}: N/A ({flag})")
                else:
                    lines.append(f"- {pair}: {val}")
            lines.append("")

        total_pnl = pd_data.get("total_pnl")
        if total_pnl is not None:
            lines.append(f"**Total Portfolio P&L: ${total_pnl:+,.2f}**")
            lines.append("")
    else:
        lines.append("*Data unavailable*")
    lines.append("")

    # Options Section
    lines.append("## Options Analysis")
    if options["status"] in ("success", "partial"):
        for t in options["data"].get("tickers", []):
            ticker = t["ticker"]
            if t.get("skipped"):
                lines.append(f"### {ticker} — *Skipped: {t.get('reason', 'N/A')}*")
                lines.append("")
                continue
            lines.append(f"### {ticker}")
            mp = t.get("max_pain")
            lines.append(f"- **Max Pain**: ${mp:.2f}" if mp else "- **Max Pain**: N/A")
            lines.append(f"- **Front Month Expiry**: {t.get('front_month_expiry', 'N/A')}")
            if t.get("pmcc"):
                pmcc = t["pmcc"]
                lines.append(f"- **PMCC Long**: ${pmcc['long_strike']} @ ${pmcc.get('long_price', 'N/A')} (Δ {pmcc.get('long_delta', 'N/A')})")
                lines.append(f"- **PMCC Short**: ${pmcc['short_strike']} @ ${pmcc.get('short_price', 'N/A')} (Δ {pmcc.get('short_delta', 'N/A')})")
                lines.append(f"- **Net Debit**: ${pmcc['net_debit']}")
            top = t.get("top_strikes", [])
            if top:
                lines.append("- **Top Strikes by OI**: " + ", ".join(
                    f"${s['strike']} ({s['total_oi']:,})" for s in top))
            lines.append("")
    else:
        lines.append("*Data unavailable*")
    lines.append("")

    # Verdict Scorecard
    lines.append("## Verdict Scorecard")
    if scorecard["status"] in ("success", "partial"):
        sc = scorecard["data"]
        summary = sc.get("summary", {})
        total_scored = summary.get("total_scored", 0)
        win_rate = summary.get("win_rate", 0)
        lines.append(f"- **Verdicts Evaluated**: {sc.get('evaluated_verdicts', 0)}")
        lines.append(f"- **Total Scored**: {total_scored}")
        if total_scored > 0:
            lines.append(f"- **Win Rate**: {win_rate:.1%}")
            lines.append(f"- **Wins / Losses**: {summary.get('total_wins', 0)} / {summary.get('total_losses', 0)}")
            by_verdict = summary.get("by_verdict", {})
            if by_verdict:
                lines.append("- **By Verdict**:")
                for v, stats in by_verdict.items():
                    if v == "REVIEW":
                        lines.append(f"  - {v}: {stats.get('tracked', 0)} tracked (unscored)")
                    elif stats.get("scored", 0) > 0:
                        lines.append(f"  - {v}: {stats.get('wins', 0)}/{stats['scored']} ({stats.get('win_rate', 0):.1%})")
        else:
            lines.append("- *No windows have elapsed yet — check back in 5 days*")
    else:
        lines.append("*No scorecard data yet — verdicts will be evaluated after first run*")
    lines.append("")

    # Watchlist Verdicts
    lines.append("## Watchlist Verdicts")
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

    insider_data = insider.get("data", {})
    scenario_data = valuation["data"].get("scenarios", [])

    verdict_icons = {"BUY": "🟢", "SELL": "🔴", "AVOID": "🟡", "HOLD": "⚪", "REVIEW": "🔵"}
    for ticker in sorted(all_tickers):
        verdict, reason = determine_verdict(
            ticker, valuation["data"], earnings["data"], portfolio["data"],
            technical["data"], sentiment["data"], risk_data,
            insider_data, scenario_data)
        lines.append(f"- {verdict_icons.get(verdict, '❓')} **{ticker}**: {verdict} — {reason}")
    lines.append("")

    # Position Sizing — after Watchlist Verdicts
    if position_sizer["status"] in ("success", "partial"):
        ps_data = position_sizer["data"]
        positions = [p for p in ps_data.get("positions", []) if p.get("recommended_shares", 0) > 0]
        if positions:
            lines.append("## Position Sizing Recommendations")
            lines.append(f"*Portfolio value: ${ps_data.get('portfolio_value', 0):,.0f} | Risk per trade: {ps_data.get('risk_per_trade_pct', 0):.0%}*")
            lines.append("")
            for p in positions:
                lines.append(f"- **{p['ticker']}**: {p['recommended_shares']} shares (${p['recommended_value']:,.0f}) — stop at ${p['stop_loss']} | {p['note']}")
            lines.append("")

    # Opportunity Scanner — after Position Sizing
    if opportunities["status"] in ("success", "partial"):
        opp_data = opportunities["data"]
        lines.append("## Opportunity Scanner")
        lines.append(f"*Scanned {opp_data.get('universe_size', 0)} tickers outside your watchlist*")
        lines.append("")

        opps = opp_data.get("opportunities", [])
        if opps:
            lines.append("### Top Opportunities (sorted by composite score)")
            for o in opps:
                pe_str = f" | PE: {o['pe_ttm']}" if o.get("pe_ttm") else ""
                lines.append(f"- 🟢 **{o['ticker']}** (composite {o['composite_score']:+.1f}) — {o['reason']}{pe_str} | Risk: {o['risk_note']}")
            lines.append("")

        sector = opp_data.get("sector_read", {})
        if sector:
            sector_names = {"XLK": "Tech", "XLF": "Financials", "XLE": "Energy",
                           "XLV": "Health Care", "XLI": "Industrials", "ARKK": "Innovation"}
            lines.append("### Sector Pulse")
            for etf, data in sector.items():
                name = sector_names.get(etf, etf)
                lines.append(f"- {etf} ({name}): {data['change_1d']:+.1f}% today, {data['change_5d']:+.1f}% this week — {data['trend']}")
            lines.append("")

    # Trade Memory — Pattern Matching
    lines.append("## Trade Memory — Pattern Matching")
    if trade_memory["status"] in ("success", "partial"):
        mem_results = trade_memory["data"].get("results", [])
        notable = [r for r in mem_results if r.get("match_result", {}).get("matches", 0) > 0]
        if notable:
            for r in notable:
                match = r["match_result"]
                confidence = match.get("confidence", "")
                if confidence == "high_win":
                    icon = "🟢"
                elif confidence == "high_loss":
                    icon = "🔴"
                else:
                    icon = "🟡"
                lines.append(f"- {icon} **{r['ticker']}**: {r['signal']}")
        else:
            lines.append("*No matching patterns found in trade history*")
    else:
        lines.append("*Data unavailable*")
    lines.append("")

    # Insider Activity (SEC Form 4)
    lines.append("## Insider Activity (SEC Form 4)")
    if insider["status"] in ("success", "partial"):
        insider_results = insider["data"].get("results", [])
        active = [r for r in insider_results if r.get("transaction_count", 0) > 0]
        if active:
            for r in active:
                icon = "🟢" if r.get("cluster_buy") else "⚪"
                lines.append(f"- {icon} **{r['ticker']}**: {r['detail']}")
                if r.get("cluster_buy"):
                    lines.append(f"  - **CLUSTER BUY detected** — {r['transaction_count']} filings in period")
        else:
            lines.append("*No recent insider activity detected*")
    else:
        lines.append("*Data unavailable*")
    lines.append("")

    # Congressional Trading (STOCK Act / Pelosi Tracker)
    if congress["status"] in ("success", "partial"):
        cdata = congress["data"]
        signal = cdata.get("signal", "no_data")
        signal_detail = cdata.get("signal_detail", "")
        stats = cdata.get("summary_stats", {})

        lines.append("## Congressional Trading (STOCK Act)")
        lines.append(f"*Signal: {signal.upper()} — {signal_detail}*")
        lines.append(f"- **Trades**: {stats.get('total_trades', 0)} ({stats.get('buy_count', 0)} buys, {stats.get('sell_count', 0)} sells)")
        lines.append(f"- **Watchlist Matches**: {stats.get('watchlist_matches', 0)}")
        lines.append("")

        clusters = cdata.get("cluster_signals", [])
        if clusters:
            lines.append("### Cluster Signals")
            for c in clusters:
                icon = "🟢" if c["direction"] == "buy" else "🔴"
                wl = " ⭐ WATCHLIST" if c.get("in_watchlist") else ""
                est = c.get("estimated_total", 0)
                est_str = f" (~${est:,.0f})" if est else ""
                lines.append(f"- {icon} **{c['ticker']}**: {c['politician_count']} politicians {c['direction']}ing{est_str}{wl}")
                lines.append(f"  - Politicians: {', '.join(c.get('politicians', []))}")
            lines.append("")

        wl_trades = cdata.get("watchlist_trades", [])
        if wl_trades:
            lines.append("### Watchlist Trades")
            for t in wl_trades[:10]:
                tx_type = t.get("transaction_type", "")
                icon = "🟢" if "purchase" in tx_type.lower() else "🔴"
                lines.append(f"- {icon} **{t.get('politician', 'Unknown')}** ({t.get('party', '')}) — {tx_type} {t['ticker']} — {t.get('amount_range', '')} — {t.get('transaction_date', '')}")
            lines.append("")

        source = cdata.get("data_source", "unknown")
        lines.append(f"*Source: {source} | STOCK Act requires disclosure within 45 days*")
        lines.append("")

    # Scenario Valuations (Bull / Base / Bear)
    scenarios = valuation["data"].get("scenarios", [])
    if scenarios:
        lines.append("## Scenario Valuations (Bull / Base / Bear)")
        for s in scenarios:
            rr = s.get("risk_reward")
            rr_str = f" | R/R: {rr:.1f}x" if rr else ""
            lines.append(f"### {s['ticker']} (current: ${s['current_price']})")
            lines.append(f"- 🟢 Bull: ${s['bull_price']} ({s['bull_upside']:+.1%})")
            lines.append(f"- ⚪ Base: ${s['base_price']} ({s['base_upside']:+.1%})")
            lines.append(f"- 🔴 Bear: ${s['bear_price']} ({s['bear_downside']:+.1%}){rr_str}")
            lines.append("")

    # Technical Signals
    lines.append("## Technical Signals")
    if technical["status"] in ("success", "partial"):
        for t in technical["data"].get("results", []):
            lines.append(f"### {t['ticker']} (Composite: {t['composite_score']:+.1f})")
            lines.append(f"- **RSI(14)**: {t.get('rsi_14', 'N/A')}")
            lines.append(f"- **MACD**: {t.get('macd_signal', 'N/A')} (histogram: {t.get('macd_histogram', 'N/A')})")
            lines.append(f"- **Bollinger**: {t.get('bb_position', 'N/A')}")
            lines.append(f"- **SMA Trend**: {t.get('sma_trend', 'N/A')}")
            lines.append(f"- **Volume Ratio**: {t.get('volume_ratio', 'N/A')}")
            vwap = t.get("vwap")
            if vwap:
                lines.append(f"- **VWAP**: ${vwap} ({t.get('vwap_signal', 'N/A')})")
            vp = t.get("volume_profile")
            if vp:
                lines.append(f"- **Point of Control**: ${vp['poc']} (strongest support/resistance)")
            lines.append("")
    else:
        lines.append("*Data unavailable*")
    lines.append("")

    # News Sentiment
    lines.append("## News Sentiment")
    if sentiment["status"] in ("success", "partial"):
        for s in sentiment["data"].get("results", []):
            score = s.get("sentiment_score", 0)
            icon = "🟢" if score > 0.2 else "🔴" if score < -0.2 else "🟡"
            lines.append(f"- {icon} **{s['ticker']}**: {score:+.2f} ({s.get('article_count', 0)} articles)")
            if s.get("key_headline"):
                lines.append(f"  - {s['key_headline']}")
            if s.get("summary"):
                lines.append(f"  - {s['summary']}")
    else:
        lines.append("*Data unavailable*")
    lines.append("")

    # Polymarket Prediction Markets
    lines.append("## Polymarket Prediction Markets")
    if polymarket["status"] in ("success", "partial"):
        pm = polymarket["data"]
        signal = pm.get("signal", "no_data")
        lines.append(f"*{pm.get('markets_scanned', 0)} markets scanned — Signal: {signal.upper()}*")
        lines.append("")

        opps = pm.get("opportunities", [])
        if opps:
            lines.append("### Opportunities (by Expected Value)")
            for o in opps[:8]:
                conf_icon = {"high": "🟢", "medium": "🟡", "low": "⚪"}.get(o.get("confidence"), "⚪")
                side = o.get("recommended_side", "?")
                edge = o.get("edge_pct", 0)
                lines.append(f"- {conf_icon} **{o['question'][:80]}**")
                lines.append(f"  - {side} @ {o.get('market_price', 0):.0%} | Edge: {edge:.1f}% | Type: {o.get('opportunity_type', '')} | {o.get('confidence', '').upper()}")
            lines.append("")

        movers = pm.get("big_movers", [])
        if movers:
            lines.append("### Big Movers (24h)")
            for m in movers[:5]:
                icon = "📈" if m.get("move_direction") == "up" else "📉"
                lines.append(f"- {icon} **{m['question'][:70]}** — {m.get('move_pct', 0):+.1f}% (Vol: ${m.get('volume_24h', 0):,.0f})")
            lines.append("")

        # Category summary
        cats = pm.get("category_summary", {})
        if cats:
            cat_line = " | ".join(f"{k}: {v['markets']}" for k, v in sorted(cats.items(), key=lambda x: x[1]['markets'], reverse=True)[:6])
            lines.append(f"*Categories: {cat_line}*")
            lines.append("")

        # Gimme Bets (High Confidence Plays)
        gimmes = pm.get("gimme_bets", [])
        if gimmes:
            lines.append("### High Confidence Plays (Gimme Bets)")
            for g in gimmes[:8]:
                risk_str = f" | Risks: {', '.join(g['risks'])}" if g.get("risks") else ""
                ann_str = f" | Ann. yield: {g['annualized_yield_pct']:.0f}%" if g.get("annualized_yield_pct") else ""
                days_str = f" | {g['days_to_expiry']}d to expiry" if g.get("days_to_expiry") is not None else ""
                lines.append(f"- **{g['question'][:75]}**")
                lines.append(f"  - {g['side']} @ {g['price']:.0%} | Return: {g['raw_return_pct']:.1f}%{days_str}{ann_str}{risk_str}")
            lines.append("")

        # Sports Events
        sports = pm.get("sports_events", [])
        if sports:
            lines.append("### Sports Events")
            for evt in sports[:5]:
                tag = " 🔴 TODAY" if evt["has_today"] else " 📅 This Week" if evt["has_this_week"] else ""
                lines.append(f"**{evt['event']}** ({evt['market_count']} markets, ${evt['total_volume']:,.0f} vol){tag}")
                for sm in evt["top_markets"][:3]:
                    day_flag = " 🔴" if sm.get("is_today") else ""
                    lines.append(f"  - {sm['question'][:70]} — YES {sm['yes_price']:.0%}{day_flag}")
                lines.append("")

        # Economics Signals
        econ_sigs = pm.get("economics_signals", [])
        if econ_sigs:
            lines.append("### Economics Signals (Macro Cross-Reference)")
            for es in econ_sigs:
                direction = es.get("edge_direction", "")
                dir_icon = {"hawkish": "🦅", "dovish": "🕊️", "higher_inflation": "🔥", "lower_inflation": "❄️"}.get(direction, "📊")
                lines.append(f"- {dir_icon} **{es['question'][:70]}** — YES {es['yes_price']:.0%}")
                for sig in es.get("macro_signals", []):
                    lines.append(f"  - {sig}")
            lines.append("")

        # Trending Markets
        trending = pm.get("trending", [])
        if trending:
            lines.append("### Trending Markets")
            for t in trending[:8]:
                icon = "🔥" if t.get("has_momentum") else "📈" if t["direction"] == "up" else "📉"
                mom_label = " MOMENTUM" if t.get("has_momentum") else ""
                lines.append(f"- {icon} **{t['question'][:70]}** — Week: {t['week_change_pct']:+.1f}% | Day: {t['day_change_pct']:+.1f}%{mom_label}")
            lines.append("")

        # Crypto Intelligence
        ci = pm.get("crypto_intelligence", {})
        if ci:
            lines.append("### Crypto Intelligence")
            fng = ci.get("fear_greed", {})
            if fng:
                val = fng.get("value", 0)
                label = fng.get("label", "")
                trend = fng.get("trend", "")
                hist = fng.get("history", [])
                spark = " → ".join(str(h["value"]) for h in reversed(hist[:5])) if hist else ""
                lines.append(f"- **Fear & Greed**: {val} ({label}) — {trend} | 7d: {spark}")

            funding = ci.get("funding_rate", {})
            if funding:
                signal = funding.get("signal", "neutral").replace("_", " ")
                lines.append(f"- **Funding Rate**: {funding.get('current', 0):.4f}% (annualized {funding.get('annualized_pct', 0):+.1f}%) — {signal}")

            btc = ci.get("btc_price", {})
            if btc:
                lines.append(f"- **BTC**: ${btc.get('current', 0):,.0f} | 24h: {btc.get('change_24h_pct', 0):+.2f}% | 7d: {btc.get('change_7d_pct', 0):+.1f}% | 30d: {btc.get('change_30d_pct', 0):+.1f}% | ATH: ${btc.get('ath', 0):,.0f} ({btc.get('ath_drop_pct', 0):+.1f}%)")
            lines.append("")

        # Contradiction Alerts
        contradictions = pm.get("contradiction_alerts", [])
        if contradictions:
            lines.append("### ⚠️ Position Contradiction Alerts")
            for c in contradictions:
                icon = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}.get(c["severity"], "ℹ️")
                lines.append(f"- {icon} **{c['severity'].upper()}**: {c['detail']}")
            lines.append("")

        # Portfolio Tracking
        port = pm.get("portfolio", {})
        if port and port.get("total_positions", 0) > 0:
            pseudo = port.get("pseudonym", "")
            name_str = f" ({pseudo})" if pseudo else ""
            lines.append(f"### Your Polymarket Portfolio{name_str}")
            lines.append(f"- **Active Positions**: {port.get('active_positions', 0)} | **Closed**: {port.get('closed_positions', 0)}")
            lines.append(f"- **Invested**: ${port.get('total_invested', 0):,.2f} | **Current Value**: ${port.get('total_current_value', 0):,.2f}")
            pnl = port.get("total_cash_pnl", 0)
            pnl_icon = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"- {pnl_icon} **P&L**: ${pnl:,.2f} ({port.get('overall_pnl_pct', 0):+.1f}%)")
            lines.append(f"- **Win Rate**: {port.get('win_rate', 0):.1f}% ({port.get('wins', 0)}W / {port.get('losses', 0)}L)")
            lines.append("")

            # Expiring soon
            expiring = port.get("expiring_soon", [])
            if expiring:
                lines.append("**⏰ Expiring Soon**")
                for e in expiring[:5]:
                    day_label = "TODAY" if e["days_left"] == 0 else f"{e['days_left']}d"
                    pnl_e = e.get("pnl", 0)
                    icon_e = "🟢" if pnl_e >= 0 else "🔴"
                    lines.append(f"- [{day_label}] {icon_e} {e['title'][:65]} — {e['outcome']} @ {e['current_price']:.0%} (${pnl_e:+,.0f})")
                lines.append("")

            # Top active positions
            tops = port.get("top_positions", [])
            if tops:
                lines.append("**Top Positions**")
                for t in tops[:5]:
                    pnl_t = t.get("pnl", 0)
                    icon_t = "🟢" if pnl_t >= 0 else "🔴"
                    lines.append(f"- {icon_t} **{t['title'][:60]}** — {t['outcome']} @ {t['current_price']:.0%} | ${t['current_value']:,.0f} ({t['pnl_pct']:+.1f}%)")
                lines.append("")

            # Today's trades
            today = port.get("today_trades", [])
            if today:
                lines.append("**Today's Trades**")
                for tr in today[:8]:
                    lines.append(f"- {tr['side']} {tr['outcome']} — {tr['title'][:55]} | {tr['size']} shares @ {tr['price']:.0%} (${tr['usdc']:,.0f})")
                lines.append("")

            # Category exposure
            exposure = port.get("category_exposure", {})
            if exposure:
                exp_parts = [f"{cat}: ${val:,.0f}" for cat, val in list(exposure.items())[:5]]
                lines.append(f"*Exposure: {' | '.join(exp_parts)}*")
                lines.append("")

        source = pm.get("data_source", "unknown")
        lines.append(f"*Source: {source} | Prediction markets are not financial advice — 80% of participants lose money*")
        lines.append("")
    else:
        lines.append("*Data unavailable*")
        lines.append("")

    # Social Intelligence (last30days v3)
    if social_intel["status"] in ("success", "partial"):
        si = social_intel["data"]
        mood = si.get("overall_mood", "neutral")
        mood_icon = {"bullish": "🟢", "bearish": "🔴", "mixed": "🟡"}.get(mood, "⚪")
        lines.append("## Social Intelligence (Reddit / X / YouTube / HN)")
        lines.append(f"*{mood_icon} Overall social mood: **{mood.upper()}** ({si.get('total_bull_signals', 0)} bull / {si.get('total_bear_signals', 0)} bear signals across {si.get('topics_researched', 0)} topics)*")
        lines.append("")

        for topic_data in si.get("topics", []):
            if topic_data.get("status") != "success":
                continue
            topic = topic_data["topic"]
            sent = topic_data.get("sentiment", "neutral")
            sent_icon = {"bullish": "🟢", "bearish": "🔴", "mixed": "🟡"}.get(sent, "⚪")
            lines.append(f"### {sent_icon} {topic}")
            srcs = topic_data.get("source_counts", {})
            src_str = " | ".join(f"{k}: {v}" for k, v in sorted(srcs.items(), key=lambda x: x[1], reverse=True)[:5])
            lines.append(f"*{topic_data.get('total_results', 0)} results ({src_str})*")
            lines.append("")

            # Top items
            for item in topic_data.get("top_items", [])[:4]:
                engagement = item.get("upvotes", 0)
                eng_str = f" ({engagement:,} upvotes)" if engagement else ""
                lines.append(f"- [{item['source']}] **{item['title'][:70]}**{eng_str}")
                if item.get("snippet"):
                    lines.append(f"  > {item['snippet'][:120]}")

            # X takes (fastest signal — breaks before everything else)
            x_takes = topic_data.get("x_takes", [])
            if x_takes:
                x_vel = topic_data.get("x_velocity", "")
                vel_str = f" | velocity: {x_vel}" if x_vel else ""
                lines.append("")
                lines.append(f"**X/Twitter hot takes{vel_str}:**")
                for xt in x_takes[:3]:
                    author = xt.get("author", "")
                    likes = xt.get("likes", 0)
                    rts = xt.get("reposts", 0)
                    at = f"@{author}" if author else "X"
                    eng = f"{likes:,} likes" + (f", {rts} RT" if rts else "")
                    lines.append(f"- \"{xt['text'][:120]}\" — {at} ({eng})")

            # Reddit takes (the real alpha)
            takes = topic_data.get("reddit_takes", [])
            if takes:
                lines.append("")
                lines.append("**Top Reddit takes:**")
                for take in takes[:3]:
                    score = take.get("score", 0)
                    sub = take.get("subreddit", "")
                    sub_str = f" (r/{sub})" if sub else ""
                    lines.append(f"- \"{take['text'][:120]}\" — {score:,} upvotes{sub_str}")
            lines.append("")

        lines.append(f"*Powered by last30days v{si.get('last30days_version', '3.0.0')} — social relevancy, not SEO relevancy*")
        lines.append("")

    # Alerts
    if alerts["status"] in ("success", "partial"):
        alert_data = alerts["data"]
        alert_list = alert_data.get("alerts", [])
        critical_alerts = [a for a in alert_list if a.get("level") == "critical"]
        high_alerts = [a for a in alert_list if a.get("level") == "high"]
        if critical_alerts or high_alerts:
            lines.append("## ⚠️ Active Alerts")
            for a in critical_alerts:
                lines.append(f"- 🚨 **CRITICAL**: {a.get('message', '')}")
            for a in high_alerts:
                lines.append(f"- 🔴 **HIGH**: {a.get('message', '')}")
            summary = alert_data.get("summary", {})
            med = summary.get("medium", 0)
            low = summary.get("low", 0)
            if med or low:
                lines.append(f"- *{med} medium, {low} low alerts (see alert_system.json)*")
            lines.append("")

    # Unusual Options Activity
    lines.append("## Unusual Options Activity")
    if unusual_opts["status"] in ("success", "partial"):
        uo_results = unusual_opts["data"].get("results", [])
        active = [r for r in uo_results if r.get("unusual_strikes")]
        if active:
            for r in active:
                sentiment_icon = "🟢" if r.get("net_sentiment") == "bullish" else "🔴" if r.get("net_sentiment") == "bearish" else "🟡"
                lines.append(f"- {sentiment_icon} **{r['ticker']}**: {r.get('call_sweep_count', 0)} call sweeps, {r.get('put_sweep_count', 0)} put sweeps — {r.get('net_sentiment', 'mixed')}")
                largest = r.get("largest_trade", {})
                if largest:
                    lines.append(f"  - Largest: ${largest.get('strike', '?')} {largest.get('type', '?')} — ${largest.get('notional', 0):,.0f} notional")
        else:
            lines.append("*No unusual activity detected*")
    else:
        lines.append("*Data unavailable*")
    lines.append("")

    # Social Sentiment
    lines.append("## Social Sentiment")
    if social_sent["status"] in ("success", "partial"):
        ss_results = social_sent["data"].get("results", [])
        market_summary = social_sent["data"].get("market_summary", {})
        if market_summary:
            mood = market_summary.get("mood_label", "neutral")
            lines.append(f"*Market mood: {mood} | Most discussed: {market_summary.get('most_discussed', 'N/A')}*")
            lines.append("")
        trending = [r for r in ss_results if r.get("trending")]
        if trending:
            for r in trending:
                score = r.get("sentiment_score", 0)
                icon = "🟢" if score > 0.2 else "🔴" if score < -0.2 else "🟡"
                lines.append(f"- {icon} **{r['ticker']}** (buzz: {r.get('buzz_score', 0)}) — sentiment {score:+.2f} ({r.get('sentiment_trend', 'stable')})")
        else:
            lines.append("*No trending tickers*")
    else:
        lines.append("*Data unavailable*")
    lines.append("")

    # Last30Days Community Research
    if last30days["status"] in ("success", "partial"):
        l30_data = last30days["data"]
        highlights = l30_data.get("highlights", [])
        if highlights:
            lines.append("## Community Intelligence (Last 30 Days)")
            lines.append(f"*Sources: Reddit, X, HN, Polymarket | Topics researched: {l30_data.get('research_count', 0)}*")
            lines.append("")
            for h in highlights:
                topic = h.get("topic", "")
                hl = h.get("highlights", {})
                lines.append(f"### {topic}")

                # Polymarket odds
                for pm in hl.get("polymarket_odds", [])[:3]:
                    lines.append(f"- 🎲 **Polymarket**: {pm['title']} — {pm['odds']}")

                # High-engagement sentiment signals
                for sig in hl.get("sentiment_signals", [])[:5]:
                    lines.append(f"- 📊 **{sig['source']}** ({sig['engagement']} engagement): {sig['title']}")

                # Top stories
                for story in hl.get("top_stories", [])[:3]:
                    lines.append(f"- 📰 **{story['source']}**: {story['title']}")

                # Best takes
                for take in hl.get("best_takes", [])[:2]:
                    lines.append(f"- 💬 *\"{take}\"*")

                lines.append("")

        # Include context markdown if available (compact summary)
        context_md = l30_data.get("context_markdown", "")
        if context_md and len(context_md) > 50:
            lines.append("### Research Context")
            # Truncate to keep brief readable
            for line in context_md.split("\n")[:20]:
                lines.append(line)
            lines.append("")
    lines.append("")

    # Dividend Tracker
    if dividends["status"] in ("success", "partial"):
        div_results = dividends["data"].get("results", [])
        div_summary = dividends["data"].get("portfolio_summary", {})
        upcoming = div_summary.get("upcoming_ex_dates", [])
        if upcoming or div_summary.get("total_annual_income", 0) > 0:
            lines.append("## Dividend Tracker")
            income = div_summary.get("total_annual_income", 0)
            if income > 0:
                lines.append(f"*Annual dividend income: ${income:,.2f} | Avg yield: {div_summary.get('avg_yield', 0):.2%}*")
                lines.append("")
            if upcoming:
                lines.append("### Upcoming Ex-Dates")
                for u in upcoming:
                    lines.append(f"- **{u['ticker']}**: {u['ex_date']} ({u['days']} days)")
                lines.append("")
            cuts = [r for r in div_results if r.get("dividend_cut")]
            if cuts:
                lines.append("### ⚠️ Dividend Cuts Detected")
                for c in cuts:
                    lines.append(f"- 🔴 **{c['ticker']}**: dividend cut detected")
                lines.append("")

    # Backtester
    if backtester["status"] in ("success", "partial"):
        bt_data = backtester["data"]
        bt_summary = bt_data.get("summary", {})
        if bt_summary.get("total_pnl") is not None:
            lines.append("## Strategy Backtest")
            lines.append(f"*Simulated P&L: ${bt_summary['total_pnl']:+,.2f} | Win rate: {bt_summary.get('win_rate', 0):.1%} | Alpha: {bt_summary.get('alpha_vs_spy', 0):+.3f} | Sharpe: {bt_summary.get('sharpe_5d', 0):.2f}*")
            lines.append("")
            by_verdict = bt_data.get("by_verdict", {})
            for v, stats in by_verdict.items():
                if stats.get("count", 0) > 0 and v != "HOLD":
                    lines.append(f"- **{v}**: {stats.get('wins', 0)}/{stats['count']} wins (${stats.get('total_pnl', 0):+,.0f})")
            best = bt_summary.get("best_trade", {})
            worst = bt_summary.get("worst_trade", {})
            if best:
                lines.append(f"- Best: {best.get('ticker', '?')} {best.get('verdict', '?')} +{best.get('return_5d', 0):.1%}")
            if worst:
                lines.append(f"- Worst: {worst.get('ticker', '?')} {worst.get('verdict', '?')} {worst.get('return_5d', 0):.1%}")
            lines.append("")

    # Correlation Heatmap link
    if corr_heatmap["status"] in ("success", "partial"):
        ch_data = corr_heatmap["data"]
        high_corrs = ch_data.get("high_correlations", [])
        clusters = ch_data.get("risk_clusters", [])
        if high_corrs or clusters:
            lines.append("## Correlation Risk")
            if clusters:
                for cl in clusters:
                    lines.append(f"- ⚠️ **Risk cluster**: {', '.join(cl.get('tickers', []))} (avg corr: {cl.get('avg_corr', 0):.2f})")
            if high_corrs:
                for hc in high_corrs[:5]:
                    lines.append(f"- {hc.get('pair', '?')}: {hc.get('corr', 0):.2f}")
            lines.append(f"*Full heatmap: data/outputs/correlation_heatmap_{date_str}.html*")
            lines.append("")

    # Disclaimer
    lines.append("---")
    lines.append("*This is analysis, not financial advice. All trading decisions are yours.*")
    lines.append("")

    return "\n".join(lines)


def main():
    setup_logging()
    logger.info("=== Morning Brief Module ===")

    brief = generate_brief()

    odir = OUTPUTS_DIR
    odir.mkdir(parents=True, exist_ok=True)
    date_str = get_eastern_date()
    output_path = odir / f"morning_brief_{date_str}.md"
    output_path.write_text(brief)
    logger.info("Morning brief written to %s", output_path)

    # Log today's verdicts for future scorecard evaluation
    try:
        log_verdicts()
    except Exception as e:
        logger.warning("Failed to log verdicts: %s", e)


if __name__ == "__main__":
    main()
