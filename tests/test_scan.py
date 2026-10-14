# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Tests for scan detection and progress estimation.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from minidlnaconfig import scan  # noqa: E402
from minidlnaconfig.models import MediaKind  # noqa: E402


def touch(root: str, *parts: str) -> None:
    path = os.path.join(root, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w"):
        pass


class ScannerPidTests(unittest.TestCase):
    def fake_proc(self, entries):
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        for pid, stat in entries.items():
            os.makedirs(os.path.join(root, str(pid)))
            with open(os.path.join(root, str(pid), "stat"), "w") as handle:
                handle.write(stat)
        os.makedirs(os.path.join(root, "sys"))   # non-numeric entries are ignored
        return root

    def test_finds_children_of_the_service(self):
        proc = self.fake_proc({
            100: "100 (minidlnad) S 1 100 100 0 -1",
            101: "101 (minidlnad) R 100 100 100 0 -1",
            200: "200 (bash) S 1 200 200 0 -1",
        })
        self.assertEqual(scan.scanner_pids(100, proc), [101])

    def test_process_name_with_spaces_and_parentheses(self):
        proc = self.fake_proc({101: "101 (my (odd) name) R 100 100 100 0 -1"})
        self.assertEqual(scan.scanner_pids(100, proc), [101])

    def test_no_children_means_not_scanning(self):
        proc = self.fake_proc({100: "100 (minidlnad) S 1 100 100 0 -1"})
        self.assertEqual(scan.scanner_pids(100, proc), [])

    def test_unknown_or_missing_main_pid(self):
        self.assertEqual(scan.scanner_pids(0), [])
        self.assertEqual(scan.scanner_pids(100, "/nonexistent/proc"), [])


class CountTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        touch(self.root, "a.mkv")
        touch(self.root, "sub", "b.MP4")
        touch(self.root, "sub", "song.flac")
        touch(self.root, "sub", "deep", "pic.jpg")
        touch(self.root, "sub", "notes.txt")
        touch(self.root, ".hidden", "c.mkv")
        touch(self.root, ".dot.mkv")

    def test_counts_by_media_kind(self):
        self.assertEqual(scan.count_media_files([(self.root, MediaKind.VIDEO)]), 2)
        self.assertEqual(scan.count_media_files([(self.root, MediaKind.AUDIO)]), 1)
        self.assertEqual(scan.count_media_files([(self.root, MediaKind.ALL)]), 4)

    def test_skips_hidden_entries(self):
        # .hidden/c.mkv and .dot.mkv exist but are not counted above.
        self.assertEqual(scan.count_media_files([(self.root, MediaKind.VIDEO)]), 2)

    def test_missing_folder_counts_nothing(self):
        self.assertEqual(scan.count_media_files([("/nonexistent/dir", MediaKind.ALL)]), 0)

    def test_can_be_abandoned(self):
        self.assertIsNone(
            scan.count_media_files([(self.root, MediaKind.ALL)], should_stop=lambda: True)
        )


class StateTests(unittest.TestCase):
    def test_fraction_unknown_without_an_estimate(self):
        self.assertIsNone(scan.ScanState(True, 50, None).fraction)
        self.assertIsNone(scan.ScanState(True, 50, 0).fraction)

    def test_fraction(self):
        self.assertAlmostEqual(scan.ScanState(True, 50, 200).fraction, 0.25)

    def test_never_complete_while_running(self):
        # The estimate can undershoot; the bar must not claim to be done.
        self.assertEqual(scan.ScanState(True, 500, 200).fraction, scan.MAX_RUNNING_FRACTION)


if __name__ == "__main__":
    unittest.main()
