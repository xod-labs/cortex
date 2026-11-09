"""The grammar of cortex.toml and cortex.local.toml — ADR-008 §3.4, one definition for the
command and the runtime — of cortex.lock, ADR-010 §3.2, and of projects.toml, ADR-011 §3.1."""

import os
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
        project.check_local({"version": "1.9.0-rc.1"})                # a developer's try (ADR-013 §3.3)
        with self.assertRaisesRegex(project.ProjectFileError, 'version "9" is no version'):
            project.check_local({"version": "9"})
        with self.assertRaisesRegex(project.ProjectFileError, 'unknown key "pinned"'):
            project.check_local({"pinned": "1.0.0"})

    def test_synced(self):
        # ADR-013 §3.1: what `cortex sync` records — each file's version, the team's, the one synced.
        project.check_local({"synced": {"team": "1.8.0", "project": "1.7.0", "pinned": "1.8.0", "reads": "1.8.0"}})
        project.check_local({"synced": {"pinned": "1.8.0", "reads": ""}})           # a checkout's
        for synced, fragment in (("1.8.0", "must be a table"), ({"pinned": "1.8.0"}, '"reads" is required'),
                                 ({"reads": "1.8.0"}, '"pinned" is required'),
                                 ({"pinned": "1.8.0", "reads": "1.8.0", "when": "now"}, 'unknown key "when"'),
                                 ({"pinned": "", "reads": "1.8.0"}, 'pinned "" is no version'),
                                 ({"pinned": "1.8.0", "reads": "1.8.0", "local": "x"}, 'local "x" is no version')):
            with self.subTest(synced=synced):
                with self.assertRaisesRegex(project.ProjectFileError, fragment):
                    project.check_local({"synced": synced})


class TeamFileTests(unittest.TestCase):
    NAME = "agents/cortex.team.toml"

    def test_a_team_s_file(self):
        project.check_team({"version": "1.8.0", "theme": "h2g2", "sync": "link", "claude_access": False,
                            "locked": ["claude_access", "theme"]})
        project.check_team({})                                       # none required
        project.check_team({"version": "2.0.0-rc.1"})
        for version in ("1.8.0-rc.1", "1.8.0-dev.3"):                 # a pre-release has its release's features
            project.check_team({"version": version})

    def test_what_it_refuses(self):
        for data, fragment in (
                ({"spec": "/x"}, 'unknown key "spec"'),
                ({"version": "1.7.0"}, 'version "1.7.0" is before 1.8.0'),
                ({"version": "1.7.1-dev"}, 'version "1.7.1-dev" is before 1.8.0'),     # one of 1.7.1
                ({"theme": "a b"}, "is no theme name"),
                ({"sync": "rsync"}, "is none of"),
                ({"locked": "theme", "theme": "h2g2"}, '"locked" must be a list'),
                ({"locked": ["version"], "version": "1.8.0"}, '"version" is never locked'),
                ({"locked": ["locked"]}, 'locked names "locked", which no lock takes'),
                ({"locked": ["theme"]}, 'locked names "theme", which the file does not set'),
                ({"locked": ["theme", "theme"], "theme": "h2g2"}, 'locked names "theme" twice'),
                ({"locked": ["claude_access"], "claude_access": True}, "claude_access is locked to false only")):
            with self.subTest(data=data):
                with self.assertRaisesRegex(project.ProjectFileError, fragment):
                    project.check_team(data, self.NAME)

    def test_versions_in_semantic_versioning_s_order(self):
        order = ["1.7.0", "1.8.0-alpha", "1.8.0-alpha.1", "1.8.0-alpha.beta", "1.8.0-rc.1", "1.8.0-rc.10", "1.8.0",
                 "1.8.1", "1.10.0"]
        self.assertEqual(sorted(reversed(order), key=project.version_key), order)

    def test_a_pre_release_has_its_release_s_features(self):
        # A release candidate, a checkout's, a development build's: 1.8.0's, which reads the team's file.
        for text in ("1.8.0-rc.1", "1.8.0-alpha", "1.8.0-dev", "1.8.0-dev.7", "1.8.0", "1.8.1-dev", "1.9.0-dev.3",
                     "2.0.0-dev", "10.0.0"):
            with self.subTest(text=text):
                self.assertFalse(project.before_team(text))
        for text in ("1.7.0", "1.7.1-dev", "1.7.99-dev.4", "1.7.99", "0.0.0-dev", "0.0.0-dev.7", "0.9.0-rc.1"):
            with self.subTest(text=text):
                self.assertTrue(project.before_team(text))


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


class RegistryTests(unittest.TestCase):
    """projects.toml: format 1, and [[project]] tables of a name and an absolute path."""

    ROOT = os.path.abspath(os.sep)          # "/" here, the drive's root on Windows

    def path(self, *parts):
        return os.path.join(self.ROOT, "code", *parts)

    def registry(self, *entries, **top):
        return {"format": 1, **top, "project": [dict(entry) for entry in entries]}

    def test_a_registry(self):
        project.check_registry({"format": 1})                     # no project yet
        project.check_registry(self.registry({"name": "acme", "path": self.path("acme")},
                                             {"name": "xod-labs", "path": self.path("xod-labs")}))

    def test_names(self):
        for name in ("a", "0", "acme", "a.b_c-d", "x" * 64, "9lives"):
            with self.subTest(name=name):
                self.assertTrue(project.is_project_name(name))
        # Case alone never tells two apart; a name is ASCII, matched whole.
        for name in ("Acme", "-acme", ".acme", "_acme", "x" * 65, "a b", "a/b", "acme\n", "", "caf\u00e9", None, 1):
            with self.subTest(name=name):
                self.assertFalse(project.is_project_name(name))

    def test_what_a_registry_refuses(self):
        acme = {"name": "acme", "path": self.path("acme")}
        for data, fragment in (
                ({"project": [acme]}, 'projects.toml: "format" is required'),
                (self.registry(acme, extra="x"), 'unknown key "extra" — projects.toml takes format and project only'),
                ({"format": "1"}, '"format" must be a positive integer'),
                ({"format": True}, '"format" must be a positive integer'),
                ({"format": 0}, '"format" must be a positive integer'),
                ({"format": 1, "project": acme}, '"project" must be tables, each headed \\[\\[project\\]\\]'),
                ({"format": 1, "project": ["acme"]}, '"project" must be tables'),
                (self.registry(acme, {**acme, "name": "b", "theme": "h2g2"}),
                 'project 2: unknown key "theme" — a project takes name and path only'),
                (self.registry({"path": self.path("acme")}), 'project 1: "name" is required'),
                (self.registry({"name": "acme"}), 'project 1: "path" is required'),
                (self.registry({**acme, "name": 1}), 'project 1: "name" must be a string'),
                (self.registry({**acme, "path": ["x"]}), 'project 1: "path" must be a string'),
                (self.registry({**acme, "name": "Acme"}), 'project 1: name "Acme" is no project name — lowercase'),
                (self.registry({**acme, "path": "code/acme"}), 'project 1: path "code/acme" is not absolute'),
                (self.registry({**acme, "path": ""}), 'project 1: path "" is not absolute'),
                (self.registry({**acme, "path": self.path("a\x00b")}),
                 'project 1: path ".*a\\\\u0000b" holds a control character — projects.toml takes none in a path'),
                (self.registry({**acme, "path": self.path("a\nb")}), 'project 1: path .* holds a control character'),
                (self.registry({**acme, "path": self.path("a\x7fb")}), 'project 1: path .* holds a control character'),
                (self.registry({**acme, "path": self.path("a\tb")}), 'project 1: path .* holds a control character'),
                (self.registry({**acme, "path": self.path("a\x1b[2Jb")}),
                 'project 1: path ".*a\\\\u001b\\[2Jb" holds a control character'),
                (self.registry(acme, {**acme, "path": self.path("other")}),
                 'project 2: name "acme" is project 1\'s too — two projects never share a name')):
            with self.subTest(data=data):
                with self.assertRaisesRegex(project.ProjectFileError, fragment):
                    project.check_registry(data)

    def test_two_entries_never_write_the_same_directory(self):
        # Read from the text: a final separator, ".", ".." and, on Windows, case.
        acme = self.path("acme")
        same = [acme + os.sep, self.path(".", "acme"), self.path("x", "..", "acme")]
        if os.path.normcase("A") == "a":
            same.append(acme.upper())
        for other in same:
            with self.subTest(other=other):
                with self.assertRaisesRegex(project.ProjectFileError,
                                            "project 2: path .* names the directory of project 1, .* — two projects "
                                            "never name the same directory"):
                    project.check_registry(self.registry({"name": "a", "path": acme}, {"name": "b", "path": other}))
        project.check_registry(self.registry({"name": "a", "path": acme}, {"name": "b", "path": self.path("acme-2")}))

    def test_the_messages_name_the_file_as_its_reader_does(self):
        with self.assertRaises(project.ProjectFileError) as caught:
            project.check_registry({"format": 1, "extra": 1}, "/home/dev/.cortex/projects.toml")
        self.assertTrue(str(caught.exception).startswith('/home/dev/.cortex/projects.toml: unknown key "extra"'))

    def test_a_later_format_is_its_own_error_whatever_it_holds(self):
        # A later format may add keys: it is said to be later, not to hold unknown keys.
        for data in ({"format": 2}, {"format": 2, "project": [{"name": "Acme", "port": 8080}], "runtime": {}}):
            with self.subTest(data=data):
                with self.assertRaisesRegex(project.LaterFormatError,
                                            "projects.toml: format 2 is written by a later Cortex — this one reads "
                                            "format 1"):
                    project.check_registry(data)

    def test_a_value_is_never_printed_raw(self):
        for data in (self.registry({"name": "\x1b[2Jred", "path": self.path("a")}),
                     self.registry({"name": "a", "path": "\x1b]0;x\x07"}),
                     {"format": 1, "\x1b[31mkey": "x"}):
            with self.subTest(data=data):
                with self.assertRaises(project.ProjectFileError) as caught:
                    project.check_registry(data)
                self.assertNotIn("\x1b", str(caught.exception))
                self.assertIn("\\u001b", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
