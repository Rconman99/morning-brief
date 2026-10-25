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
