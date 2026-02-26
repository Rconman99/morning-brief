#!/bin/bash
# Morning Brief automation script
# Runs the full trading intelligence pipeline and logs output

PROJECT_DIR="$HOME/trading-intelligence"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
LOG_FILE="$PROJECT_DIR/data/outputs/cron.log"

# Only run on weekdays (1=Mon .. 5=Fri)
DAY_OF_WEEK=$(date +%u)
if [ "$DAY_OF_WEEK" -gt 5 ]; then
    echo "$(date): Weekend — skipping." >> "$LOG_FILE"
    exit 0
fi

echo "========================================" >> "$LOG_FILE"
echo "$(date): Starting morning brief run" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

cd "$PROJECT_DIR" || exit 1
"$VENV_PYTHON" scripts/run_all.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

BRIEF_HTML="$PROJECT_DIR/data/outputs/morning_brief_$(date +%Y-%m-%d).html"
BRIEF_MD="$PROJECT_DIR/data/outputs/morning_brief_$(date +%Y-%m-%d).md"

if [ $EXIT_CODE -eq 0 ] && [ -f "$BRIEF_MD" ]; then
    # Count verdicts from the markdown brief
    BUYS=$(grep -c '🟢' "$BRIEF_MD" 2>/dev/null || echo 0)
    SELLS=$(grep -c '🔴' "$BRIEF_MD" 2>/dev/null || echo 0)
    AVOIDS=$(grep -c '🟡' "$BRIEF_MD" 2>/dev/null || echo 0)
    PNL=$(grep 'Total Portfolio P&L' "$BRIEF_MD" | sed 's/.*: //' | sed 's/\*//g')

    osascript -e "display notification \"P&L: ${PNL:-N/A} | ${BUYS} BUY, ${SELLS} SELL, ${AVOIDS} AVOID\" with title \"Morning Brief Ready\" subtitle \"$(date +%A\ %b\ %d)\" sound name \"Glass\""

    # Open HTML dashboard if available, fall back to markdown
    if [ -f "$BRIEF_HTML" ]; then
        open "$BRIEF_HTML"
    else
        open "$BRIEF_MD"
    fi
    echo "$(date): Run complete — notification sent, brief opened" >> "$LOG_FILE"
else
    osascript -e 'display notification "Pipeline failed — check cron.log" with title "Morning Brief Error" sound name "Basso"'
    echo "$(date): Run FAILED (exit code $EXIT_CODE)" >> "$LOG_FILE"
fi

echo "" >> "$LOG_FILE"
