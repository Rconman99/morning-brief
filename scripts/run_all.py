"""Orchestrator: runs all modules in sequence and reports results."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import subprocess
import time


def get_venv_python() -> str:
    """Get the venv Python path."""
    venv_python = str(PROJECT_ROOT / ".venv" / "bin" / "python")
    if not Path(venv_python).exists():
        venv_python = str(PROJECT_ROOT / ".venv" / "Scripts" / "python")
    if not Path(venv_python).exists():
        venv_python = sys.executable
    return venv_python


MODULES = [
    ("journal", "modules/journal.py"),
    ("earnings", "modules/earnings.py"),
    ("valuation", "modules/valuation.py"),
    ("portfolio", "modules/portfolio.py"),
    ("technical_signals", "modules/technical_signals.py"),
    ("news_sentiment", "modules/news_sentiment.py"),
    ("options", "modules/options.py"),
    ("scorecard", "modules/scorecard.py"),
    ("morning_brief", "modules/morning_brief.py"),
    ("brief_html", "modules/brief_html.py"),
]


def main():
    venv_python = get_venv_python()
    print(f"Using Python: {venv_python}")
    print("=" * 60)

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
