# Dead-ideas ledger

Every strategy idea that is tested gets an entry here before any summary of the result.
Verdicts are fixed: **DATA VALIDATED**, **TRADE-OFF**, or **NO EDGE**.
**NO EDGE is a result.** Do not retest a dead idea unless there is a new reason (new data, a new model, or a rule the old test could not see). Write that reason on the entry before running it again.

## Entry template

```
## R-### <question in one line>
status: open | accepted | dead
verdict: ...
evidence: <data, N, window>
next: <next step or 'do not retest without a new reason'>
```

## R-001 Do crypto gimme bets (^cpc) at ~93¢ have an edge?
status: dead
verdict: NO EDGE
evidence: Disabled by default is verified. `agent/gimme_rules.py` sets `DEFAULT_SKIP_SLUG_REGEX = r"^cpc"` (matches `cpc-`, `cpceth-`, `cpcsol-`, `cpcxrp-`). `docs/HANDOFF.md` §6 and merged PR #18 (https://github.com/Rconman99/morning-brief/pull/18) say the same thing: crypto price slugs are off, and clearing the regex still does not trade them because `model_prob_for` has no crypto model. The favorite test in that PR is a 92–99¢ ask treated as a win probability; the code threshold is the midpoint at `DEFAULT_PRICE_THRESHOLD = 0.92` (92¢), not a separate 93¢ constant. The claim "670 proposals won ~8.5%" is not in the code, `docs/HANDOFF.md`, or the PR #18 description or comments. This checkout has no `proposals.jsonl` (gitignored; PR #18 says there was nothing to score). Do not treat 670 or ~8.5% as a measured result from this repo.
next: do not retest without a new reason

## R-002 Does a weather_us gimme with own-model edge >= ask + fee + 3¢ have an edge?
status: open
verdict: (not set — still open)
evidence: The gate is verified. `passes_own_edge` in `agent/gimme_rules.py` requires `model_prob >= price + taker_fee(price) + margin`, with `DEFAULT_MIN_EDGE = 0.03` and taker fee `0.0695 * p * (1 - p)`. Weather brackets pass the unblended EMOS/ensemble `p_yes` (`model_prob_for`, `docs/HANDOFF.md` §6, PR #18). A "small sample of paper wins" is not verified. The handoff snapshot dated 2026-10-11 records 5 paper trades: 3 settled, all losses on one BTC event (−$23.05), and 2 open weather tickets (not settled). `weather_us` proposals had not been executed (group cap / risk pause). The "~50 settled trades" judge point is the existing bar, not a new sample: `agent/scoreboard.py` `MIN_TRADES_UP = 50`, and HANDOFF backlog item 1 ("aim for ~50 settled trades") for weather_us paper results.
next: judge at ~50 settled trades of this rule

## R-003 Does a held-out time split plus a parameter sweep of gimme_backtest.py change the gimme verdict?
status: open
verdict: (not set — still open)
evidence: Not implemented, and not verifiable as a prior result. `agent/gimme_backtest.py` replays logged gimme rows and reports keep/reject hit rates. It has no held-out time split and no parameter sweep. PR #18 describes that replay and what the log cannot test; it does not mention a sweep. The "~50 settled trades" deferral is not written next to the script. The number 50 in-repo is the scoreboard step-up gate (`MIN_TRADES_UP = 50`) and HANDOFF backlog item 1. This entry records the deferral; the code does not enforce it.
next: deferred until ~50 settled trades

## R-004 Does shrinking the weather model's probability toward the market, before the edge test, fix its overconfidence?
status: open
verdict: (not set — still open)
evidence: The model's own settled paper record is the reason to try. Across about 60 scored predictions the model Brier score is 0.187, worse than the market price's 0.166, and picks rated near 88% have hit about 52–55%. Those logs are not in this checkout (`agent/proposals.jsonl` and `agent/ledger_cache.json` are gitignored), so that hit rate is not re-measured here. `agent/prob_calibration.py` blends the raw side probability with the market mid. The blend weight minimizes Brier on settled `weather_us` and `gimme_bets` rows and is shrunk toward zero by `PROB_CAL_PRIOR_N` (default 80) pseudo-observations, so a small sample uses the market. Platt scaling and a binned map stay off until 80 and 200 settled rows. The result is clamped so it is never further from 50% than the raw probability and never raises the side probability. The edge test (ask + taker fee + the existing 3¢ gimme / 4¢ weather buffer) and any size above the 5-share floor use that lower number. The proposal log still scores the raw probability and also stores the calibrated one. Humility (25¢, judged before the shrink), the one-position correlation cap, the loss pause, `POLYMARKET_LIVE` / `POLYMARKET_PAPER`, and the PR #18 book, crypto-slug, and no-model rules are unchanged. Not judged yet: whether the shrunk probability beats the market on the next settled trades.
next: score the next settled weather_us and gimme_bets proposals (raw Brier vs calibrated Brier vs market Brier) before treating this as a reason to add capital
