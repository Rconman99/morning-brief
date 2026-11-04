# Improvement log

Champion/challenger record for the paper bot. Each entry tests **one change only**.
A rejected entry stays rejected: do not retry it unless this file records a new reason
(new data, a new window, or a rule the old test could not see).

`agent/loss_review.py` is analysis only. It does not change sizing, gates, or orders.
Use it to describe why a loss happened. It is not the promotion test.

`agent/challenger_gate.py` is the promotion test. It is eval only. It does not
change orders, size, gates, the loss pause, paper/live, or calibration.

## How to promote a strategy or calibration change

Every future strategy or calibration PR has to include the gate output. Do these
steps in order:

1. Keep the trading path on the champion. Put the single change in a candidate
   module (`side_probability(view)`) or a JSON config. Do not change sizing,
   gates, caps, the loss pause, paper/live, or the calibration mode in that PR
   unless the candidate *is* that one change and the gate has already passed.
2. Run the gate on the frozen rows in `tests/fixtures/real_misses/`:

   ```
   .venv/bin/python agent/challenger_gate.py --candidate <module-or-config>
   ```

   `champion` replays the current path and must stay PASS. A real challenger
   must not be worse than that path.
3. Paste the full gate output into the PR and into the next `I-###` entry under
   `held-out result`.
4. Promote only when the verdict is PASS **and** Ryan approves. A FAIL stays
   rejected. Do not retry it unless a later entry records a new reason (new
   data, a new window, or a rule this gate could not see).
5. The frozen set is the six settled losses from 2026-10-01..2026-11-03 (five
   gimme `unknown`, one weather_us `late_move`) plus the 13 settled `weather_us`
   trades (12W/1L). Weather is already scoring. A candidate that worsens its
   Brier or its win rate fails even if the rest of the set looks better.

## Template

```
## I-### <one sentence>
status: open | promoted | rejected
champion: <what is live or paper today>
challenger: <the single change>
held-out result: <window, metric, n, or "not scored">
decision: promoted | rejected
why: <one paragraph>
do not retry unless: <new reason required, or "n/a" if promoted>
```

## I-001 Shrink weather and gimme probabilities toward the market before the edge test (R-004)

status: rejected
champion: Raw side probability. `CALIBRATION_MODE` stays `shadow`, so edge and size still use the pre-calibration number. The humility cap, the one-position correlation cap, the loss pause, and the paper/live switch are unchanged.
challenger: `CALIBRATION_MODE=enforce`. One change: let the shrunk probability drop a bet or shrink a size that is already above the 5-share floor. Nothing else.
held-out result: Not scored. `research.md` R-004 cites about 60 scored predictions where the model Brier was 0.187 against the market's 0.166, and picks near 88% hit about 52–55%. Those logs are not in this checkout. The shadow file records raw prob, calibrated prob, `would_skip`, and `would_resize`. Compare would-skip counts and settled P&L with `agent/calibration_shadow.py --from YYYY-MM-DD --to YYYY-MM-DD` after about two weeks. Loss tags for the same window: `agent/loss_review.py --from YYYY-MM-DD --to YYYY-MM-DD`.
decision: rejected
why: The shrink is still a hypothesis. Promoting enforce would change which bets are kept. That is not allowed on a sample that has not been held out, and shadow mode already exists so the comparison can be made without touching a fill. R-004 in `research.md` stays open until that comparison is written here.
do not retry unless: a new held-out window shows the settled P&L of bets shadow would have skipped, with the model-vs-market Brier on the last 14 days of that window written into this entry first. The retry also has to paste a PASS from `agent/challenger_gate.py --candidate <that change>` and get Ryan's approval. The frozen weather_us rows are already ahead of the market, so a shrink that worsens their Brier or win rate fails the gate.

## I-002 Require the challenger gate before promoting a strategy or calibration change

status: open
champion: Raw side probability on the frozen paper rows. Weather uses the recorded raw probability. Gimme uses `model_prob_for`, which is still no-model on the five crypto losses. `CALIBRATION_MODE`, size, gates, the loss pause, and paper/live are unchanged.
challenger: No trading change. This entry adds the eval gate later PRs have to run (`agent/challenger_gate.py`). The candidate for the baseline is the champion itself.
held-out result: `agent/challenger_gate.py --candidate champion` on `tests/fixtures/real_misses/` (6 real misses + 13 settled weather_us). PASS. Fixed set n=13, Brier champion 0.066389 vs market 0.111777. weather_us 12W/1L, win rate 12/13, champion beats market. The five gimme losses have no champion probability (n=0). The San Francisco late_move miss stays at 0.8823 vs market 0.7500 and is not more confident. These Brier numbers use the integer percent stored in the trade reason except for that one review probability 0.8823. They are not the server's last-14-day weather Brier (0.104 vs 0.136) and not the broader N=67 model-vs-market score.
decision: rejected
why: The gate requirement above is in effect. There is nothing to promote into the order path. The gate is the step a later strategy or calibration change has to pass, plus Ryan's approval. Shipping the harness does not change a bet.
do not retry unless: n/a for this harness. A strategy or calibration retry still needs its own entry, a PASS from the gate, and Ryan's approval.
