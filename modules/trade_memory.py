"""Trade Memory: fingerprints current technical conditions and matches against journal history."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import csv
import json
import logging

from lib.data_envelope import create_envelope, save_envelope, load_envelope

logger = logging.getLogger(__name__)

MEMORY_PATH = PROJECT_ROOT / "data" / "processed" / "trade_memory_cache.json"


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


def get_all_tickers() -> list[str]:
    """Get union of watchlist tickers and portfolio holdings, deduped."""
    tickers = set()
    watchlist_path = PROJECT_ROOT / "config" / "watchlist.json"
    portfolio_path = PROJECT_ROOT / "config" / "portfolio.json"

    if watchlist_path.exists():
        try:
            wl = json.loads(watchlist_path.read_text())
            tickers.update(wl.get("tickers", []))
        except (json.JSONDecodeError, OSError):
            pass

    if portfolio_path.exists():
        try:
            pf = json.loads(portfolio_path.read_text())
            for h in pf.get("holdings", []):
                tickers.add(h["ticker"])
        except (json.JSONDecodeError, OSError):
            pass

    return sorted(tickers)


def create_fingerprint(tech_result: dict) -> dict:
    """Create a technical fingerprint from analyze_ticker output."""
    rsi = tech_result.get("rsi_14")
    macd = tech_result.get("macd_signal", "neutral")
    bb = tech_result.get("bb_position", "unknown")
    sma = tech_result.get("sma_trend", "unknown")
    vol = tech_result.get("volume_ratio")

    # Bucket RSI
    if rsi is None:
        rsi_bucket = "unknown"
    elif rsi < 30:
        rsi_bucket = "oversold"
    elif rsi < 45:
        rsi_bucket = "low"
    elif rsi < 55:
        rsi_bucket = "neutral"
    elif rsi < 70:
        rsi_bucket = "high"
    else:
        rsi_bucket = "overbought"

    # Bucket volume
    if vol is None:
        vol_bucket = "unknown"
    elif vol < 0.7:
        vol_bucket = "low"
    elif vol < 1.3:
        vol_bucket = "normal"
    elif vol < 2.0:
        vol_bucket = "elevated"
    else:
        vol_bucket = "surge"

    return {
        "rsi_bucket": rsi_bucket,
        "macd_direction": macd,
        "bb_position": bb,
        "sma_trend": sma,
        "volume_bucket": vol_bucket,
    }


def match_fingerprint(current_fp: dict, historical_fps: list[dict]) -> dict:
    """Compare current fingerprint against historical fingerprints.

    Each historical entry has: fingerprint dict + outcome (win/loss/pnl).
    Returns match stats. Requires 3+ of 5 dimensions to match.
    """
    if not historical_fps:
        return {"matches": 0, "wins": 0, "losses": 0, "confidence": "no_data"}

    matches = []
    for hist in historical_fps:
        match_count = 0
        hist_fp = hist.get("fingerprint", {})
        for key in ["rsi_bucket", "macd_direction", "bb_position", "sma_trend", "volume_bucket"]:
            if current_fp.get(key) == hist_fp.get(key) and current_fp.get(key) != "unknown":
                match_count += 1
        if match_count >= 3:
            matches.append(hist)

    if not matches:
        return {"matches": 0, "wins": 0, "losses": 0, "confidence": "no_history"}

    wins = sum(1 for m in matches if m.get("outcome") == "win")
    losses = sum(1 for m in matches if m.get("outcome") == "loss")
    total = wins + losses

    if total == 0:
        confidence = "no_scored"
    elif wins / total >= 0.7:
        confidence = "high_win"
    elif losses / total >= 0.7:
        confidence = "high_loss"
    else:
        confidence = "mixed"

    return {
        "matches": len(matches),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / total, 2) if total > 0 else None,
        "confidence": confidence,
    }


def build_historical_fingerprints() -> list[dict]:
    """Build fingerprints from journal trades.

    Caches results in trade_memory_cache.json. Only analyzes new trades.
    """
    # Load existing cache
    if MEMORY_PATH.exists():
        try:
            memory = json.loads(MEMORY_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            memory = {"fingerprints": []}
    else:
        memory = {"fingerprints": []}

    existing_keys = {(f["ticker"], f["trade_date"]) for f in memory.get("fingerprints", [])}

    # Load journal trades
    trades_path = PROJECT_ROOT / "data" / "raw" / "trades.csv"
    if not trades_path.exists():
        trades_path = PROJECT_ROOT / "data" / "sample" / "trades.csv"
    if not trades_path.exists():
        return memory.get("fingerprints", [])

    new_fps = []
    with open(trades_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row.get("ticker", "").strip()
            trade_date = row.get("date", "").strip()
            if not ticker or not trade_date:
                continue
            if (ticker, trade_date) in existing_keys:
                continue

            # Determine outcome
            try:
                pnl = float(row.get("pnl", 0) or 0)
            except (ValueError, TypeError):
                try:
                    entry = float(row.get("entry_price", 0) or 0)
                    exit_p = float(row.get("exit_price", 0) or 0)
                    shares = int(row.get("shares", 1) or 1)
                    pnl = (exit_p - entry) * shares
                except (ValueError, TypeError):
                    pnl = 0

            outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven"

            new_fps.append({
                "ticker": ticker,
                "trade_date": trade_date,
                "pnl": pnl,
                "outcome": outcome,
                "fingerprint": None,  # Filled with current technicals
            })

    return memory.get("fingerprints", []) + new_fps


def analyze_trade_memory(tickers: list[str] = None) -> dict:
    """For each ticker, create current fingerprint and match against history."""
    if tickers is None:
        tickers = get_all_tickers()

    # Load current technical signals
    tech_envelope = load_envelope("technical_signals.json")
    tech_results = tech_envelope.get("data", {}).get("results", [])

    tech_by_ticker = {t["ticker"]: t for t in tech_results}

    # Build historical fingerprints
    historical = build_historical_fingerprints()

    # Fill missing fingerprints from current tech data
    for fp in historical:
        if fp.get("fingerprint") is None and fp["ticker"] in tech_by_ticker:
            fp["fingerprint"] = create_fingerprint(tech_by_ticker[fp["ticker"]])

    # Save updated cache
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_PATH.write_text(json.dumps({"fingerprints": historical}, indent=2, default=str))

    results = []
    for ticker in tickers:
        tech = tech_by_ticker.get(ticker)
        if not tech:
            results.append({
                "ticker": ticker,
                "fingerprint": None,
                "match_result": {"matches": 0, "confidence": "no_tech_data"},
                "signal": "No technical data available",
            })
            continue

        current_fp = create_fingerprint(tech)

        # Filter historical to same ticker only
        ticker_history = [f for f in historical if f["ticker"] == ticker and f.get("fingerprint")]

        match = match_fingerprint(current_fp, ticker_history)

        # Generate signal text
        if match["confidence"] == "high_win":
            signal = f"HIGH CONFIDENCE: Setup matches {match['wins']}/{match['matches']} winning patterns ({match['win_rate']:.0%})"
        elif match["confidence"] == "high_loss":
            signal = f"WARNING: Setup matches {match['losses']}/{match['matches']} losing patterns — this has burned you before"
        elif match["confidence"] == "mixed":
            signal = f"Mixed history: {match['wins']}W/{match['losses']}L in {match['matches']} similar setups"
        elif match["confidence"] == "no_history":
            signal = "No matching historical patterns — new setup"
        else:
            signal = "Insufficient data for pattern matching"

        results.append({
            "ticker": ticker,
            "fingerprint": current_fp,
            "match_result": match,
            "signal": signal,
        })

        if match["matches"] > 0:
            logger.info("%s: %s", ticker, signal)

    return {"results": results}


def main():
    setup_logging()
    logger.info("=== Trade Memory Module ===")

    data = analyze_trade_memory()
    status = "success" if data["results"] else "error"
    error = None if data["results"] else "No tickers analyzed"

    envelope = create_envelope("trade_memory", data, status=status, error=error)
    save_envelope(envelope, "trade_memory.json")
    logger.info("Trade memory complete: %d tickers analyzed", len(data["results"]))


if __name__ == "__main__":
    main()
