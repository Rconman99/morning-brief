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
                      sentiment_data: dict = None) -> tuple[str, str]:
    """Determine BUY/SELL/AVOID/HOLD/REVIEW verdict using all data sources.

    Rules (evaluated in order):
    - SELL: trailing stop within 5% of price OR technical composite < -3
    - AVOID: news sentiment < -0.5 OR (technical composite < -2 AND earnings tone < -1)
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
    # Trailing stop within 5% of current price
    for h in holdings:
        if h.get("ticker") == ticker:
            stop = h.get("trailing_stop")
            price = h.get("current_price")
            if stop and price and price > 0:
                if stop / price > 0.95:
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
                            return "BUY", f"Strong technicals (composite {composite:.1f}) with positive sentiment and cheaper on {wins} metrics"
            if not in_comparison:
                return "BUY", f"Strong technicals (composite {composite:.1f}) with positive sentiment"

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

    data_envelope.PROCESSED_DIR = original_dir

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
            technical["data"], sentiment["data"])
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
    scorecard = load_envelope("scorecard.json")

    data_envelope.PROCESSED_DIR = original_dir

    date_str = get_eastern_date()
    modules = {"Journal": journal, "Earnings": earnings, "Valuation": valuation,
               "Portfolio": portfolio, "Technical": technical, "Sentiment": sentiment,
               "Options": options, "Scorecard": scorecard}

    lines = [
        f"# Morning Brief — {date_str}",
        "",
        "## Module Status",
        "",
    ]
    for name, env in modules.items():
        lines.append(f"- {status_icon(env['status'])} **{name}**: {env['status']}")
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

    verdict_icons = {"BUY": "🟢", "SELL": "🔴", "AVOID": "🟡", "HOLD": "⚪", "REVIEW": "🔵"}
    for ticker in sorted(all_tickers):
        verdict, reason = determine_verdict(
            ticker, valuation["data"], earnings["data"], portfolio["data"],
            technical["data"], sentiment["data"])
        lines.append(f"- {verdict_icons.get(verdict, '❓')} **{ticker}**: {verdict} — {reason}")
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
