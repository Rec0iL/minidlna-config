#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
# Install (or remove) the MiniDLNA Configuration app.
#
# By default this installs for the current user only, into ~/.local, and needs
# no root at all - matching the app itself, which asks for authorisation only
# when it actually writes /etc/minidlna.conf.
set -euo pipefail

APP_ID="minidlna-config"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

INVOCATION=("$0")
MODE="user"
PREFIX=""
LINK=0
UNINSTALL=0
MAKE_VENV=0

# ------------------------------------------------------------------ output

if [[ -t 1 ]]; then
    BOLD=$'\e[1m'; DIM=$'\e[2m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'
    RED=$'\e[31m'; RESET=$'\e[0m'
else
    BOLD=""; DIM=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi

step()  { printf '%s->%s %s\n' "$BOLD" "$RESET" "$*"; }
ok()    { printf '   %s+%s %s\n' "$GREEN" "$RESET" "$*"; }
warn()  { printf '   %s!%s %s\n' "$YELLOW" "$RESET" "$*"; }
die()   { printf '%serror:%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

usage() {
    cat <<EOF
${BOLD}Usage:${RESET} ./install.sh [options]

  (no options)      Install for the current user into ~/.local
  --system          Install for all users into /usr/local (needs root)
  --prefix DIR      Install into a specific prefix
  --link            Point the launcher at this source directory instead of
                    copying the files, so edits here take effect immediately
  --venv            Create a private virtual environment with PySide6 if it
                    is not already available
  --uninstall       Remove a previous installation
  -h, --help        Show this message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --system)    MODE="system"; INVOCATION+=("--system"); shift ;;
        --prefix)    [[ $# -ge 2 ]] || die "--prefix needs a directory"
                     MODE="prefix"; PREFIX="$2"; INVOCATION+=("--prefix" "$2"); shift 2 ;;
        --link)      LINK=1; shift ;;
        --venv)      MAKE_VENV=1; shift ;;
        --uninstall) UNINSTALL=1; shift ;;
        -h|--help)   usage; exit 0 ;;
        *)           die "unknown option: $1  (try --help)" ;;
    esac
done

# ------------------------------------------------------------------- paths

case "$MODE" in
    user)
        BIN_DIR="$HOME/.local/bin"
        DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
        ;;
    system)
        [[ $EUID -eq 0 ]] || die "--system needs root; re-run with: sudo ./install.sh --system"
        BIN_DIR="/usr/local/bin"
        DATA_DIR="/usr/local/share"
        ;;
    prefix)
        PREFIX="$(cd "$(dirname "$PREFIX")" && pwd)/$(basename "$PREFIX")"
        BIN_DIR="$PREFIX/bin"
        DATA_DIR="$PREFIX/share"
        ;;
esac

LIB_DIR="$DATA_DIR/$APP_ID"
APPS_DIR="$DATA_DIR/applications"
HICOLOR_DIR="$DATA_DIR/icons/hicolor"
ICON_DIR="$HICOLOR_DIR/scalable/apps"
ICON_SIZES=(16 22 24 32 48 64 128 256)
LAUNCHER="$BIN_DIR/$APP_ID"
DESKTOP_FILE="$APPS_DIR/$APP_ID.desktop"
ICON_FILE="$ICON_DIR/$APP_ID.svg"

refresh_caches() {
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
    fi

    # A stale icon-theme.cache shadows the directory it sits in: loaders trust
    # it and never scan for new files. If we cannot rebuild it, deleting it is
    # strictly better than leaving the old one in place.
    if [[ -e "$HICOLOR_DIR/icon-theme.cache" ]] || [[ -d "$HICOLOR_DIR" ]]; then
        if command -v gtk-update-icon-cache >/dev/null 2>&1 &&
           gtk-update-icon-cache -qtf "$HICOLOR_DIR" >/dev/null 2>&1; then
            :
        elif [[ -e "$HICOLOR_DIR/icon-theme.cache" ]]; then
            rm -f "$HICOLOR_DIR/icon-theme.cache"
        fi
    fi

    # KDE keeps its own icon lookup cache, including negative results, and
    # will not notice a newly installed icon until that cache is dropped.
    if [[ "${XDG_CURRENT_DESKTOP:-}" == *KDE* ]]; then
        rm -f "${XDG_CACHE_HOME:-$HOME/.cache}/icon-cache.kcache"
        for builder in kbuildsycoca6 kbuildsycoca5; do
            if command -v "$builder" >/dev/null 2>&1; then
                "$builder" --noincremental >/dev/null 2>&1 || true
                break
            fi
        done
    fi
}

# --------------------------------------------------------------- uninstall

if [[ $UNINSTALL -eq 1 ]]; then
    step "Removing $APP_ID"
    removed=0
    for target in "$LAUNCHER" "$DESKTOP_FILE" "$ICON_FILE"; do
        if [[ -e "$target" || -L "$target" ]]; then
            rm -f "$target"; ok "removed $target"; removed=1
        fi
    done
    for size in "${ICON_SIZES[@]}"; do
        target="$HICOLOR_DIR/${size}x${size}/apps/$APP_ID.png"
        if [[ -e "$target" ]]; then rm -f "$target"; removed=1; fi
    done
    if [[ -d "$LIB_DIR" ]]; then
        if [[ -d "$LIB_DIR/.venv" ]]; then
            warn "keeping virtual environment at $LIB_DIR/.venv"
            find "$LIB_DIR" -mindepth 1 -maxdepth 1 ! -name .venv -exec rm -rf {} +
        else
            rm -rf "$LIB_DIR"
        fi
        ok "removed $LIB_DIR"; removed=1
    fi
    refresh_caches
    [[ $removed -eq 1 ]] || warn "nothing was installed at this prefix"
    printf '\n%sDone.%s Your minidlna.conf and its backups were not touched.\n' "$BOLD" "$RESET"
    exit 0
fi

# ------------------------------------------------------------ dependencies

step "Checking for PySide6"

find_python() {
    local candidates=()
    [[ -n "${MINIDLNA_GUI_PYTHON:-}" ]] && candidates+=("$MINIDLNA_GUI_PYTHON")
    [[ -x "$LIB_DIR/.venv/bin/python3" ]] && candidates+=("$LIB_DIR/.venv/bin/python3")
    candidates+=(python3 /usr/bin/python3)
    for candidate in "${candidates[@]}"; do
        if command -v "$candidate" >/dev/null 2>&1 &&
           "$candidate" -c 'import PySide6' >/dev/null 2>&1; then
            command -v "$candidate"; return 0
        fi
    done
    return 1
}

if PYTHON="$(find_python)"; then
    ok "found $("$PYTHON" -c 'import PySide6,sys; print(f"PySide6 {PySide6.__version__} on Python {sys.version.split()[0]}")')"
elif [[ $MAKE_VENV -eq 1 ]]; then
    step "Creating a virtual environment (PySide6 is a large download)"
    mkdir -p "$LIB_DIR"
    python3 -m venv "$LIB_DIR/.venv" || die "could not create a virtual environment"
    "$LIB_DIR/.venv/bin/pip" install --quiet --upgrade pip
    "$LIB_DIR/.venv/bin/pip" install --quiet PySide6 || die "could not install PySide6"
    PYTHON="$LIB_DIR/.venv/bin/python3"
    ok "installed PySide6 into $LIB_DIR/.venv"
else
    cat >&2 <<EOF
${RED}error:${RESET} PySide6 was not found for any available Python interpreter.

Install it with your package manager:
    sudo dnf install python3-pyside6                # Fedora / Nobara
    sudo apt install python3-pyside6.qtwidgets      # Debian / Ubuntu
    sudo pacman -S pyside6                          # Arch

or let this script set up a private virtual environment instead:
    ./install.sh --venv
EOF
    exit 1
fi

# -------------------------------------------------------------- install it

step "Installing to $LIB_DIR"
mkdir -p "$BIN_DIR" "$APPS_DIR" "$ICON_DIR"

if [[ $LINK -eq 1 ]]; then
    RUN_DIR="$SOURCE_DIR"
    ok "linking against $SOURCE_DIR (edits take effect immediately)"
else
    mkdir -p "$LIB_DIR"
    # Replace a previous install but keep any virtual environment we made.
    find "$LIB_DIR" -mindepth 1 -maxdepth 1 ! -name .venv -exec rm -rf {} +
    cp -r "$SOURCE_DIR/minidlnaconfig" "$LIB_DIR/"
    cp -r "$SOURCE_DIR/assets"         "$LIB_DIR/"
    cp    "$SOURCE_DIR/main.py"        "$LIB_DIR/"
    cp    "$SOURCE_DIR/start.sh"       "$LIB_DIR/"
    chmod +x "$LIB_DIR/start.sh" "$LIB_DIR/main.py"
    find "$LIB_DIR" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
    RUN_DIR="$LIB_DIR"
    ok "copied application files"
fi

# The launcher is a thin wrapper so the interpreter is still discovered at
# run time - a Python upgrade must not break an existing installation.
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
exec "$RUN_DIR/start.sh" "\$@"
EOF
chmod +x "$LAUNCHER"
ok "launcher: $LAUNCHER"

if [[ $LINK -eq 1 ]]; then
    ln -sf "$SOURCE_DIR/assets/$APP_ID.svg" "$ICON_FILE"
else
    cp "$SOURCE_DIR/assets/$APP_ID.svg" "$ICON_FILE"
fi

# Raster sizes as well: some icon loaders will not use a scalable-only theme.
for size in "${ICON_SIZES[@]}"; do
    source_png="$SOURCE_DIR/assets/icons/${size}x${size}/apps/$APP_ID.png"
    [[ -f "$source_png" ]] || continue
    mkdir -p "$HICOLOR_DIR/${size}x${size}/apps"
    cp "$source_png" "$HICOLOR_DIR/${size}x${size}/apps/$APP_ID.png"
done

# KDE ignores a hicolor directory that has no theme index, so a user-local
# icon directory created from scratch needs one before it is looked at.
if [[ ! -f "$HICOLOR_DIR/index.theme" ]]; then
    if [[ -f /usr/share/icons/hicolor/index.theme ]]; then
        cp /usr/share/icons/hicolor/index.theme "$HICOLOR_DIR/index.theme"
    else
        printf '[Icon Theme]\nName=Hicolor\nComment=Fallback icon theme\nDirectories=scalable/apps\n\n[scalable/apps]\nSize=48\nMinSize=8\nMaxSize=512\nType=Scalable\nContext=Applications\n' \
            > "$HICOLOR_DIR/index.theme"
    fi
    ok "wrote $HICOLOR_DIR/index.theme"
fi
ok "icon: $ICON_FILE (+ ${#ICON_SIZES[@]} raster sizes)"

sed -e "s|@EXEC@|$LAUNCHER|g" -e "s|@ICON@|$APP_ID|g" \
    "$SOURCE_DIR/assets/$APP_ID.desktop.in" > "$DESKTOP_FILE"
chmod 644 "$DESKTOP_FILE"

if command -v desktop-file-validate >/dev/null 2>&1; then
    if desktop-file-validate "$DESKTOP_FILE"; then
        ok "menu entry: $DESKTOP_FILE (validated)"
    else
        warn "menu entry written but failed validation"
    fi
else
    ok "menu entry: $DESKTOP_FILE"
fi

refresh_caches

# ----------------------------------------------------------------- summary

printf '\n%sInstalled.%s\n\n' "$GREEN$BOLD" "$RESET"
printf '  Run it from your application menu, or with:\n\n      %s%s%s\n\n' "$BOLD" "$APP_ID" "$RESET"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on your PATH; run it as $LAUNCHER"
       printf '     %sor add to your shell profile:  export PATH="%s:$PATH"%s\n' \
              "$DIM" "$BIN_DIR" "$RESET" ;;
esac

printf '  %sUninstall with:  %s --uninstall%s\n' "$DIM" "${INVOCATION[*]}" "$RESET"
