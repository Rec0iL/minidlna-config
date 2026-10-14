# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""The minidlna.conf settings the app can edit, besides media folders.

A curated subset: the ones people actually change. Anything not listed here is
left exactly as it is in the file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    kind: str                      # "text" | "int" | "bool" | "choice"
    group: str
    help: str = ""
    #: What minidlna uses when the key is absent.
    default: str = ""
    choices: Tuple[Tuple[str, str], ...] = ()
    #: Changing this has consequences worth a warning before saving.
    warn: str = ""


GROUPS = ("Server", "Library", "Storage")

SPECS: Tuple[SettingSpec, ...] = (
    SettingSpec(
        "friendly_name", "Server name", "text", "Server",
        "How this server appears on your TV or player.",
        default="",
    ),
    SettingSpec(
        "port", "Port", "int", "Server",
        "The port the web interface and streams are served on.",
        default="8200",
        warn="Devices may need a moment to find the server on its new port.",
    ),
    SettingSpec(
        "network_interface", "Network interface", "text", "Server",
        "Comma-separated interface names to listen on, e.g. eth0. Empty means all.",
        default="",
    ),
    SettingSpec(
        "notify_interval", "Announce every (seconds)", "int", "Server",
        "How often minidlna tells the network it is still here.",
        default="895",
    ),
    SettingSpec(
        "strict_dlna", "Strict DLNA compliance", "bool", "Server",
        "Follow the DLNA spec exactly. Some players only work with this off.",
        default="no",
    ),
    SettingSpec(
        "inotify", "Watch folders for changes", "bool", "Library",
        "Pick up new and removed files automatically, without a rescan.",
        default="yes",
    ),
    SettingSpec(
        "root_container", "Top level of the library", "choice", "Library",
        "What a device sees first when it opens the server.",
        default=".",
        choices=(
            (".", "Folders, Music, Videos, Pictures"),
            ("B", "Browse the folder tree"),
            ("M", "Music"),
            ("V", "Videos"),
            ("P", "Pictures"),
        ),
    ),
    SettingSpec(
        "merge_media_dirs", "Merge folders of the same type", "bool", "Library",
        "Show all video folders as one list instead of one entry per folder.",
        default="no",
    ),
    SettingSpec(
        "wide_links", "Follow symbolic links", "bool", "Library",
        "Index files reached through links that point outside a media folder.",
        default="no",
    ),
    SettingSpec(
        "album_art_names", "Album art file names", "text", "Library",
        "Slash-separated file names to use as cover art, e.g. Cover.jpg/folder.jpg.",
        default="",
    ),
    SettingSpec(
        "db_dir", "Database folder", "text", "Storage",
        "Where minidlna keeps its index. The library is rebuilt if this changes.",
        default="/var/cache/minidlna",
        warn="The library will be rebuilt from scratch in the new location.",
    ),
    SettingSpec(
        "log_dir", "Log folder", "text", "Storage",
        "Where minidlna writes minidlna.log when it is not logging to the journal.",
        default="/var/log/minidlna",
    ),
    SettingSpec(
        "log_level", "Log level", "text", "Storage",
        "e.g. general,scanner,http=warn. Lower levels (info, debug) are much noisier.",
        default="",
    ),
)

SPEC_BY_KEY: Dict[str, SettingSpec] = {spec.key: spec for spec in SPECS}


def effective(spec: SettingSpec, raw: str) -> str:
    """The value minidlna will actually use for a possibly-unset setting."""
    return raw if raw else spec.default


def normalise(spec: SettingSpec, value: str) -> str:
    """Canonical text for comparing and writing a value."""
    value = (value or "").strip()
    if spec.kind == "bool" and value:
        return "yes" if value.lower() in ("yes", "true", "1", "on") else "no"
    return value
