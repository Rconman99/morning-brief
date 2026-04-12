#!/bin/bash
# Equity Trading Agent — EOD Runner
# Runs at 3:50 PM ET weekdays via launchd
# Refreshes signals, then runs the equity agent

set -euo pipefail

PROJECT_DIR="$HOME/projects/morning-brief"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
LOG_FILE="$PROJECT_DIR/agent/equity_agent.log"

cd "$PROJECT_DIR"

# Source .env for ALPACA keys
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

echo "=== Equity Agent EOD Run: $(date) ===" >> "$LOG_FILE"

# 1. Refresh signal modules (continue even if some fail)
echo "[$(date +%H:%M:%S)] Refreshing technical signals..." >> "$LOG_FILE"
$VENV_PYTHON modules/technical_signals.py >> "$LOG_FILE" 2>&1 || echo "  technical_signals failed" >> "$LOG_FILE"

echo "[$(date +%H:%M:%S)] Refreshing sector rotation..." >> "$LOG_FILE"
$VENV_PYTHON modules/sector_rotation.py >> "$LOG_FILE" 2>&1 || echo "  sector_rotation failed" >> "$LOG_FILE"

echo "[$(date +%H:%M:%S)] Refreshing congress tracker..." >> "$LOG_FILE"
$VENV_PYTHON modules/insider_tracker.py >> "$LOG_FILE" 2>&1 || echo "  insider_tracker skipped" >> "$LOG_FILE"

# 2. Run the equity agent
echo "[$(date +%H:%M:%S)] Running equity agent..." >> "$LOG_FILE"
$VENV_PYTHON agent/equity_run.py --bankroll 15000 >> "$LOG_FILE" 2>&1

echo "[$(date +%H:%M:%S)] Done." >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
