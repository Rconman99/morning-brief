# Polymarket Scanner Module

## Overview

The **Polymarket Scanner** (`modules/polymarket_scanner.py`) integrates real-time prediction market data from Polymarket into the daily Morning Brief. It scans active markets, categorizes them by domain, detects high-impact opportunities, and flags volatile markets for trader attention.

This module is **READ-ONLY** — it analyzes and recommends, never places trades.

## Features

### 1. Market Fetching
- **Primary Source**: Polymarket Gamma API (free, no authentication required)
- **Fallback**: Sample data (`data/sample/polymarket_markets.json`) when API is unavailable
- **Caching**: 4-hour cache to avoid redundant API calls
- **Volume**: Fetches top 200 markets by 24-hour volume

### 2. Market Categorization
Automatically categorizes markets into 9 domains using regex pattern matching:
- **Crypto**: Bitcoin, Ethereum, Solana, etc.
- **Weather**: Temperature, rain, snow, wind conditions
- **Politics**: Elections, executive orders, nominations
- **Economics**: Fed rates, inflation, GDP, recession indicators
- **Geopolitics**: Iran, China, Russia, military conflicts
- **Sports**: NBA, NFL, World Cup, league championships
- **Tech**: AI/AGI announcements, product launches
- **Markets**: S&P 500, stock indices, all-time highs
- **Culture**: Elon Musk tweets, celebrity news, social trends

### 3. Opportunity Detection

#### Weather Markets (Cross-Referenced with Forecasts)
- Fetches real-time weather data from **Open-Meteo API** (free)
- Compares market prices against probabilistic forecast estimates
- Detects edge opportunities (10%+ expected value minimum)
- Supports temperature, rainfall, and snowfall predictions
- Auto-detects city from question using 12 predefined coordinates

**Example**: Market prices "NYC temp >50°F" at 0.15 (15%), but forecast shows 58°F high → estimated prob 80% → +65% edge on YES.

#### Arbitrage Detection
- Flags markets where YES price + NO price ≠ 1.00
- Identifies risk-free trades with ≥2% edge

#### Big Movers
- Tracks 24-hour price changes ≥8%
- Identifies potential momentum plays or overreactions
- Useful for volatility traders

#### Crypto Correlation
- Attempts to cross-reference Bitcoin/Ethereum markets with existing technical signals
- Loads `technical_signals.json` if available

### 4. Market Quality Filters
- **Minimum Volume**: $5,000 in 24-hour trading
- **Minimum Liquidity**: $2,000 available for trades
- **Spread**: Monitors bid-ask spread for execution costs
- **Recency**: Prioritizes markets with active price changes

### 5. Signals
Output signal indicates market conditions:
- **`multiple_opportunities`**: 3+ opportunities across 2+ categories (strong)
- **`opportunities_found`**: 1–2 opportunities (watch)
- **`volatile`**: No clear edges but big price movers detected (caution)
- **`quiet`**: No significant opportunities (routine day)
- **`no_data`**: Unable to fetch market data (error state)

## Module Output

### File Location
`data/processed/polymarket.json`

### Schema
```json
{
  "module": "polymarket_scanner",
  "generated_at": "2026-03-04T02:20:46.077696+00:00",
  "status": "success|partial|error",
  "error_message": null,
  "data": {
    "opportunities": [...],
    "big_movers": [...],
    "weather_analysis": [...],
    "crypto_analysis": [...],
    "category_summary": {...},
    "top_volume": [...],
    "markets_scanned": 200,
    "signal": "volatile|multiple_opportunities|opportunities_found|quiet|no_data",
    "signal_detail": "Human-readable summary",
    "data_source": "gamma_api|sample_data|none",
    "analyzed_at": "ISO timestamp"
  }
}
```

### Opportunity Object
```json
{
  "id": "market-id",
  "question": "Will Bitcoin reach $150,000?",
  "category": "crypto",
  "yes_price": 0.45,
  "no_price": 0.55,
  "volume_24h": 1500000,
  "liquidity": 850000,
  "opportunity_type": "weather_edge|arbitrage|crypto_signal",
  "recommended_side": "YES|NO|BOTH",
  "estimated_probability": 0.72,
  "market_price": 0.45,
  "edge_pct": 27.0,
  "ev_per_dollar": 0.27,
  "confidence": "high|medium|low",
  "source": "weather_forecast|price_mismatch|crypto_signal",
  "detail": "Detailed reasoning..."
}
```

## Data Sources

### Gamma API (Primary)
```
GET https://gamma-api.polymarket.com/markets
Parameters:
  - active: "true"
  - closed: "false"
  - order: "volume24hr" (sort by volume)
  - limit: 100 (paginate for up to 200 markets)
```
**Response Fields**:
- `id`: Unique market identifier
- `question`: Market question
- `outcomePrices`: [YES price, NO price] as JSON string or array
- `volume24hr`: 24-hour volume in USDC
- `liquidity`: Available liquidity in USDC
- `bestBid`, `bestAsk`: Current bid/ask prices
- `spread`: Current bid-ask spread
- `oneDayPriceChange`: % change in last 24 hours (-0.05 to +0.05)
- `endDate`: ISO timestamp when market resolves

### Open-Meteo Weather API
```
GET https://api.open-meteo.com/v1/forecast
Parameters:
  - latitude, longitude
  - daily: temperature_2m_max, temperature_2m_min, precipitation_sum, rain_sum, snowfall_sum, wind_speed_10m_max
  - temperature_unit: "fahrenheit"
  - timezone: "America/New_York" (or other TZ)
  - forecast_days: 3
```
**Response**: Daily forecast data for next N days

### Sample Data (Fallback)
`data/sample/polymarket_markets.json` contains 12 representative markets:
- 3 crypto markets
- 3 weather markets
- 2 politics markets
- 2 economics markets
- 1 geopolitics market
- 1 tech market

Used when API is unavailable or during testing.

## Implementation Details

### Weather Probability Estimation
Temperature markets estimate probability based on forecast high and threshold:
- **margin > 10°F**: 92% estimated probability
- **margin 5–10°F**: 80%
- **margin 2–5°F**: 65%
- **margin -2 to +2°F**: 45% (highly uncertain)
- **margin -5 to -2°F**: 25%
- **margin < -5°F**: 10%

Accounts for typical forecast error of ~5°F.

### Arbitrage Edge Calculation
```python
total = YES_price + NO_price
if total < 0.97:  # Underpriced
    arbitrage_edge = 1.00 - total
    # Can lay off both sides for profit
if total > 1.03:  # Overpriced
    arbitrage_edge = total - 1.00
    # Can buy both sides for profit
```

### Crypto Signal Detection
Loads `data/processed/technical_signals.json` (if available from technical analysis module) and cross-references Bitcoin/Ethereum markets with existing indicators (RSI, composite score). Flags as bullish/bearish/neutral based on technical composite score.

### Performance
- **Market Fetching**: ~0.5–2 seconds (API call + parsing)
- **Analysis**: <100ms for 200 markets
- **Total Runtime**: ~1–3 seconds
- **Cache Hit**: <100ms when using cached data

## Error Handling

| Scenario | Status | Behavior |
|----------|--------|----------|
| API fetch fails, sample data available | `partial` | Uses sample data, logs warning |
| API fetch succeeds | `success` | Returns live market analysis |
| Both API and sample fail | `error` | Returns empty opportunities, error message |
| Weather API fails for weather market | None | Logs debug message, skips weather analysis |
| requests library not installed | None | Returns empty market list, logs warning |

## Testing

### Test Suite: `tests/test_polymarket_scanner.py`
- **23 test cases** covering all major functions
- **100% pass rate** on module execution

#### Test Categories

**Categorization** (7 tests)
- Crypto, weather, politics, economics, geopolitics, sports, other

**Price Parsing** (4 tests)
- String format `"[0.72, 0.28]"`
- List format `[0.72, 0.28]`
- None and invalid inputs

**City Detection** (3 tests)
- NYC, London, unknown city

**Analysis Engine** (6 tests)
- Empty markets
- Sample markets
- Big movers
- Category summary
- Opportunity fields
- Signal generation

**Integration** (3 tests)
- Fallback to sample data
- Envelope creation
- Sample data loading

### Running Tests
```bash
cd /sessions/zen-laughing-curie/mnt/morning-brief
python3 -m pytest tests/test_polymarket_scanner.py -v
```

**Output**:
```
23 passed in 13.57s
```

## Integration with Morning Brief

The Morning Brief (`modules/morning_brief.py`) loads `polymarket.json` and includes:
- **Opportunities Section**: Top 3–5 market opportunities with recommendation
- **Volatile Markets**: Big movers worth watching
- **Signal**: Overall market condition (quiet/volatile/opportunities)
- **Category Breakdown**: Market counts and opportunity distribution

Example Morning Brief snippet:
```markdown
## Polymarket Opportunities

**Signal**: Volatile (5 big movers, no clear edges today)

| Market | Price | Edge | Confidence |
|--------|-------|------|------------|
| Will Bitcoin reach $150K? | 0.45 YES | N/A | — |
| Iran Strait Closure | 0.71 YES | — | — |

**Categories**: 8 domains scanned, 200 markets, 0 high-conviction edges

*Note: Historical volatility detected. No weather or arbitrage opportunities today.*
```

## Configuration & Customization

### Adjustable Thresholds (in module top section)
```python
MIN_VOLUME_24H = 5000       # Minimum daily volume
MIN_LIQUIDITY = 2000        # Minimum available liquidity
MIN_EDGE_PCT = 0.10         # Minimum 10% edge for flagging
BIG_MOVE_THRESHOLD = 0.08   # Flag 8%+ price moves
```

### City Coordinates
Add more cities for weather market matching:
```python
CITY_COORDS = {
    "nyc": (40.7128, -74.0060),
    "paris": (48.8566, 2.3522),  # Add more cities
    # ...
}
```

### Category Patterns
Extend regex patterns to catch new market types:
```python
CATEGORY_PATTERNS = {
    "crypto": [r"bitcoin", r"ethereum", r"solana", ...],
    "sports": [r"nba", r"nfl", ...],
    # Add or modify patterns
}
```

## Future Enhancements

1. **Order Book Analysis**: Detect large hidden orders that influence price
2. **Sentiment Scoring**: Analyze Polymarket user comments/activity
3. **News Integration**: Cross-reference breaking news with market movements
4. **Macro Calendars**: Alert before major economic data releases
5. **ML Model**: Train probability models on historical resolution data
6. **Risk Dashboard**: Track portfolio exposure to Polymarket outcomes
7. **Alert System**: Real-time Slack/email notifications for hot markets

## Disclaimer

**This module is for analysis only. It does not:**
- Place trades automatically
- Guarantee profit
- Provide financial advice
- Account for personal risk tolerance

All trading decisions must be made by the trader after independent review.

---

**Module Author**: Trading Intelligence System  
**Last Updated**: 2026-03-04  
**Version**: 1.0
