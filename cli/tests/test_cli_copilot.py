"""``copilot_access`` — VS Code's Copilot reads the store without asking, on request (ADR-014 §3.5).

In process: the JSON-with-comments editor, the settings files of each platform, what a settings
file becomes. Through the command: ``sync`` and ``init`` keeping the entry, the registry deciding
when it goes, and the team's lock. Every command runs with a ``HOME`` of the test's own: no test
reads or writes the settings of the machine it runs on.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parents[1] / "core"), str(HERE)]
from cortex_cli import vscode  # noqa: E402
import cli_harness as harness  # noqa: E402

OWN = harness.EXPECTED_VERSION if harness.BINARY else "9.9.9"
KEY = vscode.KEY
ENTRY = "/home/dev/.cortex/versions"


def display(path):
    return str(path).replace(os.sep, "/")


class EditorTests(unittest.TestCase):
    """The entry placed and removed as a text edit: every other byte kept."""

    def round_trip(self, text, expected=None):
        written = vscode.with_entry(text, ENTRY)
        self.assertEqual(vscode.entries(written)[-1], ENTRY)
        if expected is not None:
            self.assertEqual(written, expected)
        return written, vscode.without_entry(written, ENTRY)

    def test_a_missing_file_and_an_empty_one(self):
        made = f'{{\n    "{KEY}": [\n        "{ENTRY}"\n    ]\n}}\n'
        self.round_trip(None, made)
        self.round_trip("", made)
        _, back = self.round_trip("// mine\n", f"// mine\n{made}")
        self.assertEqual(back, "// mine\n{\n}\n")

    def test_comments_are_kept_where_they_are(self):
        text = ('{\n    // the editor\n    "editor.fontSize": 14, /* big */\n    "files.exclude": {"**/.git": true} '
                '// trailing\n    /* "old": 1, */\n}\n')
        written, back = self.round_trip(text)
        self.assertEqual(written, '{\n    // the editor\n    "editor.fontSize": 14, /* big */\n    "files.exclude": '
                                  f'{{"**/.git": true}}, // trailing\n    "{KEY}": [\n        "{ENTRY}"\n    ]\n'
                                  '    /* "old": 1, */\n}\n')
        self.assertEqual(back, text)
        for comment in ("// the editor", "/* big */", "// trailing", '/* "old": 1, */'):
            self.assertIn(comment, written)

    def test_trailing_commas_are_kept_and_followed(self):
        text = '{\n\t"a": 1,\n\t"b": [\n\t\t"x",\n\t],\n}\n'
        written, back = self.round_trip(text)
        self.assertEqual(written, f'{{\n\t"a": 1,\n\t"b": [\n\t\t"x",\n\t],\n\t"{KEY}": [\n\t\t"{ENTRY}"\n\t],\n}}\n')
        self.assertEqual(back, text)

    def test_crlf_and_a_byte_order_mark(self):
        text = '﻿{\r\n  "a": "x"\r\n}\r\n'
        written, back = self.round_trip(text)
        self.assertTrue(written.startswith("﻿{\r\n"))
        self.assertNotIn("\n", written.replace("\r\n", ""))
        self.assertIn(f'  "{KEY}": [\r\n    "{ENTRY}"\r\n  ]\r\n', written)
        self.assertEqual(back, text)

    def test_the_key_there_already(self):
        text = f'{{\n  "{KEY}": [\n    "/opt/docs", // mine\n  ]\n}}\n'
        written, back = self.round_trip(text)
        self.assertEqual(written, f'{{\n  "{KEY}": [\n    "/opt/docs", // mine\n    "{ENTRY}",\n  ]\n}}\n')
        self.assertEqual(back, text)
        written, back = self.round_trip(f'{{"{KEY}": ["/opt/docs"]}}')
        self.assertEqual(written, f'{{"{KEY}": ["/opt/docs", "{ENTRY}"]}}')
        self.assertEqual(back, f'{{"{KEY}": ["/opt/docs"]}}')

    def test_a_list_left_empty_goes_unless_it_holds_a_comment(self):
        self.assertEqual(vscode.without_entry(f'{{\n  "a": 1,\n  "{KEY}": ["{ENTRY}"]\n}}', ENTRY), '{\n  "a": 1\n}')
        kept = f'{{\n  "{KEY}": [\n    // ask me\n    "{ENTRY}"\n  ]\n}}'
        self.assertEqual(vscode.without_entry(kept, ENTRY), f'{{\n  "{KEY}": [\n    // ask me\n  ]\n}}')

    def test_strings_that_look_like_comments_or_brackets(self):
        text = '{\n  "url": "http://x/*not a comment*/", "b": "]}\\" //"\n}\n'
        written, back = self.round_trip(text)
        self.assertEqual(json.loads(written)["url"], "http://x/*not a comment*/")
        self.assertEqual(back, text)

    def test_what_it_cannot_read(self):
        for text, why in (("{not json", "at line 1"), ('{\n  "a": 1\n  "b": 2\n}', "at line 3"),
                          ("[1, 2]", "no object"), ("/* never closed", "a comment that never ends"),
                          ('{"a": 1}{}', "more after"), ('{"a": "\n"}', "a string that never ends")):
            with self.subTest(text=text):
                with self.assertRaisesRegex(vscode.JsoncError, why):
                    vscode.with_entry(text, ENTRY)
        for text, why in ((f'{{"{KEY}": "/x"}}', "is no list"), (f'{{"{KEY}": [], "{KEY}": []}}', "is there twice")):
            with self.subTest(text=text):
                with self.assertRaisesRegex(vscode.SettingsError, why):
                    vscode.with_entry(text, ENTRY)

    def test_covers_compares_as_written(self):
        self.assertTrue(vscode.covers(ENTRY, ENTRY))
        self.assertTrue(vscode.covers("/home/dev/.cortex", ENTRY))
        self.assertTrue(vscode.covers("/home/dev/.cortex/", ENTRY))
        self.assertFalse(vscode.covers("/home/dev/.cortex/versions/1.9.0", ENTRY))
        self.assertFalse(vscode.covers("/home/dev/.cortex-other", ENTRY))
        self.assertFalse(vscode.covers("~/.cortex/versions", ENTRY))     # Copilot expands no ~
        self.assertFalse(vscode.covers(3, ENTRY))


class LocationTests(unittest.TestCase):
    """Which settings file, on each platform — simulated with a home of the test's own."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="cortex-vscode-home-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def files(self, system, environ=None):
        return [display(path.relative_to(self.home)) for path in
                vscode.settings_files(self.home, system, environ or {})]

    def test_none_when_no_vs_code_is_there(self):
        for system in ("linux", "darwin", "win32"):
            self.assertEqual(self.files(system), [])

    def test_each_desktop_install_on_each_platform(self):
        for system, base in (("linux", ".config"), ("darwin", "Library/Application Support"),
                             ("win32", "AppData/Roaming")):
            with self.subTest(system=system):
                for name in ("Code", "Code - Insiders"):
                    (self.home / base / name).mkdir(parents=True)
                self.assertEqual(self.files(system), [f"{base}/Code/User/settings.json",
                                                      f"{base}/Code - Insiders/User/settings.json"])
                shutil.rmtree(self.home / base.split("/")[0])
        (self.home / ".config" / "Code - Insiders").mkdir(parents=True)
        self.assertEqual(self.files("linux"), [".config/Code - Insiders/User/settings.json"])

    def test_the_variables_that_move_them(self):
        (self.home / "roaming" / "Code").mkdir(parents=True)
        (self.home / "xdg" / "Code").mkdir(parents=True)
        self.assertEqual(self.files("win32", {"APPDATA": str(self.home / "roaming")}),
                         ["roaming/Code/User/settings.json"])
        self.assertEqual(self.files("linux", {"XDG_CONFIG_HOME": str(self.home / "xdg")}),
                         ["xdg/Code/User/settings.json"])
        self.assertEqual(self.files("darwin", {"VSCODE_APPDATA": str(self.home / "xdg")}),
                         ["xdg/Code/User/settings.json"])

    def test_a_remote_server_wins_over_the_desktop(self):
        (self.home / ".config" / "Code").mkdir(parents=True)
        (self.home / ".vscode-server").mkdir()
        self.assertEqual(self.files("linux"), [".vscode-server/data/Machine/settings.json"])
        (self.home / ".vscode-server-insiders").mkdir()
        self.assertEqual(self.files("linux"), [".vscode-server/data/Machine/settings.json",
                                               ".vscode-server-insiders/data/Machine/settings.json"])


class SettingsFileTests(unittest.TestCase):
    """What ``update`` makes of one settings file."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-vscode-"))
        self.addCleanup(self.cleanup)
        self.path = self.tmp / "data" / "Machine" / "settings.json"

    def cleanup(self):
        for directory, subdirs, files in os.walk(self.tmp):
            for name in subdirs + files:
                os.chmod(os.path.join(directory, name), 0o700)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, text):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(text.encode("utf-8"))

    def text(self):
        return self.path.read_bytes().decode("utf-8")

    def test_a_new_file_its_folders_too(self):
        said, owns = vscode.update(self.path, ENTRY, None)
        self.assertTrue(owns)
        self.assertIn(f"Copilot reads {ENTRY} without asking", said)
        self.assertEqual(json.loads(self.text()), {KEY: [ENTRY]})
        self.assertEqual(vscode.update(self.path, ENTRY, ENTRY), (None, True))

    def test_the_developers_entry_is_theirs_and_stays(self):
        for mine in (ENTRY, "/home/dev/.cortex", "/home/dev"):
            with self.subTest(mine=mine):
                self.write(f'{{\n  "{KEY}": ["{mine}"]\n}}\n')
                self.assertEqual(vscode.update(self.path, ENTRY, None), (None, False))
                self.assertEqual(vscode.update(self.path, None, None), (None, False))
                self.assertEqual(self.text(), f'{{\n  "{KEY}": ["{mine}"]\n}}\n')
                self.assertTrue(vscode.allows(self.path, ENTRY))

    def test_turned_off_it_removes_only_its_own(self):
        text = '{\n  // mine\n  "a": 1\n}\n'
        self.write(text)
        vscode.update(self.path, ENTRY, None)
        said, owns = vscode.update(self.path, None, ENTRY)
        self.assertIn("no longer reads the store without asking", said)
        self.assertFalse(owns)
        self.assertEqual(self.text(), text)
        self.assertEqual(vscode.update(self.path, None, ENTRY), (None, False))

    def test_a_moved_store_moves_its_entry(self):
        self.write(f'{{"{KEY}": ["/opt/docs", "{ENTRY}"]}}')
        vscode.update(self.path, "/srv/cortex/versions", ENTRY)
        self.assertEqual(json.loads(self.text())[KEY], ["/opt/docs", "/srv/cortex/versions"])

    def test_a_file_it_cannot_place_it_in_is_left_as_it_is(self):
        for text in ("{\n  // half\n  \"a\": \n}", f'{{"{KEY}": null}}', "[]"):
            with self.subTest(text=text):
                self.write(text)
                with self.assertRaises(vscode.SettingsError) as caught:
                    vscode.update(self.path, ENTRY, None)
                self.assertIn(display(self.path), str(caught.exception))
                self.assertIn("left as it is", str(caught.exception))
                self.assertEqual(self.text(), text)
        self.path.unlink()
        self.path.mkdir()
        with self.assertRaisesRegex(vscode.SettingsError, "is no file"):
            vscode.update(self.path, ENTRY, None)

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "a mode root ignores, or Windows has not")
    def test_a_read_only_file_is_left_as_it_is(self):
        self.write("{}\n")
        self.path.chmod(0o444)
        with self.assertRaisesRegex(vscode.SettingsError, "read-only"):
            vscode.update(self.path, ENTRY, None)
        self.assertEqual(self.text(), "{}\n")

    def test_a_link_is_written_through(self):
        if os.name == "nt":
            self.skipTest("a symbolic link needs a privilege on Windows")
        dotfiles = self.tmp / "dotfiles.json"
        dotfiles.write_text("{\n}\n", encoding="utf-8")
        self.path.parent.mkdir(parents=True)
        self.path.symlink_to(dotfiles)
        vscode.update(self.path, ENTRY, None)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(json.loads(dotfiles.read_text(encoding="utf-8")), {KEY: [ENTRY]})


if __name__ == "__main__":
    unittest.main()
