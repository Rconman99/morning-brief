#!/bin/bash
# Daily Morning Brief — runs analysis and opens HTML report
set -e

PROJECT_DIR="$HOME/projects/morning-brief"
cd "$PROJECT_DIR"

# Run the analysis
.venv/bin/python scripts/run_all.py

# Open today's HTML brief
TODAY=$(date +%Y-%m-%d)
HTML_FILE="$PROJECT_DIR/data/outputs/morning_brief_${TODAY}.html"

if [ -f "$HTML_FILE" ]; then
    open "$HTML_FILE"
else
    # Fallback: open the most recent brief
    LATEST=$(ls -t "$PROJECT_DIR/data/outputs"/morning_brief_*.html 2>/dev/null | head -1)
    [ -n "$LATEST" ] && open "$LATEST"
fi
