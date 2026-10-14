# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Reading minidlna's log and turning it into things worth acting on.

minidlna logs through syslog, which on a systemd machine means the journal; a
``log_dir`` file is used when there is no journal. A scan of a large library
can produce hundreds of near-identical lines, so the useful part is grouping
them: "217 files could not be read" rather than 217 warnings.

Standard library only, so it stays testable without a display server.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .models import MediaKind

#: How much history to read, and at most how many lines of it.
DEFAULT_SINCE = "-7d"
DEFAULT_LIMIT = 20000

_LINE_RE = re.compile(
    r"^(?:\[(?P<stamp>\d{4}/\d\d/\d\d \d\d:\d\d:\d\d)\]\s+)?"
    r"(?P<source>[\w.+-]+\.c:\d+):\s+(?P<level>\w+):\s+(?P<text>.*)$"
)
_OPEN_FAILED = re.compile(r"^Opening (?P<path>.+) failed! \[(?P<why>.*)\]$")
_NO_DETAILS = re.compile(r"^Unsuccessful getting details for (?P<path>.+)$")
_NOT_ACCESSIBLE = re.compile(r'^Media directory "(?P<spec>.+)" not accessible \[(?P<why>.*)\]$')
_REBINDING = re.compile(r"^DNS rebinding attack suspected \(Host: (?P<host>.*)\)$")
_UDP = re.compile(r"^Unknown udp packet received from (?P<ip>[0-9a-fA-F.:]+):\d+$")
_NO_OBJECT = re.compile(r"UPnPError 701")
_DIGITS = re.compile(r"\d+")

#: Messages that are routine and never a problem.
_ROUTINE = (
    "Starting MiniDLNA", "HTTP listening", "received signal", "Parsing playlists",
    "Finished parsing", "New media_dir detected", "Removed media_dir detected",
    "Creating new database",
    "Scanning", "Scanned", "Playlist", "Unknown udp packet",
)

LEVEL_ORDER = {"fatal": 0, "error": 1, "warn": 2, "warning": 2, "info": 3, "debug": 4}


@dataclass
class LogEntry:
    time: float
    level: str
    source: str
    text: str
    pid: int = 0

    @property
    def is_routine(self) -> bool:
        return any(self.text.startswith(prefix) for prefix in _ROUTINE)


@dataclass
class Problem:
    """One thing the log says is wrong, with its occurrences grouped."""

    kind: str
    severity: str            # "error" | "warning" | "info"
    title: str
    detail: str = ""
    hint: str = ""
    count: int = 0
    paths: List[str] = field(default_factory=list)
    last_seen: float = 0.0
    resolved: bool = False


# --------------------------------------------------------------------- parsing


def parse_message(message: str, when: float = 0.0, pid: int = 0) -> LogEntry:
    """Split one log line into level, source location and text.

    Lines that are not minidlna's own (systemd's "Started ..." and so on) are
    kept as informational entries with no source.
    """
    match = _LINE_RE.match(message.strip())
    if not match:
        return LogEntry(when, "info", "", message.strip(), pid)
    stamp = match.group("stamp")
    if stamp and not when:
        try:
            when = time.mktime(time.strptime(stamp, "%Y/%m/%d %H:%M:%S"))
        except (ValueError, OverflowError):
            pass
    return LogEntry(when, match.group("level").lower(), match.group("source"),
                    match.group("text"), pid)


def parse_journal_json(lines: Iterable[str]) -> List[LogEntry]:
    """Parse ``journalctl -o json`` output."""
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        message = record.get("MESSAGE")
        if isinstance(message, list):               # binary payload
            try:
                message = bytes(message).decode("utf-8", "replace")
            except (TypeError, ValueError):
                continue
        if not isinstance(message, str):
            continue
        try:
            when = int(record.get("__REALTIME_TIMESTAMP", 0)) / 1_000_000
        except (TypeError, ValueError):
            when = 0.0
        try:
            pid = int(record.get("_PID", 0))
        except (TypeError, ValueError):
            pid = 0
        entries.append(parse_message(message, when, pid))
    return entries


def read_journal(
    unit: str = "minidlna.service",
    since: str = DEFAULT_SINCE,
    limit: int = DEFAULT_LIMIT,
    log_dir: str = "",
) -> Tuple[List[LogEntry], str]:
    """Read minidlna's log. Returns ``(entries, error)``; never raises."""
    journalctl = shutil.which("journalctl")
    error = ""
    if journalctl:
        try:
            result = subprocess.run(
                [journalctl, "-u", unit, "--no-pager", "-o", "json",
                 "--since", since, "-n", str(limit)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                entries = parse_journal_json(result.stdout.splitlines())
                if entries:
                    return entries, ""
            else:
                error = result.stderr.strip() or "journalctl failed"
        except (OSError, subprocess.SubprocessError) as exc:
            error = str(exc)
    else:
        error = "journalctl not available"

    # Fall back to minidlna's own log file.
    for directory in filter(None, (log_dir, "/var/log/minidlna")):
        path = os.path.join(directory, "minidlna.log")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()[-limit:]
        except OSError:
            continue
        return [parse_message(line) for line in lines if line.strip()], ""
    return [], error


# -------------------------------------------------------------------- analysis


def current_run(entries: List[LogEntry]) -> List[LogEntry]:
    """The entries since minidlna last started."""
    for index in range(len(entries) - 1, -1, -1):
        if entries[index].text.startswith("Starting MiniDLNA"):
            return entries[index:]
    return entries


def _spec_path(spec: str) -> str:
    """``V,/some/dir`` -> ``/some/dir``; a bare path is returned as is."""
    prefix, comma, rest = spec.partition(",")
    if comma:
        try:
            MediaKind.parse(prefix)
        except ValueError:
            return spec
        return rest.strip()
    return spec


def failed_files(entries: Iterable[LogEntry]) -> Dict[str, str]:
    """Files minidlna could not read, mapped to the reason it gave."""
    failed: Dict[str, str] = {}
    for entry in entries:
        match = _OPEN_FAILED.match(entry.text)
        if match:
            failed[match.group("path")] = match.group("why")
            continue
        match = _NO_DETAILS.match(entry.text)
        if match:
            failed.setdefault(match.group("path"), "could not read details")
    return failed


def skipped_directories(entries: Iterable[LogEntry]) -> List[str]:
    """Media directories minidlna reported as inaccessible, in order."""
    found = []
    for entry in entries:
        match = _NOT_ACCESSIBLE.match(entry.text)
        if match:
            path = _spec_path(match.group("spec")).rstrip("/") or "/"
            if path not in found:
                found.append(path)
    return found


def ssdp_senders(entries: Iterable[LogEntry]) -> Dict[str, Tuple[int, float]]:
    """Addresses that sent unrecognised discovery packets: ip -> (count, last)."""
    seen: Dict[str, Tuple[int, float]] = {}
    for entry in entries:
        match = _UDP.match(entry.text)
        if match:
            count, last = seen.get(match.group("ip"), (0, 0.0))
            seen[match.group("ip")] = (count + 1, max(last, entry.time))
    return seen


def _common_directory(paths: List[str]) -> str:
    try:
        return os.path.commonpath([os.path.dirname(p) for p in paths])
    except ValueError:
        return ""


def _where(paths: List[str], limit: int = 3) -> str:
    """Describe where a set of files lives: "12 under a/b, 3 under a/c"."""
    base = _common_directory(paths)
    if not base or base == "/":
        return ""
    counts: Dict[str, int] = {}
    for path in paths:
        relative = os.path.relpath(path, base)
        head = relative.split(os.sep, 1)[0] if os.sep in relative else "."
        counts[head] = counts.get(head, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: -item[1])
    if len(ranked) == 1 and ranked[0][0] == ".":
        return f"all in {base}"
    parts = [
        f"{count} in {os.path.join(base, name) if name != '.' else base}"
        for name, count in ranked[:limit]
    ]
    more = len(ranked) - limit
    return ", ".join(parts) + (f", and {more} more places" if more > 0 else "")


def analyse(
    entries: List[LogEntry],
    exists: Callable[[str], bool] = os.path.isdir,
    configured: Optional[Iterable[str]] = None,
) -> List[Problem]:
    """Group log entries into problems, most severe first.

    When ``configured`` is given, folders that were reported inaccessible but
    are no longer part of the configuration are left out.
    """
    problems: List[Problem] = []
    current = None if configured is None else {p.rstrip("/") or "/" for p in configured}

    # --- files that could not be read, grouped by reason and extension
    groups: Dict[Tuple[str, str], Dict[str, float]] = {}
    for entry in entries:
        match = _OPEN_FAILED.match(entry.text)
        if match:
            ext = os.path.splitext(match.group("path"))[1].lower() or "(no extension)"
            groups.setdefault((match.group("why"), ext), {})[match.group("path")] = entry.time
    covered = {path for group in groups.values() for path in group}
    for entry in entries:
        match = _NO_DETAILS.match(entry.text)
        if match and match.group("path") not in covered:
            ext = os.path.splitext(match.group("path"))[1].lower() or "(no extension)"
            groups.setdefault(("could not read details", ext), {})[match.group("path")] = entry.time

    for (why, ext), files in groups.items():
        paths = sorted(files)
        noun = "file" if len(paths) == 1 else "files"
        hint = "These are probably corrupt, truncated, or not really media."
        if ext == ".ts":
            hint = (
                "“.ts” is also the extension for TypeScript and build files. If this "
                "folder holds source code, point minidlna at the folder your videos "
                "are actually in instead."
            )
        problems.append(Problem(
            kind="unparseable", severity="warning",
            title=f"{len(paths):,} {ext} {noun} could not be read",
            detail=f"minidlna says: “{why}”. " + (_where(paths) + "." if _where(paths) else ""),
            hint=hint, count=len(paths), paths=paths, last_seen=max(files.values()),
        ))

    # --- folders skipped because they were not there
    seen_dirs: Dict[str, Problem] = {}
    for entry in entries:
        match = _NOT_ACCESSIBLE.match(entry.text)
        if not match:
            continue
        path = _spec_path(match.group("spec")).rstrip("/") or "/"
        if current is not None and path not in current:
            continue
        problem = seen_dirs.get(path)
        if problem is None:
            problem = Problem(kind="unreachable", severity="error", title="", count=0, paths=[path])
            seen_dirs[path] = problem
        problem.count += 1
        problem.last_seen = max(problem.last_seen, entry.time)
        problem.detail = f"minidlna reported: {match.group('why')}"
    for path, problem in seen_dirs.items():
        if exists(path):
            problem.resolved = True
            problem.severity = "info"
            problem.title = f"{path} was missing at start-up, but is available now"
            problem.hint = "Restart minidlna (or rebuild the library) so it picks the folder up."
        else:
            problem.title = f"Folder not accessible: {path}"
            problem.hint = "If this is on a drive, check that it is mounted."
        problems.append(problem)

    # --- everything else
    rebinding: Optional[Problem] = None
    stale: Optional[Problem] = None
    others: Dict[str, Problem] = {}
    for entry in entries:
        text = entry.text
        if _OPEN_FAILED.match(text) or _NO_DETAILS.match(text) or _NOT_ACCESSIBLE.match(text):
            continue
        match = _REBINDING.match(text)
        if match:
            if rebinding is None:
                rebinding = Problem(
                    kind="rebinding", severity="info",
                    title="A request was refused because it used a hostname",
                    detail=f"Host header: {match.group('host')}",
                    hint="minidlna only answers requests addressed by IP. Use "
                         "http://127.0.0.1:<port> rather than localhost.",
                )
            rebinding.count += 1
            rebinding.last_seen = max(rebinding.last_seen, entry.time)
            continue
        if _NO_OBJECT.search(text):
            if stale is None:
                stale = Problem(
                    kind="stale_item", severity="info",
                    title="A device asked for something that no longer exists",
                    detail="minidlna answered “No such object”.",
                    hint="Usually a TV holding on to an old library listing after "
                         "a rebuild. Re-open its media list, or restart the device.",
                )
            stale.count += 1
            stale.last_seen = max(stale.last_seen, entry.time)
            continue
        if entry.is_routine or entry.level not in ("error", "fatal", "warn", "warning"):
            continue
        key = _DIGITS.sub("#", text)
        problem = others.get(key)
        if problem is None:
            problem = Problem(
                kind="log", severity="error" if entry.level in ("error", "fatal") else "warning",
                title=text, detail=entry.source,
            )
            others[key] = problem
        problem.count += 1
        problem.last_seen = max(problem.last_seen, entry.time)

    for problem in [rebinding, stale, *others.values()]:
        if problem is not None:
            problems.append(problem)

    order = {"error": 0, "warning": 1, "info": 2}
    problems.sort(key=lambda p: (order.get(p.severity, 3), -p.count))
    return problems
