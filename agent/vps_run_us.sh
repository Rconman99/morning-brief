#!/bin/bash
# Polymarket agent — 15-minute cycle on the Polymarket US venue.
# systemd: polymarket-agent.service ExecStart=/bin/bash /opt/morning-brief/agent/vps_run_us.sh
#
# 1. pull latest code (same self-update behaviour as the old vps_run.sh)
# 2. scan the US exchange for candidates  -> data/processed/polymarket_us.json
# 3. run the agent (strategies -> risk gate -> executor); paper mode until
#    POLYMARKET_US_KEY_ID / POLYMARKET_US_SECRET_KEY exist in .env
# 4. tracker summary

PROJECT_DIR="${PROJECT_DIR:-/opt/morning-brief}"
cd "$PROJECT_DIR" || exit 1

if [ -f "$PROJECT_DIR/.env" ]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
fi
export POLYMARKET_VENUE="${POLYMARKET_VENUE:-us}"
BANKROLL="${POLYMARKET_BANKROLL:-95}"
LOG="$PROJECT_DIR/agent/vps.log"

git pull origin main --quiet 2>/dev/null

.venv/bin/python3 agent/us_scanner.py >> "$LOG" 2>&1
.venv/bin/python3 agent/weather_us.py >> "$LOG" 2>&1
.venv/bin/python3 agent/run.py --bankroll "$BANKROLL" >> "$LOG" 2>&1
.venv/bin/python3 agent/tracker.py >> "$LOG" 2>&1
.venv/bin/python3 agent/scoreboard.py --telegram >> "$LOG" 2>&1
echo "--- $(date) --- venue=$POLYMARKET_VENUE" >> "$LOG"
