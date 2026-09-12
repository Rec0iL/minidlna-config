# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Tests for reading minidlna's media index.

Builds a database with the same shape minidlna uses, so no real server or
scanned library is needed:

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from minidlnaconfig import library  # noqa: E402

MB = 1024 ** 2

#: (path, title, size, duration, resolution, mime)
ROWS = [
    ("/srv/media/Movies/Northern Lights.mkv", "Northern Lights", 4000 * MB, "1:42:00.000", "1920x1080", "video/x-matroska"),
    ("/srv/media/Movies/Salt Flats.mp4", "Salt Flats", 2000 * MB, "1:05:30.000", "3840x2160", "video/mp4"),
    ("/srv/media/Music/Blue Hour.flac", "Blue Hour", 40 * MB, "0:03:57.000", "", "audio/flac"),
    ("/srv/media/Music/Slow Tide.flac", "Slow Tide", 60 * MB, "0:05:47.000", "", "audio/flac"),
    ("/srv/media/Photos/IMG_0001.jpg", "IMG_0001", 6 * MB, "", "6000x4000", "image/jpeg"),
    ("/srv/other/Stray Clip.mp4", "Stray Clip", 100 * MB, "0:00:30.000", "1280x720", "video/mp4"),
    # A directory entry: minidlna stores these alongside files, without a MIME.
    ("/srv/media/Movies", "Movies", 0, "", "", None),
]


def build_database(path: str) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE DETAILS (ID INTEGER PRIMARY KEY AUTOINCREMENT, PATH TEXT, "
        "SIZE INTEGER, TIMESTAMP INTEGER, TITLE TEXT, DURATION TEXT, BITRATE INTEGER, "
        "RESOLUTION TEXT, MIME TEXT)"
    )
    now = int(time.time())
    for index, (path_, title, size, duration, resolution, mime) in enumerate(ROWS):
        con.execute(
            "INSERT INTO DETAILS (PATH, SIZE, TIMESTAMP, TITLE, DURATION, RESOLUTION, MIME)"
            " VALUES (?,?,?,?,?,?,?)",
            (path_, size, now - index * 3600, title, duration, resolution, mime),
        )
    con.commit()
    con.close()


class FormattingTests(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(library.format_size(0), "0 B")
        self.assertEqual(library.format_size(512), "512 B")
        self.assertEqual(library.format_size(1024), "1.0 KiB")
        self.assertEqual(library.format_size(1536), "1.5 KiB")
        self.assertEqual(library.format_size(5 * 1024 ** 3), "5.0 GiB")

    def test_negative_size_is_not_an_error(self):
        self.assertEqual(library.format_size(-1), "0 B")

    def test_durations(self):
        self.assertEqual(library.format_duration("0:03:57.000"), "3:57")
        self.assertEqual(library.format_duration("1:42:00.000"), "1:42:00")
        self.assertEqual(library.format_duration(""), "")

    def test_unparseable_duration_passes_through(self):
        self.assertEqual(library.format_duration("unknown"), "unknown")

    def test_counts_are_pluralised(self):
        self.assertEqual(library.format_count(1, "file"), "1 file")
        self.assertEqual(library.format_count(2, "file"), "2 files")
        self.assertEqual(library.format_count(1234, "file"), "1,234 files")


class StatsTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        self.path = handle.name
        build_database(self.path)
        self.folders = ["/srv/media/Movies", "/srv/media/Music", "/srv/media/Photos"]

    def test_counts_only_files_not_directory_entries(self):
        stats = library.read_stats(self.path, self.folders)
        self.assertTrue(stats.available)
        self.assertEqual(stats.files, 6)          # seven rows, one is a directory

    def test_totals_by_kind(self):
        stats = library.read_stats(self.path, self.folders)
        self.assertEqual(stats.kinds["video"][0], 3)
        self.assertEqual(stats.kinds["audio"][0], 2)
        self.assertEqual(stats.kinds["image"][0], 1)

    def test_size_totals(self):
        stats = library.read_stats(self.path, self.folders)
        self.assertEqual(stats.kinds["audio"][1], 100 * MB)

    def test_per_folder_breakdown(self):
        stats = library.read_stats(self.path, self.folders)
        by_path = {folder.path: folder for folder in stats.folders}
        self.assertEqual(by_path["/srv/media/Movies"].files, 2)
        self.assertEqual(by_path["/srv/media/Music"].files, 2)
        self.assertEqual(by_path["/srv/media/Photos"].files, 1)

    def test_a_folder_with_nothing_indexed_reports_zero(self):
        stats = library.read_stats(self.path, self.folders + ["/srv/media/Empty"])
        by_path = {folder.path: folder for folder in stats.folders}
        self.assertEqual(by_path["/srv/media/Empty"].files, 0)

    def test_trailing_slash_is_handled(self):
        stats = library.read_stats(self.path, ["/srv/media/Movies/"])
        self.assertEqual(stats.folders[0].files, 2)

    def test_files_outside_configured_folders_are_counted(self):
        stats = library.read_stats(self.path, self.folders)
        self.assertEqual(stats.unmatched, 1)      # /srv/other/Stray Clip.mp4

    def test_a_folder_is_not_matched_by_a_name_prefix(self):
        # "/srv/media/Movie" must not pick up "/srv/media/Movies/...".
        stats = library.read_stats(self.path, ["/srv/media/Movie"])
        self.assertEqual(stats.folders[0].files, 0)

    def test_missing_database_is_reported_not_raised(self):
        stats = library.read_stats("/nonexistent/files.db", self.folders)
        self.assertFalse(stats.available)
        self.assertIn("no media database", stats.error)

    def test_unreadable_database_is_reported_not_raised(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".db", delete=False)
        handle.write("this is not a database")
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        stats = library.read_stats(handle.name, [])
        self.assertFalse(stats.available)
        self.assertTrue(stats.error)

    def test_empty_database_is_available_but_empty(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        con = sqlite3.connect(handle.name)
        con.execute("CREATE TABLE DETAILS (ID INTEGER, PATH TEXT, SIZE INTEGER, "
                    "TIMESTAMP INTEGER, TITLE TEXT, DURATION TEXT, RESOLUTION TEXT, MIME TEXT)")
        con.commit(); con.close()
        stats = library.read_stats(handle.name, [])
        self.assertTrue(stats.available)
        self.assertTrue(stats.is_empty)

    def test_the_connection_cannot_write(self):
        # The index belongs to minidlna; a bug here must never corrupt it.
        con = library._connect(self.path)
        with self.assertRaises(sqlite3.OperationalError):
            con.execute("INSERT INTO DETAILS (PATH, MIME) VALUES ('/x', 'video/mp4')")
        con.close()

    def test_database_path_is_derived_from_the_cache_directory(self):
        self.assertEqual(library.database_path("/var/cache/minidlna"),
                         "/var/cache/minidlna/files.db")
        self.assertEqual(library.database_path(""), "/var/cache/minidlna/files.db")


class SearchTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        self.path = handle.name
        build_database(self.path)

    def test_returns_every_file_by_default(self):
        items, total = library.search_items(self.path)
        self.assertEqual(total, 6)
        self.assertEqual(len(items), 6)

    def test_filter_by_kind(self):
        items, total = library.search_items(self.path, kind="audio")
        self.assertEqual(total, 2)
        self.assertTrue(all(item.kind == "audio" for item in items))

    def test_filter_by_folder(self):
        _items, total = library.search_items(self.path, folder="/srv/media/Movies")
        self.assertEqual(total, 2)

    def test_search_matches_title(self):
        items, total = library.search_items(self.path, query="Blue")
        self.assertEqual(total, 1)
        self.assertEqual(items[0].title, "Blue Hour")

    def test_search_matches_path(self):
        _items, total = library.search_items(self.path, query="/srv/media/Photos/")
        self.assertEqual(total, 1)

    def test_search_is_case_insensitive(self):
        _items, total = library.search_items(self.path, query="blue hour")
        self.assertEqual(total, 1)

    def test_wildcards_in_the_query_are_literal(self):
        # A bare "%" would otherwise match everything.
        _items, total = library.search_items(self.path, query="%")
        self.assertEqual(total, 0)

    def test_underscore_in_the_query_is_literal(self):
        # "_" is a single-character wildcard in LIKE; IMG_0001 must match by name.
        _items, total = library.search_items(self.path, query="IMG_0001")
        self.assertEqual(total, 1)
        _items, total = library.search_items(self.path, query="IMG_000X")
        self.assertEqual(total, 0)

    def test_limit_caps_rows_but_not_the_reported_total(self):
        items, total = library.search_items(self.path, limit=2)
        self.assertEqual(len(items), 2)
        self.assertEqual(total, 6)

    def test_order_by_size(self):
        items, _total = library.search_items(self.path, order="size")
        sizes = [item.size for item in items]
        self.assertEqual(sizes, sorted(sizes, reverse=True))

    def test_order_by_name(self):
        items, _total = library.search_items(self.path, order="name")
        titles = [item.title.lower() for item in items]
        self.assertEqual(titles, sorted(titles))

    def test_missing_database_returns_nothing(self):
        self.assertEqual(library.search_items("/nonexistent/files.db"), ([], 0))

    def test_item_exposes_kind_and_folder(self):
        items, _total = library.search_items(self.path, query="Northern")
        self.assertEqual(items[0].kind, "video")
        self.assertEqual(items[0].folder, "/srv/media/Movies")


if __name__ == "__main__":
    unittest.main()
