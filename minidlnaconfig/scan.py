# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Working out whether minidlna is scanning, and how far along it is.

minidlna reports no progress of its own, so it is inferred from two things:

* While it scans, ``minidlnad`` forks a child process that does the work and
  exits when the scan is over. A child of the service's main process is
  therefore the signal that a scan is running.
* How many of the files that *should* end up in the index already have. The
  total is estimated by walking the configured folders once, using the same
  extension lists that guess a folder's media type.

The estimate is approximate - minidlna also rejects files whose contents it
cannot parse - so the fraction is held below 1 until the scan really ends.

Standard library only, so it stays testable without a display server.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Iterator, List, Optional, Sequence, Tuple

from .models import MediaKind
from .service import AUDIO_EXT, IMAGE_EXT, VIDEO_EXT

#: A scan is never reported as complete while it is still running, because the
#: estimated total can undershoot.
MAX_RUNNING_FRACTION = 0.99


@dataclass
class ScanState:
    """What is known about the scan right now."""

    scanning: bool = False
    indexed: int = 0
    #: Estimated files to index; None while that is still being counted.
    expected: Optional[int] = None

    @property
    def fraction(self) -> Optional[float]:
        """0..1 progress, or None when there is no estimate to compare to."""
        if not self.expected:
            return None
        return max(0.0, min(MAX_RUNNING_FRACTION, self.indexed / self.expected))


def scanner_pids(main_pid: int, proc: str = "/proc") -> List[int]:
    """Processes whose parent is ``main_pid``: minidlna's scanner children."""
    if main_pid <= 0:
        return []
    try:
        names = os.listdir(proc)
    except OSError:
        return []

    found = []
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc, name, "stat"), "r", encoding="utf-8", errors="replace") as handle:
                stat = handle.read()
        except OSError:
            continue  # the process exited between listdir and open
        # "pid (comm) state ppid ..." - comm may itself contain spaces and
        # parentheses, so split after the *last* closing parenthesis.
        fields = stat.rpartition(")")[2].split()
        try:
            parent = int(fields[1])
        except (IndexError, ValueError):
            continue
        if parent == main_pid:
            found.append(int(name))
    return found


def _wanted_extensions(kinds: MediaKind) -> frozenset:
    extensions = set()
    if kinds & MediaKind.AUDIO:
        extensions |= AUDIO_EXT
    if kinds & MediaKind.VIDEO:
        extensions |= VIDEO_EXT
    if kinds & MediaKind.PICTURES:
        extensions |= IMAGE_EXT
    return frozenset(extensions)


def iter_media_files(
    path: str,
    kinds: MediaKind = MediaKind.ALL,
    should_stop: Callable[[], bool] = lambda: False,
) -> Iterator[str]:
    """Yield every file under ``path`` that minidlna would try to index.

    Hidden files and directories are skipped, as minidlna skips them.
    Stops early when ``should_stop`` returns true.
    """
    wanted = _wanted_extensions(kinds)
    pending = [path]
    while pending:
        if should_stop():
            return
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.name.startswith("."):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(entry.path)
                        elif os.path.splitext(entry.name)[1].lower() in wanted:
                            yield entry.path
                    except OSError:
                        continue
        except OSError:
            continue  # unreadable or vanished: minidlna cannot see it either


def count_media_files(
    folders: Sequence[Tuple[str, MediaKind]],
    should_stop: Callable[[], bool] = lambda: False,
) -> Optional[int]:
    """Estimate how many files minidlna will index under ``folders``.

    Returns None if ``should_stop`` asked to abandon the walk, so a stale
    count is never mistaken for a real one.
    """
    total = 0
    for path, kinds in folders:
        for _ in iter_media_files(path, kinds, should_stop):
            total += 1
        if should_stop():
            return None
    return total
