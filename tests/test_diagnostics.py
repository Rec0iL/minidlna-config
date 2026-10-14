# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Tests for log analysis, the disk-vs-index check, devices and backups.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from minidlnaconfig import backups, devices, health, journal  # noqa: E402
from minidlnaconfig.config import MinidlnaConfig  # noqa: E402
from minidlnaconfig.models import MediaKind  # noqa: E402


def entries(*messages: str):
    return [journal.parse_message(m, 1000.0 + i) for i, m in enumerate(messages)]


class ParseTests(unittest.TestCase):
    def test_splits_level_source_and_text(self):
        e = journal.parse_message("metadata.c:715: warn: Opening /a/b.ts failed! [Invalid data]")
        self.assertEqual((e.level, e.source), ("warn", "metadata.c:715"))
        self.assertTrue(e.text.startswith("Opening /a/b.ts"))

    def test_file_log_timestamp_prefix(self):
        e = journal.parse_message("[2026/10/10 16:04:03] minidlna.c:669: error: Media directory x")
        self.assertEqual(e.level, "error")
        self.assertGreater(e.time, 0)

    def test_non_minidlna_lines_are_informational(self):
        e = journal.parse_message("Started minidlna.service - MiniDLNA.")
        self.assertEqual((e.level, e.source), ("info", ""))

    def test_journal_json(self):
        line = json.dumps({"MESSAGE": "minidlna.c:1: warn: hi", "__REALTIME_TIMESTAMP": "5000000", "_PID": "42"})
        parsed = journal.parse_journal_json([line, "not json", json.dumps({"MESSAGE": 5})])
        self.assertEqual(len(parsed), 1)
        self.assertEqual((parsed[0].time, parsed[0].pid), (5.0, 42))


class AnalyseTests(unittest.TestCase):
    def test_groups_unreadable_files_once_each(self):
        log = entries(
            "metadata.c:715: warn: Opening /m/a/x.ts failed! [Invalid data found]",
            "scanner.c:490: warn: Unsuccessful getting details for /m/a/x.ts",
            "metadata.c:715: warn: Opening /m/b/y.ts failed! [Invalid data found]",
            "metadata.c:715: warn: Opening /m/b/z.avi failed! [Invalid data found]",
        )
        problems = [p for p in journal.analyse(log) if p.kind == "unparseable"]
        self.assertEqual(sorted(p.count for p in problems), [1, 2])
        ts = next(p for p in problems if ".ts" in p.title)
        self.assertEqual(ts.paths, ["/m/a/x.ts", "/m/b/y.ts"])
        self.assertIn("TypeScript", ts.hint)

    def test_inaccessible_folder_and_resolution(self):
        log = entries('minidlna.c:669: error: Media directory "V,/mnt/gone" not accessible [No such file or directory]')
        missing = journal.analyse(log, exists=lambda p: False)[0]
        self.assertEqual((missing.kind, missing.severity, missing.resolved), ("unreachable", "error", False))
        back = journal.analyse(log, exists=lambda p: True)[0]
        self.assertTrue(back.resolved)
        self.assertEqual(back.severity, "info")

    def test_folders_no_longer_configured_are_ignored(self):
        log = entries('minidlna.c:669: error: Media directory "/old" not accessible [No such file]')
        self.assertEqual(journal.analyse(log, exists=lambda p: False, configured=["/new"]), [])

    def test_routine_lines_are_not_problems(self):
        log = entries(
            "minidlna.c:1134: warn: Starting MiniDLNA version 1.3.3.",
            "minidlna.c:1182: warn: HTTP listening on port 8200",
            "minidlna.c:398: warn: Removed media_dir detected; rebuilding...",
            "minissdp.c:773: warn: Unknown udp packet received from 192.168.0.33:1234",
        )
        self.assertEqual(journal.analyse(log), [])

    def test_known_causes_have_hints(self):
        log = entries(
            "upnphttp.c:938: error: DNS rebinding attack suspected (Host: localhost:8200)",
            "upnpsoap.c:127: warn: BrowseContentDirectory Returning UPnPError 701: No such object error",
        )
        kinds = {p.kind for p in journal.analyse(log)}
        self.assertEqual(kinds, {"rebinding", "stale_item"})

    def test_unknown_errors_group_by_message_shape(self):
        log = entries("x.c:1: error: Failed on item 12", "x.c:2: error: Failed on item 99")
        problems = journal.analyse(log)
        self.assertEqual((len(problems), problems[0].count, problems[0].severity), (1, 2, "error"))

    def test_current_run_starts_at_last_start(self):
        log = entries("a.c:1: warn: old", "minidlna.c:1: warn: Starting MiniDLNA version 1.3.3.", "a.c:2: warn: new")
        self.assertEqual([e.text for e in journal.current_run(log)][-1], "new")
        self.assertEqual(len(journal.current_run(log)), 2)

    def test_ssdp_senders_and_skipped_directories(self):
        log = entries(
            "minissdp.c:773: warn: Unknown udp packet received from 10.0.0.5:1",
            "minissdp.c:773: warn: Unknown udp packet received from 10.0.0.5:2",
            'minidlna.c:669: error: Media directory "PV,/a/b/" not accessible [x]',
        )
        self.assertEqual(journal.ssdp_senders(log)["10.0.0.5"][0], 2)
        self.assertEqual(journal.skipped_directories(log), ["/a/b"])


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        for name in ("ok.mkv", "bad.ts", "missed.mp4"):
            with open(os.path.join(self.root, name), "w"):
                pass

    def test_classifies_every_file(self):
        indexed = {os.path.join(self.root, "ok.mkv"), os.path.join(self.root, "gone.mkv")}
        failed = {os.path.join(self.root, "bad.ts"): "Invalid data"}
        [result] = health.compare([(self.root, MediaKind.VIDEO)], indexed, failed)
        self.assertEqual(
            (result.on_disk, result.indexed, result.unparseable, result.not_indexed, result.stale),
            (3, 1, 1, 1, 1),
        )
        self.assertEqual(result.reasons, {"Invalid data": 1})
        self.assertEqual(result.missed_extensions, {".mp4": 1})
        self.assertFalse(result.ok)

    def test_missing_folder(self):
        [result] = health.compare([("/nonexistent/x", MediaKind.ALL)], set())
        self.assertFalse(result.exists)
        self.assertFalse(result.ok)

    def test_reads_index(self):
        path = os.path.join(self.root, "files.db")
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE DETAILS (PATH TEXT, MIME TEXT)")
        con.execute("INSERT INTO DETAILS VALUES ('/a.mkv', 'video/x-matroska')")
        con.execute("INSERT INTO DETAILS VALUES ('/dir', NULL)")
        con.commit()
        con.close()
        self.assertEqual(health.indexed_paths(path), {"/a.mkv"})
        self.assertIsNone(health.indexed_paths("/nonexistent/files.db"))


STATUS_HTML = (
    "<HTML><BODY><h2>MiniDLNA status</h2><h3>Media library</h3><table>"
    "<tr><td>Audio files</td><td>3</td></tr><tr><td>Video files</td><td>920</td></tr></table>"
    "<h3>Connected clients</h3><table>"
    "<tr><td>ID</td><td>Type</td><td>IP Address</td><td>HW Address</td><td>Connections</td></tr>"
    "<tr><td>0</td><td>Samsung TV</td><td>192.168.0.33</td><td>AA:BB:CC:DD:EE:FF</td><td>2</td></tr>"
    "<tr><td>1</td><td>Unknown</td><td>127.0.0.1</td><td>FF:FF:FF:FF:FF:FF</td><td>0</td></tr>"
    "</table></BODY></HTML>"
)

SS_OUTPUT = (
    "Recv-Q Send-Q Local Address:Port Peer Address:Port\n"
    "0      0      192.168.0.153:8200   192.168.0.33:51234\n"
    "\t cubic wscale:6,10 rto:203 bytes_sent:1468012 bytes_acked:1468013 send 42200364bps\n"
    "0      0      192.168.0.153:8200   192.168.0.33:51240\n"
    "\t cubic bytes_sent:100 bytes_acked:100\n"
)


class DeviceTests(unittest.TestCase):
    def test_status_page(self):
        page = devices.parse_status_page(STATUS_HTML)
        self.assertEqual(page.counts, {"audio files": 3, "video files": 920})
        self.assertEqual(page.clients[0], ("Samsung TV", "192.168.0.33", "AA:BB:CC:DD:EE:FF", 2))

    def test_ss(self):
        connections = devices.parse_ss(SS_OUTPUT)
        self.assertEqual([(c.peer, c.bytes_sent) for c in connections],
                         [("192.168.0.33", 1468012), ("192.168.0.33", 100)])

    def test_merge_prefers_known_information_and_hides_loopback(self):
        merged = devices.merge(
            devices.parse_status_page(STATUS_HTML),
            devices.parse_ss(SS_OUTPUT),
            {"192.168.0.33": (14, 5.0), "192.168.0.99": (1, 9.0)},
            {"192.168.0.99": "11:22:33:44:55:66"},
        )
        self.assertEqual([d.ip for d in merged], ["192.168.0.33", "192.168.0.99"])
        tv = merged[0]
        self.assertEqual((tv.kind, tv.bytes_sent, tv.announced, tv.streaming), ("Samsung TV", 1468112, 14, True))
        self.assertEqual(merged[1].mac, "11:22:33:44:55:66")

    def test_format_rate(self):
        self.assertEqual(devices.format_rate(12), "12 B/s")
        self.assertEqual(devices.format_rate(3_200_000), "3.2 MB/s")


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        self.conf = os.path.join(self.dir, "minidlna.conf")
        for stamp, text in (("20260101-010101", "port=1\n"), ("20260301-010101", "port=2\n")):
            with open(f"{self.conf}.bak-{stamp}", "w") as handle:
                handle.write(text)
        with open(self.conf + ".bak-notes", "w") as handle:     # not a stamped backup
            handle.write("x")

    def test_lists_newest_first(self):
        found = backups.list_backups(self.conf)
        self.assertEqual([b.name[-15:] for b in found[:2]], ["20260301-010101", "20260101-010101"])

    def test_diff_and_summary(self):
        current = "port=8200\nfriendly_name=A\nmedia_dir=/a\n"
        other = "port=8201\nfriendly_name=A\nmedia_dir=/a\nmedia_dir=/b\n"
        kinds = [k for k, _ in backups.diff_lines(current, other)]
        self.assertEqual((kinds.count("add"), kinds.count("del")), (2, 1))
        self.assertEqual(backups.change_summary(current, other), (2, 1))
        self.assertEqual(backups.change_summary(current, current), (0, 0))


class SettingsTests(unittest.TestCase):
    TEXT = "# server\nport=8200\ninotify=yes\n#friendly_name=old\nmedia_dir=V,/a\nmedia_dir=/b\n"

    def make(self):
        config = MinidlnaConfig()
        config.lines = self.TEXT.splitlines(keepends=True)
        config._parse()
        return config

    def test_unchanged_settings_do_not_touch_the_file(self):
        config = self.make()
        self.assertEqual(config.render(settings=config.setting_values()), self.TEXT)

    def test_replace_add_and_remove(self):
        out = self.make().render(settings={"port": "9000", "friendly_name": "Den", "inotify": ""})
        self.assertIn("port=9000\n", out)
        self.assertNotIn("inotify=", out)
        self.assertIn("friendly_name=Den\n", out)
        self.assertIn("#friendly_name=old\n", out)          # comments are left alone
        self.assertLess(out.index("friendly_name=Den"), out.index("media_dir="))

    def test_booleans_are_normalised(self):
        out = self.make().render(settings={"strict_dlna": "true"})
        self.assertIn("strict_dlna=yes\n", out)

    def test_unknown_keys_are_ignored(self):
        self.assertEqual(self.make().render(settings={"nope": "x"}), self.TEXT)

    def test_adopt(self):
        config = self.make()
        config.adopt("media_dir=/z\nport=1\n")
        self.assertEqual([f.path for f in config.folders], ["/z"])
        self.assertEqual(config.port, 1)


if __name__ == "__main__":
    unittest.main()
