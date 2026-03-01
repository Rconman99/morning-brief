"""Pre-flight check: validates environment, dependencies, and config."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json


def check(label: str, condition: bool, detail: str = "") -> bool:
    icon = "✅" if condition else "❌"
    msg = f"{icon} {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return condition


def main():
    print("=" * 50)
    print("SETUP CHECK")
    print("=" * 50)
    all_ok = True

    # Python version
    v = sys.version_info
    all_ok &= check("Python >= 3.11", v.major == 3 and v.minor >= 11, f"{v.major}.{v.minor}.{v.micro}")

    # Packages
    packages = ["dotenv", "numpy", "pandas", "pandas_ta", "yfinance", "requests", "pytest"]
    for pkg in packages:
        try:
            __import__(pkg)
            all_ok &= check(f"Package: {pkg}", True)
        except ImportError:
            all_ok &= check(f"Package: {pkg}", False, "NOT INSTALLED")

    # scipy (optional)
    try:
        __import__("scipy")
        check("Package: scipy (optional)", True)
    except ImportError:
        check("Package: scipy (optional)", False, "Black-Scholes unavailable")

    # .env
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        content = env_path.read_text()
        is_demo = "demo" in content
        all_ok &= check(".env exists", True, "⚠️ Using demo key" if is_demo else "API key configured")
    else:
        all_ok &= check(".env exists", False, "Create .env with ALPHA_VANTAGE_API_KEY")

    # Config files
    for cfg in ["watchlist.json", "portfolio.json", "settings.json"]:
        path = PROJECT_ROOT / "config" / cfg
        if path.exists():
            try:
                json.loads(path.read_text())
                all_ok &= check(f"Config: {cfg}", True, "Valid JSON")
            except json.JSONDecodeError:
                all_ok &= check(f"Config: {cfg}", False, "Invalid JSON!")
        else:
            all_ok &= check(f"Config: {cfg}", False, "File not found")

    # Directories
    dirs = [
        "data/raw", "data/raw/earnings", "data/processed", "data/outputs",
        "data/sample", "modules", "lib", "tests", "scripts", "config",
    ]
    for d in dirs:
        dp = PROJECT_ROOT / d
        if not dp.exists():
            dp.mkdir(parents=True, exist_ok=True)
            check(f"Dir: {d}", True, "Created")
        else:
            check(f"Dir: {d}", True)

    # Git
    git_dir = PROJECT_ROOT / ".git"
    all_ok &= check("Git initialized", git_dir.exists())

    print("=" * 50)
    print("ALL CHECKS PASSED ✅" if all_ok else "SOME CHECKS FAILED ❌")
    print("=" * 50)


if __name__ == "__main__":
    main()
