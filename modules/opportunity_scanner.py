"""Opportunity Scanner: scans a broader universe of tickers to surface potential trades."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging

from lib.data_envelope import create_envelope, save_envelope
from lib.api import yahoo_finance_price_history, yahoo_finance_info
from modules.technical_signals import analyze_ticker

logger = logging.getLogger(__name__)


def setup_logging():
    """Call this ONLY inside main(). NEVER at module level."""
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


def load_scan_config() -> dict:
    """Load scan universe configuration."""
    config_path = PROJECT_ROOT / "config" / "scan_universe.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to parse scan_universe.json, using defaults")
    return {
        "scan_tickers": [],
        "sector_etfs": ["XLK", "XLF", "XLE", "XLV", "XLI", "ARKK"],
        "max_scan_tickers": 30,
        "min_composite_to_surface": 2.0,
        "min_volume_ratio": 1.3,
    }


def scan_sector_etfs(etfs: list[str]) -> dict:
    """Calculate 1-day and 5-day % change for each sector ETF."""
    sector_read = {}
    for etf in etfs:
        try:
            history = yahoo_finance_price_history(etf, period="1mo")
            if history.empty or len(history) < 6:
                logger.warning("%s: insufficient ETF history", etf)
                continue
            close = history["Close"]
            current = float(close.iloc[-1])
            prev_1d = float(close.iloc[-2])
            prev_5d = float(close.iloc[-6]) if len(close) >= 6 else float(close.iloc[0])

            change_1d = round((current - prev_1d) / prev_1d * 100, 2)
            change_5d = round((current - prev_5d) / prev_5d * 100, 2)

            if change_1d > 0 and change_5d > 0:
                trend = "bullish"
            elif change_1d < 0 and change_5d < 0:
                trend = "bearish"
            else:
                trend = "mixed"

            sector_read[etf] = {
                "change_1d": change_1d,
                "change_5d": change_5d,
                "trend": trend,
            }
            logger.info("%s: 1d=%+.2f%% 5d=%+.2f%% trend=%s", etf, change_1d, change_5d, trend)
        except Exception as e:
            logger.warning("%s: ETF scan failed: %s", etf, e)
    return sector_read


def scan_opportunities(scan_tickers: list[str] = None, config: dict = None) -> dict:
    """Scan a universe of tickers and filter for opportunities.

    Returns dict with opportunities list, sector_read, and metadata.
    """
    if config is None:
        config = load_scan_config()

    if scan_tickers is None:
        scan_tickers = config.get("scan_tickers", [])

    min_composite = config.get("min_composite_to_surface", 2.0)
    min_volume = config.get("min_volume_ratio", 1.3)
    sector_etfs = config.get("sector_etfs", [])

    from datetime import date
    scan_date = date.today().isoformat()

    # Scan sector ETFs
    sector_read = scan_sector_etfs(sector_etfs)

    # Analyze each scan ticker — weighted scoring instead of AND gate
    scored_tickers = []
    for ticker in scan_tickers:
        try:
            history = yahoo_finance_price_history(ticker, period="1y")
            result = analyze_ticker(ticker, history)
            if result is None:
                continue

            composite = result.get("composite_score", 0)
            rsi = result.get("rsi_14")
            volume_ratio = result.get("volume_ratio")
            sma_trend = result.get("sma_trend", "")

            # Weighted opportunity score (out of 10)
            opp_score = 0.0

            # Composite is the primary signal (0-4 points)
            opp_score += max(0.0, min(4.0, composite))

            # SMA trend (0-2 points)
            if sma_trend == "golden_cross":
                opp_score += 2.0
            elif sma_trend == "above_200":
                opp_score += 1.5
            elif sma_trend == "below_200":
                opp_score += 0.0
            elif sma_trend == "death_cross":
                opp_score -= 1.0

            # Volume confirmation (0-1.5 points)
            if volume_ratio is not None:
                if volume_ratio > 2.0:
                    opp_score += 1.5
                elif volume_ratio > 1.5:
                    opp_score += 1.0
                elif volume_ratio > 1.2:
                    opp_score += 0.5

            # RSI sweet spot bonus (0-1.5 points): best at 40-55
            if rsi is not None:
                if 35 <= rsi <= 55:
                    opp_score += 1.5  # ideal zone
                elif 30 <= rsi <= 65:
                    opp_score += 0.75  # acceptable
                elif rsi > 75:
                    opp_score -= 1.0  # overbought penalty
                elif rsi < 25:
                    opp_score += 0.5  # deeply oversold — bounce potential

            # Hard filter: minimum composite > 0 (at least slightly bullish)
            if composite <= 0:
                continue

            # Minimum opportunity score to surface
            min_opp_score = config.get("min_opportunity_score", 3.5)
            if opp_score >= min_opp_score:
                result["opportunity_score"] = round(opp_score, 2)
                scored_tickers.append(result)
                logger.info("%s: PASSED filter (opp_score=%.1f, composite=%.1f, rsi=%s, vol=%s, sma=%s)",
                            ticker, opp_score, composite, rsi, volume_ratio, sma_trend)
        except Exception as e:
            logger.warning("%s: scan failed: %s", ticker, e)

    # Rename for downstream compatibility
    survivors = scored_tickers

    # Rank by opportunity score descending (falls back to composite)
    survivors.sort(key=lambda x: x.get("opportunity_score", x.get("composite_score", 0)), reverse=True)

    # Top 5: enrich with valuation info
    opportunities = []
    for result in survivors[:5]:
        ticker = result["ticker"]
        info = yahoo_finance_info(ticker)
        pe_ttm = info.get("trailingPE")
        market_cap = info.get("marketCap")
        market_cap_b = round(market_cap / 1e9, 1) if market_cap else None

        composite = result["composite_score"]
        rsi = result["rsi_14"]
        macd_signal = result.get("macd_signal", "neutral")
        sma_trend = result.get("sma_trend", "unknown")
        volume_ratio = result.get("volume_ratio", 0)

        # Build reason string
        parts = []
        parts.append(f"Strong technicals (composite {composite:+.1f})")
        if macd_signal == "bullish_crossover":
            parts.append("bullish MACD crossover")
        if volume_ratio and volume_ratio > 1.5:
            parts.append(f"{volume_ratio:.1f}x volume surge")
        elif volume_ratio:
            parts.append(f"{volume_ratio:.1f}x volume")
        if sma_trend == "golden_cross":
            parts.append("golden cross active")
        elif sma_trend == "above_200":
            parts.append("above 200 SMA")
        reason = ", ".join(parts)

        # Build risk note
        risk_parts = []
        if rsi and rsi > 55:
            risk_parts.append(f"RSI approaching overbought at {rsi:.0f}")
        if pe_ttm and pe_ttm > 40:
            risk_parts.append("elevated PE")
        risk_note = ", ".join(risk_parts) if risk_parts else "Standard risk"

        opportunities.append({
            "ticker": ticker,
            "composite_score": composite,
            "rsi_14": rsi,
            "macd_signal": macd_signal,
            "sma_trend": sma_trend,
            "volume_ratio": volume_ratio,
            "pe_ttm": round(pe_ttm, 1) if pe_ttm else None,
            "market_cap_b": market_cap_b,
            "reason": reason,
            "risk_note": risk_note,
        })

    return {
        "scan_date": scan_date,
        "universe_size": len(scan_tickers),
        "passed_filter": len(survivors),
        "opportunities": opportunities,
        "sector_read": sector_read,
    }


def main():
    setup_logging()
    logger.info("=== Opportunity Scanner Module ===")

    data = scan_opportunities()
    status = "success"
    error = None
    if not data["opportunities"] and data["universe_size"] > 0:
        status = "partial"
    elif data["universe_size"] == 0:
        status = "error"
        error = "No scan tickers configured"

    envelope = create_envelope("opportunities", data, status=status, error=error)
    save_envelope(envelope, "opportunities.json")
    logger.info("Opportunity scan complete: %d/%d passed filter, %d surfaced",
                data["passed_filter"], data["universe_size"], len(data["opportunities"]))


if __name__ == "__main__":
    main()
