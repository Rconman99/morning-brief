"""Move old proposal-log lines into a gzip file. Never run from the timer.

``agent/proposals.jsonl`` is the scoring record. Logrotate must not touch it.
This script is the manual archive: lines whose US/Eastern day is older than
N days are copied into ``agent/archive/`` and then removed from the live file.

Default is a dry run. Nothing is rewritten unless ``--apply`` is passed.
A crash after the archive is in place and before the live file is replaced
leaves the original log intact (the archive may then contain a copy).

    .venv/bin/python deploy/archive_proposals.py --days 90
    .venv/bin/python deploy/archive_proposals.py --days 90 --apply
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.proposal_log import PROPOSAL_LOG, eastern_day

_EASTERN = ZoneInfo("America/New_York")


def _day(row: dict) -> str | None:
    raw = row.get("timestamp")
    if not raw:
        return None
    try:
        return eastern_day(str(raw))
    except (TypeError, ValueError):
        text = str(raw)
        return text[:10] if len(text) >= 10 and text[4:5] == "-" else None


def _cutoff(days: int, today: str | None = None) -> str:
    if today:
        anchor = datetime.fromisoformat(today).date()
    else:
        anchor = datetime.now(_EASTERN).date()
    return (anchor - timedelta(days=days)).isoformat()


def archive_proposals(path: Path, days: int, *, apply: bool = False,
                      today: str | None = None, archive_dir: Path | None = None) -> dict:
    """Count (and, with ``apply``, move) rows strictly older than ``days``.

    Rows with no parseable day stay in the live file. The returned dict is
    the dry-run summary even when ``apply`` is set.
    """
    path = Path(path)
    cutoff = _cutoff(days, today)
    moved = kept = unparsed = 0
    if not path.exists():
        return {"cutoff": cutoff, "moved": 0, "kept": 0, "unparsed": 0, "archive": None, "applied": False}

    archive = None
    if apply:
        folder = Path(archive_dir) if archive_dir else path.parent / "archive"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(_EASTERN).strftime("%Y%m%dT%H%M%S")
        archive = folder / f"proposals-before-{cutoff}-{stamp}.jsonl.gz"
        kept_tmp = path.with_name(path.name + ".kept.tmp")
        with path.open("r", encoding="utf-8") as src, kept_tmp.open("w", encoding="utf-8") as kept_f, gzip.open(archive, "wt", encoding="utf-8") as old_f:
            for line in src:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    kept_f.write(raw + "\n")
                    unparsed += 1
                    continue
                if not isinstance(row, dict):
                    kept_f.write(raw + "\n")
                    unparsed += 1
                    continue
                day = _day(row)
                if day is not None and day < cutoff:
                    old_f.write(raw + "\n")
                    moved += 1
                else:
                    kept_f.write(raw + "\n")
                    kept += 1
        if moved == 0:
            kept_tmp.unlink(missing_ok=True)
            archive.unlink(missing_ok=True)
            archive = None
        else:
            os.replace(kept_tmp, path)
    else:
        with path.open("r", encoding="utf-8") as src:
            for line in src:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    unparsed += 1
                    continue
                if not isinstance(row, dict):
                    unparsed += 1
                    continue
                day = _day(row)
                if day is not None and day < cutoff:
                    moved += 1
                else:
                    kept += 1
    return {
        "cutoff": cutoff,
        "moved": moved,
        "kept": kept,
        "unparsed": unparsed,
        "archive": None if archive is None else str(archive),
        "applied": bool(apply and moved),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Archive old proposals.jsonl rows. Dry-run unless --apply.")
    parser.add_argument("--days", type=int, default=90, help="Move rows older than this many US/Eastern days (default 90)")
    parser.add_argument("--proposals", type=Path, default=PROPOSAL_LOG, help="Live JSONL (default agent/proposals.jsonl)")
    parser.add_argument("--apply", action="store_true", help="Rewrite the live file. Without this, only print the counts.")
    args = parser.parse_args(argv)
    if args.days < 1:
        print("days must be >= 1", file=sys.stderr)
        return 2
    summary = archive_proposals(args.proposals, args.days, apply=args.apply)
    verb = "Moved" if summary["applied"] else "Would move"
    print(
        f"{verb} {summary['moved']} line(s) older than {summary['cutoff']} "
        f"({summary['kept']} kept, {summary['unparsed']} unparsed)."
    )
    if summary.get("archive"):
        print(f"Archive: {summary['archive']}")
    if not args.apply:
        print("Dry run. Pass --apply to rewrite the live file. This script is not on the timer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
