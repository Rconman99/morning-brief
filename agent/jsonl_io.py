"""Read JSONL one line at a time.

The proposal log and the shadow log are large enough that
``Path.read_text()`` plus a list of full objects exhausts a 1 GB droplet.
Callers keep only the fields they score.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path


def iter_jsonl(path: Path, fields: tuple[str, ...] | None = None) -> Iterator[dict]:
    """Yield one JSON object per line.

    Blank lines and broken JSON are skipped, matching the old
    ``read_text().splitlines()`` loaders. Non-objects are skipped so one bad
    line cannot build a giant string or a list of unused values. When
    ``fields`` is set, only those keys are kept, and only if the line
    actually has them. Missing keys stay missing, so ``.get(key, default)``
    behaves as it did on the full object.
    """
    path = Path(path)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            if fields is not None:
                row = {key: row[key] for key in fields if key in row}
            yield row
