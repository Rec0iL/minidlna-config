# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Privilege escalation via polkit.

The old version of this app asked for the sudo password in a Tk dialog and
piped it to ``sudo -S``.  That means the password passes through this process
and its argument/stdin plumbing.  Instead we hand the whole job to ``pkexec``,
which prompts through the desktop's own trusted polkit agent - this process
never sees the password.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import List, Optional

HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helper.py")


@dataclass
class Result:
    """Outcome of an elevated operation."""

    ok: bool
    message: str = ""
    detail: str = ""
    cancelled: bool = False


def have_pkexec() -> bool:
    return shutil.which("pkexec") is not None


def _pkexec(args: List[str], timeout: int = 120) -> Result:
    """Run ``args`` as root through pkexec."""
    pkexec = shutil.which("pkexec")
    if not pkexec:
        return Result(False, "pkexec is not installed", detail="Install polkit to make changes to system files.")

    try:
        completed = subprocess.run(
            [pkexec] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return Result(False, "The operation timed out")
    except OSError as exc:
        return Result(False, "Could not run pkexec", detail=str(exc))

    if completed.returncode == 0:
        return Result(True, completed.stdout.strip())

    # pkexec exits 126 when the user dismisses the authentication dialog,
    # 127 when authorisation is refused outright.
    if completed.returncode in (126, 127):
        return Result(False, "Authentication cancelled", cancelled=True)

    detail = (completed.stderr or completed.stdout).strip()
    return Result(False, "The operation failed", detail=detail)


def apply_config(
    text: str,
    dest: str,
    restart: bool = False,
    rescan: bool = False,
    unit: str = "minidlna.service",
    db_dir: str = "/var/cache/minidlna",
) -> Result:
    """Write the config and optionally restart/rescan - in one auth prompt.

    Saving and restarting used to be two separate elevated actions, which
    meant two password prompts for what is really a single intention.
    """
    fd, staged = tempfile.mkstemp(prefix="minidlna-conf-", suffix=".staged")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        # The helper runs as root and can read a 0600 file, but make the
        # intent explicit: only this user and root may see the staged config.
        os.chmod(staged, 0o600)

        args = [sys.executable, HELPER, "apply", "--src", staged, "--dest", dest]
        if restart:
            args += ["--restart"]
        if rescan:
            args += ["--rescan", "--db-dir", db_dir]
        args += ["--unit", unit]
        return _pkexec(args)
    finally:
        if os.path.exists(staged):
            os.unlink(staged)


def control_service(action: str, unit: str = "minidlna.service", db_dir: str = "/var/cache/minidlna") -> Result:
    """start / stop / restart / rescan the unit."""
    args = [sys.executable, HELPER, "service", "--action", action, "--unit", unit, "--db-dir", db_dir]
    return _pkexec(args)
