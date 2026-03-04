# Weekly Digest Module

## Overview

The `weekly_digest` module aggregates the past week's trading analysis into a comprehensive summary report. It combines data from daily morning briefs, portfolio tracking, verdict history, and upcoming catalysts into both JSON and markdown formats.

## Purpose

Designed to:
- Summarize portfolio performance across the week
- Track verdict accuracy and decision patterns
- Identify key events and market movements
- Flag upcoming catalysts and risks
- Provide a high-level weekly status at a glance

## Input Data Sources

### Primary Inputs
1. **Morning Brief Files** (`data/outputs/morning_brief_*.md`)
   - Scans for files from the past 7 days
   - Extracts key events and signals
   - Uses date range: 6 days ago through today

2. **Verdict Log** (`data/scorecard/verdict_log.json`)
   - Historical verdict records with dates and tickers
   - Calculates verdict breakdown by type
   - Detects verdict flips (day-to-day changes)

3. **Portfolio Configuration** (`config/portfolio.json`)
   - Current holdings list
   - Cost basis and shares for each position

4. **Module Envelopes** (`data/processed/*.json`)
   - Portfolio module output (current prices, P&L)
   - Options module output (expirations)

5. **Market Data via yfinance**
   - Price history for portfolio holdings
   - Used to calculate weekly price changes

## Analysis Functions

### 1. Portfolio Performance Calculation
**Function**: `calculate_portfolio_performance(holdings: list[dict]) -> dict`

Calculates weekly metrics for each holding:
- **Start price**: First trading day of week
- **End price**: Last trading day of week
- **Price change %**: Weekly percentage movement
- **P&L change**: Dollar change in position value
- **Current P&L**: Total unrealized profit/loss

**Output**:
```json
{
  "holdings": [
    {
      "ticker": "NVDA",
      "shares": 50,
      "start_price": 184.89,
      "end_price": 184.39,
      "price_change_pct": -0.27,
      "pnl_change": -24.85,
      "current_pnl": -15030.35
    }
  ],
  "weekly_summary": {
    "total_value_start": 52388.25,
    "total_value_end": 51315.15,
    "total_pnl": -1073.10,
    "total_pnl_pct": -2.05,
    "best_performer": {"ticker": "NVDA", "pnl_pct": -0.27},
    "worst_performer": {"ticker": "AAPL", "pnl_pct": -3.59}
  }
}
```

### 2. Verdict Scorecard
**Function**: `calculate_verdict_scorecard() -> dict`

Analyzes verdict history:
- Counts verdicts by type (BUY/SELL/HOLD/AVOID/REVIEW)
- Detects verdict flips: when a ticker changes verdicts on consecutive days
- Tracks accuracy (structure in place, 5-day return validation pending)

**Output**:
```json
{
  "total_verdicts": 39,
  "verdict_breakdown": {
    "BUY": 3,
    "SELL": 9,
    "HOLD": 25,
    "AVOID": 2
  },
  "verdict_flips": [
    {
      "date": "2026-02-27",
      "ticker": "AAPL",
      "old_verdict": "SELL",
      "new_verdict": "HOLD"
    }
  ],
  "accuracy": {...}
}
```

### 3. Key Events Extraction
**Function**: `extract_key_events() -> list[dict]`

Parses morning briefs to find:
- Events under the "## Key Events" section
- Stop breaches detected from brief content
- Unusual patterns or signals

**Parsing**:
- Extracts bullet points from Key Events section
- Searches for keywords: "stop", "breach", "unusual", etc.
- Organizes by date

### 4. Upcoming Catalysts
**Function**: `extract_upcoming_catalysts() -> dict`

Identifies risks and events for next week:
- **Stops at risk**: Holdings where trailing_stop is within 5% of current price
- **Options expirations**: Upcoming expiry dates from options module
- **Economic events**: Placeholder for economic calendar integration
- **Earnings dates**: Placeholder for earnings calendar integration

**Output**:
```json
{
  "stops_at_risk": [
    {
      "ticker": "VOO",
      "trigger_price": 614.76,
      "pct_to_stop": 2.6
    }
  ],
  "options_expiry": [
    {
      "ticker": "NVDA",
      "expiry_date": "2026-03-20"
    }
  ]
}
```

### 5. Markdown Generation
**Function**: `generate_markdown(week_data: dict) -> str`

Produces human-readable markdown report with:
- Week summary (number of briefs, date range, portfolio P&L)
- Portfolio performance table
- Verdict accuracy breakdown
- Verdict flips
- Key events timeline
- What to watch next week

**Sections**:
1. **Week Summary**: Overview stats
2. **Portfolio Performance**: Table of holdings with prices and changes
3. **Verdict Accuracy**: Count of verdicts by type, flip history
4. **Key Events This Week**: Extracted events from briefs
5. **What to Watch Next Week**: Stops at risk and options expirations
6. **Disclaimer**: Standard financial advice disclaimer

## Output Files

### 1. JSON Envelope
**Path**: `data/processed/weekly_digest.json`
**Format**: Standard data envelope with schema:
```json
{
  "module": "weekly_digest",
  "generated_at": "2026-03-04T18:58:34.061212+00:00",
  "status": "success|partial|error",
  "error_message": null,
  "data": { ... all analyses ... }
}
```

### 2. Markdown Report
**Path**: `data/outputs/weekly_digest_{YYYY-MM-DD}.md`
**Format**: Human-readable markdown, 1-3 KB typically

## Error Handling

Module is designed to be resilient:
- Missing portfolio.json: saves error envelope
- Missing verdict_log.json: returns empty scorecard, continues
- Missing price data: skips that holding, continues
- Failed analyses: logs error, saves partial status with available data

Status codes:
- **success**: All analyses completed, holding data present
- **partial**: Some analyses failed or incomplete data
- **error**: Critical data missing, cannot generate report

## Configuration

No special configuration needed. Uses existing:
- `config/portfolio.json` - for holdings list
- `config/settings.json` - future expansions
- `.env` - for optional API keys (not used by weekly_digest)

## Date Range Logic

**Window**: Past 7 days (6 days ago through today)
- Start: Today - 6 days (as string YYYY-MM-DD)
- End: Today
- Inclusive of both start and end dates

Example: If today is 2026-03-04, window is 2026-02-26 to 2026-03-04

Timezone: Uses US Eastern timezone via zoneinfo (Python 3.9+) or fallback

## Testing

Comprehensive test suite in `tests/test_weekly_digest.py`:

**Test Classes**:
1. `TestWeekDates` - Date range calculations
2. `TestPortfolioPerformance` - Price and P&L calculations
3. `TestVerdictScorecard` - Verdict tracking and flip detection
4. `TestKeyEvents` - Event extraction from briefs
5. `TestUpcomingCatalysts` - Risk and event identification
6. `TestMarkdownGeneration` - Report formatting
7. `TestMainFunctionBasic` - Integration testing

**Run tests**:
```bash
.venv/bin/python -m pytest tests/test_weekly_digest.py -v
```

**Coverage**: 13 tests, all passing
- Mocks for yfinance calls (no API calls in tests)
- Fixtures for sample data
- Monkeypatch for path isolation

## Usage Examples

### Run Weekly Digest
```bash
.venv/bin/python modules/weekly_digest.py
```

### Use in Script
```python
from modules.weekly_digest import main

main()  # Generates files in data/processed/ and data/outputs/

# Or use individual functions
from modules.weekly_digest import (
    calculate_portfolio_performance,
    calculate_verdict_scorecard,
    extract_key_events
)

holdings = [...portfolio data...]
perf = calculate_portfolio_performance(holdings)
scorecard = calculate_verdict_scorecard()
events = extract_key_events()
```

### Integration with run_all.py
Add to module execution order:
```python
result = subprocess.run(
    [VENV_PYTHON, str(PROJECT_ROOT / "modules" / "weekly_digest.py")],
    capture_output=True, text=True, timeout=120, cwd=str(PROJECT_ROOT)
)
```

## Future Enhancements

1. **Verdict Accuracy Tracking**
   - Compare verdicts with 5-day returns
   - Calculate win rate by verdict type
   - Track best/worst performing signals

2. **Sector Rotation Analysis**
   - Pull sector ETF returns
   - Compare with portfolio holdings
   - Identify sector performance winners

3. **Economic Calendar Integration**
   - Fetch upcoming economic events
   - Flag high-impact data releases
   - Correlate with market movements

4. **Risk Regime Integration**
   - Pull risk regime changes from dashboard
   - Note when regime shifted during week
   - Flag unusual volatility spikes

5. **Insider Activity Integration**
   - Track insider trades in portfolio holdings
   - Alert on significant insider activity
   - Include in Key Events section

## Module Statistics

- **Lines of Code**: ~700 (excluding tests)
- **Test Lines**: ~400
- **Dependencies**: pandas, yfinance (via lib.api)
- **Execution Time**: ~2 seconds (primarily yfinance calls)
- **Output Size**: ~1 KB markdown + ~4 KB JSON

## Logging

All operations logged to `data/outputs/run.log`:
```
2026-03-04 18:58:32,568 [__main__] INFO: === Weekly Digest Module ===
2026-03-04 18:58:34,042 [__main__] INFO: Calculated portfolio performance
2026-03-04 18:58:34,044 [__main__] INFO: Calculated verdict scorecard
2026-03-04 18:58:34,049 [__main__] INFO: Extracted key events
2026-03-04 18:58:34,050 [__main__] INFO: Extracted upcoming catalysts
2026-03-04 18:58:34,060 [__main__] INFO: Wrote markdown to ...
2026-03-04 18:58:34,063 [lib.data_envelope] INFO: Saved envelope to ...
2026-03-04 18:58:34,063 [__main__] INFO: Weekly digest complete
```

## Troubleshooting

**No holdings data in output**
- Check `config/portfolio.json` exists
- Verify yfinance can fetch price data for tickers
- Check portfolio.json has at least one holding with shares > 0

**Verdict scorecard is empty**
- Check `data/scorecard/verdict_log.json` exists
- Verify dates in verdict log match current week
- Check verdict log JSON is valid

**Missing key events**
- Verify morning_brief_*.md files exist in data/outputs/
- Check files are from past 7 days
- Verify file has "## Key Events" section

**Stop calculation looks wrong**
- Verify `current_price` is in portfolio envelope
- Check `trailing_stop` value is populated
- Calculation: (current - stop) / current * 100

## References

- Related Modules: morning_brief, portfolio, options, scorecard
- Data Format: lib/data_envelope.py
- API Helpers: lib/api.py
- Project Conventions: CLAUDE.md

