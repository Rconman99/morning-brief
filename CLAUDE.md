# Trading Intelligence System

## Project Overview
Automated trading analysis system that chains analytical modules into a daily Morning Brief.
Built by a vibecoder using Claude Code. MCP servers handle data. Python handles logic.

## CRITICAL RULES FOR AUTONOMOUS BUILD
- NEVER ask questions. Make the best decision and move on.
- NEVER stop to request confirmation. Execute all steps sequentially.
- If a dependency install fails, try an alternative and continue.
- If an API key is missing from .env, build the module anyway with mock data fallback.
- Test every module after building it. If a test fails, fix it before moving to the next module.
- Commit after each module is complete with a descriptive message.
- Print progress to stdout as you go: "=== Building module X ===" etc.

## Environment Setup (MUST happen before anything else)

### Virtual Environment (MANDATORY)
Bare `pip install` is blocked on modern systems (PEP 668). Always use the venv.
```bash
# Try python3 first, fall back to python
python3 -m venv .venv 2>/dev/null || python -m venv .venv

# IMPORTANT: Do NOT rely on `source .venv/bin/activate`.
# Activation only affects the current shell session and does NOT persist
# across separate subprocess calls that Claude Code makes.
# Instead, ALWAYS use explicit venv paths for ALL pip and python commands:
#   .venv/bin/pip  (Linux/Mac) or .venv\Scripts\pip  (Windows)
#   .venv/bin/python (Linux/Mac) or .venv\Scripts\python (Windows)
```

### Git Config (MANDATORY — commits fail without this)
```bash
git config user.name "Trading System"
git config user.email "trading@local.dev"
```

### Python and Pip Paths
Throughout this entire build, use these explicit paths. NEVER use bare `pip` or `python`:
- Linux/Mac: `.venv/bin/python` and `.venv/bin/pip`
- Windows: `.venv\\Scripts\\python` and `.venv\\Scripts\\pip`

Detect the OS and set a variable:
```bash
if [ -f .venv/bin/python ]; then
  VENV_PYTHON=.venv/bin/python
  VENV_PIP=.venv/bin/pip
else
  VENV_PYTHON=.venv/Scripts/python
  VENV_PIP=.venv/Scripts/pip
fi
```

## Tech Stack
- Python 3.11+ inside a virtual environment (.venv/)
- python-dotenv for env vars
- numpy, pandas for math (correlations, returns)
- requests for any direct API calls
- yfinance for market data (no API key needed, but unreliable — always wrap in try/except)
- scipy for optional Black-Scholes delta estimation (install may fail on some systems — that's OK)
- json for inter-module data exchange
- Python logging module for all log output
- No frameworks. No Django. No Flask. Pure Python scripts.

## Project Structure
```
trading-intelligence/
├── CLAUDE.md
├── .env
├── .gitignore
├── .python-version          # Contains "3.11" for pyenv users
├── requirements.txt
├── config/
│   ├── watchlist.json
│   ├── portfolio.json
│   └── settings.json
├── modules/
│   ├── __init__.py
│   ├── journal.py
│   ├── earnings.py
│   ├── valuation.py
│   ├── portfolio.py
│   ├── options.py
│   └── morning_brief.py
├── lib/
│   ├── __init__.py
│   ├── data_envelope.py
│   ├── cache.py
│   ├── api.py
│   └── notify.py
├── data/
│   ├── raw/
│   │   └── earnings/
│   ├── processed/
│   ├── outputs/
│   └── sample/
├── tests/
│   ├── __init__.py
│   ├── conftest.py           # Shared fixtures: mock envelopes, sample data paths
│   ├── test_journal.py
│   ├── test_earnings.py
│   ├── test_valuation.py
│   ├── test_portfolio.py
│   ├── test_options.py
│   └── test_morning_brief.py
└── scripts/
    ├── run_all.py
    └── setup_check.py
```

## Import Path Strategy (CRITICAL — read this before writing any file)
Every file in modules/, scripts/, and tests/ MUST start with this EXACT block before any local imports:
```python
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
```
Then use ABSOLUTE imports only:
```python
from lib.cache import get_cached, set_cached
from lib.data_envelope import create_envelope, save_envelope
```
NEVER use relative imports. NEVER use importlib. This pattern works everywhere: direct execution, subprocess, pytest.

## Logging Standard
```python
import logging

logger = logging.getLogger(__name__)

def setup_logging():
    """Call this ONLY inside main(). NEVER at module level. NEVER at import time."""
    log_dir = PROJECT_ROOT / "data" / "outputs"
    log_dir.mkdir(parents=True, exist_ok=True)
    # Only add handlers if none exist (prevents duplicate handlers on re-import)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(log_dir / "run.log", mode="a")
            ]
        )
```
IMPORTANT: `logger = logging.getLogger(__name__)` at module level is fine (it's just creating a named logger). But `setup_logging()` with FileHandler MUST only be called inside `main()`, not at import time, because the log directory might not exist yet during testing or early scaffold.

## Data Schema (MANDATORY for all modules)
Every module writes JSON with this envelope to data/processed/{module}.json:
```json
{
  "module": "module_name",
  "generated_at": "2026-02-26T08:15:00-07:00",
  "status": "success | partial | error",
  "error_message": null,
  "data": {}
}
```
- generated_at: `datetime.now().astimezone().isoformat()`
- "partial" = some tickers succeeded but others failed
- "error" = module could not produce meaningful output
- Use lib/data_envelope.py helpers. Never hand-write the envelope.

## Venv-Aware Execution (CRITICAL for run_all.py and tests)
The orchestrator and tests must use the venv Python, not system Python:
```python
import sys
VENV_PYTHON = str(PROJECT_ROOT / ".venv" / "bin" / "python")
if not Path(VENV_PYTHON).exists():
    VENV_PYTHON = str(PROJECT_ROOT / ".venv" / "Scripts" / "python")
if not Path(VENV_PYTHON).exists():
    VENV_PYTHON = sys.executable  # last resort fallback
```
Use VENV_PYTHON in all subprocess.run() calls instead of sys.executable.

## Module Specifications

### lib/data_envelope.py
```python
def create_envelope(module_name: str, data: dict, status: str = "success", error: str = None) -> dict
def save_envelope(envelope: dict, filename: str) -> Path  # writes to data/processed/
def load_envelope(filename: str) -> dict
    # Returns envelope from data/processed/. If file not found, returns:
    # {"module": "unknown", "generated_at": None, "status": "missing", "error_message": "File not found", "data": {}}
```

### lib/cache.py
```python
def make_cache_key(source: str, endpoint: str, params: dict) -> str
    # Sort params keys alphabetically, json.dumps(params, sort_keys=True),
    # SHA256 hash first 12 chars, format: "{source}_{endpoint}_{hash}_{YYYYMMDD}"
def get_cached(key: str, max_age_hours: int = 24) -> dict | None
    # Returns None if file missing or older than max_age_hours
def set_cached(key: str, data: dict) -> Path
    # Saves to data/raw/{cache_key}.json
```

### lib/api.py
```python
def alpha_vantage_call(function: str, params: dict) -> dict
    # Check cache first. If cache miss AND key != "demo": call API, sleep 12s, cache response.
    # Return {} on ANY error (never raise).
def yahoo_finance_price_history(ticker: str, period: str = "2y") -> pd.DataFrame
    # Wraps yfinance.download(). Returns empty DataFrame on failure.
def yahoo_finance_info(ticker: str) -> dict
    # Wraps Ticker(ticker).info. UNRELIABLE — always use .get() to access fields.
    # Returns {} on failure.
def yahoo_finance_options(ticker: str) -> tuple[list, dict]
    # Returns (expiry_dates_list, {"calls": DataFrame, "puts": DataFrame})
    # Returns ([], {}) on failure. ETFs like VOO often have no options.
```

### modules/journal.py
- INPUT: check data/raw/trades.csv first, fall back to data/sample/trades.csv
- CSV columns: date,ticker,direction,entry_price,exit_price,pnl,hold_time_hours,rationale
- ERROR HANDLING: for each row, check if critical fields (date, ticker, entry_price, exit_price) are non-empty. Skip bad rows with logger.warning(). Calculate pnl from prices if pnl field is empty. Set hold_time to "unknown" if empty.
- ANALYSIS:
  - Win rate overall and by day-of-week
  - Revenge trading detection: 3+ losing trades on the same ticker where each trade's date is within 48 hours of the PREVIOUS same-ticker losing trade (regardless of other tickers traded in between). Sort trades by date first, then group by ticker, then check date gaps between consecutive same-ticker losses.
  - Day-of-week clustering: if any day has win_rate < 30% with >= 3 trades, flag it
  - Generate 1 checklist rule per pattern
- OUTPUT: data/processed/journal.json

### Sample data: data/sample/trades.csv
30 rows. SPECIFIC requirements:
- 6 tickers: AAPL, NVDA, AMD, TSLA, MSFT, META
- ~18 winners, ~12 losers
- 5 Tuesday trades, ALL losers (dates: Jan 6, 13, 20, 27, and one more on Jan 7 which is also a Tuesday — wait, check the calendar. January 2026: Jan 6 is Tuesday, Jan 13 is Tuesday, Jan 20 is Tuesday, Jan 27 is Tuesday. Use these 4 Tuesdays plus one trade on a Tuesday that spans two entries)
- 3 TSLA losing trades on Jan 14 (Wed) afternoon, Jan 15 (Thu) morning, Jan 15 (Thu) afternoon — within 48hrs, same ticker = revenge pattern
- 2 rows: leave rationale column completely empty (just consecutive commas in CSV)
- 1 row: leave hold_time_hours empty
- Dates: Jan 2-31, 2026 (only weekdays)
- PnL range: -$800 to +$1200
- Hold times: 0.5 to 120 hours

### modules/earnings.py
- INPUT: for each ticker in config/watchlist.json earnings_watch, check data/raw/earnings/{TICKER}_transcript.txt
- If no transcripts found at all: try data/sample/earnings_transcript.txt as fallback, attribute to "SAMPLE_AAPL"
- ANALYSIS per transcript:
  - HEDGE phrases to count: "we anticipate", "we expect", "we believe", "we hope", "potentially", "may", "might", "could", "uncertain"
  - DEFINITIVE phrases: "we will", "we are confident", "we are committed", "we have decided", "our plan is", "we are certain"
  - confidence_score = definitive_count / max(1, definitive_count + hedge_count)
  - tone_score = round((confidence_score * 10) - 5, 1) — maps 0.0->-5, 0.5->0, 1.0->+5
  - risk_factors: sentences containing "risk", "headwind", "challenge", "uncertainty", "decline", "pressure"
  - summary: 2-3 sentences
- OUTPUT: data/processed/earnings_tone.json
- DOCSTRING must say: "Requires manual transcript input. No free API for transcripts."

### Sample transcript: data/sample/earnings_transcript.txt
~500 word mock AAPL Q1 2026 call. Structure:
- CEO Tim (use generic name) opens: "record quarter", "strong momentum", "we are confident in our services trajectory"
- CFO transitions: "gross margins were [X]%", "we expect some pressure on hardware margins"
- Guidance: "we anticipate headwinds from semiconductor supply constraints", "we believe regulatory costs in the EU may impact operating expenses", "potentially slower growth in Greater China"
- Q&A: analyst asks about AI spending, CEO hedges: "we are making significant investments but it might take several quarters to see returns"
- Include at least 6 hedge phrases and 4 definitive phrases (so tone_score should land around -1 to +1)
- COPY to both: data/sample/earnings_transcript.txt AND data/raw/earnings/AAPL_transcript.txt

### modules/valuation.py
- INPUT: config/watchlist.json comparison_pairs
- DATA: try Alpha Vantage first (if key != "demo"), fall back to yfinance .info (always .get() for every field)
- METRICS (use None if unavailable):
  - P/E TTM: info.get("trailingPE") or AV "PERatio"
  - EV/EBITDA: info.get("enterpriseToEbitda") or AV "EVToEBITDA"
  - Price/FCF: info.get("marketCap") / info.get("freeCashflow") if both truthy, else None
  - PEG: info.get("pegRatio") or AV "PEGRatio"
  - Price/Book: info.get("priceToBook") or AV "PriceToBookRatio"
- Comparison: count non-None metrics where stock A is cheaper. If A wins more: A is cheaper. Tied or <2 comparable metrics: "inconclusive"
- 1-sentence thesis per pair
- OUTPUT: data/processed/valuation.json

### modules/portfolio.py
- INPUT: config/portfolio.json + config/settings.json
- DATA: yfinance price history (period="2y", auto_adjust=True)
- ANALYSIS:
  - Current price = most recent row of history (NOT .info — more reliable)
  - P&L per holding: (current_price - cost_basis) * shares
  - Daily returns: close.pct_change().dropna()
  - Correlation: ONLY for pairs with >= settings.min_correlation_days overlapping days. Pairs below threshold: correlation = null, flag "insufficient data"
  - 14-day ATR:
    - If history has High/Low columns: TR = max(H-L, abs(H-prevC), abs(L-prevC)), ATR = TR.rolling(14).mean().iloc[-1]
    - Fallback: ATR ≈ returns.std() * current_price * (14 ** 0.5)
  - Trailing stop = current_price - (settings.atr_multiplier_default * ATR)
  - Locked profit = (trailing_stop - cost_basis) * shares
- OUTPUT: data/processed/portfolio.json

### modules/options.py
- INPUT: config/portfolio.json holdings
- DATA: yfinance Ticker.options and Ticker.option_chain()
- Per holding:
  - Get expiry dates. If EMPTY (VOO, ETFs): skip, log, continue to next ticker.
  - Pick front_month: nearest expiry >= 20 calendar days from today
  - Pick leaps: nearest expiry >= 180 calendar days from today. If none: skip PMCC.
  - Get option chain. AFTER calling option_chain(), CHECK if calls DataFrame is empty: `if chain.calls.empty: skip ticker, log warning, continue`
  - PMCC selection via MONEYNESS (primary method — yfinance has no Greeks):
    - Long leg (LEAPS): strike closest to current_price * 0.85 (deep ITM ≈ 0.75 delta)
    - Short leg (front): strike closest to current_price * 1.05 (slightly OTM ≈ 0.30 delta)
    - Skip if lastPrice is 0 or NaN for either leg
  - Optional Black-Scholes delta (only if scipy importable):
    ```python
    try:
        from scipy.stats import norm
        import math
        S = current_price; K = strike; T = days_to_expiry / 365
        r = 0.045; sigma = historical_30d_vol or 0.30
        d1 = (math.log(S/K) + (r + sigma**2/2)*T) / (sigma * math.sqrt(T))
        delta = norm.cdf(d1)
    except Exception:
        delta = None  # fall back to moneyness
    ```
  - Max pain calculation (PRECISE pseudocode):
    ```
    For each candidate_price in all_strike_prices:
        total_pain = 0
        For each strike in all_strikes:
            total_pain += call_OI_at_strike * max(0, candidate_price - strike)
            total_pain += put_OI_at_strike * max(0, strike - candidate_price)
        Record (candidate_price, total_pain)
    max_pain = candidate_price with LOWEST total_pain
    ```
    This iterates candidate settlement prices, NOT "current stock price". Get this right.
  - Top 3 strikes by (call_OI + put_OI)
- OUTPUT: data/processed/options.json

### modules/morning_brief.py
- INPUT: glob data/processed/*.json, load each via load_envelope()
- Date: US Eastern timezone. Use `from zoneinfo import ZoneInfo` (Python 3.9+) or `from datetime import timezone, timedelta` as fallback
- VERDICT RULES for Watchlist Actions (do NOT invent criteria — use these exact rules):
  - BUY: valuation shows stock is cheaper than peer on 3+ metrics AND earnings tone_score > 0 (or no earnings data)
  - SELL: portfolio trailing stop is within 5% of current price (stop_price / current_price > 0.95)
  - AVOID: earnings tone_score <= -2 OR valuation shows stock is more expensive on 4+ metrics
  - HOLD: default for everything else
  - REVIEW: insufficient data to make any determination (valuation and portfolio both missing/error)
- MUST generate even if some modules have status "error" — show "Data unavailable" for missing sections
- Include Module Status section: ✅ success / ⚠️ partial / ❌ error / ⬜ missing
- Include disclaimer: "This is analysis, not financial advice. All trading decisions are yours."
- OUTPUT: data/outputs/morning_brief_{YYYY-MM-DD}.md

### scripts/run_all.py
- MUST use venv Python path for subprocess calls:
  ```python
  VENV_PYTHON = str(PROJECT_ROOT / ".venv" / "bin" / "python")
  if not Path(VENV_PYTHON).exists():
      VENV_PYTHON = str(PROJECT_ROOT / ".venv" / "Scripts" / "python")
  if not Path(VENV_PYTHON).exists():
      VENV_PYTHON = sys.executable  # last resort
  ```
- Execute each module via subprocess:
  ```python
  result = subprocess.run(
      [VENV_PYTHON, str(PROJECT_ROOT / "modules" / "journal.py")],
      capture_output=True, text=True, timeout=120, cwd=str(PROJECT_ROOT)
  )
  ```
- Run order: journal -> earnings -> valuation -> portfolio -> options -> morning_brief
- Per module: print status, capture stdout/stderr, continue on failure
- After all: print summary table with pass/fail and total runtime

### scripts/setup_check.py
- Check Python >= 3.11
- Check each package importable
- Check .env exists (warn if key is "demo", don't fail)
- Check config/*.json exist and are valid JSON
- Check all directories exist (create any missing)
- Check git initialized
- Print ✅/❌ per check

### tests/conftest.py
Shared pytest fixtures:
```python
import pytest
import json
from pathlib import Path

@pytest.fixture
def project_root():
    return Path(__file__).resolve().parent.parent

@pytest.fixture
def sample_dir(project_root):
    return project_root / "data" / "sample"

@pytest.fixture
def processed_dir(project_root, tmp_path):
    """Use tmp_path for test outputs so tests don't pollute real data."""
    return tmp_path / "processed"

@pytest.fixture
def mock_envelopes(tmp_path):
    """Create mock processed/*.json files for morning_brief tests."""
    processed = tmp_path / "processed"
    processed.mkdir()
    modules = {
        "journal": {"win_rate": 0.62, "patterns": []},
        "earnings_tone": {"results": [{"ticker": "AAPL", "tone_score": -1.2}]},
        "valuation": {"comparisons": [{"stock_a": "NVDA", "stock_b": "AMD", "cheaper": "AMD"}]},
        "portfolio": {"holdings": [{"ticker": "NVDA", "current_price": 900, "pnl": 20750}], "correlations": {}},
        "options": {"tickers": [{"ticker": "NVDA", "max_pain": 880}]},
    }
    for name, data in modules.items():
        envelope = {
            "module": name,
            "generated_at": "2026-02-26T08:00:00-07:00",
            "status": "success",
            "error_message": None,
            "data": data,
        }
        (processed / f"{name}.json").write_text(json.dumps(envelope, indent=2))
    return processed
```

### Test Requirements
- Every module has a test file
- Tests use sample data and mocks only — NO network calls
- Mock yfinance and API calls with unittest.mock.patch
- test_morning_brief.py MUST use the mock_envelopes fixture to pre-create all data/processed/*.json files, then point morning_brief at that directory
- Each test validates: (1) output file created, (2) valid JSON matching envelope schema, (3) status is "success" or "partial"
- tests/__init__.py must exist (required for pytest discovery)
- Run: .venv/bin/python -m pytest tests/ -v

## Config File Defaults

### config/watchlist.json
```json
{
  "tickers": ["AAPL", "NVDA", "MSFT", "AMD", "GOOGL", "AMZN", "TSLA", "META"],
  "comparison_pairs": [["NVDA", "AMD"], ["AAPL", "MSFT"], ["GOOGL", "META"]],
  "earnings_watch": ["AAPL", "NVDA", "MSFT"]
}
```

### config/portfolio.json
```json
{
  "holdings": [
    {"ticker": "NVDA", "shares": 50, "cost_basis": 485.00, "date_acquired": "2025-06-15"},
    {"ticker": "AAPL", "shares": 100, "cost_basis": 178.50, "date_acquired": "2025-03-20"},
    {"ticker": "VOO", "shares": 25, "cost_basis": 420.00, "date_acquired": "2024-11-01"}
  ],
  "cash": 15000,
  "options_positions": []
}
```

### config/settings.json
```json
{
  "correlation_alert_threshold": 0.80,
  "atr_multiplier_default": 2.0,
  "min_correlation_days": 60,
  "max_api_calls_per_run": 20,
  "cache_expiry_hours": 24,
  "output_format": "markdown"
}
```

## Git Workflow
- .gitignore: .env, .venv/, data/raw/, data/processed/, __pycache__/, *.pyc, .pytest_cache/, *.egg-info/
- NOTE: data/outputs/ is NOT gitignored — morning briefs are committed for history
- Commit after each major step
- Final commit: "feat: complete trading intelligence system v1.0"

## Dependencies (requirements.txt)
```
python-dotenv
numpy
pandas
yfinance
requests
pytest
```
Note: scipy is installed separately with a fallback. Do NOT put it in requirements.txt because its install failure should not block the build.

## Active Skills
<!-- Auto-detected by ~/.claude/scripts/select-skills.py — update as the project evolves -->
- llm-app-patterns — LLM integration architecture
- prompt-engineering-patterns — prompt design
- data-engineering-data-pipeline — data flow

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **morning-brief** (882 symbols, 2314 relationships, 72 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## When Debugging

1. `gitnexus_query({query: "<error or symptom>"})` — find execution flows related to the issue
2. `gitnexus_context({name: "<suspect function>"})` — see all callers, callees, and process participation
3. `READ gitnexus://repo/morning-brief/process/{processName}` — trace the full execution flow step by step
4. For regressions: `gitnexus_detect_changes({scope: "compare", base_ref: "main"})` — see what your branch changed

## When Refactoring

- **Renaming**: MUST use `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` first. Review the preview — graph edits are safe, text_search edits need manual review. Then run with `dry_run: false`.
- **Extracting/Splitting**: MUST run `gitnexus_context({name: "target"})` to see all incoming/outgoing refs, then `gitnexus_impact({target: "target", direction: "upstream"})` to find all external callers before moving code.
- After any refactor: run `gitnexus_detect_changes({scope: "all"})` to verify only expected files changed.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Tools Quick Reference

| Tool | When to use | Command |
|------|-------------|---------|
| `query` | Find code by concept | `gitnexus_query({query: "auth validation"})` |
| `context` | 360-degree view of one symbol | `gitnexus_context({name: "validateUser"})` |
| `impact` | Blast radius before editing | `gitnexus_impact({target: "X", direction: "upstream"})` |
| `detect_changes` | Pre-commit scope check | `gitnexus_detect_changes({scope: "staged"})` |
| `rename` | Safe multi-file rename | `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` |
| `cypher` | Custom graph queries | `gitnexus_cypher({query: "MATCH ..."})` |

## Impact Risk Levels

| Depth | Meaning | Action |
|-------|---------|--------|
| d=1 | WILL BREAK — direct callers/importers | MUST update these |
| d=2 | LIKELY AFFECTED — indirect deps | Should test |
| d=3 | MAY NEED TESTING — transitive | Test if critical path |

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/morning-brief/context` | Codebase overview, check index freshness |
| `gitnexus://repo/morning-brief/clusters` | All functional areas |
| `gitnexus://repo/morning-brief/processes` | All execution flows |
| `gitnexus://repo/morning-brief/process/{name}` | Step-by-step execution trace |

## Self-Check Before Finishing

Before completing any code modification task, verify:
1. `gitnexus_impact` was run for all modified symbols
2. No HIGH/CRITICAL risk warnings were ignored
3. `gitnexus_detect_changes()` confirms changes match expected scope
4. All d=1 (WILL BREAK) dependents were updated

## CLI

- Re-index: `npx gitnexus analyze`
- Check freshness: `npx gitnexus status`
- Generate docs: `npx gitnexus wiki`

<!-- gitnexus:end -->
