"""Morning Brief generator: aggregates all module outputs into a daily markdown report."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import datetime

from lib.data_envelope import load_envelope

logger = logging.getLogger(__name__)

# Allow override for testing
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


def status_icon(status: str) -> str:
    icons = {"success": "✅", "partial": "⚠️", "error": "❌", "missing": "⬜"}
    return icons.get(status, "❓")


def determine_verdict(ticker: str, valuation_data: dict, earnings_data: dict,
                      portfolio_data: dict) -> tuple[str, str]:
    """Determine BUY/SELL/AVOID/HOLD/REVIEW verdict using exact rules from spec."""
    val_comparisons = valuation_data.get("comparisons", [])
    earnings_results = earnings_data.get("results", [])
    holdings = portfolio_data.get("holdings", [])

    # Check SELL: trailing stop within 5% of current price
    for h in holdings:
        if h.get("ticker") == ticker:
            stop = h.get("trailing_stop")
            price = h.get("current_price")
            if stop and price and price > 0:
                if stop / price > 0.95:
                    return "SELL", f"Trailing stop ${stop:.2f} is within 5% of current price ${price:.2f}"

    # Get earnings tone for this ticker
    tone_score = None
    for e in earnings_results:
        if e.get("ticker") == ticker or e.get("ticker") == f"SAMPLE_{ticker}":
            tone_score = e.get("tone_score")
            break

    # Check AVOID: tone_score <= -2 OR more expensive on 4+ metrics
    if tone_score is not None and tone_score <= -2:
        return "AVOID", f"Earnings tone score is {tone_score} (≤ -2)"

    expensive_count = 0
    for comp in val_comparisons:
        if comp.get("stock_a") == ticker and comp.get("cheaper") == comp.get("stock_b"):
            expensive_count = comp.get("a_wins", 0)
            total_comparable = comp.get("comparable_metrics", 0)
            if total_comparable - expensive_count >= 4:
                return "AVOID", f"More expensive than {comp['stock_b']} on 4+ metrics"
        elif comp.get("stock_b") == ticker and comp.get("cheaper") == comp.get("stock_a"):
            expensive_count = comp.get("b_wins", 0)
            total_comparable = comp.get("comparable_metrics", 0)
            if total_comparable - expensive_count >= 4:
                return "AVOID", f"More expensive than {comp['stock_a']} on 4+ metrics"

    # Check BUY: cheaper on 3+ metrics AND tone > 0 (or no earnings data)
    for comp in val_comparisons:
        if comp.get("stock_a") == ticker and comp.get("cheaper") == ticker:
            if comp.get("a_wins", 0) >= 3:
                if tone_score is None or tone_score > 0:
                    return "BUY", f"Cheaper than {comp['stock_b']} on {comp['a_wins']} metrics with positive/neutral earnings tone"
        elif comp.get("stock_b") == ticker and comp.get("cheaper") == ticker:
            if comp.get("b_wins", 0) >= 3:
                if tone_score is None or tone_score > 0:
                    return "BUY", f"Cheaper than {comp['stock_a']} on {comp['b_wins']} metrics with positive/neutral earnings tone"

    # Check REVIEW: insufficient data (valuation and portfolio both missing)
    has_valuation = len(val_comparisons) > 0
    has_portfolio = len(holdings) > 0
    if not has_valuation and not has_portfolio:
        return "REVIEW", "Insufficient data to make any determination"

    return "HOLD", "Default — no strong signal in either direction"


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
    options = load_envelope("options.json")

    data_envelope.PROCESSED_DIR = original_dir

    date_str = get_eastern_date()
    modules = {"Journal": journal, "Earnings": earnings, "Valuation": valuation,
               "Portfolio": portfolio, "Options": options}

    lines = [
        f"# Morning Brief — {date_str}",
        "",
        "## Module Status",
        "",
    ]
    for name, env in modules.items():
        lines.append(f"- {status_icon(env['status'])} **{name}**: {env['status']}")
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

    # Watchlist Verdicts
    lines.append("## Watchlist Verdicts")
    all_tickers = set()
    for comp in valuation["data"].get("comparisons", []):
        all_tickers.add(comp.get("stock_a", ""))
        all_tickers.add(comp.get("stock_b", ""))
    for h in portfolio["data"].get("holdings", []):
        all_tickers.add(h.get("ticker", ""))
    all_tickers.discard("")

    verdict_icons = {"BUY": "🟢", "SELL": "🔴", "AVOID": "🟡", "HOLD": "⚪", "REVIEW": "🔵"}
    for ticker in sorted(all_tickers):
        verdict, reason = determine_verdict(
            ticker, valuation["data"], earnings["data"], portfolio["data"])
        lines.append(f"- {verdict_icons.get(verdict, '❓')} **{ticker}**: {verdict} — {reason}")
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


if __name__ == "__main__":
    main()
