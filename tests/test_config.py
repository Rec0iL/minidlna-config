# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Tests for config parsing and writing.

Uses stdlib unittest so they run anywhere with no extra packages:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from minidlnaconfig.config import MinidlnaConfig, atomic_write  # noqa: E402
from minidlnaconfig.models import MediaFolder, MediaKind  # noqa: E402
from minidlnaconfig.service import _unescape_mount, mount_points  # noqa: E402

SAMPLE = """\
# port for HTTP traffic
port=8200

# set this to the directory you want scanned.
#   + "A" for audio  (eg. media_dir=A,/home/jmaggard/Music)
#media_dir=/tmp/commented-out
media_dir=V,/srv/video
media_dir=PV,/srv/camera
media_dir=/srv/everything

# set this if you want to customize the name
friendly_name=Living Room
inotify=yes
"""


class MediaKindTests(unittest.TestCase):
    def test_single_letters(self):
        self.assertIs(MediaKind.parse("A"), MediaKind.AUDIO)
        self.assertIs(MediaKind.parse("V"), MediaKind.VIDEO)
        self.assertIs(MediaKind.parse("P"), MediaKind.PICTURES)

    def test_empty_prefix_means_all(self):
        self.assertIs(MediaKind.parse(""), MediaKind.ALL)
        self.assertEqual(MediaKind.ALL.letters, "")

    def test_combined_flags_round_trip(self):
        # The previous version raised ValueError here and dropped the entry.
        self.assertEqual(MediaKind.parse("PV").letters, "PV")
        self.assertEqual(MediaKind.parse("AV").letters, "AV")

    def test_case_insensitive(self):
        self.assertIs(MediaKind.parse("v"), MediaKind.VIDEO)

    def test_unknown_letter_rejected(self):
        with self.assertRaises(ValueError):
            MediaKind.parse("X")

    def test_labels(self):
        self.assertEqual(MediaKind.parse("PV").label, "Pictures + Video")
        self.assertEqual(MediaKind.ALL.label, "All media")


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.config = self._load(SAMPLE)

    def _load(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return MinidlnaConfig.load(handle.name)

    def test_reads_all_entries(self):
        self.assertEqual(len(self.config.folders), 3)

    def test_ignores_commented_entries(self):
        paths = [folder.path for folder in self.config.folders]
        self.assertNotIn("/tmp/commented-out", paths)

    def test_parses_flags(self):
        self.assertEqual(self.config.folders[0].kinds, MediaKind.VIDEO)
        self.assertEqual(self.config.folders[1].kinds.letters, "PV")
        self.assertIs(self.config.folders[2].kinds, MediaKind.ALL)

    def test_reads_other_settings(self):
        self.assertEqual(self.config.port, 8200)
        self.assertEqual(self.config.friendly_name, "Living Room")
        self.assertEqual(self.config.db_dir, "/var/cache/minidlna")

    def test_path_containing_a_comma(self):
        config = self._load("media_dir=/srv/Rock, Paper & Scissors\n")
        self.assertEqual(len(config.folders), 1)
        self.assertEqual(config.folders[0].path, "/srv/Rock, Paper & Scissors")

    def test_missing_file_is_not_an_error(self):
        config = MinidlnaConfig.load("/nonexistent/minidlna.conf")
        self.assertFalse(config.exists)
        self.assertEqual(config.folders, [])


class RenderTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
        handle.write(SAMPLE)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        self.path = handle.name
        self.config = MinidlnaConfig.load(self.path)

    def test_unchanged_render_is_byte_identical(self):
        self.assertEqual(self.config.render(), SAMPLE)

    def test_preserves_comments_and_other_settings(self):
        output = self.config.render([MediaFolder(MediaKind.AUDIO, "/srv/music")])
        self.assertIn("# port for HTTP traffic", output)
        self.assertIn("friendly_name=Living Room", output)
        self.assertIn("inotify=yes", output)
        self.assertIn('#   + "A" for audio', output)

    def test_replaces_entries_in_place(self):
        output = self.config.render([MediaFolder(MediaKind.AUDIO, "/srv/music")])
        self.assertEqual(output.count("media_dir=A,/srv/music"), 1)
        self.assertNotIn("media_dir=V,/srv/video", output)
        # The commented-out example must survive untouched.
        self.assertIn("#media_dir=/tmp/commented-out", output)

    def test_new_block_lands_where_the_old_one_was(self):
        output = self.config.render([MediaFolder(MediaKind.AUDIO, "/srv/music")])
        lines = output.splitlines()
        self.assertLess(lines.index("media_dir=A,/srv/music"),
                        lines.index("friendly_name=Living Room"))

    def test_all_media_written_without_prefix(self):
        output = self.config.render([MediaFolder(MediaKind.ALL, "/srv/all")])
        self.assertIn("media_dir=/srv/all\n", output)

    def test_removing_every_folder_keeps_the_rest_of_the_file(self):
        output = self.config.render([])
        self.assertNotIn("media_dir=V", output)
        self.assertIn("friendly_name=Living Room", output)
        self.assertIn("port=8200", output)

    def test_appends_when_file_has_no_entries(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
        handle.write("port=8200\nfriendly_name=Test\n")
        handle.close()
        self.addCleanup(os.unlink, handle.name)

        config = MinidlnaConfig.load(handle.name)
        output = config.render([MediaFolder(MediaKind.VIDEO, "/srv/video")])
        self.assertIn("media_dir=V,/srv/video", output)
        self.assertIn("port=8200", output)
        self.assertTrue(output.endswith("\n"))

    def test_output_always_ends_with_a_newline(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
        handle.write("port=8200")           # no trailing newline
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        config = MinidlnaConfig.load(handle.name)
        self.assertTrue(config.render([]).endswith("\n"))


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.target = os.path.join(self.directory, "minidlna.conf")
        with open(self.target, "w") as handle:
            handle.write("original\n")

    def test_replaces_content(self):
        atomic_write(self.target, "updated\n")
        with open(self.target) as handle:
            self.assertEqual(handle.read(), "updated\n")

    def test_makes_a_backup(self):
        backup = atomic_write(self.target, "updated\n")
        self.assertTrue(os.path.exists(backup))
        with open(backup) as handle:
            self.assertEqual(handle.read(), "original\n")

    def test_preserves_permissions(self):
        os.chmod(self.target, 0o640)
        atomic_write(self.target, "updated\n")
        self.assertEqual(os.stat(self.target).st_mode & 0o777, 0o640)

    def test_leaves_no_temporary_files_behind(self):
        atomic_write(self.target, "updated\n", backup=False)
        leftovers = [n for n in os.listdir(self.directory) if n.startswith(".minidlna-conf-")]
        self.assertEqual(leftovers, [])

    def test_creates_a_new_file_when_missing(self):
        target = os.path.join(self.directory, "new.conf")
        backup = atomic_write(target, "fresh\n")
        self.assertEqual(backup, "")
        self.assertTrue(os.path.exists(target))


MOUNTS = """\
/dev/sda3 / btrfs rw,relatime 0 0
proc /proc proc rw,nosuid 0 0
tmpfs /run tmpfs rw,nosuid 0 0
/dev/sdb1 /home btrfs rw,relatime 0 0
/dev/sda2 /boot vfat rw,relatime 0 0
/dev/sda1 /boot/efi vfat rw,relatime 0 0
/dev/loop0 /var/lib/snapd/snap/ngrok/424 squashfs ro,nodev 0 0
/dev/sde1 /run/media/user/NVME ext4 rw,nosuid 0 0
/dev/sdd1 /run/media/user/My\\040Drive btrfs rw,nosuid 0 0
tmpfs /run/user/1000 tmpfs rw,nosuid 0 0
"""


class MountPointTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile("w", delete=False)
        handle.write(MOUNTS)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        self.path = handle.name

    def _paths(self):
        # Only directories that exist are returned, so compare against the
        # parser's view of the fixture rather than the live filesystem.
        return [path for _label, path in mount_points(self.path)]

    def test_octal_escapes_are_decoded(self):
        self.assertEqual(_unescape_mount(r"/run/media/user/My Drive"),
                         "/run/media/user/My Drive")

    def test_backslash_without_an_escape_survives(self):
        self.assertEqual(_unescape_mount(r"/srv/a"), r"/srv/a")

    def test_pseudo_filesystems_are_excluded(self):
        for path in ("/proc", "/run", "/run/user/1000"):
            self.assertNotIn(path, self._paths())

    def test_boot_and_snap_mounts_are_excluded(self):
        for path in ("/boot", "/boot/efi", "/var/lib/snapd/snap/ngrok/424"):
            self.assertNotIn(path, self._paths())

    def test_real_mounts_are_kept(self):
        # These exist on any Linux system, so they survive the isdir() filter.
        paths = self._paths()
        self.assertIn("/", paths)
        self.assertIn("/home", paths)

    def test_root_sorts_first(self):
        paths = self._paths()
        self.assertEqual(paths[0], "/")

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(mount_points("/nonexistent/mounts"), [])


if __name__ == "__main__":
    unittest.main()
