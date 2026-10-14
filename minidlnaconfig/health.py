# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Comparing what is on disk with what minidlna indexed.

For each configured folder this answers "is anything missing, and why": files
minidlna tried and could not read, files it never picked up, and index entries
that no longer have a file behind them (a drive that was unplugged, say).

Standard library only, so it stays testable without a display server.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from . import library, scan
from .models import MediaKind

#: Example paths kept per category, so a huge library stays small in memory.
EXAMPLE_LIMIT = 200


@dataclass
class FolderHealth:
    path: str
    exists: bool = True
    on_disk: int = 0
    indexed: int = 0
    unparseable: int = 0
    not_indexed: int = 0
    stale: int = 0
    #: category -> example paths. Categories: unparseable, not_indexed, stale
    examples: Dict[str, List[str]] = field(default_factory=dict)
    #: reason minidlna gave for unparseable files -> count
    reasons: Dict[str, int] = field(default_factory=dict)
    #: extension -> count, for files that were never picked up
    missed_extensions: Dict[str, int] = field(default_factory=dict)

    @property
    def missing(self) -> int:
        return self.unparseable + self.not_indexed

    @property
    def ok(self) -> bool:
        return self.exists and self.missing == 0 and self.stale == 0


def indexed_paths(db_path: str) -> Optional[Set[str]]:
    """Every file path in minidlna's index, or None if it cannot be read."""
    if not os.path.isfile(db_path):
        return None
    try:
        with library._connect(db_path) as con:
            rows = con.execute("SELECT PATH FROM DETAILS WHERE MIME IS NOT NULL").fetchall()
    except (sqlite3.DatabaseError, OSError):
        return None
    return {row[0] for row in rows if row[0]}


def _remember(health: FolderHealth, category: str, path: str) -> None:
    bucket = health.examples.setdefault(category, [])
    if len(bucket) < EXAMPLE_LIMIT:
        bucket.append(path)


def compare(
    folders: Sequence[Tuple[str, MediaKind]],
    indexed: Set[str],
    failed: Optional[Dict[str, str]] = None,
    should_stop: Callable[[], bool] = lambda: False,
) -> List[FolderHealth]:
    """Check each folder against the index."""
    failed = failed or {}
    results = []

    for path, kinds in folders:
        health = FolderHealth(path=path)
        if not os.path.isdir(path):
            health.exists = False
            results.append(health)
            continue

        seen: Set[str] = set()
        for file_path in scan.iter_media_files(path, kinds, should_stop):
            seen.add(file_path)
            health.on_disk += 1
            if file_path in indexed:
                health.indexed += 1
            elif file_path in failed:
                health.unparseable += 1
                why = failed[file_path]
                health.reasons[why] = health.reasons.get(why, 0) + 1
                _remember(health, "unparseable", file_path)
            else:
                health.not_indexed += 1
                ext = os.path.splitext(file_path)[1].lower() or "(none)"
                health.missed_extensions[ext] = health.missed_extensions.get(ext, 0) + 1
                _remember(health, "not_indexed", file_path)

        prefix = path.rstrip("/") + "/"
        for entry in indexed:
            if entry.startswith(prefix) and entry not in seen:
                health.stale += 1
                _remember(health, "stale", entry)

        if should_stop():
            break
        results.append(health)
    return results
