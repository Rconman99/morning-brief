"""Orchestrator: runs all modules in sequence and reports results."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import subprocess
import time


def get_venv_python() -> str:
    """Get the venv Python path.

    Search order:
    1. .venv/bin/python (Linux/Mac standard)
    2. .venv/Scripts/python (Windows)
    3. ../trading-venv/bin/python (VM fallback — session-level venv)
    4. sys.executable (last resort)

    Also verifies the found Python is actually executable (not a broken symlink).
    """
    candidates = [
        PROJECT_ROOT / ".venv" / "bin" / "python",
        PROJECT_ROOT / ".venv" / "Scripts" / "python",
        PROJECT_ROOT.parent / "trading-venv" / "bin" / "python",
    ]
    for candidate in candidates:
        candidate_str = str(candidate)
        if Path(candidate_str).exists():
            # Verify it's not a broken symlink
            try:
                result = subprocess.run(
                    [candidate_str, "--version"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    return candidate_str
            except Exception:
                continue
    return sys.executable


MODULES = [
    ("journal", "modules/journal.py"),
    ("earnings", "modules/earnings.py"),
    ("valuation", "modules/valuation.py"),
    ("portfolio", "modules/portfolio.py"),
    ("technical_signals", "modules/technical_signals.py"),
    ("news_sentiment", "modules/news_sentiment.py"),
    ("options", "modules/options.py"),
    ("insider_tracker", "modules/insider_tracker.py"),
    ("congress_tracker", "modules/congress_tracker.py"),
    ("polymarket_scanner", "modules/polymarket_scanner.py"),
    ("economic_calendar", "modules/economic_calendar.py"),
    ("macro_dashboard", "modules/macro_dashboard.py"),
    ("sector_rotation", "modules/sector_rotation.py"),
    ("opportunity_scanner", "modules/opportunity_scanner.py"),
    ("unusual_options", "modules/unusual_options.py"),
    ("social_sentiment", "modules/social_sentiment.py"),
    ("dividend_tracker", "modules/dividend_tracker.py"),
    ("risk_dashboard", "modules/risk_dashboard.py"),
    ("trade_memory", "modules/trade_memory.py"),
    ("position_sizer", "modules/position_sizer.py"),
    ("correlation_heatmap", "modules/correlation_heatmap.py"),
    ("backtester", "modules/backtester.py"),
    ("alert_system", "modules/alert_system.py"),
    ("scorecard", "modules/scorecard.py"),
    ("morning_brief", "modules/morning_brief.py"),
    ("weekly_digest", "modules/weekly_digest.py"),
    ("brief_html", "modules/brief_html.py"),
]


SYNC_STEP = ("sync_portfolio", "scripts/sync_portfolio.py")


def main():
    venv_python = get_venv_python()
    print(f"Using Python: {venv_python}")
    print("=" * 60)

    # Step 0: Portfolio Sync (non-blocking)
    print("\n=== Step 0: Portfolio Sync ===")
    try:
        result = subprocess.run(
            [venv_python, str(PROJECT_ROOT / SYNC_STEP[1])],
            capture_output=True, text=True, timeout=30, cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0:
            if result.stdout.strip():
                print(result.stdout.strip())
            print("Portfolio synced successfully")
        else:
            print(f"Sync skipped: {result.stderr.strip()[:100]}")
    except Exception as e:
        print(f"Sync skipped: {e}")

    results = []
    total_start = time.time()

    for name, module_path in MODULES:
        print(f"\n=== Running: {name} ===")
        start = time.time()

        try:
            result = subprocess.run(
                [venv_python, str(PROJECT_ROOT / module_path)],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(PROJECT_ROOT),
            )
            elapsed = round(time.time() - start, 1)
            status = "PASS" if result.returncode == 0 else "FAIL"

            if result.stdout:
                print(result.stdout.strip())
            if result.stderr and result.returncode != 0:
                print(f"STDERR: {result.stderr.strip()[:200]}")

            results.append((name, status, elapsed))
            print(f"--- {name}: {status} ({elapsed}s) ---")

        except subprocess.TimeoutExpired:
            elapsed = round(time.time() - start, 1)
            results.append((name, "TIMEOUT", elapsed))
            print(f"--- {name}: TIMEOUT ({elapsed}s) ---")
        except Exception as e:
            elapsed = round(time.time() - start, 1)
            results.append((name, "ERROR", elapsed))
            print(f"--- {name}: ERROR ({elapsed}s) - {e} ---")

    total_elapsed = round(time.time() - total_start, 1)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"{'Module':<20} {'Status':<10} {'Time':>8}")
    print("-" * 40)
    for name, status, elapsed in results:
        icon = "✅" if status == "PASS" else "❌"
        print(f"{icon} {name:<18} {status:<10} {elapsed:>6.1f}s")

    passed = sum(1 for _, s, _ in results if s == "PASS")
    total = len(results)
    print("-" * 40)
    print(f"Total: {passed}/{total} passed in {total_elapsed}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
