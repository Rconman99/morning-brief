"""Learning system for the Morning Brief HTML dashboard.

Provides an inline, interactive education layer: slide-out concept panel,
ELI5/Detailed toggle, contextual quizzes, guided tour, learning path,
and a 'How to Use This Brief' guide.  Everything renders into the
self-contained HTML output — no external dependencies.
"""

import json
from typing import Optional

# ──────────────────────────────────────────────────────────────────────
# 1. KNOWLEDGE BASE — 45 concepts, 6 categories, 4 tiers
# ──────────────────────────────────────────────────────────────────────

KNOWLEDGE_BASE = {
    # ── TIER 1 — Foundations ──────────────────────────────────────────
    "what_is_a_stock": {
        "title": "What Is a Stock?",
        "category": "market_basics",
        "tier": 1,
        "eli5": "A stock is a tiny piece of ownership in a company. If the company is a pizza, a stock is one slice. When the company does well, your slice becomes more valuable.",
        "detailed": "A stock (equity share) represents fractional ownership of a corporation. Shareholders are entitled to a proportional claim on assets and earnings. Stocks trade on exchanges (NYSE, NASDAQ) via bid-ask auctions. Price reflects the market's consensus of discounted future cash flows.",
        "quiz": [{"q": "If a company has 1,000 shares and you own 10, what percentage of the company do you own?", "choices": ["0.1%", "1%", "10%"], "answer": 1, "explanation": "10 / 1,000 = 1%. You own 1% of the company and are entitled to 1% of its profits."}],
        "connects_narrative": "Stocks are the atoms of everything in this brief — every ticker, every price, every verdict starts here.",
        "connects_to": ["market_hours", "what_moves_prices"],
        "resources": [{"label": "Investopedia: What Is a Stock?", "url": "https://www.investopedia.com/terms/s/stock.asp"}]
    },
    "market_hours": {
        "title": "Market Hours & Sessions",
        "category": "market_basics",
        "tier": 1,
        "eli5": "The stock market is like a store with set hours: 9:30 AM to 4:00 PM Eastern, Monday through Friday. There's also pre-market (early birds) and after-hours (night owls) trading with less activity.",
        "detailed": "Regular trading hours: 9:30-16:00 ET. Pre-market: 4:00-9:30 ET. After-hours: 16:00-20:00 ET. Extended hours have wider spreads and lower liquidity. Key moments: 9:30-10:00 (opening volatility), 11:30-13:00 (lunch lull), 15:00-16:00 (closing push). The Trading Windows section in this brief shows real-time market status.",
        "quiz": [{"q": "Why might you want to avoid trading in the first 15 minutes after market open?", "choices": ["Markets are closed then", "Volatility is highest — prices swing wildly on overnight order flow", "Stocks only go down in the morning"], "answer": 1, "explanation": "The opening 15 minutes see heavy order flow from overnight news and pre-market activity, causing erratic price swings. Experienced traders often wait for patterns to stabilize."}],
        "connects_narrative": "The Trading Windows section uses this concept to show you when markets are open for each exchange.",
        "connects_to": ["what_is_a_stock", "what_moves_prices"],
        "resources": [{"label": "NYSE Market Hours", "url": "https://www.nyse.com/markets/hours-calendars"}]
    },
    "what_moves_prices": {
        "title": "What Moves Stock Prices?",
        "category": "market_basics",
        "tier": 1,
        "eli5": "Stock prices move based on supply and demand — more buyers push prices up, more sellers push prices down. News, earnings, and how people feel about the future all affect whether people want to buy or sell.",
        "detailed": "Price is set by the balance of buy and sell orders at the margin. Key drivers: earnings reports (actual vs expected), macroeconomic data (jobs, inflation, Fed policy), sector rotation, institutional fund flows, and sentiment. Technical analysis studies price patterns; fundamental analysis studies business value. Both appear in this brief.",
        "quiz": [{"q": "A company beats earnings expectations by 20% but the stock drops. What's the most likely explanation?", "choices": ["The market is broken", "Guidance was weak — investors look forward, not backward", "Earnings don't matter"], "answer": 1, "explanation": "Markets are forward-looking. Even a great quarter can cause a sell-off if the company's future guidance disappoints expectations. 'Buy the rumor, sell the news' captures this dynamic."}],
        "connects_narrative": "Every section in this brief measures something that moves prices: technicals, earnings tone, sentiment, valuations.",
        "connects_to": ["bull_bear", "fomo", "loss_aversion"],
        "resources": [{"label": "What Moves Stock Prices?", "url": "https://www.investopedia.com/articles/basics/04/100804.asp"}]
    },
    "bull_bear": {
        "title": "Bull vs Bear Markets",
        "category": "market_basics",
        "tier": 1,
        "eli5": "A bull market is when stocks are generally going up and people are optimistic — like a bull charging forward. A bear market is when stocks are falling and people are pessimistic — like a bear swiping down. The Risk Dashboard tells you which environment we're in.",
        "detailed": "A bull market is typically defined as a 20%+ rise from recent lows; a bear market is a 20%+ decline from recent highs. The Risk Dashboard's regime indicator (LOW_RISK, NORMAL, CAUTION, DANGER) approximates this using VIX, yield curves, and price trends. Your strategy should adapt: bull markets reward buying dips, bear markets reward capital preservation.",
        "quiz": [{"q": "The Risk Dashboard shows 'CAUTION' regime. What does this suggest?", "choices": ["Load up on risky stocks", "Be selective — reduce position sizes and tighten stops", "Sell everything immediately"], "answer": 1, "explanation": "CAUTION means elevated risk but not a full bear market. The prudent approach is to be more selective, use smaller position sizes, and keep tighter stop losses rather than going all-in or all-out."}],
        "connects_narrative": "The Risk Dashboard regime banner directly reflects bull/bear conditions for your trading decisions.",
        "connects_to": ["risk_management", "what_moves_prices"],
        "resources": [{"label": "Bull vs Bear Markets Explained", "url": "https://www.investopedia.com/terms/b/bullmarket.asp"}]
    },
    "brokers": {
        "title": "Brokers & Order Execution",
        "category": "market_basics",
        "tier": 1,
        "eli5": "A broker is the middleman between you and the stock exchange. You tell your broker 'buy 10 shares of NVDA' and they go find a seller for you. Modern brokers like Schwab, Fidelity, or Interactive Brokers do this electronically in milliseconds.",
        "detailed": "Brokers route your orders to exchanges or market makers. Key considerations: commission structure (most are $0 for stocks now), execution quality (PFOF vs direct routing), margin rates, options approval levels, and platform features. Order types (market, limit, stop) determine how your broker executes.",
        "quiz": [{"q": "What's the main risk of a market order vs a limit order?", "choices": ["Market orders take longer", "You might get a worse price than expected, especially in volatile stocks", "Limit orders are more expensive"], "answer": 1, "explanation": "A market order fills immediately at the best available price, which can be far from the last trade price in volatile or illiquid stocks. A limit order guarantees your price but might not fill."}],
        "connects_narrative": "Position Sizing recommendations assume you'll use limit orders at the suggested stop-loss levels.",
        "connects_to": ["order_types", "position_sizing_process"],
        "resources": [{"label": "Choosing a Broker", "url": "https://www.investopedia.com/best-online-brokers-4587872"}]
    },
    "order_types": {
        "title": "Order Types",
        "category": "market_basics",
        "tier": 1,
        "eli5": "There are different ways to tell your broker to buy or sell. A market order says 'buy now at whatever price.' A limit order says 'buy only at this price or better.' A stop order says 'sell if the price drops to this level' — like a safety net.",
        "detailed": "Market order: immediate fill at best available price. Limit order: fill only at specified price or better (may not fill). Stop order: triggers a market order when price hits the stop level. Stop-limit: triggers a limit order at the stop. Trailing stop: adjusts the stop price as the stock moves in your favor — this is what the Portfolio section tracks.",
        "quiz": [{"q": "Your trailing stop is set at $850 for a stock currently at $900. The stock drops to $849. What happens?", "choices": ["Nothing — stops only work on exact prices", "A sell order is triggered to protect your gains", "The stop automatically moves down to $799"], "answer": 1, "explanation": "When the price hits or crosses the stop level ($850), a sell order is triggered. This is how trailing stops protect profits — they lock in gains as the stock rises and sell automatically if it reverses."}],
        "connects_narrative": "The Trail Stop column in Portfolio Holdings uses stop orders. The Position Sizing section recommends stop-loss levels based on ATR.",
        "connects_to": ["brokers", "trailing_stop", "atr"],
        "resources": [{"label": "Order Types Explained", "url": "https://www.investopedia.com/terms/o/order.asp"}]
    },
    "fomo": {
        "title": "FOMO (Fear of Missing Out)",
        "category": "psychology",
        "tier": 1,
        "eli5": "FOMO is when you see a stock going up and feel like you HAVE to buy it right now or you'll miss the rocket ship. It's the voice in your head screaming 'Buy! Buy!' — and it almost always leads to buying at the top.",
        "detailed": "FOMO causes traders to enter positions late in a move, chasing momentum after the easy gains are gone. Neurologically, it activates the same reward pathways as gambling. Antidote: have a pre-defined watchlist with entry criteria (this brief provides both). If you missed the move, wait for the next setup rather than chasing.",
        "quiz": [{"q": "A stock is up 15% today on news. You didn't own it. What's the best response?", "choices": ["Buy immediately — it's going higher!", "Check this brief's verdict and wait for a proper setup", "Short it — it must come down"], "answer": 1, "explanation": "Chasing a 15% move puts you at risk of buying at the peak. The brief's verdict system evaluates whether a stock is worth entering based on multiple factors, not just momentum. Discipline beats FOMO."}],
        "connects_narrative": "The Verdict system helps combat FOMO by giving you objective buy/hold/avoid signals instead of emotional reactions.",
        "connects_to": ["loss_aversion", "overtrading", "patience"],
        "resources": [{"label": "Trading Psychology: FOMO", "url": "https://www.investopedia.com/terms/r/regrettheory.asp"}]
    },
    "loss_aversion": {
        "title": "Loss Aversion",
        "category": "psychology",
        "tier": 1,
        "eli5": "Losing $100 feels about twice as bad as gaining $100 feels good. This is why traders hold losing stocks too long ('it'll come back!') and sell winners too early ('let me lock in this profit!'). Your brain is wired to avoid pain, not maximize gains.",
        "detailed": "Loss aversion (Kahneman & Tversky, 1979) shows losses are psychologically weighted ~2x gains. In trading, this creates the disposition effect: holding losers (to avoid realizing losses) and cutting winners (to realize gains). Trailing stops counteract this by automating the sell decision. The journal's revenge trading detection catches the spiral when loss aversion leads to doubling down.",
        "quiz": [{"q": "You bought at $100, it's now $80. Your trailing stop was $90 but you didn't set it. What should you do?", "choices": ["Hold and hope — it'll come back eventually", "Evaluate the brief's current verdict. If SELL, accept the loss and move on", "Buy more to lower your average cost"], "answer": 1, "explanation": "Loss aversion makes you want to hold and hope, but the data should drive your decision. If the brief's verdict says SELL, the rational move is to accept the loss. Averaging down without a thesis is just throwing good money after bad."}],
        "connects_narrative": "Trailing stops in Portfolio, journal pattern detection, and verdict automation all help overcome loss aversion.",
        "connects_to": ["fomo", "first_loss", "trailing_stop"],
        "resources": [{"label": "Loss Aversion in Trading", "url": "https://www.investopedia.com/terms/l/loss-psychology.asp"}]
    },
    "confirmation_bias": {
        "title": "Confirmation Bias",
        "category": "psychology",
        "tier": 1,
        "eli5": "You see what you want to see. If you love NVDA, you'll notice every bullish article and ignore every warning sign. It's like only listening to friends who agree with you. This brief gives you ALL the signals — bullish AND bearish — so you can't cherry-pick.",
        "detailed": "Confirmation bias causes traders to seek information that confirms their existing position and ignore contradictory evidence. In practice: you hold NVDA, so you focus on the bullish MACD crossover and dismiss the overbought RSI. This brief counteracts it by presenting all signals side-by-side with a mechanical verdict that weighs bull and bear factors equally.",
        "quiz": [{"q": "You're bullish on a stock. The brief shows BUY verdict but RSI is 78 (overbought). How should you interpret this?", "choices": ["Ignore the RSI — the verdict confirms my view", "Note the tension: the verdict is positive overall but the overbought RSI means wait for a pullback entry", "The RSI must be wrong"], "answer": 1, "explanation": "Confirmation bias would have you ignore the RSI warning. A complete analysis acknowledges both: the overall setup is bullish (BUY verdict) but the entry timing could be better (overbought RSI). Wait for RSI to cool before entering."}],
        "connects_narrative": "The brief presents technical, fundamental, and sentiment data together specifically to counteract confirmation bias.",
        "connects_to": ["overconfidence", "verdict_framework", "decision_when_conflict"],
        "resources": [{"label": "Confirmation Bias in Investing", "url": "https://www.investopedia.com/terms/c/confirmation-bias.asp"}]
    },
    "overtrading": {
        "title": "Overtrading",
        "category": "psychology",
        "tier": 1,
        "eli5": "Trading too much is like eating too much — it feels productive but makes things worse. Every trade has costs (spread, slippage, taxes) and emotional weight. The best traders often do LESS, not more. Your journal tracks this.",
        "detailed": "Overtrading manifests as excessive frequency (too many trades), excessive size (too large per trade), or both. Causes: boredom, revenge trading after losses, or mistaking activity for productivity. The Trade Journal section tracks patterns and flags revenge trading (3+ losses on the same ticker in 48 hours). Antidote: follow the brief's position sizing and wait for high-conviction setups only.",
        "quiz": [{"q": "The journal flags 'revenge trading: TSLA 3 consecutive losses within 48hrs.' What should you do?", "choices": ["Try one more TSLA trade to recover the losses", "Step away from TSLA entirely and review your process", "Switch to options on TSLA for faster recovery"], "answer": 1, "explanation": "Revenge trading is trying to recover losses by trading the same stock more aggressively. It almost always makes things worse. The correct response is to stop trading that ticker, review what went wrong, and wait for the next day's brief."}],
        "connects_narrative": "The Trade Journal's pattern detection specifically watches for overtrading behaviors like revenge trading and day-of-week clustering.",
        "connects_to": ["fomo", "loss_aversion", "patience"],
        "resources": [{"label": "How to Stop Overtrading", "url": "https://www.investopedia.com/articles/trading/06/overtrading.asp"}]
    },
    "first_loss": {
        "title": "Your First Loss Is Your Best Loss",
        "category": "psychology",
        "tier": 1,
        "eli5": "When a trade goes against you, cutting it early hurts less than holding and hoping. A $200 loss today is better than a $2,000 loss next week. The trailing stop is your 'eject button' — use it.",
        "detailed": "This principle, popular among professional traders, means: when your thesis is invalidated, exit immediately. The math is brutal: a 50% loss requires a 100% gain to recover. A 10% loss only needs an 11% gain. Trailing stops (ATR-based, as shown in this brief) automate this discipline. The position sizer ensures no single loss exceeds your risk budget.",
        "quiz": [{"q": "Your position is down 8% and your stop was at -10%. The stock just had bad earnings. What do you do?", "choices": ["Wait for the stop to trigger — rules are rules", "Re-evaluate: if the thesis is broken, exit now rather than waiting for -10%", "Remove the stop to give it more room"], "answer": 1, "explanation": "The stop is a maximum pain threshold, not a target. If new information (bad earnings) breaks your thesis, exiting at -8% is better than waiting for -10%. Your first loss IS your best loss. Never remove stops."}],
        "connects_narrative": "Portfolio trailing stops and position sizing work together to enforce this principle systematically.",
        "connects_to": ["loss_aversion", "trailing_stop", "risk_management"],
        "resources": [{"label": "Cut Your Losses Short", "url": "https://www.investopedia.com/articles/trading/08/cut-losses.asp"}]
    },
    "patience": {
        "title": "Patience & Sitting on Hands",
        "category": "psychology",
        "tier": 1,
        "eli5": "The best trade is often no trade at all. Like a fisherman waiting for the right fish, profitable traders wait for setups that match their criteria. This brief tells you when to act AND when to sit tight.",
        "detailed": "Jesse Livermore: 'Money is made by sitting, not trading.' Most market days don't offer high-conviction opportunities. The verdict system's HOLD rating is not a failure — it's discipline. The brief's Opportunity Scanner specifically looks for setups that meet multiple criteria simultaneously, filtering out the noise so you only see what matters.",
        "quiz": [{"q": "The brief shows 4 HOLD verdicts and no BUY signals today. What's the right response?", "choices": ["Find something else to buy — can't waste a trading day", "Accept that today isn't a setup day and review your existing positions", "Override the verdicts and trade based on gut feeling"], "answer": 1, "explanation": "Not every day is a trading day. HOLD means your existing positions are fine and no new entries meet the criteria. Using this time to review positions, tighten stops, or simply rest is more productive than forcing trades."}],
        "connects_narrative": "HOLD verdicts and empty Opportunity Scanner results are the brief telling you to be patient.",
        "connects_to": ["overtrading", "fomo", "verdict_framework"],
        "resources": [{"label": "The Virtue of Patience in Trading", "url": "https://www.investopedia.com/articles/trading/02/112502.asp"}]
    },

    # ── TIER 2 — Core ─────────────────────────────────────────────────
    "rsi": {
        "title": "RSI (Relative Strength Index)",
        "category": "technical",
        "tier": 2,
        "eli5": "RSI is like a speedometer for buying and selling pressure. It goes from 0 to 100. Above 70 means the stock has been bought aggressively (might be tired). Below 30 means it's been sold hard (might bounce). Between 30-70 is cruising speed.",
        "detailed": "RSI(14) measures the ratio of average gains to average losses over 14 periods. Formula: RSI = 100 - (100 / (1 + RS)), where RS = avg gain / avg loss. >70 = overbought (potential reversal down), <30 = oversold (potential bounce). Not a timing signal alone — combine with MACD and volume. Divergences (price makes new high but RSI doesn't) are powerful reversal signals.",
        "quiz": [{"q": "A stock's RSI is 75. What does this suggest?", "choices": ["Strong buy signal — momentum is high", "Overbought — the stock might pull back soon, so be cautious with new entries", "The stock is broken and will crash"], "answer": 1, "explanation": "RSI 75 means the stock has been heavily bought. It CAN go higher, but the risk of a pullback increases. Smart traders either wait for RSI to cool off or use it as a signal to tighten stops on existing positions."}],
        "connects_narrative": "RSI = speedometer, MACD = compass direction, Volume = fuel gauge. Together they paint the momentum picture.",
        "connects_to": ["macd", "bollinger", "composite_score"],
        "resources": [{"label": "Investopedia: RSI", "url": "https://www.investopedia.com/terms/r/rsi.asp"}]
    },
    "macd": {
        "title": "MACD (Moving Average Convergence Divergence)",
        "category": "technical",
        "tier": 2,
        "eli5": "MACD is like a compass for stock direction. When the fast line crosses above the slow line ('bullish crossover'), momentum is shifting up. When it crosses below ('bearish crossover'), momentum is shifting down. The histogram shows how strong that momentum is.",
        "detailed": "MACD = 12-period EMA minus 26-period EMA. Signal line = 9-period EMA of MACD. Histogram = MACD minus signal. Bullish crossover: MACD crosses above signal line. Bearish crossover: MACD crosses below. Histogram expansion = strengthening trend. Zero-line crossovers indicate trend changes. Most reliable when combined with volume confirmation.",
        "quiz": [{"q": "MACD shows 'bullish_crossover' with histogram at +2.45. What does this mean?", "choices": ["The stock is overpriced", "Momentum is shifting bullish and the signal is strengthening", "Time to sell immediately"], "answer": 1, "explanation": "A bullish crossover means the short-term trend is gaining strength relative to the longer-term trend. The positive histogram (+2.45) confirms the bullish momentum is accelerating. This is a positive technical signal, especially when confirmed by volume."}],
        "connects_narrative": "MACD shows the direction, RSI shows the intensity, and the composite score weights them together.",
        "connects_to": ["rsi", "sma_trend", "composite_score"],
        "resources": [{"label": "Investopedia: MACD", "url": "https://www.investopedia.com/terms/m/macd.asp"}]
    },
    "sma_trend": {
        "title": "SMA Trend (Simple Moving Average)",
        "category": "technical",
        "tier": 2,
        "eli5": "Moving averages smooth out daily noise to show the real trend. A 'golden cross' (50-day crosses above 200-day) is like a green traffic light — the trend is turning bullish. A 'death cross' (50-day crosses below 200-day) is a red light.",
        "detailed": "SMA = average closing price over N days. Common: 50-day (intermediate), 200-day (long-term). Golden cross: 50 SMA > 200 SMA (bullish). Death cross: 50 SMA < 200 SMA (bearish). 'Above 200' means price > 200 SMA (uptrend intact). These are lagging indicators — they confirm trends rather than predict them. Best for timing entries, not exits.",
        "quiz": [{"q": "A stock shows 'golden_cross' trend. What does this mean for the brief's analysis?", "choices": ["The stock will definitely go up", "The intermediate trend has turned bullish — a positive signal in the composite score", "Time to sell because it's too late"], "answer": 1, "explanation": "A golden cross adds +0.5 to the composite score, indicating the intermediate trend has turned bullish. It's not a guarantee (it's a lagging indicator), but it means the trend structure is favorable for long positions."}],
        "connects_narrative": "SMA trend is one of the five indicators that feed into the composite score: RSI, MACD, Bollinger, SMA, Volume.",
        "connects_to": ["macd", "composite_score", "bollinger"],
        "resources": [{"label": "Investopedia: Moving Averages", "url": "https://www.investopedia.com/terms/s/sma.asp"}]
    },
    "volume_ratio": {
        "title": "Volume Ratio",
        "category": "technical",
        "tier": 2,
        "eli5": "Volume is like the crowd size at a concert. If a stock moves up on 2x normal volume, the crowd is excited and the move is more likely to stick. If it moves on low volume, it's like three people clapping — not very convincing.",
        "detailed": "Volume ratio = today's volume / 20-day average volume. >1.5x = high volume (moves are more significant). <0.5x = low volume (moves are less reliable). Volume confirms price: a breakout on 2x volume is trustworthy; a breakout on 0.5x volume might be a fake-out. The brief flags high volume (>1.5x) as notable and factors it into the composite score.",
        "quiz": [{"q": "A stock breaks to a new high with volume ratio of 0.6x. Should you trust the breakout?", "choices": ["Yes — new highs are always bullish", "Be skeptical — low volume breakouts often fail (fake-out)", "Volume doesn't matter for breakouts"], "answer": 1, "explanation": "Low volume (0.6x) means fewer traders participated in the breakout. This makes it more likely to be a false breakout that reverses. Strong breakouts are confirmed by above-average volume, ideally 1.5x or higher."}],
        "connects_narrative": "Volume is the fuel gauge — RSI and MACD tell direction and speed, but volume tells you if there's fuel behind the move.",
        "connects_to": ["rsi", "composite_score", "vwap"],
        "resources": [{"label": "Volume Analysis", "url": "https://www.investopedia.com/terms/v/volume.asp"}]
    },
    "momentum": {
        "title": "Momentum",
        "category": "technical",
        "tier": 2,
        "eli5": "Momentum is the tendency for stocks that have been going up to keep going up, and stocks going down to keep going down. It's like a snowball rolling downhill — the bigger it gets, the harder it is to stop.",
        "detailed": "Momentum is measured by rate of change in price over time. In this brief, multiple indicators capture momentum from different angles: RSI (recent buying/selling pressure), MACD (trend direction and acceleration), volume (conviction behind moves). The composite score synthesizes these into a single momentum reading from strongly bearish (-5) to strongly bullish (+5).",
        "quiz": [{"q": "A stock has composite score +3.5. What does this tell you about momentum?", "choices": ["It's neutral", "Strong bullish momentum across multiple indicators", "It's about to crash"], "answer": 1, "explanation": "+3.5 is a strongly bullish composite score, meaning RSI, MACD, SMA, and volume are all leaning bullish. While no signal is perfect, this level of agreement across indicators suggests real momentum behind the move."}],
        "connects_narrative": "The composite score in Technical Signals IS the momentum reading — it combines all individual technical indicators into one number.",
        "connects_to": ["composite_score", "rsi", "macd"],
        "resources": [{"label": "Momentum Trading", "url": "https://www.investopedia.com/terms/m/momentum.asp"}]
    },
    "pe_ratio": {
        "title": "P/E Ratio (Price-to-Earnings)",
        "category": "valuation",
        "tier": 2,
        "eli5": "P/E ratio tells you how much you're paying for each dollar of profit. If NVDA has a P/E of 65, you're paying $65 for every $1 of annual profit. A lower P/E means you're getting more profit per dollar — like finding a better deal.",
        "detailed": "P/E = share price / earnings per share (EPS). Trailing P/E uses last 12 months of actual earnings. Forward P/E uses estimated future earnings. High P/E (>30) = growth expectations are priced in. Low P/E (<15) = value territory OR the market expects declining earnings. Compare P/E to the sector average and the stock's own historical range, not in isolation.",
        "quiz": [{"q": "Stock A has P/E 20, Stock B has P/E 40. Is Stock A automatically the better buy?", "choices": ["Yes — lower P/E is always better", "Not necessarily — Stock B might be growing faster, justifying the premium", "P/E doesn't matter at all"], "answer": 1, "explanation": "P/E must be considered alongside growth rate. A stock with P/E 40 but 50% earnings growth (PEG = 0.8) is arguably cheaper than P/E 20 with 5% growth (PEG = 4.0). That's why this brief shows multiple valuation metrics, not just P/E."}],
        "connects_narrative": "The Valuation Comparisons section uses P/E alongside other metrics (EV/EBITDA, PEG, P/B) to determine which stock in a pair is truly cheaper.",
        "connects_to": ["peg_ratio", "ev_ebitda", "price_book"],
        "resources": [{"label": "P/E Ratio Explained", "url": "https://www.investopedia.com/terms/p/price-earningsratio.asp"}]
    },
    "peg_ratio": {
        "title": "PEG Ratio (Price/Earnings to Growth)",
        "category": "valuation",
        "tier": 2,
        "eli5": "PEG adjusts the P/E ratio for growth. A P/E of 40 sounds expensive, but if the company is growing earnings at 40% per year, PEG = 1.0 — that's actually fair value. PEG below 1 = you're getting growth at a discount.",
        "detailed": "PEG = P/E / annual EPS growth rate (%). PEG < 1 = undervalued relative to growth. PEG 1-2 = fairly valued. PEG > 2 = potentially overvalued. Peter Lynch popularized this metric. Limitation: relies on growth estimates which can be wrong. Works best for growth stocks (>10% earnings growth), less useful for mature/cyclical companies.",
        "quiz": [{"q": "Stock X: P/E 50, EPS growth 60%. Stock Y: P/E 15, EPS growth 5%. Which has the better PEG?", "choices": ["Stock Y (lower P/E)", "Stock X (PEG 0.83 vs PEG 3.0 — cheaper relative to growth)", "They're equal"], "answer": 1, "explanation": "Stock X: PEG = 50/60 = 0.83. Stock Y: PEG = 15/5 = 3.0. Despite having a much higher P/E, Stock X is actually cheaper when you account for its growth rate. This is why PEG matters more than P/E alone for growth stocks."}],
        "connects_narrative": "PEG is one of the metrics used in Valuation Comparisons to determine which stock in a pair is truly cheaper on a growth-adjusted basis.",
        "connects_to": ["pe_ratio", "ev_ebitda", "scenario_valuation"],
        "resources": [{"label": "PEG Ratio", "url": "https://www.investopedia.com/terms/p/pegratio.asp"}]
    },
    "price_book": {
        "title": "Price-to-Book Ratio (P/B)",
        "category": "valuation",
        "tier": 2,
        "eli5": "Price-to-Book compares the stock price to the company's actual 'stuff' — buildings, equipment, cash. A P/B of 1 means you're paying exactly what the company's assets are worth. Below 1 is a bargain; above 5 means you're mostly paying for intangible value like brand and technology.",
        "detailed": "P/B = market price per share / book value per share. Book value = total assets - total liabilities. P/B < 1 = stock trades below liquidation value (potential deep value). P/B 1-3 = typical for established companies. P/B > 5 = common for tech/growth companies with high intangible assets. Less useful for asset-light businesses (software, services). Most useful for financials, REITs, and industrials.",
        "quiz": [{"q": "A software company has P/B of 15. Is this a red flag?", "choices": ["Yes — it's way overpriced", "Not necessarily — software companies have few physical assets, so P/B is naturally high", "P/B is meaningless for all companies"], "answer": 1, "explanation": "Software companies have minimal physical assets (their value is in code, IP, and talent), so P/B is naturally high. P/B is most useful for comparing asset-heavy companies like banks (typical P/B 1-2) or manufacturers. Use P/E and EV/EBITDA for software."}],
        "connects_narrative": "P/B is one of up to five metrics used in Valuation Comparisons — it matters most when comparing asset-heavy companies.",
        "connects_to": ["pe_ratio", "peg_ratio", "ev_ebitda"],
        "resources": [{"label": "Price-to-Book Ratio", "url": "https://www.investopedia.com/terms/p/price-to-bookratio.asp"}]
    },
    "tone_score": {
        "title": "Earnings Tone Score",
        "category": "valuation",
        "tier": 2,
        "eli5": "The tone score measures how confident or uncertain executives sound on earnings calls. Positive = they use confident language ('we will', 'we're committed'). Negative = lots of hedging ('we might', 'potentially', 'uncertain'). It's like reading body language, but with words.",
        "detailed": "Tone score ranges from -5 (very hedgy) to +5 (very confident). Calculated from the ratio of definitive phrases ('we will', 'we are confident') to hedge phrases ('we anticipate', 'might', 'could'). Tone < -2 triggers an AVOID verdict. High hedge counts with low definitives suggest management uncertainty. The confidence score (0-1) is the raw ratio before the tone mapping.",
        "quiz": [{"q": "An earnings call has 12 hedge phrases and 3 definitive phrases. What's the confidence score and tone?", "choices": ["Confidence 0.80, tone positive", "Confidence 0.20, tone negative — management is hedging heavily", "Not enough data to calculate"], "answer": 1, "explanation": "Confidence = definitive / (definitive + hedge) = 3/15 = 0.20. Tone = (0.20 * 10) - 5 = -3.0. This is a negative tone — management used 4x more hedging language than confident language, suggesting they're uncertain about the outlook."}],
        "connects_narrative": "Tone score directly feeds into the verdict system: tone < -2 can trigger an AVOID verdict, while positive tone supports BUY candidates.",
        "connects_to": ["confidence_score", "verdict_framework", "pe_ratio"],
        "resources": [{"label": "Earnings Call Analysis", "url": "https://www.investopedia.com/terms/e/earnings-call.asp"}]
    },
    "verdict_framework": {
        "title": "Verdict Framework",
        "category": "strategy",
        "tier": 2,
        "eli5": "The verdict is the brief's final answer for each stock: BUY, SELL, HOLD, AVOID, or REVIEW. It's not a guess — it's a mechanical decision based on specific rules combining valuation, earnings tone, and technical signals. Think of it as an unbiased second opinion.",
        "detailed": "Verdict rules: BUY = cheaper on 3+ valuation metrics AND earnings tone > 0. SELL = trailing stop within 5% of current price. AVOID = tone <= -2 OR expensive on 4+ metrics. REVIEW = insufficient data. HOLD = default when none of the above trigger. The Verdict Scorecard tracks historical accuracy, so you can see how reliable these verdicts have been.",
        "quiz": [{"q": "A stock is cheaper on 4 valuation metrics but has an earnings tone of -3. What verdict does it get?", "choices": ["BUY — it's cheap on many metrics", "AVOID — the negative tone overrides the valuation advantage", "HOLD — conflicting signals cancel out"], "answer": 1, "explanation": "AVOID triggers when tone <= -2, regardless of valuation. The system prioritizes risk: even if a stock looks cheap, management uncertainty (tone -3) is a warning sign. The stock might be cheap for a reason."}],
        "connects_narrative": "The Watchlist Verdicts section shows the output of this framework. The Scorecard tracks its accuracy over time.",
        "connects_to": ["tone_score", "pe_ratio", "trailing_stop", "how_to_read_brief"],
        "resources": []
    },
    "how_to_read_brief": {
        "title": "How to Read This Brief",
        "category": "strategy",
        "tier": 2,
        "eli5": "Read top to bottom: Risk Dashboard tells you the environment, Verdicts tell you what to do, then drill into specific sections for the 'why'. Don't let one section override the others — look for agreement across multiple signals.",
        "detailed": "Recommended flow: (1) Risk Dashboard — is the environment favorable? (2) Verdicts — what's the brief recommending? (3) Position Sizing — how much? (4) Technical Signals — is the timing right? (5) Earnings + Sentiment — is the narrative supportive? (6) Options — any smart options plays? (7) Journal — am I following my rules? Each section adds a layer of context. Conflicting signals = reduce size or wait.",
        "quiz": [{"q": "Risk Dashboard shows DANGER, but one stock has a BUY verdict. What should you do?", "choices": ["Follow the BUY — the verdict overrides risk", "Reduce position size or wait — a DANGER environment affects all stocks, even good setups", "Ignore the Risk Dashboard entirely"], "answer": 1, "explanation": "The Risk Dashboard sets the context for all other signals. In DANGER mode, even BUY verdicts should be taken with smaller position sizes and tighter stops. The best trade in a bad environment is often no trade."}],
        "connects_narrative": "This concept is your roadmap for reading the entire brief effectively.",
        "connects_to": ["verdict_framework", "risk_management", "decision_when_conflict"],
        "resources": []
    },

    # ── TIER 3 — Advanced ─────────────────────────────────────────────
    "bollinger": {
        "title": "Bollinger Bands",
        "category": "technical",
        "tier": 3,
        "eli5": "Bollinger Bands are like guard rails around a stock price. The price usually stays between the upper and lower bands. When it hits the upper band, it might be stretched too far up. When it hits the lower band, it might bounce. When the bands squeeze tight, a big move is coming.",
        "detailed": "Bollinger Bands: middle = 20 SMA, upper = +2 std dev, lower = -2 std dev. Price at upper band = overbought; at lower = oversold; 'squeeze' (narrow bands) = low volatility precedes a breakout. In this brief, bb_position reports: 'upper' (overbought), 'lower' (oversold), or 'middle' (neutral). Works best in range-bound markets; in strong trends, price can 'walk the band' without reversing.",
        "quiz": [{"q": "Bollinger position is 'upper' and RSI is 72. What does this confluence suggest?", "choices": ["Strong buy signal — momentum is great", "Double overbought warning — consider tightening stops or waiting for a pullback", "The indicators conflict"], "answer": 1, "explanation": "When both Bollinger Bands AND RSI indicate overbought conditions simultaneously, it's a stronger signal than either alone. This doesn't mean the stock will definitely drop, but it increases the risk of a pullback. Confluence of signals is more reliable than any single indicator."}],
        "connects_narrative": "Bollinger is one of the five technical indicators in the composite score, alongside RSI, MACD, SMA, and Volume.",
        "connects_to": ["rsi", "composite_score", "atr"],
        "resources": [{"label": "Bollinger Bands", "url": "https://www.investopedia.com/terms/b/bollingerbands.asp"}]
    },
    "vwap": {
        "title": "VWAP (Volume-Weighted Average Price)",
        "category": "technical",
        "tier": 3,
        "eli5": "VWAP is the 'true average price' that accounts for how much was traded at each price level. If the stock is above VWAP, buyers are in control (they paid more than average). Below VWAP, sellers are dominating.",
        "detailed": "VWAP = cumulative(price * volume) / cumulative(volume). Resets daily. Institutional benchmark — large funds often measure execution quality against VWAP. Price > VWAP = bullish intraday bias (buyers aggressive). Price < VWAP = bearish intraday bias (sellers aggressive). VWAP acts as dynamic support/resistance. In this brief, vwap_signal reports 'above' or 'below'.",
        "quiz": [{"q": "A stock is trading above VWAP. What does this suggest about institutional interest?", "choices": ["Institutions are selling", "Institutions are buying — price is above the volume-weighted average, meaning buyers are more aggressive", "VWAP has nothing to do with institutions"], "answer": 1, "explanation": "Institutions track VWAP closely and often use it as a benchmark. Price above VWAP suggests net buying pressure — institutions are willing to pay above-average prices, which is a bullish signal."}],
        "connects_narrative": "VWAP provides intraday institutional context that complements the daily-timeframe RSI and MACD readings.",
        "connects_to": ["poc", "volume_ratio", "composite_score"],
        "resources": [{"label": "VWAP Explained", "url": "https://www.investopedia.com/terms/v/vwap.asp"}]
    },
    "poc": {
        "title": "POC (Point of Control)",
        "category": "technical",
        "tier": 3,
        "eli5": "POC is the price where the most shares were traded — the price that the most people agreed on. It acts like a magnet: prices tend to get pulled back to the POC. It's the stock's 'home base'.",
        "detailed": "POC = price level with the highest volume in the volume profile (a histogram of volume at each price). The POC acts as support when price is above it and resistance when below. High volume nodes cluster around fair value zones. In this brief, POC is shown in Technical Signals alongside VWAP to give you the key institutional price levels.",
        "quiz": [{"q": "Current price is $900 and POC is $880. What does this relationship suggest?", "choices": ["The stock is undervalued", "The stock is trading above its highest-volume price level — $880 is support if it pulls back", "POC doesn't matter once price moves away from it"], "answer": 1, "explanation": "The POC at $880 means the most shares changed hands there — it represents strong agreement on that price. If the stock pulls back from $900, $880 is likely support because many traders have positions there and will defend them."}],
        "connects_narrative": "POC + VWAP together show you the key institutional price levels where support and resistance are strongest.",
        "connects_to": ["vwap", "volume_ratio", "trailing_stop"],
        "resources": [{"label": "Volume Profile & POC", "url": "https://www.investopedia.com/terms/v/volume-profile.asp"}]
    },
    "atr": {
        "title": "ATR (Average True Range)",
        "category": "technical",
        "tier": 3,
        "eli5": "ATR measures how much a stock typically moves in a day — its 'normal wiggle room.' A stock with ATR of $25 normally moves $25/day, so you shouldn't panic over a $15 drop. Your trailing stop uses ATR to set a distance that's outside normal noise but inside real trouble.",
        "detailed": "ATR(14) = 14-day average of True Range, where TR = max(High-Low, |High-prevClose|, |Low-prevClose|). Used for stop-loss placement: stop = price - (multiplier * ATR). The brief uses a 2x ATR multiplier by default (configurable in settings.json). This means the stop is set 2 'normal days' of movement below the price — wide enough to avoid noise, tight enough to limit losses.",
        "quiz": [{"q": "A stock at $900 has ATR of $25. With a 2x ATR stop, where's the trailing stop?", "choices": ["$875", "$850", "$825"], "answer": 1, "explanation": "$900 - (2 x $25) = $850. The stop is set 2 ATRs below the current price, giving the stock room for normal daily volatility ($25) without triggering. Only a move of more than 2x the normal daily range would trigger a sell."}],
        "connects_narrative": "ATR is the engine behind the trailing stops in Portfolio Holdings and the risk calculations in Position Sizing.",
        "connects_to": ["trailing_stop", "position_sizing_process", "risk_management"],
        "resources": [{"label": "ATR Explained", "url": "https://www.investopedia.com/terms/a/atr.asp"}]
    },
    "composite_score": {
        "title": "Composite Score",
        "category": "technical",
        "tier": 3,
        "eli5": "The composite score combines five different technical signals into one number from -5 to +5. It's like a weather report that combines temperature, wind, and clouds into one 'nice day' rating. Higher = more bullish, lower = more bearish.",
        "detailed": "Composite = sum of individual signal scores: RSI (0 neutral, +-1 extreme), MACD (+1 bullish crossover, -1 bearish), Bollinger (+0.5/-0.5 band position), SMA (+0.5 golden cross, -0.5 death cross), Volume (+0.5 high, -0.5 low). Range: roughly -5 to +5. >2 = strongly bullish, <-2 = strongly bearish. The composite is a quick-glance momentum indicator, not a standalone trading signal.",
        "quiz": [{"q": "Stock A has composite +1.5, Stock B has composite -0.5. Which has better technical momentum?", "choices": ["Stock A — positive composite means more indicators are bullish", "Stock B — negative is better", "They're about the same"], "answer": 0, "explanation": "+1.5 means multiple indicators (MACD, SMA, etc.) are flashing bullish, while -0.5 means the technical picture is slightly bearish. Stock A has better momentum, though +1.5 is moderate — not a screaming buy. The strongest signals are above +2 or below -2."}],
        "connects_narrative": "The composite score is the headline number in Technical Signals — it's the single most important technical reading for each ticker.",
        "connects_to": ["rsi", "macd", "sma_trend", "bollinger", "volume_ratio"],
        "resources": []
    },
    "ev_ebitda": {
        "title": "EV/EBITDA",
        "category": "valuation",
        "tier": 3,
        "eli5": "EV/EBITDA is like P/E but smarter. It looks at the total cost to buy the whole company (including its debt) divided by its operating profit. A lower number means you're getting more profit for your money. It works better than P/E for comparing companies with different debt levels.",
        "detailed": "EV = market cap + total debt - cash. EBITDA = earnings before interest, taxes, depreciation, amortization (proxy for operating cash flow). EV/EBITDA < 10 = potentially undervalued. 10-20 = fairly valued. >20 = expensive or high-growth. Advantages over P/E: accounts for capital structure (debt), not affected by tax differences, and works for companies with negative earnings (if EBITDA is positive).",
        "quiz": [{"q": "Company A: EV/EBITDA 12, low debt. Company B: EV/EBITDA 12, high debt. Which is riskier?", "choices": ["Same — they have the same EV/EBITDA", "Company B — high debt amplifies downside risk even though the EV/EBITDA ratio is identical", "Company A — low debt means less leverage for upside"], "answer": 1, "explanation": "While both have the same EV/EBITDA, Company B's high debt means its equity holders bear more risk. In a downturn, debt obligations must be paid before shareholders get anything. EV/EBITDA captures the debt in the numerator, but the debt burden's risk impact is still important context."}],
        "connects_narrative": "EV/EBITDA is one of the metrics in Valuation Comparisons — it's especially useful for comparing companies with different capital structures.",
        "connects_to": ["pe_ratio", "peg_ratio", "price_book"],
        "resources": [{"label": "EV/EBITDA", "url": "https://www.investopedia.com/terms/e/ev-ebitda.asp"}]
    },
    "confidence_score": {
        "title": "Confidence Score",
        "category": "valuation",
        "tier": 3,
        "eli5": "The confidence score (0 to 1) measures how much management says 'we will' vs 'we might' on earnings calls. Score of 0.8 means 80% of their language was confident. Score of 0.2 means they were mostly hedging. Low confidence often precedes bad guidance.",
        "detailed": "Confidence score = definitive phrases / (definitive + hedge phrases). Range: 0 (all hedging) to 1 (all confident). Maps to tone_score via: tone = (confidence * 10) - 5. A confidence score below 0.30 is concerning — management is hedging 70%+ of their forward-looking statements. This metric is derived from text analysis of earnings call transcripts.",
        "quiz": [{"q": "Confidence score is 0.40. What does this translate to in tone?", "choices": ["Tone = +5.0 (very positive)", "Tone = -1.0 (slightly negative)", "Tone = 0.0 (neutral)"], "answer": 1, "explanation": "Tone = (0.40 * 10) - 5 = -1.0. A confidence score of 0.40 means management used more hedging language than confident language, resulting in a slightly negative tone. Not alarming, but not inspiring either."}],
        "connects_narrative": "Confidence score is the raw input that generates the tone score shown in the Earnings Tone section.",
        "connects_to": ["tone_score", "verdict_framework"],
        "resources": []
    },
    "scenario_valuation": {
        "title": "Scenario Valuation (Bull/Base/Bear)",
        "category": "valuation",
        "tier": 3,
        "eli5": "Scenario valuation gives you three price targets: optimistic (bull), realistic (base), and pessimistic (bear). The risk/reward ratio tells you if the potential upside is worth the potential downside. Above 1.5x = good deal. Below 1.0x = the risk isn't worth the reward.",
        "detailed": "Bull, base, and bear price targets are derived from forward P/E estimates and scenario-adjusted earnings. Risk/reward = bull upside / |bear downside|. R/R > 1.5 = favorable asymmetry. R/R 1.0-1.5 = neutral. R/R < 1.0 = risk outweighs reward. The brief also shows the current price relative to each scenario to help you gauge where we are in the range.",
        "quiz": [{"q": "A stock has bull upside +22%, bear downside -17%. What's the risk/reward?", "choices": ["0.77x — bad risk/reward", "1.29x — slightly favorable", "22/17 = 1.29x — moderately favorable asymmetry"], "answer": 2, "explanation": "R/R = 22% / 17% = 1.29x. For every dollar of downside risk, you get $1.29 of upside potential. This is slightly favorable but not compelling — 1.5x+ is the sweet spot where the upside meaningfully exceeds the downside."}],
        "connects_narrative": "The Scenario Valuations section shows these price targets for each ticker, helping you set realistic expectations.",
        "connects_to": ["pe_ratio", "risk_management", "position_sizing_process"],
        "resources": []
    },
    "insider_activity": {
        "title": "Insider Activity (SEC Form 4)",
        "category": "strategy",
        "tier": 3,
        "eli5": "When company executives buy or sell their own stock, they have to report it publicly. If a CEO is buying shares with their own money, that's a strong signal — they know the company best. Multiple insiders buying at once ('cluster buy') is even more bullish.",
        "detailed": "SEC Form 4 filings report insider transactions within 2 business days. Cluster buys (3+ insiders buying within 30 days) are among the strongest bullish signals in fundamental analysis — insiders are risking personal capital. Insider selling is less informative (could be diversification, tax planning, or pre-planned). The brief tracks filing count, signal level, and cluster buy detection.",
        "quiz": [{"q": "A stock shows 4 Form 4 filings with 'cluster_buy: YES'. How should you weight this information?", "choices": ["Ignore it — insider buying means nothing", "Very bullish signal — multiple insiders buying with their own money suggests they expect the stock to go higher", "Bearish — they're trying to manipulate the price"], "answer": 1, "explanation": "Cluster buys are one of the most researched bullish indicators in finance. When multiple insiders buy simultaneously with their own money, they're expressing strong confidence in the company's prospects. It doesn't guarantee gains, but it significantly tilts the odds."}],
        "connects_narrative": "The Insider Activity section flags cluster buys as a bullish catalyst that complements the technical and valuation signals.",
        "connects_to": ["verdict_framework", "what_moves_prices"],
        "resources": [{"label": "Insider Trading Signals", "url": "https://www.investopedia.com/terms/i/insider-buying.asp"}]
    },
    "congressional_trading": {
        "title": "Congressional Trading (STOCK Act)",
        "category": "strategy",
        "tier": 2,
        "eli5": "Members of Congress trade stocks too, and they have to report it publicly within 45 days. Some politicians (like Nancy Pelosi) have beaten the market consistently. When several politicians buy the same stock at once, that's like insider info going mainstream. It's not a guarantee, but it's a signal worth watching.",
        "detailed": "The STOCK Act (2012) requires all members of Congress to disclose stock trades within 45 days. This module tracks House and Senate disclosures and cross-references against your watchlist. Key signals: (1) Cluster buys — 3+ politicians buying the same stock suggests shared intelligence from committee briefings. (2) Big trades ($500k+) — large positions indicate high conviction. (3) Notable traders — historically accurate politicians (e.g., Pelosi's tech trades) carry more signal weight. Limitations: 45-day delay means trades are backward-looking, and politicians may have diversification or tax reasons unrelated to market outlook.",
        "quiz": [{"q": "Three politicians from the Intelligence Committee all bought the same defense stock last month. How should you interpret this?", "choices": ["Ignore it — politicians don't know more than the market", "Strong signal — committee members may have non-public briefing context, worth investigating the stock further", "Buy immediately — they have insider info"], "answer": 1, "explanation": "Congressional cluster buys from committee members are notable because they may have access to policy briefings before public announcements. However, the 45-day disclosure delay means this is backward-looking. The right approach: use it as a research catalyst, not a blind buy signal. Investigate the stock using the brief's other signals (technicals, valuation, sentiment) before acting."}],
        "connects_narrative": "The Congressional Trading section shows STOCK Act disclosures filtered to your watchlist. Cluster signals indicate multiple politicians trading the same ticker, which can confirm or add conviction to other brief signals.",
        "connects_to": ["insider_activity", "verdict_framework", "what_moves_prices"],
        "resources": [{"label": "STOCK Act Overview", "url": "https://www.investopedia.com/terms/s/stop-trading-on-congressional-knowledge-act.asp"}, {"label": "Capitol Trades", "url": "https://www.capitoltrades.com/"}]
    },
    "economic_calendar": {
        "title": "Economic Calendar",
        "category": "market_basics",
        "tier": 2,
        "eli5": "The economic calendar tells you when the government releases big reports about the economy — like the jobs report, inflation numbers, and Fed meetings. These reports move the ENTIRE market, not just individual stocks. Knowing what's coming this week helps you avoid getting blindsided.",
        "detailed": "Key events ranked by market impact: (1) FOMC meetings — the Federal Reserve sets interest rates, affecting every asset class. (2) Non-Farm Payrolls (NFP) — first Friday of each month, shows how many jobs were added. Strong jobs = hawkish Fed = stocks may dip. (3) CPI/PPI — inflation data that directly influences Fed policy. (4) GDP — quarterly economic growth. The calendar module tracks all of these plus FRED data for current readings on fed funds rate, unemployment, inflation, and yields. Pro tip: don't open new positions the day before a high-impact release — the move after the number can be violent in either direction.",
        "quiz": [{"q": "It's Thursday and NFP comes out tomorrow morning. You have a BUY signal on NVDA. What should you do?", "choices": ["Buy now — the signal is the signal", "Wait until after the NFP release to see the market reaction, then decide", "Sell everything before the report"], "answer": 1, "explanation": "High-impact events like NFP can move the entire market 1-2% in minutes. Even a strong BUY signal on a single stock can be overwhelmed by a macro shock. The smart move is to wait for the dust to settle after the release, then re-evaluate. The BUY signal will still be valid if the macro doesn't change."}],
        "connects_narrative": "The Economic Calendar section shows upcoming events and their impact level. Red = high impact (be cautious), Yellow = moderate, Green = low.",
        "connects_to": ["risk_management", "what_moves_prices", "macro_indicators"],
        "resources": [{"label": "FRED Economic Data", "url": "https://fred.stlouisfed.org"}, {"label": "Economic Calendar Guide", "url": "https://www.investopedia.com/terms/e/economic-calendar.asp"}]
    },
    "macro_indicators": {
        "title": "Macro Indicators (VIX, DXY, Yields, Oil, Gold)",
        "category": "market_basics",
        "tier": 2,
        "eli5": "Think of macro indicators as the weather report for the whole market. VIX = fear level (high = stormy). Dollar strength = headwind for big companies that sell overseas. Bond yields = the price of borrowing money. Oil = energy costs. Gold = panic buying. Together they tell you if it's a good day to be aggressive or defensive.",
        "detailed": "The 5 key macro indicators: (1) VIX — CBOE Volatility Index. Below 15 = calm/complacent, 15-20 = normal, 20-30 = elevated fear, 30+ = panic. High VIX often means opportunity (buy when others are fearful). (2) DXY (US Dollar Index) — strong dollar hurts multinationals (AAPL, MSFT) and commodities. (3) 10Y Treasury Yield — rising yields compete with stocks for capital. (4) Crude Oil — inflation proxy, rising oil = inflation concern. (5) Gold — safe haven, rising gold + rising VIX = risk-off. Signal logic: VIX > 25 with rising gold and falling yields = risk-off. VIX < 15 with stable yields = risk-on.",
        "quiz": [{"q": "VIX is at 28, gold is rising, and 10Y yields are falling. What's the regime?", "choices": ["Risk-on — great time to buy growth stocks", "Risk-off — money is flowing to safety (gold, bonds)", "Mixed — no clear signal"], "answer": 1, "explanation": "This is a classic risk-off pattern: elevated fear (VIX 28), money flowing into gold (safe haven), and bonds rallying (yields fall when bond prices rise). In this environment, defensive sectors outperform and growth stocks underperform. Consider reducing exposure or tightening stops on aggressive positions."}],
        "connects_narrative": "The Macro Dashboard shows all 5 indicators with trend arrows, change percentages, and a regime interpretation. Red = crisis, Yellow = risk-off, Green = risk-on, Blue = mixed.",
        "connects_to": ["risk_management", "sector_rotation", "economic_calendar"],
        "resources": [{"label": "VIX Explained", "url": "https://www.investopedia.com/terms/v/vix.asp"}, {"label": "Macro Trading Basics", "url": "https://www.investopedia.com/terms/m/macro-environment.asp"}]
    },
    "sector_rotation": {
        "title": "Sector Rotation",
        "category": "strategy",
        "tier": 2,
        "eli5": "Sector rotation is when big money moves from one part of the market to another. Like musical chairs — when Tech is losing its seat, Energy might be gaining one. If you see money rotating INTO defensive sectors (utilities, staples), it usually means smart money is getting nervous. If money is rotating INTO growth (tech, discretionary), the mood is optimistic.",
        "detailed": "The 11 S&P 500 sectors cycle through leadership based on the economic cycle: Early Recovery → Tech & Discretionary lead. Mid-Expansion → Industrials & Financials lead. Late Cycle → Energy & Materials lead. Recession → Utilities, Staples & Health Care lead. The Sector Rotation module tracks all 11 Select Sector SPDRs against SPY. Relative strength = sector return minus SPY return. If a sector beats SPY, money is flowing in. If it lags, money is flowing out. Breadth measures how many sectors are positive — 8+ = broad rally (bullish), 3 or fewer = broad selloff (bearish). Key insight: when leadership changes between timeframes (5d vs 21d leaders differ), rotation is happening NOW.",
        "quiz": [{"q": "Tech (XLK) led over 21 days, but Utilities (XLU) and Staples (XLP) are leading over 5 days. What does this mean?", "choices": ["Buy more tech — it's still the 21-day leader", "Rotation alert — defensive sectors taking over suggests risk-off is starting", "Nothing — short-term noise"], "answer": 1, "explanation": "When 5-day leaders shift from growth to defensives while 21-day leaders are still growth, it's an early warning. Smart money is rotating to safety. This doesn't mean sell everything, but it means tighten stops and reduce size on aggressive positions. The Sector Rotation module flags this as a 'defensive_rotation' alert."}],
        "connects_narrative": "The Sector Rotation section shows leaders, laggards, rotation type, and breadth. Watch for defensive rotation as a leading risk indicator.",
        "connects_to": ["macro_indicators", "risk_management", "momentum"],
        "resources": [{"label": "Sector Rotation Strategy", "url": "https://www.investopedia.com/terms/s/sectorrotation.asp"}, {"label": "SPDR Sector ETFs", "url": "https://www.sectorspdrs.com"}]
    },
    "prediction_markets": {
        "title": "Prediction Markets (Polymarket)",
        "category": "strategy",
        "tier": 2,
        "eli5": "Prediction markets are like betting markets for real-world events. People buy 'shares' that pay $1 if something happens and $0 if it doesn't. If a share costs 72 cents, the crowd thinks there's a 72% chance it happens. When you know something the crowd doesn't — like checking a weather forecast — you can find shares that are too cheap or too expensive.",
        "detailed": "Polymarket is the largest prediction market, running on the Polygon blockchain with USDC. Markets cover politics, crypto, weather, economics, sports, and more. The key concept is Expected Value (EV): if you estimate a 70% probability event is priced at 50 cents, your EV is 0.70 * $1.00 - $0.50 = +$0.20 per share. The scanner cross-references market prices against external data sources (weather forecasts, technical signals) to identify mispricing. Important: 80% of participants lose money — edge comes from domain expertise and alternative data, not luck.",
        "quiz": [{"q": "A weather market prices 'NYC high above 50°F tomorrow' at 15 cents (YES). NOAA forecasts 52°F with high confidence (~70% probability). What's the expected value per share?", "choices": ["$0.15 — you pay 15 cents so that's the value", "$0.55 — the 70% probability times $1.00 minus the 15 cent cost", "$0.70 — that's the probability"], "answer": 1, "explanation": "EV = (probability * payout) - cost = (0.70 * $1.00) - $0.15 = $0.55 per share. This is a massive edge — you're buying a 70% probability for just 15 cents. Weather markets are where bots have made the most consistent profits because free forecast data gives you a genuine information advantage."}],
        "connects_narrative": "The Polymarket section shows opportunities where external data (weather forecasts, technical signals) disagrees with market pricing, creating potential expected value.",
        "connects_to": ["risk_management", "what_moves_prices", "congressional_trading"],
        "resources": [{"label": "Polymarket", "url": "https://polymarket.com"}, {"label": "Prediction Market Strategy Guide", "url": "https://defiprime.com/definitive-guide-to-the-polymarket-ecosystem"}]
    },
    "decision_when_conflict": {
        "title": "What to Do When Signals Conflict",
        "category": "strategy",
        "tier": 3,
        "eli5": "When the brief shows bullish technicals but bearish earnings, or a BUY verdict but DANGER risk regime, don't panic. Conflicting signals mean REDUCE SIZE, not ignore everything. The strongest trades happen when most signals agree.",
        "detailed": "Signal hierarchy for conflicts: (1) Risk regime — overrides everything. DANGER = reduce all sizes. (2) Verdict — the mechanical decision. (3) Technical timing — when to enter. (4) Sentiment/Insider — confirming or cautioning. When signals conflict: reduce position to 50% of normal, use tighter stops, or simply wait. Alignment across 3+ signal types = high conviction. Alignment on 1-2 = lower conviction, smaller size.",
        "quiz": [{"q": "BUY verdict, bullish MACD, but Risk Dashboard shows CAUTION. What's the right approach?", "choices": ["Full position — two of three signals are bullish", "Half position with a tighter stop — respect the CAUTION regime while acting on the BUY signal", "No trade — CAUTION means no trading at all"], "answer": 1, "explanation": "CAUTION doesn't mean stop trading, but it does mean the environment adds risk. The balanced approach: take the BUY trade but at reduced size (50-75%) with a tighter stop. This respects both the opportunity (BUY verdict) and the risk (CAUTION regime)."}],
        "connects_narrative": "This is the decision framework for resolving the tensions you'll see between different sections of the brief.",
        "connects_to": ["risk_management", "verdict_framework", "how_to_read_brief"],
        "resources": []
    },
    "position_sizing_process": {
        "title": "Position Sizing Process",
        "category": "strategy",
        "tier": 3,
        "eli5": "Position sizing answers 'how much should I buy?' It limits any single trade to a fixed percentage of your portfolio (usually 2% risk). This means even if you're dead wrong, you only lose 2% — you live to trade another day.",
        "detailed": "This brief uses ATR-based position sizing: recommended shares = (portfolio * risk%) / (ATR * multiplier). With a $100,000 portfolio and 2% risk: max loss per trade = $2,000. If ATR = $25 and multiplier = 2 (stop at $50 below entry): shares = $2,000 / $50 = 40 shares. Max position is also capped at 15% of portfolio to prevent over-concentration. Both limits are shown in the Position Sizing section.",
        "quiz": [{"q": "Portfolio: $100K, risk 2%, ATR stop distance $50. How many shares should you buy?", "choices": ["20 shares", "40 shares", "100 shares"], "answer": 1, "explanation": "Risk budget = $100,000 * 2% = $2,000. Shares = $2,000 / $50 = 40 shares. If the stop hits, you lose exactly $2,000 (2% of portfolio). This mechanical approach prevents emotional over-sizing on 'sure thing' trades."}],
        "connects_narrative": "The Position Sizing section shows the exact calculation: recommended shares, stop loss, risk per share, and percentage of portfolio.",
        "connects_to": ["atr", "trailing_stop", "risk_management"],
        "resources": []
    },
    "trailing_stop": {
        "title": "Trailing Stop Loss",
        "category": "strategy",
        "tier": 3,
        "eli5": "A trailing stop follows the stock price upward but never moves down. If your stock is at $900 with a trailing stop at $850, and the stock rises to $950, the stop moves up to $900. If it then drops to $895, the stop stays at $900 and sells. It locks in your gains automatically.",
        "detailed": "Trailing stop = highest price since entry - (ATR * multiplier). As price rises, the stop ratchets up. It never decreases. This brief shows the current trailing stop for each holding in the Portfolio section, calculated as current_price - (ATR_multiplier * ATR). Locked profit = (trailing_stop - cost_basis) * shares — the minimum profit you'd keep if the stop triggers.",
        "quiz": [{"q": "You bought at $485, current price $900, trailing stop $850. What's your locked profit per share?", "choices": ["$415 (current - cost)", "$365 (stop - cost)", "$50 (current - stop)"], "answer": 1, "explanation": "Locked profit = trailing stop - cost basis = $850 - $485 = $365 per share. This is the MINIMUM profit you'd keep even if the stock crashed overnight. The $415 unrealized gain is only locked in if you sell now, but the trailing stop guarantees at least $365."}],
        "connects_narrative": "The Portfolio Holdings table shows trailing stops and locked profits for each position, updated daily with the latest ATR data.",
        "connects_to": ["atr", "order_types", "loss_aversion"],
        "resources": [{"label": "Trailing Stop Orders", "url": "https://www.investopedia.com/terms/t/trailingstop.asp"}]
    },
    "risk_management": {
        "title": "Risk Management",
        "category": "strategy",
        "tier": 3,
        "eli5": "Risk management is about surviving bad days so you can profit on good ones. It's not about avoiding risk — it's about controlling it. Position sizing, stop losses, and diversification are your three shields.",
        "detailed": "The brief's risk framework operates at three levels: (1) Portfolio level — Risk Dashboard monitors VIX, yields, and regime. (2) Position level — trailing stops limit individual losses. (3) Sizing level — 2% risk per trade, 15% max position. Additionally, high correlations are flagged to prevent concentration risk (two correlated holdings amplify losses). The goal: no single trade, no single day, no single event can seriously damage your portfolio.",
        "quiz": [{"q": "Your two largest holdings have 0.92 correlation. Why is this a risk?", "choices": ["High correlation is always good — they move together", "They'll both drop together in a downturn, doubling your loss instead of diversifying it", "Correlation doesn't affect risk"], "answer": 1, "explanation": "0.92 correlation means these stocks move in near-lockstep. If one drops 10%, the other likely drops ~9.2%. You effectively have double the position size in one bet. The brief flags correlations >= 0.80 so you can reduce one position or add an uncorrelated hedge."}],
        "connects_narrative": "The Risk Dashboard, Portfolio Holdings (correlations + stops), and Position Sizing work together as the brief's risk management system.",
        "connects_to": ["position_sizing_process", "trailing_stop", "atr", "decision_when_conflict"],
        "resources": [{"label": "Risk Management Basics", "url": "https://www.investopedia.com/terms/r/riskmanagement.asp"}]
    },

    # ── TIER 4 — Expert ───────────────────────────────────────────────
    "max_pain": {
        "title": "Max Pain (Options)",
        "category": "options",
        "tier": 4,
        "eli5": "Max pain is the stock price where option sellers make the most money and option buyers lose the most. Stocks tend to drift toward max pain as expiration approaches — like a gravitational pull. If max pain is $880 and the stock is at $900, there's a pull toward $880 near expiry.",
        "detailed": "Max pain = strike price where total dollar value of expiring options (calls + puts) is minimized. Calculated by summing call pain and put pain at each strike: for each candidate price, total_pain = sum(call_OI * max(0, price - strike) + put_OI * max(0, strike - price)). Min total_pain = max pain price. The theory: market makers (who sold the options) hedge in ways that push the stock toward max pain. Most relevant in the last 3-5 days before expiration.",
        "quiz": [{"q": "Max pain is $880, stock is at $910, expiration is in 3 days. What might happen?", "choices": ["Stock will definitely drop to $880", "There's gravitational pull toward $880, so the stock may drift lower — consider tightening stops if you're long", "Max pain only works after expiration"], "answer": 1, "explanation": "Max pain theory suggests stocks drift toward the max pain price as expiration approaches, especially in the final week. With the stock $30 above max pain and only 3 days left, there's a statistical tendency for downward pressure. It's not a guarantee, but it's worth noting for stop-loss placement."}],
        "connects_narrative": "Max pain is shown in the Options section — compare it to the current price to gauge near-term gravitational pull around expiration.",
        "connects_to": ["open_interest", "front_month", "pmcc_setup"],
        "resources": [{"label": "Max Pain Theory", "url": "https://www.investopedia.com/terms/m/maxpain.asp"}]
    },
    "pmcc_setup": {
        "title": "PMCC (Poor Man's Covered Call)",
        "category": "options",
        "tier": 4,
        "eli5": "A PMCC is like renting a stock instead of buying it. You buy a long-dated, deep-in-the-money call (LEAPS) as a stock substitute, then sell short-term calls against it to collect income. You get the benefits of a covered call strategy for a fraction of the capital.",
        "detailed": "PMCC structure: Long leg = LEAPS call (180+ days, ~0.75 delta, ~85% ITM). Short leg = front-month call (~30 days, ~0.30 delta, ~105% of current price). Net debit = long premium - short premium. Max loss = net debit. The long LEAPS mimics stock ownership at lower cost, while the short call generates recurring income. Risks: stock drops below long strike (full loss), stock gaps above short strike (capped upside).",
        "quiz": [{"q": "PMCC: Long $765 call, Short $945 call, Net debit $142. What's the max profit per spread?", "choices": ["$142", "$765", "$945 - $765 - $142 = $38 (per share, $3,800 per contract)", "Unlimited"], "answer": 2, "explanation": "Max profit = short strike - long strike - net debit = $945 - $765 - $142 = $38 per share = $3,800 per contract. This occurs when the stock is at or above $945 at short leg expiration. The max loss is the net debit: $142 per share ($14,200 per contract)."}],
        "connects_narrative": "The PMCC Setup section in Options shows specific long/short strikes and net debit for each eligible holding.",
        "connects_to": ["leaps", "front_month", "delta"],
        "resources": [{"label": "PMCC Strategy", "url": "https://www.investopedia.com/terms/p/poormans-covered-call.asp"}]
    },
    "front_month": {
        "title": "Front Month Expiry",
        "category": "options",
        "tier": 4,
        "eli5": "The 'front month' is the nearest expiration date that's at least 20 days away. This is where you sell short calls in a PMCC and where max pain matters most. Options decay fastest in the front month — time is literally money.",
        "detailed": "Front month selection: nearest expiry >= 20 calendar days from today. Theta (time decay) accelerates dramatically in the last 30 days, making this the optimal window for selling options. In PMCC, the short leg uses front-month options to maximize theta income. At expiration, the short call either expires worthless (you keep the premium) or you roll it to the next month.",
        "quiz": [{"q": "Today is March 1. Available expiries: March 14, March 20, April 17. Which is the front month?", "choices": ["March 14 (nearest expiry)", "March 20 (nearest expiry >= 20 days away)", "April 17 (most time value)"], "answer": 1, "explanation": "Front month = nearest expiry >= 20 days. March 14 is only 13 days away (too close — not enough time value to sell). March 20 is 19 days... close but might be selected. April 17 is the safe pick at 47 days. The system picks the nearest expiry with at least 20 days remaining."}],
        "connects_narrative": "The Front Month shown in Options is the expiration date used for the short leg of the PMCC and for max pain calculations.",
        "connects_to": ["pmcc_setup", "max_pain", "delta"],
        "resources": [{"label": "Options Expiration", "url": "https://www.investopedia.com/terms/e/expirationdate.asp"}]
    },
    "leaps": {
        "title": "LEAPS (Long-term Options)",
        "category": "options",
        "tier": 4,
        "eli5": "LEAPS are options that expire far in the future (6+ months). They cost more than regular options but give you more time for your thesis to play out. In a PMCC, LEAPS are your 'stock substitute' — they move like stock but cost way less.",
        "detailed": "LEAPS = Long-term Equity Anticipation Securities. Minimum 180 days to expiry. In PMCC: long LEAPS call at ~85% of stock price (deep ITM, high delta ~0.75-0.85). The deep ITM LEAPS has minimal extrinsic value and moves nearly dollar-for-dollar with the stock. Capital efficiency: instead of buying 100 shares at $900 ($90,000), you might buy 1 LEAPS for ~$14,200. Tradeoff: you pay a time premium and the position expires (unlike stock).",
        "quiz": [{"q": "Why use a deep ITM LEAPS instead of buying the stock outright?", "choices": ["LEAPS are always more profitable", "Capital efficiency — you control 100 shares for a fraction of the cost, freeing capital for other positions", "There's no advantage to LEAPS"], "answer": 1, "explanation": "A deep ITM LEAPS at $765 might cost ~$142/share ($14,200 total) vs buying 100 shares at $900 ($90,000). You save $75,800 in capital while getting ~75% of the stock's upside movement. The tradeoff: LEAPS expire (stock doesn't) and you pay a time premium."}],
        "connects_narrative": "The LEAPS strike is the 'long leg' shown in the PMCC Setup — it's the deep ITM option that acts as your stock substitute.",
        "connects_to": ["pmcc_setup", "delta", "front_month"],
        "resources": [{"label": "LEAPS Options", "url": "https://www.investopedia.com/terms/l/leaps.asp"}]
    },
    "delta": {
        "title": "Delta (Options Greek)",
        "category": "options",
        "tier": 4,
        "eli5": "Delta tells you how much an option's price moves when the stock moves $1. A delta of 0.75 means the option gains $0.75 for every $1 the stock goes up. Higher delta = acts more like owning the stock. Lower delta = cheaper but less responsive.",
        "detailed": "Delta ranges from 0 to 1 for calls, 0 to -1 for puts. Deep ITM calls: delta ~0.80-0.95 (moves almost like stock). ATM calls: delta ~0.50. OTM calls: delta ~0.15-0.30. In PMCC: long leg delta should be ~0.75+ (stock substitute), short leg delta ~0.25-0.35 (high probability of expiring worthless). Delta also approximates the probability of finishing ITM at expiration.",
        "quiz": [{"q": "Your LEAPS has 0.75 delta. Stock drops $10. How much does your LEAPS lose (approximately)?", "choices": ["$10.00", "$7.50", "$0.75"], "answer": 1, "explanation": "Loss = delta * stock move = 0.75 * $10 = $7.50 per share ($750 per contract). The 0.75 delta means you capture 75% of the stock's movement, both up and down. This is the tradeoff for lower capital commitment."}],
        "connects_narrative": "Delta values are shown in the PMCC Setup for both the long and short legs — they tell you how responsive each option is to stock price changes.",
        "connects_to": ["pmcc_setup", "leaps", "front_month"],
        "resources": [{"label": "Options Delta", "url": "https://www.investopedia.com/terms/d/delta.asp"}]
    },
    "open_interest": {
        "title": "Open Interest",
        "category": "options",
        "tier": 4,
        "eli5": "Open interest is the total number of options contracts that exist at a given strike price. High open interest at a strike means lots of people have bets there — it acts like a wall that the stock price may struggle to break through.",
        "detailed": "Open interest (OI) = total outstanding contracts at a strike. High OI strikes act as support/resistance because market makers hedge their exposure there. Top OI strikes indicate where the market expects the most action. In this brief, the top 3 strikes by combined call+put OI are shown for each ticker. Unusual OI spikes can signal institutional positioning.",
        "quiz": [{"q": "The top OI strikes are $900 (50K), $850 (45K), $950 (40K). Stock is at $910. What does this suggest?", "choices": ["The stock is overvalued", "$900 is a key level — heavy options positioning creates support there, while $950 is resistance", "Open interest doesn't affect stock price"], "answer": 1, "explanation": "The $900 strike has the most open interest, meaning market makers have significant hedging positions there. This creates gravitational pull and makes $900 a key support level. The $950 strike acts as resistance. These levels matter most as expiration approaches."}],
        "connects_narrative": "Top OI strikes in the Options section show you the key price levels where options market makers are positioned — these act as support/resistance.",
        "connects_to": ["max_pain", "pmcc_setup", "front_month"],
        "resources": [{"label": "Open Interest", "url": "https://www.investopedia.com/terms/o/openinterest.asp"}]
    },
    "anchoring": {
        "title": "Anchoring Bias",
        "category": "psychology",
        "tier": 4,
        "eli5": "Anchoring is when your brain fixates on a number — like your purchase price — and judges everything relative to that. 'I bought at $200, it's only $180, I'll wait for it to get back to $200.' The stock doesn't know what you paid. The market doesn't care about your anchor.",
        "detailed": "Anchoring bias: overweighting a specific reference point (often entry price, all-time high, or round numbers) when making decisions. In trading, it causes: (1) holding losers waiting to 'get back to even', (2) refusing to buy a stock that 'used to be cheaper', (3) setting arbitrary price targets based on historical levels. Antidote: focus on forward-looking metrics (the brief's verdicts and scenarios) rather than backward-looking reference points.",
        "quiz": [{"q": "You bought at $500, the stock dropped to $400, the brief says SELL. You think 'I'll wait for $500.' What bias is this?", "choices": ["Loss aversion", "Anchoring — you're fixated on your purchase price instead of the current thesis", "Confirmation bias"], "answer": 1, "explanation": "This is classic anchoring: you've anchored to your $500 purchase price and are making decisions relative to that number rather than the current market reality. The stock doesn't know you bought at $500. If the brief says SELL, the forward-looking thesis has changed regardless of your entry price."}],
        "connects_narrative": "Anchoring explains why the brief uses mechanical verdicts instead of letting you decide based on 'feel' or past prices.",
        "connects_to": ["loss_aversion", "confirmation_bias", "overconfidence"],
        "resources": [{"label": "Anchoring Bias", "url": "https://www.investopedia.com/terms/a/anchoring.asp"}]
    },
    "when_to_act": {
        "title": "When to Act vs When to Wait",
        "category": "strategy",
        "tier": 4,
        "eli5": "ACT when: the verdict says BUY/SELL, the risk regime supports it, and your position sizing is calculated. WAIT when: signals conflict, risk is elevated, or you're emotional. The brief is designed to answer 'what?' and 'when?' — you just need to follow the system.",
        "detailed": "Action triggers: (1) SELL verdict or trailing stop hit = exit immediately. (2) BUY verdict + LOW_RISK/NORMAL regime = enter with position sizing. (3) Cluster buy + BUY verdict = high conviction entry. Wait triggers: (1) CAUTION/DANGER regime = reduce sizes. (2) HOLD verdict = maintain positions, no new entries. (3) Conflicting signals = wait for alignment. (4) Post-loss = wait one session before re-entering (anti-revenge trading).",
        "quiz": [{"q": "Trailing stop just triggered on your NVDA position. What's the immediate action?", "choices": ["Cancel the stop and hold — it's just a dip", "Execute the sell. This is a pre-committed decision, not a real-time judgment call", "Buy more to average down while it's lower"], "answer": 1, "explanation": "A trailing stop trigger is a pre-committed exit signal. The time for deciding whether to use stops was when you entered the trade, not when they trigger. Overriding stops in the moment is one of the most destructive habits in trading — it turns small losses into large ones."}],
        "connects_narrative": "This concept ties together the entire brief's action framework: Risk regime, Verdict, Sizing, Execution.",
        "connects_to": ["verdict_framework", "risk_management", "first_loss"],
        "resources": []
    },
    "overconfidence": {
        "title": "Overconfidence Bias",
        "category": "psychology",
        "tier": 4,
        "eli5": "After a winning streak, your brain tells you you've 'figured out the market.' You start taking bigger positions, ignoring stops, and dismissing risk. This is when the biggest losses happen. The market humbles everyone eventually.",
        "detailed": "Overconfidence manifests as: (1) overestimating prediction accuracy, (2) underestimating downside risks, (3) increasing position sizes after wins (not based on system), (4) ignoring contrary evidence. Studies show traders are most overconfident after 3-5 consecutive wins. Antidote: mechanical position sizing (same % risk regardless of streak), maintaining stops, and reviewing the Verdict Scorecard to ground expectations in actual win rates (~60-65% is elite).",
        "quiz": [{"q": "You've had 5 winners in a row. Your next trade looks great. What should you do?", "choices": ["Go big — you're on a hot streak!", "Stick to the same position sizing formula. Streaks don't change the math, and the reversal is statistically closer", "Take a break — winning streaks are always followed by losing streaks"], "answer": 1, "explanation": "Each trade is independent. Your 5-win streak doesn't make the 6th trade more likely to win. Increasing size after wins (and decreasing after losses) is a well-documented bias. The Position Sizing section calculates the right size based on risk math, not emotions or streaks."}],
        "connects_narrative": "The Verdict Scorecard's win rate data helps ground your confidence in actual performance rather than feeling.",
        "connects_to": ["confirmation_bias", "anchoring", "position_sizing_process"],
        "resources": [{"label": "Overconfidence in Trading", "url": "https://www.investopedia.com/terms/o/overconfidence.asp"}]
    },
}


# ──────────────────────────────────────────────────────────────────────
# 2. LABEL / SECTION CONCEPT MAPS
# ──────────────────────────────────────────────────────────────────────

LABEL_CONCEPT_MAP = {
    # Technical Signals
    "Composite": "composite_score",
    "RSI(14)": "rsi",
    "RSI": "rsi",
    "MACD": "macd",
    "Bollinger": "bollinger",
    "SMA Trend": "sma_trend",
    "Vol Ratio": "volume_ratio",
    "VWAP": "vwap",
    "POC": "poc",
    # Earnings
    "Tone Score": "tone_score",
    "Confidence": "confidence_score",
    "Hedge / Definitive": "confidence_score",
    # Options
    "Max Pain": "max_pain",
    "Front Month": "front_month",
    "Top OI": "open_interest",
    "Net Debit": "pmcc_setup",
    # Valuation
    "Cheaper": "pe_ratio",
    "Metrics": "pe_ratio",
    "PE": "pe_ratio",
    "R/R Ratio": "scenario_valuation",
    "Bull": "scenario_valuation",
    "Base": "scenario_valuation",
    "Bear": "scenario_valuation",
    # Risk
    "Risk Score": "risk_management",
    "VIX": "risk_management",
    "10Y Yield": "risk_management",
    # Trade Memory
    "Win Rate": "verdict_framework",
    "Record": "verdict_framework",
    "Matches": "verdict_framework",
    # Sentiment
    "Sentiment": "tone_score",
    "Articles": "tone_score",
    # Portfolio
    "ATR": "atr",
    "Trail Stop": "trailing_stop",
    "Locked Profit": "trailing_stop",
    # Position Sizing
    "Shares": "position_sizing_process",
    "Stop": "trailing_stop",
    "Risk/Share": "position_sizing_process",
    "% of Portfolio": "position_sizing_process",
    # Journal
    "Total Trades": "overtrading",
    "Winners": "patience",
    "Losers": "first_loss",
    # Volume
    "Volume": "volume_ratio",
}

SECTION_CONCEPT_MAP = {
    "Risk Dashboard": "risk_management",
    "Trading Windows": "market_hours",
    "Watchlist Verdicts": "verdict_framework",
    "Position Sizing": "position_sizing_process",
    "Opportunity Scanner": "momentum",
    "Trade Memory": "verdict_framework",
    "Insider Activity (SEC Form 4)": "insider_activity",
    "Congressional Trading (STOCK Act)": "congressional_trading",
    "Scenario Valuations (Bull / Base / Bear)": "scenario_valuation",
    "Verdict Scorecard": "verdict_framework",
    "Portfolio Holdings": "trailing_stop",
    "Trade Journal": "overtrading",
    "Earnings Tone Analysis": "tone_score",
    "Valuation Comparisons": "pe_ratio",
    "Technical Signals": "composite_score",
    "News Sentiment": "tone_score",
    "Options Analysis": "max_pain",
    "Polymarket Prediction Markets": "prediction_markets",
    "Economic Calendar": "economic_calendar",
    "Macro Dashboard": "macro_indicators",
    "Sector Rotation": "sector_rotation",
}

LEARNING_PATH = [
    {"tier": 1, "name": "Foundations", "description": "Market basics & psychology", "count": 12},
    {"tier": 2, "name": "Core", "description": "Key indicators & metrics", "count": 11},
    {"tier": 3, "name": "Advanced", "description": "Deep analysis & strategy", "count": 13},
    {"tier": 4, "name": "Expert", "description": "Options & mastery", "count": 9},
]


# ──────────────────────────────────────────────────────────────────────
# 3. LEARNING CSS
# ──────────────────────────────────────────────────────────────────────

LEARNING_CSS = """
/* ── Learning System ─────────────────────────────────────────────── */
.learnable {
    border-bottom: 1.5px dotted var(--accent);
    cursor: pointer;
    transition: border-color 0.2s, color 0.2s;
}
.learnable:hover {
    border-bottom-color: var(--green);
    color: var(--green);
}
.learn-hint {
    display: inline-flex; align-items: center; justify-content: center;
    width: 20px; height: 20px; border-radius: 50%;
    background: var(--blue-bg); color: var(--accent);
    font-size: 0.7rem; font-weight: 700; margin-left: 8px;
    cursor: pointer; transition: background 0.2s;
    vertical-align: middle;
}
.learn-hint:hover { background: var(--accent); color: var(--bg-primary); }

/* Slide-out Panel */
#learn-overlay {
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,0.5); z-index: 9998;
}
#learn-overlay.active { display: block; }
#learn-panel {
    display: none; position: fixed; top: 0; right: 0;
    width: min(420px, 90vw); height: 100vh;
    background: var(--bg-card); border-left: 1px solid var(--border);
    z-index: 9999; overflow-y: auto;
    transform: translateX(100%); transition: transform 0.3s ease;
    padding: 24px;
}
#learn-panel.active { display: block; transform: translateX(0); }
#learn-panel .panel-close {
    position: absolute; top: 12px; right: 16px;
    background: none; border: none; color: var(--text-secondary);
    font-size: 1.5rem; cursor: pointer; line-height: 1;
}
#learn-panel .panel-close:hover { color: var(--text-primary); }
#learn-panel .panel-title {
    font-size: 1.2rem; font-weight: 700; color: var(--text-primary);
    margin-bottom: 4px; padding-right: 32px;
}
#learn-panel .panel-category {
    font-size: 0.75rem; text-transform: uppercase; letter-spacing: 1px;
    color: var(--accent); margin-bottom: 16px;
}
#learn-panel .panel-body {
    font-size: 0.9rem; line-height: 1.6; color: var(--text-secondary);
    margin-bottom: 20px;
}
#learn-panel .panel-narrative {
    font-size: 0.85rem; color: var(--text-muted); font-style: italic;
    margin-bottom: 16px; padding: 10px; border-radius: 6px;
    background: rgba(88,166,255,0.06);
}

/* Mode toggle (ELI5 / Detailed) */
.mode-toggle {
    display: flex; gap: 0; margin-bottom: 16px;
    border: 1px solid var(--border); border-radius: 6px; overflow: hidden;
}
.mode-btn {
    flex: 1; padding: 6px 12px; text-align: center; font-size: 0.8rem;
    font-weight: 600; cursor: pointer; border: none;
    background: var(--bg-primary); color: var(--text-secondary);
    transition: background 0.2s, color 0.2s;
}
.mode-btn.active {
    background: var(--accent); color: var(--bg-primary);
}

/* Quiz */
.quiz-section { margin-top: 20px; }
.quiz-section .quiz-q {
    font-size: 0.9rem; font-weight: 600; color: var(--text-primary);
    margin-bottom: 10px;
}
.quiz-choice {
    display: block; width: 100%; text-align: left;
    padding: 8px 12px; margin-bottom: 6px;
    background: var(--bg-primary); border: 1px solid var(--border);
    border-radius: 6px; color: var(--text-secondary);
    font-size: 0.85rem; cursor: pointer; transition: border-color 0.2s;
}
.quiz-choice:hover:not(.answered) { border-color: var(--accent); color: var(--text-primary); }
.quiz-choice.correct { border-color: var(--green); background: var(--green-bg); color: var(--green); }
.quiz-choice.incorrect { border-color: var(--red); background: var(--red-bg); color: var(--red); }
.quiz-choice.answered { cursor: default; }
.quiz-explanation {
    display: none; margin-top: 8px; padding: 10px;
    font-size: 0.85rem; color: var(--text-secondary);
    background: rgba(63,185,80,0.06); border-radius: 6px;
    line-height: 1.5;
}
.quiz-explanation.visible { display: block; }

/* Concept chips */
.concept-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
.concept-chip {
    display: inline-block; padding: 3px 10px;
    background: var(--bg-primary); border: 1px solid var(--border);
    border-radius: 12px; font-size: 0.75rem; color: var(--accent);
    cursor: pointer; transition: background 0.2s;
}
.concept-chip:hover { background: var(--blue-bg); }

/* Resource links */
.resource-links { margin-top: 12px; }
.resource-links a {
    display: block; font-size: 0.8rem; color: var(--accent);
    text-decoration: none; margin-bottom: 4px;
}
.resource-links a:hover { text-decoration: underline; }

/* Learning Path */
.learning-path {
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px 20px; margin-bottom: 24px;
}
.learning-path h3 {
    font-size: 0.95rem; font-weight: 600; color: var(--text-primary);
    margin-bottom: 12px;
}
.lp-steps { display: flex; gap: 0; align-items: center; }
.lp-step {
    flex: 1; text-align: center; position: relative; padding: 8px 4px;
}
.lp-step::after {
    content: ''; position: absolute; top: 22px; left: 50%; width: 100%;
    height: 2px; background: var(--border);
}
.lp-step:last-child::after { display: none; }
.lp-dot {
    width: 28px; height: 28px; border-radius: 50%;
    background: var(--bg-primary); border: 2px solid var(--border);
    display: inline-flex; align-items: center; justify-content: center;
    font-size: 0.7rem; font-weight: 700; color: var(--text-muted);
    position: relative; z-index: 1;
    transition: background 0.3s, border-color 0.3s, color 0.3s;
}
.lp-step.started .lp-dot { border-color: var(--accent); color: var(--accent); }
.lp-step.complete .lp-dot { background: var(--green); border-color: var(--green); color: var(--bg-primary); }
.lp-name {
    display: block; font-size: 0.7rem; color: var(--text-muted);
    margin-top: 4px;
}
.lp-progress {
    font-size: 0.65rem; color: var(--text-muted); margin-top: 2px;
}

/* How to Use */
.how-to-use {
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px 20px; margin-bottom: 24px;
}
.how-to-use h3 {
    font-size: 0.95rem; font-weight: 600; color: var(--text-primary);
    margin-bottom: 12px;
}
.process-steps { list-style: none; counter-reset: step; }
.process-steps li {
    counter-increment: step; position: relative;
    padding-left: 36px; margin-bottom: 10px;
    font-size: 0.85rem; color: var(--text-secondary); line-height: 1.5;
}
.process-steps li::before {
    content: counter(step);
    position: absolute; left: 0; top: 0;
    width: 24px; height: 24px; border-radius: 50%;
    background: var(--blue-bg); color: var(--accent);
    font-size: 0.75rem; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
}

/* Tour */
#tour-overlay {
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,0.7); z-index: 10000;
    align-items: center; justify-content: center;
}
#tour-overlay.active { display: flex; }
.tour-card {
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: 12px; padding: 28px 32px; max-width: 480px;
    width: 90vw; text-align: center;
}
.tour-card h2 {
    font-size: 1.3rem; color: var(--text-primary);
    margin-bottom: 8px;
}
.tour-card p {
    font-size: 0.9rem; color: var(--text-secondary);
    line-height: 1.6; margin-bottom: 20px;
}
.tour-step-indicator {
    font-size: 0.75rem; color: var(--text-muted);
    margin-bottom: 16px;
}
.tour-buttons { display: flex; gap: 10px; justify-content: center; }
.tour-btn {
    padding: 8px 20px; border-radius: 6px; font-size: 0.85rem;
    font-weight: 600; cursor: pointer; border: none;
    transition: background 0.2s;
}
.tour-btn-primary { background: var(--accent); color: var(--bg-primary); }
.tour-btn-primary:hover { background: #79bbff; }
.tour-btn-secondary { background: var(--bg-primary); color: var(--text-secondary); border: 1px solid var(--border); }
.tour-btn-secondary:hover { color: var(--text-primary); }
.tour-btn-skip { background: none; border: none; color: var(--text-muted); font-size: 0.8rem; cursor: pointer; }
.tour-btn-skip:hover { color: var(--text-secondary); }
"""


# ──────────────────────────────────────────────────────────────────────
# 4. LEARNING JS
# ──────────────────────────────────────────────────────────────────────

# SAFETY NOTE: All content rendered by this JS comes from the Python
# KNOWLEDGE_BASE (developer-controlled) and is HTML-escaped via the
# esc() function.  There are no user-input vectors in this self-contained
# HTML file.  DOM manipulation uses textContent where possible; the panel
# render uses the esc() helper for all dynamic strings.

LEARNING_JS = """
(function() {
    'use strict';
    var CONCEPTS = window.__LEARN_CONCEPTS || {};
    var mode = 'eli5';
    var currentSlug = null;
    var visited = {};

    function $(id) { return document.getElementById(id); }

    function esc(s) {
        var d = document.createElement('div');
        d.textContent = String(s);
        return d.innerHTML;
    }

    // ── Panel ────────────────────────────────────────────────────
    function openPanel(slug) {
        if (!CONCEPTS[slug]) return;
        currentSlug = slug;
        visited[slug] = true;
        renderPanel();
        $('learn-overlay').classList.add('active');
        $('learn-panel').classList.add('active');
        updateProgress();
    }

    function closePanel() {
        $('learn-overlay').classList.remove('active');
        $('learn-panel').classList.remove('active');
        currentSlug = null;
    }

    function setMode(m) {
        mode = m;
        document.querySelectorAll('.mode-btn').forEach(function(b) {
            b.classList.toggle('active', b.dataset.mode === m);
        });
        if (currentSlug) renderPanel();
    }

    function renderPanel() {
        var c = CONCEPTS[currentSlug];
        if (!c) return;
        var panel = $('learn-panel');
        // Build panel content using DOM methods for safety
        panel.textContent = '';

        var closeBtn = document.createElement('button');
        closeBtn.className = 'panel-close';
        closeBtn.dataset.action = 'close-panel';
        closeBtn.textContent = '\\u00d7';
        panel.appendChild(closeBtn);

        var title = document.createElement('div');
        title.className = 'panel-title';
        title.textContent = c.title;
        panel.appendChild(title);

        var cat = document.createElement('div');
        cat.className = 'panel-category';
        cat.textContent = c.category + ' — Tier ' + c.tier;
        panel.appendChild(cat);

        // Mode toggle
        var toggle = document.createElement('div');
        toggle.className = 'mode-toggle';
        ['eli5', 'detailed'].forEach(function(m) {
            var btn = document.createElement('button');
            btn.className = 'mode-btn' + (mode === m ? ' active' : '');
            btn.dataset.mode = m;
            btn.dataset.action = 'set-mode';
            btn.textContent = m === 'eli5' ? 'ELI5' : 'Detailed';
            toggle.appendChild(btn);
        });
        panel.appendChild(toggle);

        var body = document.createElement('div');
        body.className = 'panel-body';
        body.textContent = mode === 'eli5' ? c.eli5 : c.detailed;
        panel.appendChild(body);

        if (c.connects_narrative) {
            var narr = document.createElement('div');
            narr.className = 'panel-narrative';
            narr.textContent = c.connects_narrative;
            panel.appendChild(narr);
        }

        // Quiz
        if (c.quiz && c.quiz.length > 0) {
            panel.appendChild(buildQuizDOM(c.quiz[0]));
        }

        // Related concepts
        if (c.connects_to && c.connects_to.length > 0) {
            var relLabel = document.createElement('div');
            relLabel.style.cssText = 'font-size:0.8rem;color:var(--text-muted);margin-top:12px;font-weight:600';
            relLabel.textContent = 'Related Concepts';
            panel.appendChild(relLabel);

            var chips = document.createElement('div');
            chips.className = 'concept-chips';
            c.connects_to.forEach(function(slug) {
                var rel = CONCEPTS[slug];
                if (!rel) return;
                var chip = document.createElement('span');
                chip.className = 'concept-chip';
                chip.dataset.concept = slug;
                chip.dataset.action = 'navigate';
                chip.textContent = rel.title;
                chips.appendChild(chip);
            });
            panel.appendChild(chips);
        }

        // Resources
        if (c.resources && c.resources.length > 0) {
            var resDiv = document.createElement('div');
            resDiv.className = 'resource-links';
            resDiv.style.marginTop = '12px';
            c.resources.forEach(function(r) {
                var a = document.createElement('a');
                a.href = r.url;
                a.target = '_blank';
                a.rel = 'noopener';
                a.textContent = r.label + ' \\u2192';
                resDiv.appendChild(a);
            });
            panel.appendChild(resDiv);
        }
    }

    function buildQuizDOM(q) {
        var section = document.createElement('div');
        section.className = 'quiz-section';

        var qText = document.createElement('div');
        qText.className = 'quiz-q';
        qText.textContent = q.q;
        section.appendChild(qText);

        q.choices.forEach(function(choice, i) {
            var btn = document.createElement('button');
            btn.className = 'quiz-choice';
            btn.dataset.action = 'quiz';
            btn.dataset.idx = String(i);
            btn.dataset.answer = String(q.answer);
            btn.textContent = choice;
            section.appendChild(btn);
        });

        var explain = document.createElement('div');
        explain.className = 'quiz-explanation';
        explain.dataset.quizExplain = '';
        explain.textContent = q.explanation;
        section.appendChild(explain);

        return section;
    }

    function handleQuiz(btn) {
        var idx = parseInt(btn.dataset.idx, 10);
        var answer = parseInt(btn.dataset.answer, 10);
        var parent = btn.closest('.quiz-section');
        var buttons = parent.querySelectorAll('.quiz-choice');
        buttons.forEach(function(b) {
            b.classList.add('answered');
            var bIdx = parseInt(b.dataset.idx, 10);
            if (bIdx === answer) b.classList.add('correct');
            else if (bIdx === idx && idx !== answer) b.classList.add('incorrect');
        });
        parent.querySelector('[data-quiz-explain]').classList.add('visible');
    }

    // ── Progress ─────────────────────────────────────────────────
    function updateProgress() {
        var tiers = [0, 0, 0, 0];
        var tierTotals = [0, 0, 0, 0];
        for (var slug in CONCEPTS) {
            var t = CONCEPTS[slug].tier - 1;
            tierTotals[t]++;
            if (visited[slug]) tiers[t]++;
        }
        for (var i = 0; i < 4; i++) {
            var step = document.querySelector('.lp-step[data-tier="' + (i+1) + '"]');
            if (!step) continue;
            var pEl = step.querySelector('.lp-progress');
            if (pEl) pEl.textContent = tiers[i] + '/' + tierTotals[i];
            step.classList.remove('started', 'complete');
            if (tiers[i] >= tierTotals[i] && tierTotals[i] > 0) step.classList.add('complete');
            else if (tiers[i] > 0) step.classList.add('started');
        }
    }

    // ── Tour ─────────────────────────────────────────────────────
    var tourSteps = [
        {title: 'Welcome to Your Morning Brief', body: 'This brief gives you everything you need to make informed trading decisions. Let me show you the key areas.'},
        {title: 'Start with Risk', body: 'The Risk Dashboard at the top tells you the market environment. DANGER means be cautious. LOW_RISK means lean into setups. Always check this first.'},
        {title: 'Read the Verdicts', body: 'Watchlist Verdicts are the brief\\'s recommendations: BUY, SELL, HOLD, AVOID, or REVIEW. These are mechanical, with no emotion and no bias.'},
        {title: 'Click to Learn', body: 'See the dotted underlines on metric labels? Click any of them to open a learning panel that explains what the metric means and why it matters.'},
        {title: 'Track Your Progress', body: 'The Learning Path at the top tracks which concepts you\\'ve explored. Work through all four tiers to build your trading knowledge. Enjoy!'},
    ];
    var tourIdx = 0;

    function showTour() {
        tourIdx = 0;
        renderTourStep();
        $('tour-overlay').classList.add('active');
    }

    function renderTourStep() {
        var step = tourSteps[tourIdx];
        var card = $('tour-overlay').querySelector('.tour-card');
        card.querySelector('h2').textContent = step.title;
        card.querySelector('p').textContent = step.body;
        card.querySelector('.tour-step-indicator').textContent = 'Step ' + (tourIdx+1) + ' of ' + tourSteps.length;
        var backBtn = card.querySelector('[data-action="tour-back"]');
        if (backBtn) backBtn.style.display = tourIdx === 0 ? 'none' : '';
        var nextBtn = card.querySelector('[data-action="tour-next"]');
        if (nextBtn) nextBtn.textContent = tourIdx === tourSteps.length - 1 ? 'Got it!' : 'Next';
    }

    function closeTour() {
        $('tour-overlay').classList.remove('active');
        try { localStorage.setItem('brief_tour_done', '1'); } catch(e) {}
    }

    // ── Events ───────────────────────────────────────────────────
    document.addEventListener('click', function(e) {
        var el = e.target;
        if (el.classList.contains('learnable') && el.dataset.concept) { openPanel(el.dataset.concept); return; }
        if (el.classList.contains('learn-hint') && el.dataset.concept) { openPanel(el.dataset.concept); return; }
        if (el.dataset.action === 'close-panel' || el.id === 'learn-overlay') { closePanel(); return; }
        if (el.dataset.action === 'set-mode') { setMode(el.dataset.mode); return; }
        if (el.dataset.action === 'quiz' && !el.classList.contains('answered')) { handleQuiz(el); return; }
        if (el.dataset.action === 'navigate' && el.dataset.concept) { openPanel(el.dataset.concept); return; }
        if (el.dataset.action === 'tour-next') { if (tourIdx >= tourSteps.length - 1) closeTour(); else { tourIdx++; renderTourStep(); } return; }
        if (el.dataset.action === 'tour-back') { if (tourIdx > 0) { tourIdx--; renderTourStep(); } return; }
        if (el.dataset.action === 'tour-skip') { closeTour(); return; }
        if (el.dataset.action === 'start-tour') { showTour(); return; }
    });

    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            if ($('learn-panel').classList.contains('active')) closePanel();
            else if ($('tour-overlay').classList.contains('active')) closeTour();
        }
    });

    // Auto-show tour on first visit
    try {
        if (!localStorage.getItem('brief_tour_done')) {
            setTimeout(showTour, 600);
        }
    } catch(e) {}
})();
"""


# ──────────────────────────────────────────────────────────────────────
# 5. HELPER FUNCTIONS
# ──────────────────────────────────────────────────────────────────────

def _esc(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def label(text: str, fallback: Optional[str] = None) -> str:
    """Render a card-label span, adding learnable data-concept if mapped."""
    concept = LABEL_CONCEPT_MAP.get(text)
    if concept and concept in KNOWLEDGE_BASE:
        return f'<span class="card-label learnable" data-concept="{concept}">{_esc(text)}</span>'
    fb = fallback or text
    return f'<span class="card-label">{_esc(fb)}</span>'


def section_title(text: str, concept: Optional[str] = None, extra_html: str = "") -> str:
    """Render a section-title div with optional learn-hint badge.

    Args:
        text: The visible title text (may contain HTML like &mdash;).
        concept: Override concept slug. If None, looks up SECTION_CONCEPT_MAP.
        extra_html: Additional HTML to append inside the div (e.g. regime span).
    """
    slug = concept or SECTION_CONCEPT_MAP.get(text)
    hint = ""
    if slug and slug in KNOWLEDGE_BASE:
        hint = f' <span class="learn-hint" data-concept="{slug}">?</span>'
    return f'<div class="section-title">{text}{extra_html}{hint}</div>'


def build_data_snapshot(
    portfolio_data: dict,
    technical_data: dict,
    earnings_data: dict,
    risk_data: dict,
) -> dict:
    """Extract live values for quiz token replacement.

    Returns a flat dict like {"NVDA_RSI": "62.3", "NVDA_COMPOSITE": "+1.5", ...}
    """
    snap = {}
    for t in technical_data.get("results", []):
        tk = t.get("ticker", "")
        if not tk:
            continue
        snap[f"{tk}_RSI"] = str(t.get("rsi_14", "N/A"))
        snap[f"{tk}_COMPOSITE"] = f'{t.get("composite_score", 0):+.1f}'
        snap[f"{tk}_MACD"] = str(t.get("macd_signal", "N/A"))
        snap[f"{tk}_VWAP"] = str(t.get("vwap", "N/A"))
    for h in portfolio_data.get("holdings", []):
        tk = h.get("ticker", "")
        if not tk:
            continue
        snap[f"{tk}_PRICE"] = f'${h.get("current_price", 0):,.2f}'
        snap[f"{tk}_STOP"] = f'${h.get("trailing_stop", 0):,.2f}'
    for r in earnings_data.get("results", []):
        tk = r.get("ticker", "")
        if not tk:
            continue
        snap[f"{tk}_TONE"] = f'{r.get("tone_score", 0):+.1f}'
    snap["VIX"] = str(risk_data.get("vix", "N/A"))
    snap["REGIME"] = str(risk_data.get("regime", "UNKNOWN"))
    return snap


def resolve_quiz_tokens(kb: dict, snapshot: dict) -> dict:
    """Replace {TOKEN} placeholders in quiz questions with live data.

    Returns a new KNOWLEDGE_BASE dict with tokens resolved (shallow copy).
    """
    resolved = {}
    for slug, concept in kb.items():
        new_concept = dict(concept)
        new_quiz = []
        for q in concept.get("quiz", []):
            new_q = dict(q)
            question = new_q["q"]
            for token, value in snapshot.items():
                question = question.replace("{" + token + "}", value)
            new_q["q"] = question
            new_quiz.append(new_q)
        new_concept["quiz"] = new_quiz
        resolved[slug] = new_concept
    return resolved


def build_concepts_json(kb: dict) -> str:
    """Serialize knowledge base to JSON for embedding in <script>."""
    slim = {}
    for slug, c in kb.items():
        slim[slug] = {
            "title": c["title"],
            "category": c["category"],
            "tier": c["tier"],
            "eli5": c["eli5"],
            "detailed": c["detailed"],
            "quiz": c.get("quiz", []),
            "connects_narrative": c.get("connects_narrative", ""),
            "connects_to": c.get("connects_to", []),
            "resources": c.get("resources", []),
        }
    return json.dumps(slim, separators=(",", ":"))


def build_panel_html() -> str:
    """Return the panel + overlay DOM structure."""
    return """
<div id="learn-overlay"></div>
<div id="learn-panel"></div>
"""


def build_tour_html() -> str:
    """Return the guided tour overlay DOM."""
    return """
<div id="tour-overlay">
    <div class="tour-card">
        <h2></h2>
        <div class="tour-step-indicator"></div>
        <p></p>
        <div class="tour-buttons">
            <button class="tour-btn tour-btn-secondary" data-action="tour-back">Back</button>
            <button class="tour-btn tour-btn-primary" data-action="tour-next">Next</button>
        </div>
        <button class="tour-btn-skip" data-action="tour-skip">Skip tour</button>
    </div>
</div>
"""


def build_learning_path_html() -> str:
    """Return the 4-tier learning path stepper."""
    html = '<div class="learning-path">'
    html += '<h3>Learning Path <button class="tour-btn-skip" data-action="start-tour" style="margin-left:8px;color:var(--accent);font-size:0.75rem;cursor:pointer;background:none;border:none">Restart Tour</button></h3>'
    html += '<div class="lp-steps">'
    for step in LEARNING_PATH:
        html += f'<div class="lp-step" data-tier="{step["tier"]}">'
        html += f'<span class="lp-dot">{step["tier"]}</span>'
        html += f'<span class="lp-name">{_esc(step["name"])}</span>'
        html += f'<span class="lp-progress">0/{step["count"]}</span>'
        html += '</div>'
    html += '</div></div>'
    return html


def build_how_to_use_html() -> str:
    """Return the 'How to Use This Brief' process guide."""
    steps = [
        "<strong>Check the Risk Dashboard</strong> — Is the environment favorable? DANGER means reduce sizes.",
        "<strong>Read the Verdicts</strong> — BUY, SELL, HOLD, AVOID, or REVIEW for each ticker.",
        "<strong>Size your positions</strong> — The Position Sizing table tells you exactly how many shares and where to set stops.",
        "<strong>Confirm with technicals</strong> — Check composite score, RSI, and MACD for timing.",
        "<strong>Review earnings &amp; sentiment</strong> — Is the narrative supporting or contradicting the verdict?",
        "<strong>Check options flow</strong> — Max pain and top OI strikes show where smart money is positioned.",
        "<strong>Learn as you go</strong> — Click any <span style='border-bottom:1.5px dotted var(--accent);color:var(--accent)'>underlined label</span> to learn what it means.",
    ]
    html = '<div class="how-to-use">'
    html += '<h3>How to Use This Brief</h3>'
    html += '<ol class="process-steps">'
    for s in steps:
        html += f'<li>{s}</li>'
    html += '</ol></div>'
    return html
