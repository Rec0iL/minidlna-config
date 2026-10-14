# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Previous versions of minidlna.conf.

Every save keeps a ``minidlna.conf.bak-YYYYMMDD-HHMMSS`` copy next to the
file. This lists them and shows how each one differs from the current file,
so a bad edit can be rolled back with confidence.
"""

from __future__ import annotations

import difflib
import glob
import os
import re
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

_STAMP_RE = re.compile(r"\.bak-(\d{8})-(\d{6})$")


@dataclass
class Backup:
    path: str
    time: float
    size: int

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


def _timestamp(path: str) -> float:
    match = _STAMP_RE.search(path)
    if match:
        try:
            return time.mktime(time.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S"))
        except (ValueError, OverflowError):
            pass
    # A stamp that is not a real date: fall back to when the file changed.
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def list_backups(conf_path: Optional[str]) -> List[Backup]:
    """Backups of ``conf_path``, newest first."""
    if not conf_path:
        return []
    found = []
    for path in glob.glob(glob.escape(conf_path) + ".bak-*"):
        if not _STAMP_RE.search(path) or not os.path.isfile(path):
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        found.append(Backup(path, _timestamp(path), size))
    found.sort(key=lambda b: (b.time, b.path), reverse=True)
    return found


def read_backup(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def diff_lines(current: str, other: str) -> List[Tuple[str, str]]:
    """Line diff from ``current`` to ``other`` as ``(kind, text)`` pairs.

    ``kind`` is ``"add"`` (present in ``other`` only), ``"del"`` (present in
    ``current`` only) or ``"ctx"``. Unchanged runs are trimmed to three lines
    of context, with ``"gap"`` entries where lines were skipped.
    """
    result: List[Tuple[str, str]] = []
    matcher = difflib.SequenceMatcher(
        a=current.splitlines(), b=other.splitlines(), autojunk=False
    )
    a_lines, b_lines = current.splitlines(), other.splitlines()
    for group in matcher.get_grouped_opcodes(3):
        if result:
            result.append(("gap", "…"))
        for tag, a1, a2, b1, b2 in group:
            if tag == "equal":
                result.extend(("ctx", line) for line in a_lines[a1:a2])
                continue
            if tag in ("replace", "delete"):
                result.extend(("del", line) for line in a_lines[a1:a2])
            if tag in ("replace", "insert"):
                result.extend(("add", line) for line in b_lines[b1:b2])
    return result


def change_summary(current: str, other: str) -> Tuple[int, int]:
    """``(added, removed)`` line counts going from ``current`` to ``other``."""
    added = removed = 0
    for kind, _ in diff_lines(current, other):
        if kind == "add":
            added += 1
        elif kind == "del":
            removed += 1
    return added, removed
