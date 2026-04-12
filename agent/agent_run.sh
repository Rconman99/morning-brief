#!/bin/bash
# Polymarket Trading Agent — auto-runner
# Runs every 15 minutes via launchd, scans markets, and executes trades

PROJECT_DIR="$HOME/projects/morning-brief"
cd "$PROJECT_DIR" || exit 1

# Load env files
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
fi
if [ -f "$HOME/.config/last30days/.env" ]; then
    set -a; source "$HOME/.config/last30days/.env"; set +a
fi

# Run the polymarket scanner first to get fresh signals
.venv/bin/python modules/polymarket_scanner.py >> agent/agent.log 2>&1

# Run the agent with $100 bankroll (adjust as needed)
.venv/bin/python agent/run.py --bankroll 100 >> agent/agent.log 2>&1
