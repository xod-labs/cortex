"""The grammar of cortex.toml and cortex.local.toml — ADR-008 §3.4, one definition for the
command and the runtime — and of cortex.lock, ADR-010 §3.2."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cortex_core import project  # noqa: E402


class VersionTests(unittest.TestCase):
    def test_versions(self):
        for text in ("1.0.0", "0.0.0-dev", "0.0.0-dev.3", "1.0.0-rc.1", "10.20.30", "1.0.0-x-y.7z"):
            with self.subTest(text=text):
                self.assertTrue(project.is_version(text))

    def test_what_is_no_version(self):
        # A trailing newline and the digits of other scripts would name another directory of the
        # store; leading zeros are no semver.
        for text in ("1.0.0\n", "1.0.0\n2", "١.0.0", "1.0.0-١", "01.0.0", "1.0.0-01", "1.0", "v1.0.0",
                     "1.0.0+build", "1.0.0 ", "", None, 1):
            with self.subTest(text=text):
                self.assertFalse(project.is_version(text))


class FileTests(unittest.TestCase):
    def test_a_project_file(self):
        project.check_project({"version": "1.0.0", "theme": "h2g2", "sync": "link"})

    def test_what_a_project_file_refuses(self):
        for data, fragment in (({"version": "1.0.0\n", "theme": "h2g2"}, 'version "1.0.0\\n" is no version'),
                               ({"version": "1.0.0", "theme": "h2g2\n"}, "is no theme name"),
                               ({"version": "1.0.0", "theme": "../x"}, "is no theme name"),
                               ({"version": "1.0.0"}, '"theme" is required'),
                               ({"version": "1.0.0", "theme": "h2g2", "sync": "bogus"}, 'sync "bogus"'),
                               ({"version": "1.0.0", "theme": "h2g2", "verison": "x"}, 'unknown key "verison"')):
            with self.subTest(data=data):
                with self.assertRaises(project.ProjectFileError) as caught:
                    project.check_project(data)
                self.assertIn(fragment, str(caught.exception))

    def test_a_value_is_never_printed_raw(self):
        # A committed cortex.toml may hold a terminal's control sequence: the message escapes it.
        for data in ({"version": "1.0.0", "theme": "\x1b[2Jred"}, {"version": "\x1b]0;x\x07", "theme": "h2g2"},
                     {"version": "1.0.0", "theme": "h2g2", "\x1b[31mkey": "x"}):
            with self.subTest(data=data):
                with self.assertRaises(project.ProjectFileError) as caught:
                    project.check_project(data)
                self.assertNotIn("\x1b", str(caught.exception))
                self.assertIn("\\u001b", str(caught.exception))

    def test_a_local_file(self):
        project.check_local({"theme": "none", "spec": "/x"})
        # sync's own keys: the developer's claude_access, and the entry it keeps for Claude Code.
        project.check_local({"spec": "/x", "claude_access": False, "claude_entry": "/home/x/.cortex/versions/1.0.0"})
        for data, fragment in (({"claude_entry": 1}, '"claude_entry" must be a string'),
                               ({"claude_entry": ""}, '"claude_entry" is empty'),
                               ({"claude_access": "yes"}, '"claude_access" must be true or false')):
            with self.subTest(data=data):
                with self.assertRaisesRegex(project.ProjectFileError, fragment):
                    project.check_local(data)
        with self.assertRaisesRegex(project.ProjectFileError, 'unknown key "claude_entry"'):
            project.check_project({"version": "1.0.0", "theme": "h2g2", "claude_entry": "/x"})
        with self.assertRaisesRegex(project.ProjectFileError, 'unknown key "version"'):
            project.check_local({"version": "1.0.0"})


class LockTests(unittest.TestCase):
    DIGEST = "sha256:" + "0123456789abcdef" * 4

    def test_a_lock(self):
        project.check_lock({"format": 1, "version": "1.4.0", "digest": self.DIGEST})

    def test_what_a_lock_refuses(self):
        good = {"format": 1, "version": "1.4.0", "digest": self.DIGEST}
        for change, fragment in (({"extra": "x"}, 'unknown key "extra" — cortex.lock takes format, version and digest only'),
                                 ({"format": "1"}, '"format" must be a positive integer'),
                                 ({"format": True}, '"format" must be a positive integer'),
                                 ({"format": 0}, '"format" must be a positive integer'),
                                 ({"version": "1.4"}, 'version "1.4" is no version'),
                                 ({"digest": self.DIGEST.upper()}, "is no digest"),
                                 ({"digest": "sha1:" + "0" * 40}, "is no digest"),
                                 ({"digest": self.DIGEST + "\n"}, "is no digest")):
            with self.subTest(change=change):
                with self.assertRaisesRegex(project.ProjectFileError, fragment):
                    project.check_lock({**good, **change})
        for key in project.LOCK_KEYS:
            with self.subTest(missing=key):
                with self.assertRaisesRegex(project.ProjectFileError, f'"{key}" is required'):
                    project.check_lock({k: v for k, v in good.items() if k != key})

    def test_a_later_format_is_its_own_error(self):
        with self.assertRaisesRegex(project.LaterFormatError, "format 2 is written by a later Cortex"):
            project.check_lock({"format": 2, "version": "1.4.0", "digest": self.DIGEST})


if __name__ == "__main__":
    unittest.main()
