#!/bin/bash
# Daily Morning Brief — runs full analysis pipeline and opens HTML report
set -e

PROJECT_DIR="$HOME/projects/morning-brief"
cd "$PROJECT_DIR"

# Load last30days env (ScrapeCreators key, etc.) so social_intelligence module works
if [ -f "$HOME/.config/last30days/.env" ]; then
    set -a
    source "$HOME/.config/last30days/.env"
    set +a
fi

# Load project .env if it exists
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

# Run the full analysis pipeline
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
