"""The configuration cascade — ADR-013 §3.1: each setting, its value and its origin, one resolution
for the command and the runtime — and the machine's file, ADR-013 §3.7."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cortex_core import cascade, project  # noqa: E402

PROJECT = {"version": "1.7.0", "theme": "h2g2"}


class ResolveTests(unittest.TestCase):
    def test_a_project_of_1_7_0_reads_as_1_7_0_did(self):
        # The local theme and claude_access over cortex.toml's, sync "store" by default, claude_access unset.
        found = cascade.resolve({**PROJECT, "claude_access": True}, {"theme": "none", "spec": "x"}, {}, {})
        self.assertEqual(found.value("version"), "1.7.0")
        self.assertEqual((found["theme"].value, found["theme"].origin), ("none", project.LOCAL_FILE))
        self.assertEqual(found["theme"].overridden, ((project.PROJECT_FILE, "h2g2"),))
        self.assertEqual((found["sync"].value, found["sync"].origin), ("store", cascade.DEFAULT))
        self.assertEqual((found["claude_access"].value, found["claude_access"].origin), (True, project.PROJECT_FILE))
        self.assertIsNone(cascade.resolve(PROJECT, None, {}, {}).value("claude_access"))
        self.assertIs(cascade.resolve(PROJECT, {"claude_access": False}, {}, {}).value("claude_access"), False)
        self.assertNotIn("spec", found)                             # sync's own keys are no settings

    def test_the_team_s_resolution_leaves_cortex_local_toml_out(self):
        found = cascade.resolve(PROJECT, {"theme": "none", "claude_access": True}, {}, {}, locked=True)
        self.assertEqual((found.value("theme"), found["theme"].origin), ("h2g2", project.PROJECT_FILE))
        self.assertIsNone(found.value("claude_access"))
        self.assertTrue(found.locked)

    def test_the_machine_s_keys_the_variables_over_the_file(self):
        machine = {"releases_url": "https://mirror.example/r", "update_check": False, "register": False}
        found = cascade.resolve(PROJECT, None, machine, {}, machine_name="/h/config.toml")
        self.assertEqual((found.value("releases_url"), found["releases_url"].origin),
                         ("https://mirror.example/r", "/h/config.toml"))
        self.assertIs(found.value("update_check"), False)
        environ = {"CORTEX_RELEASES_URL": "https://other.example/r", "CORTEX_NO_UPDATE_CHECK": "1",
                   "CORTEX_NO_REGISTER": "yes"}
        found = cascade.resolve(PROJECT, None, machine, environ, machine_name="/h/config.toml")
        self.assertEqual((found.value("releases_url"), found["releases_url"].origin),
                         ("https://other.example/r", "CORTEX_RELEASES_URL"))
        self.assertEqual(found["releases_url"].overridden, (("/h/config.toml", "https://mirror.example/r"),))
        self.assertEqual((found.value("register"), found["register"].origin), (False, "CORTEX_NO_REGISTER"))

    def test_an_empty_variable_is_unset_and_none_turns_a_key_back_on(self):
        environ = {"CORTEX_RELEASES_URL": "", "CORTEX_NO_UPDATE_CHECK": "", "CORTEX_NO_REGISTER": ""}
        found = cascade.resolve(PROJECT, None, {"update_check": False}, environ, machine_name="m")
        self.assertEqual((found.value("releases_url"), found["releases_url"].origin),
                         (project.DEFAULT_RELEASES_URL, cascade.DEFAULT))
        self.assertEqual((found.value("update_check"), found["update_check"].origin), (False, "m"))
        self.assertEqual((found.value("register"), found["register"].origin), (True, cascade.DEFAULT))

    def test_a_variable_that_reads_as_no_still_turns_its_key_off(self):
        # Set and not empty is off, whatever it says — as since they were variables only: "0" or
        # "false" turns no key back on.
        for raw in ("0", "false", "no"):
            with self.subTest(raw=raw):
                found = cascade.resolve(PROJECT, None, {}, {"CORTEX_NO_UPDATE_CHECK": raw, "CORTEX_NO_REGISTER": raw})
                self.assertEqual((found.value("update_check"), found["update_check"].origin),
                                 (False, "CORTEX_NO_UPDATE_CHECK"))
                self.assertEqual((found.value("register"), found["register"].origin), (False, "CORTEX_NO_REGISTER"))

    def test_outside_a_project_the_machine_s_keys_only(self):
        found = cascade.resolve(None, None, {}, {})
        self.assertEqual(list(found.settings), list(project.MACHINE_KEYS))
        self.assertIsNone(found.committed)

    def test_the_version_is_cortex_toml_s_and_no_other_tier_s(self):
        # A local "version" is not a setting yet: it cannot win, low or high, until it is made one.
        found = cascade.resolve(PROJECT, {"version": "0.1.0"}, {"version": "9.0.0"}, {"CORTEX_VERSION": "9.0.0"})
        self.assertEqual((found.value("version"), found["version"].origin), ("1.7.0", project.PROJECT_FILE))
        self.assertEqual(found["version"].overridden, ())

    def test_the_variables_are_the_reader_s_own(self):
        found = cascade.resolve(PROJECT, None, {}, {"CORTEX_NO_REGISTER": "1", "OURS": "1"}, variables={"register": "OURS"})
        self.assertEqual((found.value("register"), found["register"].origin), (False, "OURS"))
        self.assertEqual(cascade.resolve(PROJECT, None, {}, {"CORTEX_NO_REGISTER": "1"}, variables={})["register"].origin,
                         cascade.DEFAULT)
        self.assertEqual(list(cascade.resolve(PROJECT, None, {}, {}, machine=False).settings),
                         list(cascade.PROJECT_SETTINGS))
        found = cascade.resolve(PROJECT, None, {}, {"CORTEX_THEME": "none"}, variables={"theme": "CORTEX_THEME"})
        self.assertEqual((found.value("theme"), found["theme"].origin), ("none", "CORTEX_THEME"))   # its value

    def test_committed_is_the_caller_s_answer(self):
        self.assertIs(cascade.resolve(PROJECT, None, {}, {}, committed=False).committed, False)
        self.assertIsNone(cascade.resolve(PROJECT, None, {}, {}).committed)


class MachineFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-cascade-"))
        self.path = self.tmp / "config.toml"

    def test_what_reads_and_what_only_warns(self):
        self.path.write_text('releases_url = "https://m.example/r"\nupdate_check = "no"\nregister = false\n'
                             'endpoints = "later"\n', encoding="utf-8")
        values, warnings = cascade.read_machine(str(self.path), "config.toml")
        self.assertEqual(values, {"releases_url": "https://m.example/r", "register": False})
        self.assertEqual(len(warnings), 2)
        self.assertIn('config.toml: "update_check" must be true or false', warnings[0])
        self.assertIn('config.toml: unknown key "endpoints", not read', warnings[1])

    def test_a_number_is_no_boolean(self):
        # TOML's 1 and 0 are integers, and Python's True is one: an integer is refused all the same.
        self.path.write_text("update_check = 1\nregister = 0\n", encoding="utf-8")
        values, warnings = cascade.read_machine(str(self.path), "config.toml")
        self.assertEqual(values, {})
        self.assertEqual([warning.split(" must")[0] for warning in warnings],
                         ['config.toml: "update_check"', 'config.toml: "register"'])

    def test_a_file_that_cannot_be_read_is_a_warning(self):
        self.path.write_bytes(b"releases_url = [\n")
        values, warnings = cascade.read_machine(str(self.path), "config.toml")
        self.assertEqual(values, {})
        self.assertIn("config.toml is not read", warnings[0])
        self.assertEqual(cascade.read_machine(str(self.tmp / "none.toml")), ({}, []))

    def test_a_directory_or_a_home_that_cannot_be_searched_warns(self):
        self.path.mkdir()
        self.assertEqual(cascade.read_machine(str(self.path), "config.toml"), ({}, ["config.toml is not a file, and is not read"]))
        if hasattr(os, "geteuid") and os.geteuid() != 0 and os.name != "nt":   # root searches any directory
            home = self.tmp / "locked"
            home.mkdir()
            (home / "config.toml").write_text("update_check = false\n", encoding="utf-8")
            home.chmod(0)
            self.addCleanup(home.chmod, 0o700)
            values, warnings = cascade.read_machine(str(home / "config.toml"), "config.toml")
            self.assertEqual(values, {})
            self.assertIn("config.toml cannot be read", warnings[0])

    @unittest.skipIf(os.name == "nt", "a link to a device")
    def test_a_device_or_a_pipe_is_not_a_file(self):
        # Read, a pipe would block the command until something writes to it: what is not a regular
        # file is not opened. A link to the null device stands for both, and never blocks.
        os.symlink(os.devnull, self.path)
        self.assertEqual(cascade.read_machine(str(self.path), "config.toml"), ({}, ["config.toml is not a file, and is not read"]))

    def test_a_byte_order_mark_and_an_empty_url(self):
        self.path.write_bytes('﻿releases_url = ""\n'.encode("utf-8"))
        values, warnings = cascade.read_machine(str(self.path), "config.toml")
        self.assertEqual(values, {})
        self.assertIn("not empty", warnings[0])


class ProjectFileTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cortex-cascade-"))

    def test_both_files_read_and_checked(self):
        (self.root / "cortex.toml").write_text('version = "1.7.0"\ntheme = "h2g2"\n', encoding="utf-8")
        (self.root / "cortex.local.toml").write_text('theme = "none"\n', encoding="utf-8")
        data, local = cascade.read_project(str(self.root))
        self.assertEqual((data["version"], local["theme"]), ("1.7.0", "none"))
        self.assertIsNone(cascade.read_project(str(self.root), local=False)[1])
        (self.root / "cortex.local.toml").write_text('verison = "x"\n', encoding="utf-8")
        with self.assertRaisesRegex(project.ProjectFileError, 'cortex.local.toml: unknown key "verison"'):
            cascade.read_project(str(self.root))
        (self.root / "cortex.local.toml").write_bytes(b"theme = \"\xff\"\n")
        with self.assertRaisesRegex(project.ProjectFileError, "cortex.local.toml: not UTF-8 text"):
            cascade.read_project(str(self.root))

    def test_the_version_is_read_before_any_key(self):
        # A later version's key does not hide the version that pins it (ADR-013 §3.8, step 1).
        (self.root / "cortex.toml").write_text('version = "9.0.0"\ntheme = "h2g2"\nlater = 1\n', encoding="utf-8")
        self.assertEqual(cascade.loose_version(str(self.root)), ("cortex.toml", "9.0.0"))
        (self.root / "cortex.toml").write_text('version = "nine"\n', encoding="utf-8")
        self.assertIsNone(cascade.loose_version(str(self.root)))
        os.unlink(self.root / "cortex.toml")
        self.assertIsNone(cascade.loose_version(str(self.root)))


if __name__ == "__main__":
    unittest.main()
