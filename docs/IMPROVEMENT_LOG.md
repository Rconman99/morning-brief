# Improvement log

Champion/challenger record for the paper bot. Each entry tests **one change only**.
A rejected entry stays rejected: do not retry it unless this file records a new reason
(new data, a new window, or a rule the old test could not see).

`agent/loss_review.py` is analysis only. It does not change sizing, gates, or orders.
Use it to fill the held-out result before promoting anything.

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
do not retry unless: a new held-out window shows the settled P&L of bets shadow would have skipped, with the model-vs-market Brier on the last 14 days of that window written into this entry first.
