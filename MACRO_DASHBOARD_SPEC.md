# Macro Dashboard Module - Technical Specification

## Module Overview
The `macro_dashboard` module provides real-time market regime detection through 5 key macroeconomic indicators. It serves as the "market weather report" for daily trading intelligence briefings.

## Architecture

### Import Pattern
```python
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.data_envelope import create_envelope, save_envelope
from lib.cache import get_cached, set_cached, make_cache_key
```

Uses absolute imports following project standards. No relative imports.

### Logging Configuration
```python
import logging
logger = logging.getLogger(__name__)

def setup_logging():
    """Call ONLY inside main(). NEVER at module level."""
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
```

## Data Sources

### yfinance Integration
```python
def yahoo_finance_price_history(ticker: str, period: str = "3mo") -> pd.DataFrame:
    """Download price history via yfinance."""
    try:
        import yfinance as yf
        data = yf.download(ticker, period=period, auto_adjust=True, progress=False)
        return data if not data.empty else pd.DataFrame()
    except Exception as e:
        logger.warning("yfinance error for %s: %s", ticker, e)
        return pd.DataFrame()
```

- Fetches 3 months of price history
- Auto-adjusted for splits/dividends
- Returns empty DataFrame on failure (never raises)

### Caching Strategy
```python
cache_key = make_cache_key("yfinance", f"history_{ticker}", {"period": "3mo"})
cached = get_cached(cache_key, max_age_hours=24)
```

- 24-hour cache TTL per indicator
- Keys include ticker, period in hash
- Reduces API calls while maintaining freshness

## Indicator Calculations

### Five Macro Indicators
```python
MACRO_TICKERS = {
    "VIX": "^VIX",           # Volatility Index
    "DXY": "DX-Y.NYB",       # US Dollar Index
    "US10Y": "^TNX",         # 10-Year Treasury Yield
    "OIL": "CL=F",           # Crude Oil Futures
    "GOLD": "GC=F",          # Gold Futures
}
```

### Per-Indicator Metrics
```python
{
    "value": float,              # Current price (rounded to 2 decimals)
    "change_1d": float,          # 1-day percent change
    "change_5d": float,          # 5-day percent change
    "change_20d": float,         # 20-day percent change
    "high_20d": float,           # 20-day high
    "low_20d": float,            # 20-day low
    "percentile_1y": float,      # Rank vs 1-year range [0, 1]
    "trend": str,                # "rising" | "falling" | "flat"
    "interpretation": str,       # Context-specific assessment
}
```

### Trend Detection
```python
if change_5d > 1.0:
    trend = "rising"
elif change_5d < -1.0:
    trend = "falling"
else:
    trend = "flat"
```

### Percentile Calculation
```python
hist_for_percentile = close.iloc[-min(252, len(close)):]  # ~1 year of trading days
hist_min = hist_for_percentile.min()
hist_max = hist_for_percentile.max()
if hist_max > hist_min:
    percentile = (current - hist_min) / (hist_max - hist_min)
else:
    percentile = 0.5
```

- Uses available data, capped at ~252 trading days (1 year)
- Returns 0.0 if at 1-year low, 1.0 if at 1-year high
- Defaults to 0.5 if no range exists

### Context-Specific Interpretation
```python
def _get_interpretation(name: str, value: float, change_1d: float,
                       percentile: float, trend: str) -> str:
    if name == "VIX":
        if value > 35:
            return "Extreme fear — panic selling"
        elif value > 25:
            return "Elevated fear — above normal range"
        # ... etc
```

Different thresholds for each indicator:
- **VIX**: Thresholds at 35, 25, 20, 15
- **DXY**: Percentile-based (>75% = strong, <25% = weak)
- **US10Y**: Value-based (>5%, >4%, >3%)
- **OIL**: Value-based (>90, >70)
- **GOLD**: Percentile-based (>75%, >50%)

## Signal Determination

### Four Signal Types
```python
def determine_signal(indicators: dict) -> str:
    """Returns: "crisis" | "risk_off" | "risk_on" | "mixed" """
```

### Signal Logic
1. **Crisis** (Highest priority)
   - VIX > 35: Extreme panic conditions

2. **Risk-Off** (Safety-seeking)
   - VIX > 25 AND gold trending "rising" AND yields trending "falling"
   - Indicates flight-to-safety behavior

3. **Risk-On** (Growth-seeking)
   - VIX < 15 AND yields "flat"/"falling" AND DXY "flat"/"falling"
   - Indicates appetite for risk assets

4. **Mixed** (Default)
   - All other combinations
   - Default when signal criteria not met

### Signal Detail (One-Sentence Explanation)
```python
def determine_signal_detail(indicators: dict, signal: str) -> str:
    # Crisis: "VIX at extreme level (38.5) — panic conditions."
    # Risk-Off: "VIX elevated (27.3) with gold rising — flight to safety."
    # Risk-On: "Low VIX (11.2) with stable yields — favorable for equities."
    # Mixed: "VIX elevated (22.5) with rising oil — inflation watch."
```

## Regime Summary Generation

### Five Common Regime Patterns
```python
def determine_regime_summary(indicators: dict) -> str:
```

1. **Flight to Safety**
   - Condition: VIX rising + gold rising + yields falling
   - Summary: "Flight to safety — risk-off environment"

2. **Risk-On Environment**
   - Condition: VIX < 15 + yields flat/falling + DXY falling
   - Summary: "Risk-on environment — equities favored"

3. **Inflation Concerns**
   - Condition: Oil rising + yields > 4%
   - Summary: "Inflation concerns — watch energy costs"

4. **Equity-Specific Fear**
   - Condition: VIX > 25 + oil < 60
   - Summary: "Equity-specific fear — flight to quality"

5. **USD Strength Regime**
   - Condition: DXY rising + oil falling
   - Summary: "Risk-off USD strength — dollar pressuring commodities"

6. **Fallback/Mixed**
   - Comprehensive summary of all indicators

## Output Structure

### Data Envelope (Standard Format)
```json
{
  "module": "macro_dashboard",
  "generated_at": "2026-03-04T09:30:00-07:00",
  "status": "success",
  "error_message": null,
  "data": {
    "signal": "mixed",
    "signal_detail": "VIX elevated (22.5) with rising oil — inflation watch.",
    "regime_summary": "Mixed signals...",
    "indicators": { ... },
    "data_source": "yfinance",
    "analyzed_at": "2026-03-04T09:30:00-07:00"
  }
}
```

### Status Codes
- **"success"**: All 5 indicators fetched successfully
- **"partial"**: 1+ indicators failed, at least 1 succeeded
- **"error"**: All indicators failed, no meaningful analysis

## Error Handling

### Graceful Degradation
```python
for name, ticker in MACRO_TICKERS.items():
    data = get_indicator_data(name, ticker)
    if data is not None:
        indicators[name] = data
    else:
        failed_tickers.append(name)

if not indicators:
    status = "error"
elif failed_tickers:
    status = "partial"
else:
    status = "success"
```

Module continues even if individual indicators fail. Status reflects overall success rate.

### Exception Handling
- All yfinance calls wrapped in try/except
- Returns None on any exception
- Logs warnings but never raises
- Cache misses handled gracefully

## Testing Strategy

### Test Classes (36 Total Tests)
1. **TestIndicatorInterpretation** (8): Context-specific meanings
2. **TestSignalDetermination** (5): Signal classification logic
3. **TestSignalDetail** (4): 1-sentence explanations
4. **TestRegimeSummary** (4): Regime assessments
5. **TestIndicatorDataFetch** (6): Data fetching & calculations
6. **TestAnalyzeMacro** (2): Full analysis pipeline
7. **TestMainFunction** (3): Entry point integration
8. **TestOutputStructure** (2): Envelope schema validation
9. **TestGracefulDegradation** (2): Error resilience

### Mock Strategy
```python
@patch("modules.macro_dashboard.get_cached")
@patch("modules.macro_dashboard.yahoo_finance_price_history")
def test_example(self, mock_yf, mock_cache):
    mock_cache.return_value = None  # Force fresh fetch
    mock_yf.return_value = make_mock_history(60, 100.0)
```

Mocks yfinance and cache to avoid network calls. Uses deterministic mock data.

## Integration Points

### Input Dependencies
- `lib/data_envelope.py`: Envelope creation/loading
- `lib/cache.py`: Caching infrastructure
- yfinance: Market data API

### Output Recipients
- `modules/morning_brief.py`: Macro context for recommendations
- `data/processed/macro_dashboard.json`: Consumable output
- `data/outputs/run.log`: Execution logs

### Orchestration
Part of daily pipeline in `scripts/run_all.py`:
```python
# Run order: ... -> macro_dashboard -> ... -> morning_brief
```

## Performance Characteristics

### Time Complexity
- Per indicator: O(N) where N = days of history (~60)
- Total: O(5N) = ~300 operations per run
- Typical runtime: < 1 second per full run

### Space Complexity
- DataFrame storage: ~5KB per indicator
- Cache files: ~1-2KB each
- Total per run: < 50KB memory

### Network Calls
- Fresh run: 5 yfinance calls (1 per indicator)
- Cached run: 0 network calls
- With 24-hour cache: ~5 calls/day maximum

## Failure Modes & Recovery

### Failure Mode 1: Single Indicator Unavailable
- Status: "partial"
- Impact: Reduced regime assessment quality, signal still valid
- Recovery: Automatic, module continues

### Failure Mode 2: Network Timeout
- Status: "partial" (if others succeed) or "error"
- Impact: Could miss regime signal
- Recovery: Cache from previous run (if available)

### Failure Mode 3: All Indicators Fail
- Status: "error"
- Impact: No regime signal, must skip
- Recovery: Log warning, return empty analysis

### Failure Mode 4: Invalid Yfinance Response
- Status: Depends on number failing
- Impact: Reduced coverage
- Recovery: Logged, module continues

## Configuration & Customization

### Adjustable Thresholds
Via code modification (future: config file):
```python
# Signal thresholds
VIX_CRISIS_THRESHOLD = 35
VIX_RISK_OFF_THRESHOLD = 25
VIX_RISK_ON_THRESHOLD = 15

# Trend thresholds
TREND_RISE_THRESHOLD = 1.0  # >1% = rising
TREND_FALL_THRESHOLD = -1.0  # <-1% = falling

# Cache TTL
CACHE_MAX_AGE_HOURS = 24

# Historical window
PERCENTILE_WINDOW_DAYS = 252  # ~1 year
```

### Adding New Indicators
1. Add to `MACRO_TICKERS` dict
2. Implement ticker symbol in yfinance
3. Update `_get_interpretation()` with thresholds
4. Update regime pattern detection
5. Add sample data
6. Add tests

## Best Practices

### For Developers
- Always mock yfinance in tests
- Use `get_cached()` before fetching
- Log at INFO level for success, WARNING for issues
- Validate JSON envelope structure
- Handle empty DataFrames gracefully

### For Operations
- Monitor run.log for "error" status
- Alert if "error" status persists > 1 hour
- Review historical signals for pattern validation
- Cache can be manually cleared if stale data suspected
- Verify yfinance uptime before troubleshooting

### For Integration
- Always use `load_envelope()` to read output
- Check `status` field before using `data`
- Missing indicators are normal in partial status
- Regime summary provides natural language explanation
- Signal provides actionable classification

## References

### Data Sources
- **yfinance**: https://github.com/ranaroussi/yfinance
- **VIX**: CBOE Volatility Index (ticker: ^VIX)
- **DXY**: US Dollar Index (ticker: DX-Y.NYB)
- **US10Y**: 10-Year Treasury Yield (ticker: ^TNX)
- **OIL**: Crude Oil Front Month Futures (ticker: CL=F)
- **GOLD**: Gold Futures (ticker: GC=F)

### Project Standards
- Data Envelope: `/lib/data_envelope.py`
- Cache System: `/lib/cache.py`
- Logging: Standard Python logging module
- Testing: pytest with unittest.mock
