"""Create a consistent SQLite backup using its online backup API."""

import argparse
import os
import sqlite3
from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.config import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    url = make_url(Settings().database_url)
    if url.drivername != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("Backup requires a file-backed SQLite database")
    target = args.destination.resolve()
    if not Path(url.database).is_file() or target == Path(url.database).resolve():
        raise ValueError("A distinct destination and existing source database are required")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with sqlite3.connect(url.database) as source, sqlite3.connect(target) as destination:
            source.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    print(f"Backup created: {target}")


if __name__ == "__main__":
    main()
