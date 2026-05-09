#!/bin/bash
# Pre-brief orchestrator — runs the entire pipeline and the local-LLM
# draft writer BEFORE the Claude trigger fires. By the time Claude opens
# this trigger, briefs/YYYY-MM-DD.draft.md already exists with a complete
# curated brief — Claude only edits and saves the final.
#
# This is the cron-side of the morning-brief offload pattern.

set -u

REPO="$HOME/projects/morning-brief"
LOG="$HOME/.cache/morning-brief-pre.log"
TODAY=$(date +%F)

mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG" >&2; }

cd "$REPO" || { log "✗ Repo not found at $REPO"; exit 1; }

# Ensure venv exists (ranked: project venv → fallback session venv)
PYBIN=""
for cand in "$REPO/.venv/bin/python" "$HOME/projects/trading-venv/bin/python"; do
  if [ -x "$cand" ]; then PYBIN="$cand"; break; fi
done
if [ -z "$PYBIN" ]; then
  log "→ Creating .venv (first run)"
  python3 -m venv "$REPO/.venv" 2>>"$LOG"
  "$REPO/.venv/bin/pip" install -r requirements.txt --quiet 2>>"$LOG"
  PYBIN="$REPO/.venv/bin/python"
fi
log "Using Python: $PYBIN"

# Run the full pipeline
log "→ Running pipeline (run_all.py)"
"$PYBIN" scripts/run_all.py >>"$LOG" 2>&1
PIPELINE_EXIT=$?
log "Pipeline exit: $PIPELINE_EXIT"

# Confirm auto-brief was generated for today
AUTO="$REPO/data/outputs/morning_brief_${TODAY}.md"
if [ ! -f "$AUTO" ]; then
  log "✗ Auto-brief missing at $AUTO — pipeline may have errored. Claude will need to run manually."
  exit 1
fi
log "✓ Auto-brief present: $AUTO"

# Draft the curated brief locally (mistral-nemo:12b → free)
log "→ Drafting curated brief locally"
"$PYBIN" scripts/draft_brief.py "$TODAY" >>"$LOG" 2>&1
DRAFT_EXIT=$?
DRAFT="$REPO/briefs/${TODAY}.draft.md"

if [ "$DRAFT_EXIT" -eq 0 ] && [ -f "$DRAFT" ]; then
  log "✓ Draft ready: $DRAFT ($(wc -l < "$DRAFT") lines)"
  exit 0
else
  log "⚠ Draft writer failed (exit=$DRAFT_EXIT). Claude trigger will fall back to writing from scratch."
  # Don't fail the cron — Claude can still run, just without the local draft.
  exit 0
fi
