# HANDOFF — Polymarket trading bot (state as of 2026-10-11 11:20 PT)

Read this top to bottom before touching anything. It is written for an AI assistant
taking over from Claude. Owner: **Ryan** (non-developer "vibecoder"; wants exact
commands he can paste, plain-English explanations, impact level on recommendations).

---

## 1. One-paragraph summary

A Python bot runs every 15 minutes on a DigitalOcean droplet and trades
**Polymarket US** (polymarket.us — the CFTC-regulated US exchange, USD balances,
Ed25519 API keys, official SDK `polymarket-us`). It is currently in **PAPER mode**
(no API keys installed). Adding keys later does **not** go live: real orders are
sent only when `POLYMARKET_LIVE=1` (also `true`/`yes`), both US keys are present,
and `POLYMARKET_PAPER` is not set. `POLYMARKET_PAPER=1` forces paper even if
`POLYMARKET_LIVE=1`. Two strategies run: `gimme_bets` (buy heavy favorites ≥92¢ as a maker) and
`weather_us` (a calibrated multi-model temperature forecast for daily-high markets).
Every trade is scored against the exchange's real settlement; capital is allocated
to strategies by realized performance; a "capital ladder" says when the owner has
earned the right to add money ($100 → $250 → … → $5,000). A full code audit was
done 2026-10-10; 2 CRITICAL + 5 HIGH bugs were fixed and re-verified.

**Goal:** prove an edge on ~$95, then scale into the thousands, giving more capital
to winning strategies and less to losers, continuously improving.

---

## 2. Current state (verified 2026-10-11 11:16 PT)

| Item | State |
|---|---|
| Code on server | commit `6df6d15` (repo `Rconman99/morning-brief`, branch `main`) |
| Mode | **paper** (`POLYMARKET_VENUE=us`, `POLYMARKET_BANKROLL=95`, no US keys). Stays paper after keys are added until `POLYMARKET_LIVE=1`. |
| Timer / dashboard | both active; ~68 cycles/24h; 0 errors in last 12h |
| Paper trades | 5 total (all `gimme_bets`, all from the FIRST cycle before the fixes) |
| Settled | 3 — **all lost, −$23.05**: three bets on the same BTC 5pm-ET print (YES ≥$84,250, YES ≥$85,250, NO ≥$81,250). This is the correlation risk the fixes now block (crypto `cpc-` brackets skipped; 1 position per event group). |
| Open | 2 (Miami 93–94°F NO @0.93, LA "69 or below" NO @0.94 — the weather model rated the LA one ~50/50) |
| Risk pause | **Active**: "Daily realized loss $23.05 > 5% of bankroll". Window is keyed on trade-open time (2026-10-10 20:10 UTC), so it lifts automatically ~2026-10-11 20:10 UTC (~1:10pm PT). Do not "fix" this by deleting trades. |
| weather_us | producing 1–2 proposals/cycle; none executed yet (blocked by group cap / risk pause). |
| Scoreboard | HOLD — needs ≥50 settled trades, ROI ≥1%, taker-bound ROI >0, ≥80% live fills |
| Server security | **NOT hardened yet** (see §9). Root password SSH enabled under active brute force (~8.9k failed attempts/24h), firewall off, dashboard :8080 public, reboot + 40 updates pending, no backups. |
| Owner to-dos | add US API keys, fund polymarket.us, harden server, (optional) recover $88 pUSD |

---

## 3. History (why things are the way they are)

1. Bot originally traded **polymarket.com** (Polygon CLOB) from this droplet.
   No live order succeeded after **2026-04-28**.
2. **2026-06-28** the bankroll was swept to a fresh wallet. Polymarket CLOB V2 only
   accepts direct orders from grandfathered wallets; others must use a Polymarket
   "Deposit Wallet", which only Polymarket can create (factory is operator-only —
   verified on-chain). Every order failed: `maker address not allowed, please use the deposit wallet flow`.
3. Creating a deposit wallet needs a polymarket.com login, which **blocks US users**.
   Decision: **move to Polymarket US**. Do NOT try to route around the polymarket.com
   geo-block.
4. 2026-10-09/10: Claude migrated the code, built the US venue, real-settlement
   ledger, risk controls, scoreboard, weather model; ran an independent audit; fixed
   findings. The old evaluator had *assumed* every ≥90¢ paper trade won — that is where
   an earlier fake "7-0" record came from. Weights were reset to 1.0.

---

## 4. Access & operations

- **Server:** DigitalOcean droplet `polymarket-agent`, `139.59.64.154` (Bangalore, Ubuntu 24.04, ~$6.62/mo).
- **SSH (from Ryan's Mac only):** `ssh -i ~/.ssh/id_polymarket root@139.59.64.154`
  (`~/.ssh/polymarket` does NOT work). Fallback: DigitalOcean web console.
- **Project dir:** `/opt/morning-brief`, venv `/opt/morning-brief/.venv` (Python 3.12).
- **systemd:** `polymarket-agent.timer` (every 15 min) → `polymarket-agent.service`
  → `/bin/bash /opt/morning-brief/agent/vps_run_us.sh`; `polymarket-dashboard.service`
  (`agent/dashboard.py 8080`).
- **Cycle** (`agent/vps_run_us.sh`, with `flock`): `us_scanner.py` → `weather_us.py`
  → `run.py --bankroll $POLYMARKET_BANKROLL` → `tracker.py` → `scoreboard.py --telegram`.
  Logs: `agent/vps.log`; runs: `agent/runs.jsonl`; trades: `agent/paper_trades.jsonl`
  (live and paper both logged here).
- **Deploys are manual** (auto `git pull` removed in the audit):
  `cd /opt/morning-brief && git pull --ff-only && git log --oneline -1`
- **Trigger one cycle now:** `systemctl start --no-block polymarket-agent.service`
- **Useful reads:**
  - `tail -80 agent/vps.log`
  - `.venv/bin/python agent/scoreboard.py`
  - `.venv/bin/python agent/ledger.py`
  - `.venv/bin/python agent/weather_us.py --print` (model vs market per bracket)
  - `.venv/bin/python agent/verify_us.py --preview` (after keys exist)
  - `.venv/bin/python agent/weather_settlement_backtest.py` (CLI highs vs past settlements; writes the city-skip file)
  - `.venv/bin/python agent/scoreboard.py` (includes proposal Brier / CLV; not a ladder gate)
- Telegram: daily scoreboard ~8am PT (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` in `.env`).

### `.env` variables (names only — never print values)
`POLYMARKET_PRIVATE_KEY` (old Polygon wallet key; legacy), `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, `POLYMARKET_SIG_TYPE` (legacy), `OPENROUTER_API_KEY`,
`POLYMARKET_VENUE=us`, `POLYMARKET_BANKROLL=95`.
To be added by Ryan: `POLYMARKET_US_KEY_ID`, `POLYMARKET_US_SECRET_KEY`.
Live trading is opt-in: `POLYMARKET_LIVE=1` (also `true`/`yes`) **and** both keys
**and** `POLYMARKET_PAPER` not set. Keys alone stay paper (trades still logged and
scored; no orders, no cancels, no equity-gate skip).
Optional tunables: `POLYMARKET_PAPER=1` (force paper; overrides `POLYMARKET_LIVE`), `POLYMARKET_US_MAKER` (default 1),
`POLYMARKET_US_ORDER_TTL_MIN` (120), `MAX_SINGLE_POSITION_PCT` (0.05),
`MAX_POSITIONS_PER_GROUP` (1), `US_GIMME_SKIP_CATEGORIES` (default `sports`),
`US_GIMME_SKIP_SLUG_REGEX` (default `^cpc-`), `WX_MIN_PROB` (0.80), `WX_MIN_EDGE` (0.04),
`WX_MAX_EDGE` (0.25), `WX_CAL_DAYS` (75), `WX_ENSEMBLE` (default `1`; set `0` to
use the older residual kernel and skip the log-odds blend), `WX_MODEL_WEIGHT`
(default `0.40`, model share of the blend), `WX_EMOS_MIN_DAYS` (40),
`WX_OBS_FLOOR_MARGIN` (1°F),
`WX_SKIP_MISMATCH_CITIES` (default `1`: skip a city only if
`data/processed/weather_settlement_backtest.json` lists it), `WX_SKIP_CITIES`
(comma-separated extra skips, e.g. `lax`), `WX_SKIP_ON_RULES_MISMATCH` (default
`0`: log a rules/station disagreement, do not skip unless set to `1`).

---

## 5. Architecture / code map (`agent/`)

| File | Role |
|---|---|
| `pm_us.py` | Polymarket US client (paced public client, auth client), BBO, market list, **wire price conversion**, orders (post-only maker, synchronous), positions, equity, settlement, stale-order sweep, event `group_key()` |
| `us_scanner.py` | Builds `gimme_bets` candidates from US quotes → `data/processed/polymarket_us.json`. Skips sports + `cpc-` crypto brackets by default. |
| `weather_us.py` | Temperature model → `data/processed/weather_us.json`; calibration cache `weather_us_calibration.json`. Skips a city the settlement backtest has flagged. Ensemble path is on unless `WX_ENSEMBLE=0`. |
| `weather_ensemble.py` | GEFS / ECMWF ENS / ICON / GEM daily highs, EMOS bias+spread, observed-high floor, log-odds blend. No API key. |
| `weather_settlement_check.py` | Station map (KMIA, KNYC Central Park, KMDW, KSFO, KLAX), LST climate day, inclusive whole-degree brackets, rules-text check |
| `weather_settlement_backtest.py` | Past US temperature settlements vs IEM CLI highs → `data/processed/weather_settlement_backtest.json` |
| `proposal_log.py` | Appends every proposal to `agent/proposals.jsonl`; Brier, log loss, CLV math |
| `strategies.py` | `scan_weather_us`, `scan_gimme_bets` (+ weather veto), `run_all_strategies` (US venue keeps only `venue=="us"` proposals); signal files >30 min old ignored |
| `risk_gate.py` | All limits (see §6); live mode reads exposure from the exchange |
| `executor.py` | `get_mode()` is the only paper/live switch. US is live only with `POLYMARKET_LIVE` + both keys and without `POLYMARKET_PAPER`. `place_limit_order` → `_place_limit_order_us` |
| `run.py` | Main loop: log mode → equity cap (live only) → evaluator → stale-order sweep (live only) → auto-exit (live only) → strategies → risk gate → execute → log |
| `ledger.py` | Scores every US trade vs real settlement; fees; paper "taker-bound" lower bound. Also scores logged proposals (Brier / log loss / CLV) once the same settlement cache knows the market. |
| `evaluator.py` | Every 5 days moves strategy weights on settled, taker-bound ROI (≥10 settled per strategy). Records proposal evidence on the eval row; does **not** move weights from it. |
| `scoreboard.py` | Per-strategy table + capital ladder + model-vs-market section + Telegram (short). Ladder rules unchanged. |
| `close_positions.py` | Exit orders (live only) |
| `tracker.py`, `dashboard.py` | Reporting (dashboard on :8080). Tracker does not read the account unless live. The dashboard header shows PAPER or LIVE from `get_mode()`. |
| `verify_us.py` | Read-only key/balance/positions check; `--preview` validates a YES **and** a NO order server-side without placing. Does not turn the bot live. |
| `cash_out_pusd.py` | Swaps the old bot wallet's pUSD → USDC on Uniswap V3 (dry-run default) |
| `pm_client.py`, `fund_deposit_wallet.py` | Legacy polymarket.com (Polygon) path — not used on US venue |

---

## 6. Trading rules (the "why" is evidence-based — keep them)

**Evidence:** Kalshi study of 46k contracts (Bürgi/Deng/Whelan): ≤10¢ contracts lose
>60%; the only positive post-fee bucket is **buying >50¢ (esp. >70¢) as a maker** (~+2.6%,
σ≈33%). Polymarket US fees (eff. 2026-10-07): taker `0.0695·C·p(1−p)`, **maker rebate
`0.0125·C·p(1−p)`**. <1% of wallets take ~half of profits; surviving playbooks are sports
buy-and-hold, fast-bot crypto MM (not us), and low-frequency model trades (e.g. weather).

- **Maker only:** post-only bid at/just above best bid, never at/above the ask, never above
  the strategy's limit. Stale resting bids cancelled after 120 min (live).
- **⚠️ Price convention (critical, audit-fixed):** Polymarket US `price.value` is ALWAYS the
  YES price for every intent. To buy NO at X send `1 − X` (BUY_SHORT). Internally the bot
  works in "price of the outcome held"; conversion happens ONLY in `pm_us.build_order`
  (`to_wire_price`) and back via `from_wire_price` for orders/positions/fills. Do not
  convert twice. Not yet confirmed against the live API with real keys — run
  `verify_us.py --preview` and check "preview NO ACCEPTED" before trusting NO trades.
- **Sizing:** 5% of bankroll per position, ≥5 whole shares; 95% max deployed; 50% per
  category; 25%/day and 50%/week deployment caps.
- **Correlation:** max 1 position per event group (same underlying + date, e.g. `btc|2026-10-10`, `miahigh|2026-10-11`).
- **Loss pause:** realized loss >5% of bankroll in 24h (or >10% in 7d) → no new entries.
- **Live:** bankroll = min(configured, real account equity); cycle skipped if equity
  unknown or < $5; exposure/groups from exchange positions + resting orders.
- **Capital follows performance:** bankroll split across active strategies ∝ weight
  (weights 0.3–2.5; ±5% per 5-day evaluation; losing strategies always shrink).
- **Capital ladder** (`scoreboard.py`): $100 → $250 → $500 → $1k → $2.5k → $5k.
  UP: ≥50 settled trades this stage, ROI ≥ +1%, taker-bound ROI > 0, ≥80% live fills,
  drawdown < 15%. DOWN: drawdown > 20% or ROI < −3% over ≥30 trades. It never moves money.
- **Exits:** auto-exit at ≥98.5¢ — live mode only.

### weather_us model
- Markets: daily high at KMIA, KNYC (Central Park), KMDW, KSFO, KLAX; 6 two-degree
  brackets per city-day; settle on the NWS Daily Climate Report (local *standard* time day).
- Default path (`WX_ENSEMBLE=1`): Open-Meteo ensemble members — GEFS (`gfs_seamless`),
  ECMWF ENS (`ecmwf_ifs025`), ICON (`icon_seamless`, often down), GEM (`gem_global`).
  Equal weight per model that returns. Daily high is the member's max over the
  local-standard-time climate day. `WX_ENSEMBLE=0` restores the blend below and
  the old edge test (no log-odds blend).
- EMOS: additive bias + one spread, fit by CRPS on that station's day-ahead
  forecast errors (previous-runs blend vs CLI), last `WX_CAL_DAYS` (75). Needs
  `WX_EMOS_MIN_DAYS` (40) or the raw ensemble is used. The free ensemble API does
  not keep past members (those hours come back null), so the spread is not a
  regression on historical ensemble width; today's ensemble spread is a floor
  under the fitted spread. A failed download falls back to the residual kernel
  and does not abort the cycle.
- Residual kernel (flag off, or fallback): blend of GFS, ECMWF, ICON, NBM (×2).
  (Open-Meteo "best_match" at US points is GFS-only and was 3–6°F off consensus.)
  Day-ahead bias/sd from the last fit: MIA +2.6/1.7, NYC −0.8/1.8, MDW +1.2/2.0,
  SFO −1.2/3.1, LAX −1.1/2.7 °F.
- Same day: observed max so far at the settlement station (api.weather.gov,
  User-Agent set). Brackets entirely below that reading, minus `WX_OBS_FLOOR_MARGIN`
  (1°F, because hourly obs understate the CLI integer), are zeroed and the rest
  renormalized. The residual path still uses its older kernel floor.
- Trade: after a log-odds blend with the market mid (`WX_MODEL_WEIGHT` 0.40 model),
  side prob ≥80%, edge ≥4¢ after worst-case fee, **humility cap** skip if edge >25¢.
  The proposal log scores the unblended model probability and records `prob_method`
  (`emos`, `ensemble_raw`, or `residual_kernel`).
  Skip a city/day unless brackets tile the full range. `gimme_bets` won't trade a temperature
  bracket without a fresh model view that agrees.
- Settlement check: rules text is compared to the station map when the exchange
  sends it. Bracket edges are whole °F, inclusive ("62 to 63" contains 62 and 63).
  A city is dropped from proposals when the backtest file reports any mismatch
  (`WX_SKIP_MISMATCH_CITIES=1`). No file yet means nothing is skipped.
- Every cycle appends proposals (approved or not) to `agent/proposals.jsonl`.
  After settlement, scoreboard shows model Brier vs market Brier, mean CLV, and
  a 10-bucket calibration. That block does not change the ladder.
- External APIs (free, no key): open-meteo ensemble + forecast + previous-runs,
  mesonet.agron.iastate.edu (CLI), api.weather.gov observations.

---

## 7. Audit 2026-10-10 (all fixed in `83ff766`, `2ffabd6`; re-verified)

CRITICAL: NO-side wire price (above) · exits could fire in paper mode.
HIGH: legacy polymarket.com proposals fuzzy-matched onto US markets (removed) · fixed
bankroll / no loss stop (equity cap + loss pause) · NO fills scored with YES price ·
weather same-day overconfidence · missing brackets renormalized into fake edge.
MEDIUM: live exposure from paper log · unconfirmed orders counted (now synchronous +
reject handling) · stale sweep by side (now intent) · stale signal files · scoreboard could
step up on paper · auto `git pull` with live keys · overlapping cycles.

**Still open (low):** ledger ignores early exits (assumes hold to settlement); daily loss
window keyed on trade-open time; BBO `longQuote/shortQuote` semantics unconfirmed
(used as YES/NO ask); live NO convention unconfirmed until a real preview.

---

## 8. Money

| Where | Amount | Notes |
|---|---|---|
| Old bot wallet `0x1E520EF05E126d642c4B07e0A7b3a0fCFb2E6356` (Polygon) | 88.39 pUSD + 2.67 POL | `pUSD.unwrap()` is restricted to Polymarket; exit = Uniswap V3 pUSD/USDC 0.01% pool (quote ~99.99%). `cash_out_pusd.py --execute --send-to 0x<exchange USDC address on POLYGON>` — Ryan runs it. |
| Older wallet `0x0B7600714bA48449c42022c76C0a68189408D43D` | 8.4 POL + ~$9.68 resolved position | Needs the old private key (not on server). |
| polymarket.us account (ryanconwell99@gmail.com) | unfunded | USD only: ACH/debit (3–4 business days), wire (~1 day). |

---

## 9. Owner action items (in order)

1. **Harden the server** (paste in DigitalOcean console; Mac key login keeps working):
   ```
   sed -i 's/^PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/50-cloud-init.conf
   sed -i 's/^PermitRootLogin yes/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
   sshd -t && systemctl reload ssh
   ufw allow OpenSSH && ufw --force enable
   apt-get update && apt-get -y upgrade && reboot
   ```
   Dashboard afterwards: `ssh -i ~/.ssh/id_polymarket -L 8080:localhost:8080 root@139.59.64.154` → http://localhost:8080. Enable DO backups.
2. **API key** from polymarket.us/developer, added **without pasting into any chat**:
   DigitalOcean console `echo 'POLYMARKET_US_KEY_ID=…' >> /opt/morning-brief/.env` (same for
   `POLYMARKET_US_SECRET_KEY`), or on the Mac `bash ~/add_pm_keys.sh` (hidden prompt).
   Then `.venv/bin/python agent/verify_us.py --preview` → both YES and NO ACCEPTED.
   Keys do **not** turn the bot live. It stays PAPER (paper trades still logged and
   scored; no orders, no cancels, no balance gate skipping the cycle) until you add
   `POLYMARKET_LIVE=1`. `POLYMARKET_PAPER=1` forces paper even if `POLYMARKET_LIVE=1`.
   Go live only after preview passes and the account is funded: set `POLYMARKET_LIVE=1`
   and leave `POLYMARKET_PAPER` unset. The next tick trades live if equity ≥ $5.
3. **Fund** polymarket.us; set `POLYMARKET_BANKROLL` = deposit.
4. Optional: recover the $88 pUSD (§8).

---

## 10. Rules for the next assistant

- **Never ask for, read, print, or paste secrets** (.env values, private keys, API secrets).
  Check presence with `grep -c '^NAME='` only. Keys go in by Ryan's hand.
- **Never move money or place live orders yourself.** Scripts that move funds default to
  dry-run; Ryan runs `--execute`.
- **Paper first.** Any strategy change runs in paper until the ledger shows real settled results.
- **Don't trust paper P&L at face value:** paper maker fills are assumed; use the
  taker-bound column.
- **Deploy explicitly** (`git pull --ff-only` on the server) and check `vps.log` after one cycle.
- **Don't remove the humility cap, the correlation cap, or the loss pause** to "get more trades".
- Don't attempt to access polymarket.com trading from the US (geo-blocked; off-limits).
- Commit messages describe *why*; keep `docs/HANDOFF.md` and the runbook current.
- The repo's `CLAUDE.md` is from the original "morning brief" build (GitNexus etc.); the
  agent/ subsystem conventions above take precedence for trading code.

---

## 11. Backlog (ranked)

| # | Item | Impact | Effort |
|---|---|---|---|
| 1 | Watch weather_us paper results once the loss pause lifts; aim for ~50 settled trades | High | wait ~10–14 days |
| 2 | Sports favorites buy-and-hold as maker (`US_GIMME_SKIP_CATEGORIES=`), paper first | Medium | config |
| 3 | Log exits so the ledger scores early sells correctly | Medium | small |
| 4 | Daily loss window keyed on settlement time instead of open time | Low | small |
| 5 | Weather v2: NWS point forecast as 5th input; per-season calibration | Medium | half day |
| 6 | BTC brackets priced vs live spot + volatility (only then un-skip `cpc-`) | Medium | 1 day |
| 7 | `ANTHROPIC_API_KEY` for probability_arb (currently dropped on US venue anyway) | Low | — |

Research upgrades landed in paper mode (do not treat as a green light to add capital):
proposal log + Brier/log-loss/CLV on the scoreboard, the weather settlement
backtest with a per-city skip, and ensemble/EMOS probabilities with a same-day
observed-high floor and a log-odds blend (`WX_ENSEMBLE=0` turns that path off).
Still not done: fractional Kelly. The humility cap, one-position correlation
cap, loss pause, and maker-only path are unchanged.

---

## 12. Reference docs
- Polymarket US: fees https://docs.polymarket.us/fees · orders/price convention https://docs.polymarket.us/api-reference/orders/overview · developer keys https://polymarket.us/developer
- Kalshi makers/takers study: https://www.karlwhelan.com/Papers/Kalshi.pdf
- Project docs (claude.ai project "Polymarkt trading app"): `polymarket-bot-runbook.md`, `polymarket-strategy-research.md`
