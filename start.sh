#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
# Launch the minidlna configuration GUI.
#
# Unlike the previous version, this does NOT elevate the whole application.
# The GUI runs as you; it asks polkit for authorisation only at the moment it
# writes /etc/minidlna.conf or controls the service.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Find an interpreter that actually has PySide6 available.
pick_python() {
    local candidates=()
    [[ -n "${MINIDLNA_GUI_PYTHON:-}" ]] && candidates+=("$MINIDLNA_GUI_PYTHON")
    [[ -x "$DIR/.venv/bin/python3" ]] && candidates+=("$DIR/.venv/bin/python3")
    candidates+=(python3 /usr/bin/python3)

    for candidate in "${candidates[@]}"; do
        if command -v "$candidate" >/dev/null 2>&1 &&
           "$candidate" -c 'import PySide6' >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

if ! PYTHON="$(pick_python)"; then
    echo "PySide6 was not found for any available Python interpreter." >&2
    echo >&2
    echo "Install it with your package manager, for example:" >&2
    echo "    sudo dnf install python3-pyside6      # Fedora / Nobara" >&2
    echo "    sudo apt install python3-pyside6.qtwidgets   # Debian / Ubuntu" >&2
    echo "    sudo pacman -S pyside6                # Arch" >&2
    echo >&2
    echo "or into a local virtual environment:" >&2
    echo "    python3 -m venv \"$DIR/.venv\" && \"$DIR/.venv/bin/pip\" install PySide6" >&2
    exit 1
fi

exec "$PYTHON" "$DIR/main.py" "$@"
