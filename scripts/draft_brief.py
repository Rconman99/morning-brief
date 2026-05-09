#!/usr/bin/env python3
"""Draft tomorrow's curated brief locally — no Claude tokens spent.

Reads the auto-generated brief at data/outputs/morning_brief_YYYY-MM-DD.md plus
key processed JSON files, hands them to mistral-nemo:12b (128K context) with a
template, and writes briefs/YYYY-MM-DD.draft.md. The Claude scheduled trigger
then opens the draft, makes judgment edits, and saves the final.

Why local: the curated brief is mostly template-fill + data summarization. The
auto-brief is already comprehensive — the curated version is a transformation,
not a fresh analysis. mistral-nemo:12b handles transformation tasks well at $0.

Usage:
    python3 scripts/draft_brief.py            # uses today's date
    python3 scripts/draft_brief.py 2026-05-06 # explicit date

Exit codes:
    0 = draft written
    1 = error (missing data, LLM down)
    2 = auto-brief not yet generated for today (run run_all.py first)
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUTPUTS = ROOT / "data" / "outputs"
PROCESSED = ROOT / "data" / "processed"
BRIEFS = ROOT / "briefs"

# Borrow the local-llm client from ~/projects/local-llm-toolkit. Falls back to
# the in-project copy at agent/llm.py if the toolkit isn't installed.
TOOLKIT = pathlib.Path.home() / "projects" / "local-llm-toolkit" / "scripts"
TOOLKIT_LLM = TOOLKIT / "ollama-client.py"
LOCAL_LLM = ROOT / "agent" / "llm.py"

# Drift detection + telemetry from local-llm-toolkit. Soft-import so the script
# still runs if the toolkit isn't there.
if TOOLKIT.exists() and str(TOOLKIT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT))
try:
    from llm_golden import compare_to_golden, add_to_golden  # type: ignore
    from llm_telemetry import log_run, estimate_savings  # type: ignore
except ImportError:
    def compare_to_golden(*a, **k): return {"drift_flag": "no-baseline"}
    def add_to_golden(*a, **k): return None
    def log_run(*a, **k): pass
    def estimate_savings(**k): return 0.0


def _load_llm():
    target = TOOLKIT_LLM if TOOLKIT_LLM.exists() else LOCAL_LLM
    if not target.exists():
        return None
    spec = importlib.util.spec_from_file_location("llm_client", target)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_json(name: str) -> dict | None:
    """Load a processed JSON file. Returns None on missing/error so the prompt
    can note absence cleanly rather than failing the whole brief.
    """
    path = PROCESSED / name
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _summarize_json(name: str) -> str:
    """Compact a JSON file into ≤2KB of text for the prompt context. Drops
    metadata wrappers and trims long arrays."""
    raw = _load_json(name)
    if raw is None:
        return f"({name}: missing)"
    payload = raw.get("data", raw) if isinstance(raw, dict) else raw
    text = json.dumps(payload, indent=2, default=str)
    # Truncate aggressively — local LLM context limits matter
    if len(text) > 2500:
        text = text[:2500] + "\n... (truncated)"
    return f"### {name}\n```json\n{text}\n```"


PROMPT_TEMPLATE = """\
You are drafting Ryan's morning trading brief for {date} ({weekday}).

Below is today's auto-generated comprehensive brief plus JSON data from the pipeline.
Transform this into a CURATED brief following the template structure exactly. Keep it
concise and actionable — Ryan reads this every morning before trading.

## Template structure (keep all sections, even if brief)
1. # Morning Brief — {weekday}, {long_date}
2. > One-line top status (pipeline pass-rate, key data freshness, FOMC distance, biggest risk)
3. ## Market Context — week-ahead table + risk regime + day-specific rules (Tuesday blackout, TSLA rule)
4. ## {weekday} {open_or_debrief} — what to do TODAY based on day of week
5. ## Portfolio — current positions with stops
6. ## Watchlist Verdicts — table of tickers + verdict
7. ## Top Trade Setups — numbered list (max 5), each with entry/stop/target
8. ## Polymarket — portfolio breakdown if data present
9. ## Crypto Intelligence — F&G, BTC funding, momentum, ATH distance
10. ## Contradiction Alerts — positions that disagree with signals
11. ## Social Intelligence — top Reddit/X/YouTube takes if present
12. ## Congressional Signals — recent congressional trades
13. ## Active Journal Rules — TSLA, Tuesday blackout, etc.
14. ## Risk Notes — anything flagged as DANGER/HIGH severity
15. ## Module Status — pass/fail summary

## Day-of-week behaviors (CRITICAL):
- Monday: weekend debrief, fresh-week positioning
- Tuesday: BLACKOUT day — no entries (0% historical win rate)
- Wednesday: ENTRY WINDOW — primary trade execution day
- Thursday: continuation, watch jobless claims
- Friday: end-of-week positioning, exit-into-weekend bias

## Auto-generated comprehensive brief (the source of truth — pull facts FROM this)
{auto_brief}

## Polymarket portfolio data
{polymarket}

## Social intelligence data
{social}

## Risk dashboard data
{risk}

## Economic calendar data
{calendar}

Now write the curated brief. Use markdown tables where the auto-brief has tabular data.
Be specific — quote real prices, real percentages, real dates. Do not invent.
If a section has no data, write "No data available — check at broker" rather than skipping.
Keep total length 200-300 lines. Do not add commentary outside the brief itself.
"""


def main() -> int:
    started_at = time.time()
    target_date = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    try:
        d = dt.date.fromisoformat(target_date)
    except ValueError:
        print(f"ERROR: bad date '{target_date}', use YYYY-MM-DD", file=sys.stderr)
        return 1

    weekday = d.strftime("%A")
    long_date = d.strftime("%B %d, %Y")
    open_or_debrief = "Open — Weekend Debrief" if weekday == "Monday" else "Open" if weekday in {"Wednesday", "Thursday", "Friday"} else "Plan"

    auto_path = OUTPUTS / f"morning_brief_{target_date}.md"
    if not auto_path.exists():
        print(f"ERROR: auto-brief not found at {auto_path}. Run scripts/run_all.py first.", file=sys.stderr)
        return 2
    auto_brief = auto_path.read_text()
    # Trim auto-brief to fit local LLM context — keep first 8K and last 4K
    if len(auto_brief) > 12000:
        auto_brief = auto_brief[:8000] + "\n\n... (middle sections trimmed) ...\n\n" + auto_brief[-4000:]

    prompt = PROMPT_TEMPLATE.format(
        date=target_date,
        weekday=weekday,
        long_date=long_date,
        open_or_debrief=open_or_debrief,
        auto_brief=auto_brief,
        polymarket=_summarize_json("polymarket.json"),
        social=_summarize_json("social_intelligence.json"),
        risk=_summarize_json("risk_dashboard.json"),
        calendar=_summarize_json("economic_calendar.json"),
    )

    llm = _load_llm()
    if llm is None:
        print("ERROR: no LLM client available (looked for local-llm-toolkit and agent/llm.py)", file=sys.stderr)
        return 1

    print(f"Drafting brief for {target_date} ({weekday}) via mistral-nemo:12b...", file=sys.stderr)
    # mistral-nemo handles 128K context and is good at template-fill summarization.
    # Token budget is generous because the brief is ~2.5K tokens of output.
    text = llm.generate_text(prompt, max_tokens=4000, model="mistral-nemo:12b")
    if not text:
        # Fall back through router (qwen3:8b) — may produce shorter brief
        if hasattr(llm, "generate_text_routed"):
            text = llm.generate_text_routed(prompt, max_tokens=4000)
    if not text:
        print("ERROR: local LLM returned no output", file=sys.stderr)
        return 1

    # Strip any preface the model may have added before the actual brief starts
    if "# Morning Brief" in text:
        text = text[text.index("# Morning Brief"):]

    BRIEFS.mkdir(parents=True, exist_ok=True)
    out_path = BRIEFS / f"{target_date}.draft.md"
    out_path.write_text(text)
    print(f"✓ Draft written to {out_path} ({len(text)} chars)", file=sys.stderr)

    # Drift check — compare against golden set. Flagged drift goes to stderr but
    # doesn't block the run (Claude polish step can still salvage it).
    drift = compare_to_golden("morning-brief", text)
    flag = drift.get("drift_flag", "no-baseline")
    if flag == "drift":
        print(f"⚠ DRIFT: best-match similarity {drift.get('best_similarity'):.2f} vs goldens. "
              f"Brief may have collapsed structurally — review before publishing.", file=sys.stderr)
    elif flag == "collapse":
        print(f"⚠ COLLAPSE: draft is {drift.get('length_ratio_vs_median'):.0%} of median golden length. "
              f"Local LLM probably failed mid-output.", file=sys.stderr)
    elif flag == "no-baseline":
        print("ℹ No goldens yet — use llm_golden.py to seed from prior briefs/*.md", file=sys.stderr)
    else:
        print(f"✓ Drift OK (similarity {drift.get('best_similarity'):.2f} vs {drift.get('best_match')})",
              file=sys.stderr)

    runtime = time.time() - started_at
    log_run(
        "morning-brief-draft",
        status="ok" if flag in ("ok", "no-baseline") else "drift",
        # Saving here is the difference between Claude reading the auto-brief +
        # JSON files (~30K input) and writing 250 lines (~6K output) vs. just
        # editing our draft (~2K + 1K).
        tokens_saved=33_000,
        est_usd_saved=estimate_savings(input_tokens=28_000, output_tokens=5_000, model="sonnet"),
        runtime_sec=runtime,
        extra={"draft_chars": len(text), "drift": drift},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
