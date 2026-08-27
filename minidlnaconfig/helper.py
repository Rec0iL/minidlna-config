#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Root-side worker, launched by pkexec.  Never run this directly.

Kept deliberately small and free of GUI imports: everything here executes with
full privileges, so it does exactly one of a few well-defined jobs and then
exits.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

# Allow "python3 /path/to/helper.py" to find the package, since pkexec
# deliberately discards PYTHONPATH and most of the environment.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from minidlnaconfig.config import atomic_write  # noqa: E402

#: Backups older than this many copies are pruned so /etc does not fill up.
KEEP_BACKUPS = 10


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"
    return subprocess.run([systemctl, *args], capture_output=True, text=True, timeout=60)


def _prune_backups(dest: str) -> None:
    directory = os.path.dirname(os.path.abspath(dest)) or "."
    prefix = os.path.basename(dest) + ".bak-"
    try:
        backups = sorted(name for name in os.listdir(directory) if name.startswith(prefix))
    except OSError:
        return
    for stale in backups[:-KEEP_BACKUPS]:
        try:
            os.unlink(os.path.join(directory, stale))
        except OSError:
            pass


def _clear_database(db_dir: str) -> None:
    """Remove the media database so minidlna rebuilds it from scratch."""
    if not db_dir or db_dir in ("/", "/etc", "/usr", "/var", "/home"):
        raise SystemExit(f"refusing to clear implausible db_dir {db_dir!r}")
    for name in ("files.db", "art_cache"):
        target = os.path.join(db_dir, name)
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        elif os.path.exists(target):
            try:
                os.unlink(target)
            except OSError:
                pass


def command_apply(args: argparse.Namespace) -> int:
    if not os.path.isfile(args.src):
        print(f"staged file {args.src} is missing", file=sys.stderr)
        return 2

    with open(args.src, "r", encoding="utf-8") as handle:
        text = handle.read()
    if not text.strip():
        print("refusing to write an empty configuration", file=sys.stderr)
        return 2

    backup = atomic_write(args.dest, text, backup=True)
    _prune_backups(args.dest)

    messages = ["Configuration saved"]
    if backup:
        messages.append(f"backup: {os.path.basename(backup)}")

    if args.rescan:
        _systemctl("stop", args.unit)
        _clear_database(args.db_dir)
        result = _systemctl("start", args.unit)
        if result.returncode != 0:
            print(result.stderr.strip(), file=sys.stderr)
            return 1
        messages.append("library rescan started")
    elif args.restart:
        result = _systemctl("restart", args.unit)
        if result.returncode != 0:
            print(result.stderr.strip(), file=sys.stderr)
            return 1
        messages.append("service restarted")

    print(" · ".join(messages))
    return 0


def command_service(args: argparse.Namespace) -> int:
    if args.action == "rescan":
        _systemctl("stop", args.unit)
        _clear_database(args.db_dir)
        result = _systemctl("start", args.unit)
        label = "Library rescan started"
    else:
        result = _systemctl(args.action, args.unit)
        label = f"Service {args.action}ed" if args.action != "stop" else "Service stopped"

    if result.returncode != 0:
        print(result.stderr.strip() or "systemctl failed", file=sys.stderr)
        return 1
    print(label)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Privileged helper for minidlna-config")
    sub = parser.add_subparsers(dest="command", required=True)

    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--src", required=True)
    apply_parser.add_argument("--dest", required=True)
    apply_parser.add_argument("--restart", action="store_true")
    apply_parser.add_argument("--rescan", action="store_true")
    apply_parser.add_argument("--db-dir", default="/var/cache/minidlna")
    apply_parser.add_argument("--unit", default="minidlna.service")
    apply_parser.set_defaults(func=command_apply)

    service_parser = sub.add_parser("service")
    service_parser.add_argument("--action", required=True,
                                choices=["start", "stop", "restart", "rescan"])
    service_parser.add_argument("--unit", default="minidlna.service")
    service_parser.add_argument("--db-dir", default="/var/cache/minidlna")
    service_parser.set_defaults(func=command_service)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
