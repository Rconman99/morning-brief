# Morning Brief

Automated trading analysis system that chains analytical modules into a daily Morning Brief. Built with Claude Code.

## What it does

Runs 6 analysis modules and compiles results into a single markdown report:

- **Journal** — Analyzes trade history for win rates, revenge trading patterns, and day-of-week clustering
- **Earnings** — Scores earnings call transcripts for management confidence vs. hedging language
- **Valuation** — Compares stock pairs across P/E, EV/EBITDA, PEG, Price/FCF, and Price/Book
- **Portfolio** — Calculates P&L, correlations, ATR-based trailing stops, and locked profit
- **Options** — Finds PMCC setups, max pain levels, and high open-interest strikes
- **Morning Brief** — Aggregates all modules into a daily report with BUY/SELL/HOLD/AVOID verdicts

## Setup

```bash
# Clone and enter
git clone https://github.com/Rconman99/morning-brief.git
cd morning-brief

# Create venv and install deps
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Copy the example env file and add your API keys (optional)
cp .env.example .env

# Verify setup
.venv/bin/python scripts/setup_check.py
```

### API Keys (optional)

Create a `.env` file:

```
ALPHA_VANTAGE_KEY=demo
```

The system works without API keys — it falls back to yfinance for market data and sample data for earnings transcripts.

## Usage

```bash
# Run all modules and generate the morning brief
.venv/bin/python scripts/run_all.py

# Run individual modules
.venv/bin/python modules/journal.py
.venv/bin/python modules/portfolio.py

# Output lands in data/outputs/morning_brief_YYYY-MM-DD.md
```

## Configuration

Edit files in `config/` to customize:

- `portfolio.json` — Your holdings (ticker, shares, cost basis)
- `watchlist.json` — Tickers to track, comparison pairs, earnings watch list
- `settings.json` — Correlation thresholds, ATR multipliers, cache settings

## Tests

```bash
.venv/bin/python -m pytest tests/ -v
```

## Project Structure

```
morning-brief/
├── config/          # Portfolio, watchlist, and settings
├── modules/         # Analysis modules (journal, earnings, valuation, etc.)
├── lib/             # Shared utilities (caching, API wrappers, data envelope)
├── scripts/         # Orchestration and setup scripts
├── tests/           # Pytest test suite
└── data/
    ├── sample/      # Sample data for testing
    ├── raw/         # Cached API responses (gitignored)
    ├── processed/   # Module output JSONs (gitignored)
    └── outputs/     # Generated morning briefs
```
