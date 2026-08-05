#!/usr/bin/env python3
"""Regenerate the bundled catalogue snapshot from a models.dev api.json dump.

Usage (from collie-core/):

    .venv/bin/python tools/update_catalogue_snapshot.py /tmp/modelsdev.json

Writes ``collie_core/catalog/snapshot.json`` (the reviewed, trimmed snapshot
that ships with every release so onboarding works offline). The Collie-owned
curated layer (``collie_core/catalog/curated.py``) is merged in here — a
refresh at runtime applies the same trim, so the two can never drift apart.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from collie_core.catalog.snapshot_util import trim_live_catalogue, validate_catalogue_schema

REPO_CORE = Path(__file__).resolve().parent.parent
OUTPUT = REPO_CORE / "collie_core" / "catalog" / "snapshot.json"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    source = Path(sys.argv[1])
    if not source.is_file():
        print(f"Source not found: {source}", file=sys.stderr)
        return 2
    raw = json.loads(source.read_text(encoding="utf-8"))
    trimmed = trim_live_catalogue(raw, generated_at=datetime.now(timezone.utc).isoformat())
    if trimmed is None:
        print("Trim failed: the source is not a usable models.dev document.", file=sys.stderr)
        return 1
    if not validate_catalogue_schema(trimmed):
        print("Trimmed snapshot failed schema validation.", file=sys.stderr)
        return 1
    payload = json.dumps(trimmed, ensure_ascii=False, indent=1) + "\n"
    OUTPUT.write_text(payload, encoding="utf-8")
    size_kb = len(payload.encode("utf-8")) / 1024
    providers = len(trimmed["providers"])
    models = sum(len(p["models"]) for p in trimmed["providers"])
    print(f"Wrote {OUTPUT}")
    print(f"  {providers} providers, {models} models, {size_kb:.1f} KB")
    print(f"  source sha256: {trimmed['source']['sha256'][:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
