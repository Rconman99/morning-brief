"""Standardized data envelope for all module outputs."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


def create_envelope(module_name: str, data: dict, status: str = "success", error: str = None) -> dict:
    """Create a standardized data envelope."""
    return {
        "module": module_name,
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": status,
        "error_message": error,
        "data": data,
    }


def save_envelope(envelope: dict, filename: str) -> Path:
    """Write envelope JSON to data/processed/."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    path = PROCESSED_DIR / filename
    path.write_text(json.dumps(envelope, indent=2, default=str))
    logger.info("Saved envelope to %s", path)
    return path


def load_envelope(filename: str) -> dict:
    """Load envelope from data/processed/. Returns a missing-status envelope if not found."""
    path = PROCESSED_DIR / filename
    if not path.exists():
        return {
            "module": "unknown",
            "generated_at": None,
            "status": "missing",
            "error_message": "File not found",
            "data": {},
        }
    return json.loads(path.read_text())
