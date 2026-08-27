# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Querying and controlling the minidlna systemd service (read-only side)."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional

#: Distributions disagree on the unit name; probe in this order.
UNIT_CANDIDATES = ("minidlna.service", "minidlnad.service")

#: File extensions used to guess what kind of media lives in a directory.
AUDIO_EXT = {".mp3", ".flac", ".ogg", ".oga", ".m4a", ".wav", ".wma", ".aac", ".opus", ".ape"}
VIDEO_EXT = {".mp4", ".mkv", ".avi", ".m4v", ".mpg", ".mpeg", ".mov", ".wmv", ".flv", ".ts", ".webm", ".m2ts"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic"}


@dataclass
class ServiceStatus:
    """A snapshot of the service state."""

    available: bool = False
    unit: str = ""
    active: bool = False
    state: str = "unknown"
    sub_state: str = ""
    pid: int = 0
    error: str = ""

    @property
    def summary(self) -> str:
        if not self.available:
            return "service not found"
        if self.active:
            return f"running · pid {self.pid}" if self.pid else "running"
        return self.state or "stopped"


def _run(args: List[str], timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


class ServiceController:
    """Read-only queries about the minidlna unit.

    Anything that changes state goes through :mod:`minidlnaconfig.privilege`
    so that authentication is handled by polkit rather than by this process.
    """

    def __init__(self, unit: Optional[str] = None):
        self._systemctl = shutil.which("systemctl")
        self.unit = unit or self._detect_unit()

    def _detect_unit(self) -> str:
        if not self._systemctl:
            return UNIT_CANDIDATES[0]
        try:
            result = _run([self._systemctl, "list-unit-files", "--no-legend", "--no-pager"])
        except (OSError, subprocess.SubprocessError):
            return UNIT_CANDIDATES[0]
        listed = result.stdout
        for candidate in UNIT_CANDIDATES:
            if candidate in listed:
                return candidate
        return UNIT_CANDIDATES[0]

    def status(self) -> ServiceStatus:
        """Current unit state.  Never raises; failures land in ``error``."""
        if not self._systemctl:
            return ServiceStatus(error="systemctl not available")
        try:
            result = _run([
                self._systemctl, "show", self.unit,
                "-p", "ActiveState", "-p", "SubState", "-p", "MainPID", "-p", "LoadState",
            ])
        except (OSError, subprocess.SubprocessError) as exc:
            return ServiceStatus(error=str(exc))

        values = {}
        for line in result.stdout.splitlines():
            key, _, value = line.partition("=")
            values[key] = value

        if values.get("LoadState") in (None, "not-found"):
            return ServiceStatus(unit=self.unit, error="unit not found")

        active_state = values.get("ActiveState", "unknown")
        try:
            pid = int(values.get("MainPID", "0"))
        except ValueError:
            pid = 0

        return ServiceStatus(
            available=True,
            unit=self.unit,
            active=active_state == "active",
            state=active_state,
            sub_state=values.get("SubState", ""),
            pid=pid,
        )

    def service_user(self) -> str:
        """The user the unit runs as - the account that must be able to read
        the media directories.  Empty when it cannot be determined."""
        if not self._systemctl:
            return ""
        try:
            result = _run([self._systemctl, "show", self.unit, "-p", "User"])
        except (OSError, subprocess.SubprocessError):
            return ""
        _, _, value = result.stdout.strip().partition("=")
        return value


def guess_kinds(path: str, sample_limit: int = 400):
    """Guess which media types a directory holds by sampling its contents.

    Walks breadth-first and stops after ``sample_limit`` files so that adding
    a huge directory stays instant.  Returns a MediaKind.
    """
    from .models import MediaKind

    audio = video = image = 0
    seen = 0
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")][:20]
            for name in files:
                extension = os.path.splitext(name)[1].lower()
                if extension in AUDIO_EXT:
                    audio += 1
                elif extension in VIDEO_EXT:
                    video += 1
                elif extension in IMAGE_EXT:
                    image += 1
                else:
                    continue
                seen += 1
                if seen >= sample_limit:
                    raise StopIteration
    except StopIteration:
        pass
    except (OSError, PermissionError):
        pass

    if not seen:
        return MediaKind.ALL

    # Include any type making up at least a tenth of the sample.
    threshold = max(1, seen // 10)
    kinds = None
    for count, member in ((audio, MediaKind.AUDIO), (image, MediaKind.PICTURES), (video, MediaKind.VIDEO)):
        if count >= threshold:
            kinds = member if kinds is None else kinds | member
    return kinds or MediaKind.ALL


def readable_by(path: str, user: str) -> Optional[bool]:
    """Whether ``user`` can traverse and read ``path``.

    minidlna runs as its own account, so a media directory under a 0700 home
    directory is invisible to it - the single most common reason a folder
    silently stays empty.  Returns None when the check cannot be performed.
    """
    if not user or user == "root":
        return True
    try:
        import pwd
    except ImportError:
        return None
    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        return None

    uid, gid = entry.pw_uid, entry.pw_gid
    try:
        groups = {gid}
        import grp
        for group in grp.getgrall():
            if user in group.gr_mem:
                groups.add(group.gr_gid)
    except (OSError, ImportError):
        groups = {gid}

    def bits(target: str):
        """Return (read, execute) permission bits that apply to ``user``."""
        info = os.stat(target)
        mode = info.st_mode
        if info.st_uid == uid:
            read_bit, exec_bit = 0o400, 0o100
        elif info.st_gid in groups:
            read_bit, exec_bit = 0o040, 0o010
        else:
            read_bit, exec_bit = 0o004, 0o001
        return bool(mode & read_bit), bool(mode & exec_bit)

    # Traversing a parent needs only the execute bit - a 0710 home directory
    # is perfectly traversable by its group without being listable.
    parts = [part for part in os.path.abspath(path).split(os.sep) if part]
    try:
        for index in range(len(parts)):
            parent = os.sep + os.sep.join(parts[:index])
            if not bits(parent)[1]:
                return False
        # The directory itself must be both listable and traversable.
        readable, executable = bits(os.path.abspath(path))
        return readable and executable
    except OSError:
        return False


#: Filesystems that never hold user media and only clutter a places list.
PSEUDO_FILESYSTEMS = {
    "autofs", "bpf", "cgroup", "cgroup2", "configfs", "debugfs", "devpts",
    "devtmpfs", "efivarfs", "fuse.gvfsd-fuse", "fuse.portal", "fusectl",
    "hugetlbfs", "mqueue", "proc", "pstore", "ramfs", "securityfs", "squashfs",
    "sysfs", "tmpfs", "tracefs",
}

#: Mount points that are real but are never media locations.
BORING_MOUNTS = ("/boot", "/var/lib/snapd", "/run/credentials", "/run/user")


def _unescape_mount(field: str) -> str:
    """/proc/mounts escapes spaces and friends as octal (``\\040``)."""
    out = []
    index = 0
    while index < len(field):
        if field[index] == "\\" and index + 3 < len(field):
            chunk = field[index + 1:index + 4]
            if len(chunk) == 3 and all(c in "01234567" for c in chunk):
                out.append(chr(int(chunk, 8)))
                index += 4
                continue
        out.append(field[index])
        index += 1
    return "".join(out)


def mount_points(source: str = "/proc/mounts"):
    """Currently mounted filesystems that could plausibly hold media.

    Returns a list of ``(label, path)`` pairs: real block devices only, with
    system locations filtered out. Used to populate the folder picker's
    sidebar so removable and secondary drives are one click away.
    """
    results = []
    seen = set()
    try:
        with open(source, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return results

    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        device, raw_path, fstype = parts[0], _unescape_mount(parts[1]), parts[2]

        if fstype in PSEUDO_FILESYSTEMS or not device.startswith("/dev/"):
            continue
        if raw_path in seen:
            continue
        if any(raw_path == b or raw_path.startswith(b + "/") for b in BORING_MOUNTS):
            continue
        if not os.path.isdir(raw_path):
            continue

        seen.add(raw_path)
        label = os.path.basename(raw_path.rstrip("/")) or raw_path
        results.append((label, raw_path))

    # Root and /home first, then removable media by name.
    def sort_key(entry):
        _, path = entry
        return (0 if path == "/" else 1 if path == "/home" else 2, path)

    results.sort(key=sort_key)
    return results
