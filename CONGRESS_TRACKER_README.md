# Congressional Trading Tracker Module

A module for the Trading Intelligence System that monitors STOCK Act disclosures from House and Senate members and flags overlaps with your watchlist and portfolio.

## Overview

Congressional trading data is valuable for retail traders because:
- Members of Congress often have insider knowledge about regulatory and policy changes
- Bulk purchases/sales can signal confidence or concern about future performance
- Pattern clustering (multiple members trading the same stock) indicates stronger signals

## Module: `modules/congress_tracker.py`

### Data Sources (Priority Order)

1. **House Stock Watcher API** (Free, no key needed)
   - https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json
   - Official House disclosures with 45-day delay

2. **Senate Stock Watcher** (Free, no key needed)
   - https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json
   - Official Senate disclosures with 45-day delay

3. **Sample Data Fallback**
   - data/sample/congress_trades.json
   - Used when APIs are unavailable or rate-limited

### Key Functions

#### run_congress_tracker(lookback_days: int = 90) -> dict
Main entry point that orchestrates the entire analysis pipeline.

Returns analysis with watchlist_trades, notable_trades, cluster_signals, and summary_stats.

#### analyze_trades(trades: list[dict], watchlist_tickers: list[str]) -> dict
Core analysis function that processes congressional trades and returns:

- watchlist_trades: Trades matching your portfolio/watchlist tickers
- notable_trades: Trades by historically active politicians
- cluster_signals: Multiple politicians trading same ticker same direction
- big_trades: Individual trades > $500k estimated value
- summary_stats: Aggregate statistics and breakdown

### Signal Levels

| Signal | Meaning |
|--------|---------|
| cluster_detected | 3+ politicians trading same ticker, in your watchlist |
| significant_activity | 2+ large trades ($500k+) in watchlist |
| active | 3+ congressional trades in watchlist |
| normal | 1-2 trades in watchlist |
| quiet | 0 trades in watchlist |
| no_data | No data available from any source |

## Output Format

Module writes to data/processed/congress_tracker.json with standard envelope containing:
- watchlist_trades (list)
- notable_trades (list)
- cluster_signals (list)
- big_trades (list)
- summary_stats (dict with totals and breakdowns)
- signal (string: cluster_detected|significant_activity|active|normal|quiet|no_data)
- data_source (string: sample_data|house_api|senate_api|api)

## Testing

Run tests with:
```
python3 tests/test_congress_tracker.py
```

All 15 tests pass covering:
- Amount parsing
- Watchlist filtering
- Notable trader detection
- Cluster signal detection
- Big trade identification
- Partisan breakdown analysis

## Integration

The module is integrated into scripts/run_all.py and runs after insider_tracker.

## Important Notes

### API Limitations
- House and Senate APIs have 45-day disclosure delay (STOCK Act requirement)
- APIs may be rate-limited (no auth key available)
- Sample data fallback used with "partial" status when APIs unavailable

### Interpretation
Congressional trades are:
- NOT insider trading (legally distinct for members of Congress)
- Delayed 45 days minimum (not real-time)
- Self-reported (quality varies)
- Useful for pattern recognition (clusters are signals)

### Cache Strategy
- API responses cached for 12 hours
- Congress tracker runs once per day in morning orchestrator
- Cache keys: congress_house_YYYY-MM-DD, congress_senate_YYYY-MM-DD

---

**Built by**: Claude (vibecoder)
**Date**: 2026-03-04
**Status**: Production-ready with sample data fallback
