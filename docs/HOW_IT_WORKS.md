# Trading Intelligence System

## What It Does

Trading Intelligence is an automated daily analysis system that reads your real portfolio, runs it through six analytical modules, and produces a Morning Brief telling you what to do: **BUY, SELL, HOLD, or AVOID** each position — with the math to back it up.

It runs every morning before market open. No manual input required beyond keeping your portfolio config up to date.

## Why It Makes You Money

Most retail traders lose money for three reasons: emotional decisions, no systematic risk management, and ignoring their own patterns. This system attacks all three:

**1. It removes emotion from exit decisions.**
The trailing stop system uses ATR (Average True Range) — a volatility-based measure — to calculate exactly where your stop should be for each position. When QQQ's trailing stop at $585.96 gets within 5% of its $605.55 price, the system flags SELL. No gut feeling, no "maybe it'll bounce." Math.

**2. It catches your bad habits before you repeat them.**
The trade journal module analyzes every trade you've logged and finds patterns you'd never spot yourself. Trading on Tuesdays and losing every time? It'll flag that. Rage-trading TSLA after three losses in a row? It'll catch that revenge pattern and generate a rule: stop trading that ticker for 24 hours.

**3. It reads earnings calls better than you can.**
The earnings module sends transcripts to Claude AI for semantic analysis. When a CEO says "we anticipate strong growth," a human might miss the hedge word. Claude catches the nuance: that's cautiously optimistic, not bearish. The system scores management tone from -5 (crisis) to +5 (blowout) and uses it to inform BUY/AVOID verdicts.

**4. It finds relative value between stocks.**
The valuation module compares pairs of stocks on five fundamental metrics (P/E, EV/EBITDA, Price/FCF, PEG, Price/Book). If NVDA is cheaper than PLTR on 4 of 4 comparable metrics, the system says BUY NVDA. Simple relative value, no guesswork.

**5. It monitors concentration risk.**
If two holdings have >80% correlation, the system flags it. QQQ and SOXL at 0.89 correlation means they move almost identically — you're taking the same bet twice. The brief surfaces this so you can decide if that's intentional.

**6. It keeps score.**
The scorecard module tracks every verdict the system has made and checks whether it was right 5, 10, and 30 days later. BUY calls are scored against SPY — did the stock outperform the market? SELL calls are scored on the inverse. Over time, this builds a track record showing whether the system's signals actually work.

---

## The Six Modules

### 1. Trade Journal (`journal.py`)

**What it reads:** Your trade log CSV (date, ticker, direction, entry/exit price, P&L)

**What it calculates:**
- Overall win rate
- Win rate by day of week
- Revenge trading detection (3+ losses on the same ticker within 48 hours)
- Day-of-week clustering (any day with <30% win rate on 3+ trades)

**What it outputs:** Behavioral patterns and checklist rules to follow before trading.

**Why it matters:** The best edge in trading is not repeating your own mistakes. This module forces you to confront the data.

---

### 2. Earnings Tone Analysis (`earnings.py`)

**What it reads:** Earnings call transcripts (placed manually in `data/raw/earnings/`)

**How it works — two paths:**

| Path | When | Method |
|------|------|--------|
| **Claude AI** | `ANTHROPIC_API_KEY` is set | Sends transcript to Claude Haiku for semantic analysis |
| **Regex fallback** | No API key | Counts hedge phrases vs. definitive phrases |

**AI analysis returns:**
- **Tone Score** (-5 to +5): Semantic read of management confidence
- **Confidence Score** (0 to 1): Ratio of confident vs. hedging language
- **Risk Factors**: Up to 5 specific risks mentioned in the call
- **Summary**: 2-3 sentence takeaway

**Hedge phrases counted:** "we anticipate", "we expect", "we believe", "we hope", "potentially", "may", "might", "could", "uncertain"

**Definitive phrases counted:** "we will", "we are confident", "we are committed", "we have decided", "our plan is", "we are certain"

**Risk keywords:** "risk", "headwind", "challenge", "uncertainty", "decline", "pressure"

**Scoring formula (regex path):**
```
confidence = definitive_count / max(1, definitive_count + hedge_count)
tone_score = (confidence * 10) - 5
```

**Why it matters:** A tone score <= -2 triggers an AVOID verdict. A positive tone combined with favorable valuation triggers BUY. Management tone is one of the strongest forward indicators — executives know their numbers before you do.

---

### 3. Valuation Comparisons (`valuation.py`)

**What it reads:** Comparison pairs from your watchlist config

**Five metrics compared:**

| Metric | What It Measures | Lower = Cheaper |
|--------|-----------------|-----------------|
| **P/E TTM** | Price per dollar of trailing earnings | Yes |
| **EV/EBITDA** | Enterprise value per dollar of operating profit | Yes |
| **Price/FCF** | Price per dollar of free cash flow | Yes |
| **PEG** | P/E adjusted for growth rate | Yes |
| **Price/Book** | Price per dollar of book value | Yes |

**Decision logic:**
- Count metrics where Stock A < Stock B (A is cheaper)
- Need >= 2 comparable metrics (both stocks have data)
- If A wins on more metrics: A is cheaper
- If tied or <2 metrics: inconclusive

**Data sources:** Alpha Vantage API (primary), yfinance (fallback)

**Why it matters:** Relative valuation is the simplest edge. If two companies in the same space have similar growth but one trades at half the P/E, the cheaper one has a margin of safety. The system automates this comparison daily.

---

### 4. Portfolio Analysis (`portfolio.py`)

**What it reads:** Your holdings (ticker, shares, cost basis) and 2 years of price history

**Indicators calculated per holding:**

#### P&L
```
pnl = (current_price - cost_basis) * shares
```
Straightforward. Uses the most recent closing price from history (more reliable than real-time quotes).

#### 14-Day ATR (Average True Range)
```
True Range = max(High - Low, |High - PrevClose|, |Low - PrevClose|)
ATR = 14-day rolling average of True Range
```
ATR measures how much a stock moves on a typical day. High ATR = volatile stock. Used to set position-appropriate trailing stops — a volatile stock gets a wider stop so you don't get shaken out by normal noise.

#### Trailing Stop
```
trailing_stop = current_price - (2.0 * ATR)
```
Two ATR widths below the current price. This means your stop is set at roughly 2 average daily moves below the current price. Tight enough to protect profits, wide enough to avoid being stopped out by noise.

#### Locked Profit
```
locked_profit = (trailing_stop - cost_basis) * shares
```
If the trailing stop is above your cost basis, you've "locked in" profit even if the stop triggers. If negative, hitting the stop means a loss.

#### Correlation Matrix
For every pair of holdings:
- Align daily returns on common trading dates
- Require minimum 60 overlapping days
- Calculate Pearson correlation (-1 to +1)
- Flag any pair >= 0.80 as high correlation risk

**Why it matters:** ATR-based trailing stops are the single most important risk management tool. They adapt to each stock's volatility instead of using arbitrary percentages. The correlation matrix catches hidden concentration — if 3 of your 9 holdings move in lockstep, you don't have 9 positions, you have 7.

---

### 5. Options Analysis (`options.py`)

**What it reads:** Your holdings, live options chains from yfinance

**Indicators calculated per holding:**

#### Max Pain
The price at which option holders (both calls and puts) lose the most money at expiration.
```
For each possible settlement price:
    total_pain = sum of all call holders' losses + all put holders' losses
max_pain = price where total_pain is lowest
```
Market makers often pin prices near max pain into expiry. If your stock is far from max pain with 3 weeks to expiry, expect gravitational pull.

#### PMCC (Poor Man's Covered Call) Setup
A capital-efficient income strategy using LEAPS:

**Long leg (LEAPS, deep ITM):**
- Strike at 85% of current price (~0.75 delta)
- Expiry >= 180 days out
- Acts as a stock substitute at lower cost

**Short leg (front month, slightly OTM):**
- Strike at 105% of current price (~0.30 delta)
- Expiry >= 20 days out
- Collects premium (income)

```
Net Debit = Long Price - Short Price
```

#### Black-Scholes Delta
```
d1 = (ln(S/K) + (r + sigma^2/2) * T) / (sigma * sqrt(T))
delta = N(d1)    # cumulative normal distribution
```
Where S = stock price, K = strike, r = 0.045 (risk-free rate), sigma = 30-day historical volatility, T = days to expiry / 365.

Delta tells you how much the option moves per $1 move in the stock. 0.75 delta = option gains $0.75 for every $1 the stock gains.

#### Top 3 Strikes by Open Interest
Combined call + put open interest at each strike, ranked. High OI strikes are magnetic — they attract price action and represent where the most money is at stake.

**Why it matters:** Max pain gives you a gravity target for the current expiry cycle. PMCC setups let you generate income on existing holdings with defined risk. OI analysis shows you where the big money is positioned.

---

### 6. Morning Brief (`morning_brief.py`)

**What it reads:** All 5 module outputs + scorecard

**What it produces:** A daily report with verdicts for every position.

#### Verdict Rules (in priority order)

| Verdict | Trigger | What It Means |
|---------|---------|---------------|
| **SELL** | Trailing stop is within 5% of current price | Price is approaching your stop. Risk/reward has deteriorated. |
| **AVOID** | Earnings tone <= -2 OR more expensive than peer on 4+ metrics | Fundamentals are warning you away. |
| **BUY** | Cheaper than peer on 3+ metrics AND positive/neutral earnings | Relative value + sentiment alignment. |
| **REVIEW** | No valuation data AND no portfolio data | Can't make a determination. Investigate manually. |
| **HOLD** | Default — none of the above triggered | No strong signal. Stay the course. |

#### Verdict Scorecard
Every verdict gets logged with the date, ticker, price, and SPY price. After 5, 10, and 30 days:
- **BUY/HOLD**: Win if the stock outperformed SPY
- **SELL/AVOID**: Win if the stock underperformed SPY
- **REVIEW**: Not scored

This creates an honest track record. If the system's BUY calls consistently beat the market, the signals are working. If not, you know to adjust the thresholds.

---

## How the Modules Chain Together

```
run_all.py (orchestrator)
   |
   ├── journal.py ────────────> journal.json
   ├── earnings.py ───────────> earnings_tone.json
   ├── valuation.py ──────────> valuation.json
   ├── portfolio.py ──────────> portfolio.json
   ├── options.py ────────────> options.json
   ├── scorecard.py ──────────> scorecard.json
   |
   ├── morning_brief.py ──────> Reads all 6, generates verdicts
   |   ├── morning_brief_YYYY-MM-DD.md
   |   └── verdict_log.json (appends)
   |
   └── brief_html.py ─────────> morning_brief_YYYY-MM-DD.html
```

Each module is independent — if earnings fails, the rest still run. The morning brief gracefully degrades, showing "Data unavailable" for any failed section. This means you always get a brief, even on bad data days.

---

## Configuration

### Portfolio (`config/portfolio.json`)
Your actual holdings: ticker, shares, cost basis. Update this when you buy or sell.

### Watchlist (`config/watchlist.json`)
- **tickers**: Universe of stocks you're tracking
- **comparison_pairs**: Which stocks to compare on valuation (should be same-sector peers)
- **earnings_watch**: Which tickers to look for transcripts

### Settings (`config/settings.json`)
| Setting | Default | What It Controls |
|---------|---------|-----------------|
| `correlation_alert_threshold` | 0.80 | Correlation level that triggers a warning |
| `atr_multiplier_default` | 2.0 | How many ATRs below price for trailing stop |
| `min_correlation_days` | 60 | Minimum overlapping days to calculate correlation |
| `cache_expiry_hours` | 24 | How long API responses are cached |

---

## The Edge

This system doesn't predict the future. It does three things consistently:

1. **Tells you when to get out** — ATR trailing stops tighten as volatility compresses and widen when it expands. You'll never hold a position that's given back all its gains because your stop adapts.

2. **Tells you where the value is** — Relative valuation across 5 metrics finds the cheaper stock in each comparison pair. Over long periods, buying relatively cheap and avoiding relatively expensive outperforms.

3. **Holds you accountable** — The scorecard tracks every call. The journal catches every pattern. You can't lie to the data. Over time, this feedback loop makes you a better trader because you see exactly which decisions worked and which didn't.

The compounding effect is the real edge: better exits preserve capital, better entries compound it, and pattern awareness prevents the blowup trades that wipe out months of gains. This is not a get-rich-quick system. It's a get-rich-slowly system that keeps you from getting poor quickly.

---

*This is analysis, not financial advice. All trading decisions are yours.*
