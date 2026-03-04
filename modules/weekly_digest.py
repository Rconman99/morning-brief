"""Weekly Digest: Roll up the week's briefs and module data into a comprehensive summary."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
import re
from datetime import datetime, timedelta
from collections import defaultdict
import pandas as pd

from lib.data_envelope import create_envelope, save_envelope, load_envelope
from lib.api import yahoo_finance_price_history

logger = logging.getLogger(__name__)

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "data" / "outputs"
SCORECARD_DIR = PROJECT_ROOT / "data" / "scorecard"


def setup_logging():
    """Initialize logging."""
    outputs_dir = OUTPUTS_DIR
    outputs_dir.mkdir(parents=True, exist_ok=True)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(outputs_dir / "run.log", mode="a"),
            ],
        )


def get_eastern_date() -> str:
    """Get current date in US Eastern timezone."""
    try:
        from zoneinfo import ZoneInfo
        eastern = ZoneInfo("America/New_York")
        return datetime.now(eastern).strftime("%Y-%m-%d")
    except ImportError:
        from datetime import timezone, timedelta as td
        eastern = timezone(td(hours=-5))
        return datetime.now(eastern).strftime("%Y-%m-%d")


def get_week_dates() -> tuple[str, str]:
    """Return (start_date, end_date) for the past 7 days (Sunday-Saturday window)."""
    today = datetime.strptime(get_eastern_date(), "%Y-%m-%d")
    # Start from 6 days ago
    start = (today - timedelta(days=6)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    return start, end


def get_morning_briefs_this_week() -> list[tuple[str, str]]:
    """Scan data/outputs/ for morning_brief_*.md files from the past 7 days.
    Return list of (date_str, file_path_str) tuples."""
    start_date, end_date = get_week_dates()
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    briefs = []
    if not OUTPUTS_DIR.exists():
        return briefs

    for brief_file in OUTPUTS_DIR.glob("morning_brief_*.md"):
        # Extract date from filename morning_brief_YYYY-MM-DD.md
        match = re.search(r"morning_brief_(\d{4}-\d{2}-\d{2})", brief_file.name)
        if not match:
            continue
        date_str = match.group(1)
        try:
            brief_dt = datetime.strptime(date_str, "%Y-%m-%d")
            if start_dt <= brief_dt <= end_dt:
                briefs.append((date_str, str(brief_file)))
        except ValueError:
            continue

    briefs.sort()
    return briefs


def calculate_portfolio_performance(holdings: list[dict]) -> dict:
    """Calculate weekly price changes and P&L changes for holdings.
    
    Returns:
    {
        "weekly_summary": {
            "total_value_start": float,
            "total_value_end": float,
            "total_pnl": float,
            "total_pnl_pct": float,
            "best_performer": {"ticker": str, "pnl_pct": float},
            "worst_performer": {"ticker": str, "pnl_pct": float}
        },
        "holdings": [
            {
                "ticker": str,
                "shares": float,
                "start_price": float,
                "end_price": float,
                "price_change_pct": float,
                "pnl_change": float,
                "current_pnl": float
            }
        ]
    }
    """
    start_date, end_date = get_week_dates()
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    results = {"weekly_summary": {}, "holdings": []}
    ticker_performances = []
    total_value_start = 0
    total_value_end = 0

    for holding in holdings:
        ticker = holding.get("ticker")
        shares = holding.get("shares", 0)
        if not ticker or shares <= 0:
            continue

        # Get 1-week of price data
        df = yahoo_finance_price_history(ticker, period="2y")
        if df.empty:
            logger.warning("No price data for %s", ticker)
            continue

        # Find prices at start and end of week
        start_price = None
        end_price = None

        for idx, row in df.iterrows():
            # Handle both datetime index and regular index
            try:
                if hasattr(idx, 'date'):
                    row_date = idx.date()
                else:
                    row_date = pd.Timestamp(idx).date()
            except Exception:
                continue

            if start_price is None and row_date >= start_dt.date():
                start_price = float(row.get("Close", row.get("close")))
            if row_date <= end_dt.date():
                end_price = float(row.get("Close", row.get("close")))

        if start_price is None and not df.empty:
            start_price = float(df.iloc[0].get("Close", df.iloc[0].get("close")))
        if end_price is None and not df.empty:
            end_price = float(df.iloc[-1].get("Close", df.iloc[-1].get("close")))

        if start_price is None or end_price is None:
            continue

        # Calculate current P&L if available
        current_pnl = holding.get("current_pnl", 0)
        cost_basis = holding.get("cost_basis", 0)
        if cost_basis > 0 and "current_pnl" not in holding:
            current_pnl = (end_price - cost_basis) * shares

        # Calculate metrics
        price_change_pct = ((end_price - start_price) / start_price * 100) if start_price else 0
        start_value = start_price * shares
        end_value = end_price * shares
        pnl_change = end_value - start_value

        total_value_start += start_value
        total_value_end += end_value

        holding_result = {
            "ticker": ticker,
            "shares": shares,
            "start_price": round(start_price, 2),
            "end_price": round(end_price, 2),
            "price_change_pct": round(price_change_pct, 2),
            "pnl_change": round(pnl_change, 2),
            "current_pnl": round(current_pnl, 2),
        }
        results["holdings"].append(holding_result)
        ticker_performances.append((ticker, price_change_pct))

    # Summary stats
    total_pnl = total_value_end - total_value_start
    total_pnl_pct = ((total_pnl / total_value_start) * 100) if total_value_start > 0 else 0

    best_perf = max(ticker_performances, key=lambda x: x[1], default=(None, 0))
    worst_perf = min(ticker_performances, key=lambda x: x[1], default=(None, 0))

    results["weekly_summary"] = {
        "total_value_start": round(total_value_start, 2),
        "total_value_end": round(total_value_end, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "best_performer": {"ticker": best_perf[0], "pnl_pct": round(best_perf[1], 2)} if best_perf[0] else {},
        "worst_performer": {"ticker": worst_perf[0], "pnl_pct": round(worst_perf[1], 2)} if worst_perf[0] else {},
    }

    return results


def calculate_verdict_scorecard() -> dict:
    """Analyze verdict_log.json and calculate accuracy for the week.
    
    Returns:
    {
        "total_verdicts": int,
        "verdict_breakdown": {"BUY": int, "SELL": int, "HOLD": int, "AVOID": int, "REVIEW": int},
        "verdict_flips": list[{date, ticker, old_verdict, new_verdict}],
        "accuracy": {
            "BUY": {"issued": int, "correct": int, "accuracy_pct": float},
            ...
        }
    }
    """
    scorecard_file = SCORECARD_DIR / "verdict_log.json"
    if not scorecard_file.exists():
        logger.warning("verdict_log.json not found")
        return {
            "total_verdicts": 0,
            "verdict_breakdown": {},
            "verdict_flips": [],
            "accuracy": {},
        }

    try:
        log_data = json.loads(scorecard_file.read_text())
        entries = log_data.get("entries", [])
    except Exception as e:
        logger.error("Error reading verdict_log.json: %s", e)
        return {
            "total_verdicts": 0,
            "verdict_breakdown": {},
            "verdict_flips": [],
            "accuracy": {},
        }

    start_date, end_date = get_week_dates()
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    # Filter entries for the week
    week_entries = []
    for entry in entries:
        entry_date = entry.get("date", "")
        try:
            entry_dt = datetime.strptime(entry_date, "%Y-%m-%d")
            if start_dt <= entry_dt <= end_dt:
                week_entries.append(entry)
        except ValueError:
            continue

    # Count verdicts by type
    verdict_breakdown = defaultdict(int)
    for entry in week_entries:
        verdict = entry.get("verdict", "HOLD").upper()
        verdict_breakdown[verdict] += 1

    # Detect verdict flips (same ticker, different verdict on consecutive days)
    ticker_verdicts = defaultdict(lambda: [])
    for entry in sorted(week_entries, key=lambda x: (x.get("ticker"), x.get("date"))):
        ticker = entry.get("ticker")
        verdict = entry.get("verdict", "HOLD").upper()
        ticker_verdicts[ticker].append({"date": entry.get("date"), "verdict": verdict})

    verdict_flips = []
    for ticker, verdicts_list in ticker_verdicts.items():
        for i in range(len(verdicts_list) - 1):
            if verdicts_list[i]["verdict"] != verdicts_list[i + 1]["verdict"]:
                verdict_flips.append({
                    "date": verdicts_list[i + 1]["date"],
                    "ticker": ticker,
                    "old_verdict": verdicts_list[i]["verdict"],
                    "new_verdict": verdicts_list[i + 1]["verdict"],
                })

    # TODO: Calculate accuracy by comparing 5-day returns after verdict to verdict signal
    # For now, return empty accuracy
    accuracy = {v: {"issued": verdict_breakdown.get(v, 0), "correct": 0, "accuracy_pct": 0}
                for v in ["BUY", "SELL", "HOLD", "AVOID", "REVIEW"]}

    return {
        "total_verdicts": len(week_entries),
        "verdict_breakdown": dict(verdict_breakdown),
        "verdict_flips": verdict_flips,
        "accuracy": accuracy,
    }


def extract_key_events() -> list[dict]:
    """Extract key events from morning briefs during the week.
    
    Returns list of {date, event_type, description} dicts.
    """
    briefs = get_morning_briefs_this_week()
    events = []

    for date_str, brief_path in briefs:
        try:
            content = Path(brief_path).read_text()

            # Extract Key Events section
            if "## Key Events" in content or "## 🔥 Key Events" in content:
                match = re.search(r"## (🔥 )?Key Events(.*?)(?=##|$)", content, re.DOTALL)
                if match:
                    events_text = match.group(2).strip()
                    for line in events_text.split("\n"):
                        line = line.strip()
                        if line.startswith("-") or line.startswith("•"):
                            line = line.lstrip("-•").strip()
                            if line:
                                events.append({
                                    "date": date_str,
                                    "type": "brief_event",
                                    "description": line,
                                })

            # Extract notable patterns (revenge trading, stop breaches, etc.)
            if "Stop within 3%" in content or "stop breach" in content.lower():
                events.append({
                    "date": date_str,
                    "type": "stop_breach",
                    "description": "Trailing stop near price",
                })

        except Exception as e:
            logger.warning("Error parsing brief %s: %s", brief_path, e)

    return events


def extract_upcoming_catalysts() -> dict:
    """Extract upcoming events for next week from current module data.
    
    Returns {
        "earnings_dates": list[{ticker, date}],
        "options_expiry": list[{ticker, expiry_date}],
        "stops_at_risk": list[{ticker, trigger_price}],
        "economic_events": list[{date, description}]
    }
    """
    catalysts = {
        "earnings_dates": [],
        "options_expiry": [],
        "stops_at_risk": [],
        "economic_events": [],
    }

    # Try to load portfolio data for stops
    portfolio_env = load_envelope("portfolio.json")
    if portfolio_env.get("status") == "success":
        holdings = portfolio_env.get("data", {}).get("holdings", [])
        for h in holdings:
            ticker = h.get("ticker")
            trailing_stop = h.get("trailing_stop")
            current_price = h.get("current_price")
            if ticker and trailing_stop and current_price:
                pct_to_stop = ((current_price - trailing_stop) / current_price) * 100
                if pct_to_stop <= 5:
                    catalysts["stops_at_risk"].append({
                        "ticker": ticker,
                        "trigger_price": round(trailing_stop, 2),
                        "pct_to_stop": round(pct_to_stop, 2),
                    })

    # Try to load options data for expirations
    options_env = load_envelope("options.json")
    if options_env.get("status") == "success":
        tickers = options_env.get("data", {}).get("tickers", [])
        for t in tickers:
            ticker = t.get("ticker")
            expirations = t.get("expirations", [])
            if ticker and expirations:
                # Pick the nearest one
                nearest = expirations[0] if isinstance(expirations, list) else None
                if nearest:
                    catalysts["options_expiry"].append({
                        "ticker": ticker,
                        "expiry_date": nearest,
                    })

    return catalysts


def generate_markdown(week_data: dict) -> str:
    """Generate formatted markdown for the weekly digest.
    
    Args:
        week_data: dict with portfolio, verdict, and other analyses
    
    Returns:
        markdown string
    """
    start_date, end_date = get_week_dates()
    week_label = f"Week of {start_date} to {end_date}"

    md_parts = [
        f"# Weekly Digest — {week_label}\n",
        f"*Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n",
    ]

    # Week Summary
    md_parts.append("## Week Summary\n")
    briefs = get_morning_briefs_this_week()
    md_parts.append(f"- **Briefs generated**: {len(briefs)}\n")
    md_parts.append(f"- **Date range**: {start_date} to {end_date}\n")

    summary = week_data.get("portfolio_performance", {}).get("weekly_summary", {})
    if summary:
        total_pnl = summary.get("total_pnl", 0)
        total_pnl_pct = summary.get("total_pnl_pct", 0)
        md_parts.append(f"- **Portfolio P&L**: ${total_pnl:,.2f} ({total_pnl_pct:+.2f}%)\n")

    md_parts.append("\n")

    # Portfolio Performance
    md_parts.append("## Portfolio Performance\n")
    holdings = week_data.get("portfolio_performance", {}).get("holdings", [])
    if holdings:
        md_parts.append("| Ticker | Start | End | Change | Weekly P&L |\n")
        md_parts.append("|--------|-------|-----|--------|------------|\n")
        for h in holdings:
            ticker = h.get("ticker", "")
            start_p = h.get("start_price", 0)
            end_p = h.get("end_price", 0)
            change = h.get("price_change_pct", 0)
            pnl = h.get("pnl_change", 0)
            md_parts.append(f"| {ticker} | ${start_p:.2f} | ${end_p:.2f} | {change:+.2f}% | ${pnl:,.2f} |\n")
    else:
        md_parts.append("No holdings data available.\n")
    md_parts.append("\n")

    # Verdict Scorecard
    md_parts.append("## Verdict Accuracy\n")
    scorecard = week_data.get("verdict_scorecard", {})
    total_verdicts = scorecard.get("total_verdicts", 0)
    breakdown = scorecard.get("verdict_breakdown", {})
    md_parts.append(f"- **Total verdicts issued**: {total_verdicts}\n")
    for verdict_type in ["BUY", "SELL", "HOLD", "AVOID", "REVIEW"]:
        count = breakdown.get(verdict_type, 0)
        if count > 0:
            md_parts.append(f"- **{verdict_type}**: {count}\n")

    flips = scorecard.get("verdict_flips", [])
    if flips:
        md_parts.append(f"\n**Verdict Flips This Week**: {len(flips)}\n")
        for flip in flips[:5]:  # Show top 5
            date = flip.get("date", "")
            ticker = flip.get("ticker", "")
            old_v = flip.get("old_verdict", "")
            new_v = flip.get("new_verdict", "")
            md_parts.append(f"- {date} {ticker}: {old_v} → {new_v}\n")

    md_parts.append("\n")

    # Key Events
    md_parts.append("## Key Events This Week\n")
    events = extract_key_events()
    if events:
        for event in events[:10]:  # Show top 10
            date = event.get("date", "")
            desc = event.get("description", "")
            md_parts.append(f"- **{date}**: {desc}\n")
    else:
        md_parts.append("No major events logged.\n")
    md_parts.append("\n")

    # What to Watch Next Week
    md_parts.append("## What to Watch Next Week\n")
    catalysts = extract_upcoming_catalysts()

    stops = catalysts.get("stops_at_risk", [])
    if stops:
        md_parts.append("**Stops at Risk**:\n")
        for stop in stops:
            ticker = stop.get("ticker", "")
            trigger = stop.get("trigger_price", 0)
            pct = stop.get("pct_to_stop", 0)
            md_parts.append(f"- {ticker}: ${trigger:.2f} ({pct:.1f}% away)\n")
        md_parts.append("\n")

    expirations = catalysts.get("options_expiry", [])
    if expirations:
        md_parts.append("**Options Expiring**:\n")
        for exp in expirations[:5]:
            ticker = exp.get("ticker", "")
            expiry = exp.get("expiry_date", "")
            md_parts.append(f"- {ticker}: {expiry}\n")
        md_parts.append("\n")

    md_parts.append("---\n")
    md_parts.append("*Trading Intelligence System | Generated weekly digest*\n")
    md_parts.append("*This is analysis, not financial advice. All trading decisions are yours.*\n")

    return "".join(md_parts)


def main():
    """Main entry point."""
    setup_logging()
    logger.info("=== Weekly Digest Module ===")

    # Load current portfolio for performance calculation
    portfolio_path = PROJECT_ROOT / "config" / "portfolio.json"
    if not portfolio_path.exists():
        logger.error("portfolio.json not found")
        envelope = create_envelope("weekly_digest", {}, status="error", error="portfolio.json not found")
        save_envelope(envelope, "weekly_digest.json")
        return

    try:
        portfolio = json.loads(portfolio_path.read_text())
        holdings = portfolio.get("holdings", [])
    except Exception as e:
        logger.error("Error reading portfolio: %s", e)
        envelope = create_envelope("weekly_digest", {}, status="error", error=str(e))
        save_envelope(envelope, "weekly_digest.json")
        return

    # Collect analyses
    analyses = {}

    try:
        analyses["portfolio_performance"] = calculate_portfolio_performance(holdings)
        logger.info("Calculated portfolio performance")
    except Exception as e:
        logger.error("Portfolio performance calculation failed: %s", e)
        analyses["portfolio_performance"] = {"holdings": [], "weekly_summary": {}}

    try:
        analyses["verdict_scorecard"] = calculate_verdict_scorecard()
        logger.info("Calculated verdict scorecard")
    except Exception as e:
        logger.error("Verdict scorecard calculation failed: %s", e)
        analyses["verdict_scorecard"] = {}

    try:
        analyses["key_events"] = extract_key_events()
        logger.info("Extracted key events")
    except Exception as e:
        logger.error("Key events extraction failed: %s", e)
        analyses["key_events"] = []

    try:
        analyses["upcoming_catalysts"] = extract_upcoming_catalysts()
        logger.info("Extracted upcoming catalysts")
    except Exception as e:
        logger.error("Catalyst extraction failed: %s", e)
        analyses["upcoming_catalysts"] = {}

    # Generate markdown
    try:
        markdown = generate_markdown(analyses)
        end_date = get_eastern_date()
        outputs_dir = OUTPUTS_DIR
        outputs_dir.mkdir(parents=True, exist_ok=True)
        md_path = outputs_dir / f"weekly_digest_{end_date}.md"
        md_path.write_text(markdown)
        logger.info("Wrote markdown to %s", md_path)
    except Exception as e:
        logger.error("Markdown generation failed: %s", e)

    # Create envelope and save
    status = "success" if analyses.get("portfolio_performance", {}).get("holdings") else "partial"
    envelope = create_envelope("weekly_digest", analyses, status=status)
    save_envelope(envelope, "weekly_digest.json")
    logger.info("Weekly digest complete")


if __name__ == "__main__":
    main()
