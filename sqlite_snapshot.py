#!/usr/bin/env python3
"""Create and verify a consistent SQLite snapshot from a live database."""

import argparse
import sqlite3
from pathlib import Path


def _remove_with_wal_sidecars(path: Path) -> None:
    """Remove path plus any -wal/-shm sidecar files SQLite may have created
    alongside it. A WAL-mode connection creates these lazily on its first
    read/write against the file (PRAGMA integrity_check below is enough to
    trigger it, backup() alone is not) — cleaning up only the main file left
    <path>-shm/<path>-wal behind on every run."""
    path.unlink(missing_ok=True)
    Path(f"{path}-wal").unlink(missing_ok=True)
    Path(f"{path}-shm").unlink(missing_ok=True)


def snapshot(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    _remove_with_wal_sidecars(temporary)
    try:
        with sqlite3.connect(source) as source_db, sqlite3.connect(temporary) as target_db:
            source_db.backup(target_db)
            result = target_db.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"SQLite snapshot integrity check failed: {result}")
        temporary.replace(destination)
    finally:
        _remove_with_wal_sidecars(temporary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    snapshot(args.source, args.destination)


if __name__ == "__main__":
    main()