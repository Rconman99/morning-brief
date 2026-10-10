#!/bin/bash
# Polymarket agent — 15-minute cycle on the Polymarket US venue.
# systemd: polymarket-agent.service ExecStart=/bin/bash /opt/morning-brief/agent/vps_run_us.sh
#
# 1. scan the US exchange for candidates  -> data/processed/polymarket_us.json
# 2. run the agent (strategies -> risk gate -> executor)
# Bankroll comes from the exchange balance when live (see --bankroll below).

PROJECT_DIR="${PROJECT_DIR:-/opt/morning-brief}"
cd "$PROJECT_DIR" || exit 1

if [ -f "$PROJECT_DIR/.env" ]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
fi
export POLYMARKET_VENUE="${POLYMARKET_VENUE:-us}"

BANKROLL="${POLYMARKET_BANKROLL:-95}"

.venv/bin/python agent/us_scanner.py >> agent/agent.log 2>&1
.venv/bin/python agent/run.py --bankroll "$BANKROLL" >> agent/agent.log 2>&1
