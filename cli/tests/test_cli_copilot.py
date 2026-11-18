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


class CommandTests(unittest.TestCase):
    """``cortex sync`` and ``cortex init``, with a home of the test's own that has a VS Code server."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-copilot-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "cortex-home"
        self.user = self.tmp / "user"
        (self.user / ".vscode-server").mkdir(parents=True)
        self.env = harness.environment(CORTEX_HOME=str(self.home), CORTEX_SOURCE_VERSION=OWN, HOME=str(self.user),
                                       USERPROFILE=str(self.user))
        self.project = self.tmp / "project"
        self.project.mkdir()
        self.settings = self.user / ".vscode-server" / "data" / "Machine" / "settings.json"
        self.entry = display(self.home / "versions")

    def run_cortex(self, *args, cwd=None, stdin=subprocess.DEVNULL, code=0):
        proc = harness.run(*args, cwd=cwd or self.project, env=self.env, stdin=stdin)
        proc.out, proc.err = proc.stdout.decode(), proc.stderr.decode()
        self.assertEqual(proc.returncode, code, proc.err)
        return proc

    def allowed(self, path=None):
        path = path or self.settings
        if not path.exists():
            return None
        return vscode.entries(path.read_bytes().decode("utf-8"))

    def local(self, project=None):
        path = (project or self.project) / "cortex.local.toml"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def toml(self, extra="", project=None):
        (project or self.project).mkdir(exist_ok=True)
        ((project or self.project) / "cortex.toml").write_text(f'version = "{OWN}"\ntheme = "h2g2"\n{extra}',
                                                               encoding="utf-8")

    def test_the_teams_setting_allows_the_store(self):
        self.toml("copilot_access = true\n")
        proc = self.run_cortex("sync")
        self.assertEqual(self.allowed(), [self.entry])
        self.assertIn(f"Copilot reads {self.entry} without asking", proc.out)
        self.assertIn(f'copilot_entry = "{self.entry}"', self.local())
        self.run_cortex("sync")
        self.assertEqual(self.allowed(), [self.entry])          # once

    def test_off_by_default_and_never_set_it_leaves_the_settings_alone(self):
        self.toml()
        mine = f'{{\n  // mine\n  "{KEY}": ["{self.entry}"]\n}}\n'
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(mine, encoding="utf-8")
        self.run_cortex("sync")
        self.assertEqual(self.settings.read_text(encoding="utf-8"), mine)
        self.assertNotIn("copilot_", self.local())

    def test_the_desktops_settings_without_a_server(self):
        shutil.rmtree(self.user / ".vscode-server")
        desktop = self.user / ".config" / "Code"
        if sys.platform == "darwin":
            desktop = self.user / "Library" / "Application Support" / "Code"
        elif os.name == "nt":
            desktop = self.user / "AppData" / "Roaming" / "Code"
        desktop.mkdir(parents=True)
        self.toml("copilot_access = true\n")
        self.run_cortex("sync")
        self.assertEqual(self.allowed(desktop / "User" / "settings.json"), [self.entry])

    def test_no_vs_code_writes_nothing_and_says_so_when_asked(self):
        shutil.rmtree(self.user / ".vscode-server")
        self.toml()
        proc = self.run_cortex("sync", "--copilot-access")
        self.assertIn("no VS Code settings on this machine", proc.err)
        self.assertNotIn("copilot_entry", self.local())
        self.assertIn("copilot_access = true", self.local())
        self.assertNotIn("no VS Code settings", self.run_cortex("sync").err)

    def test_the_developer_turns_it_on_and_off_for_themself(self):
        self.toml()
        self.run_cortex("sync", "--copilot-access")
        self.assertIn("copilot_access = true", self.local())
        self.assertEqual(self.allowed(), [self.entry])
        proc = self.run_cortex("sync", "--no-copilot-access")
        self.assertIn("Copilot no longer reads the store without asking", proc.out)
        self.assertIn("copilot_access = false", self.local())
        self.assertNotIn("copilot_entry", self.local())
        self.assertEqual(self.allowed(), [])
        self.run_cortex("sync")                     # the choice is kept
        self.assertEqual(self.allowed(), [])

    def test_the_key_removed_removes_the_entry_and_no_other(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(f'{{\n  "{KEY}": [\n    "/opt/docs" // mine\n  ]\n}}\n', encoding="utf-8")
        self.toml("copilot_access = true\n")
        self.run_cortex("sync")
        self.assertEqual(self.allowed(), ["/opt/docs", self.entry])
        self.toml()
        self.run_cortex("sync")
        self.assertEqual(self.settings.read_text(encoding="utf-8"),
                         f'{{\n  "{KEY}": [\n    "/opt/docs" // mine\n  ]\n}}\n')

    def test_turned_off_with_an_entry_of_the_developers_it_says_what_still_allows(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(f'{{"{KEY}": ["{self.entry}"]}}', encoding="utf-8")
        self.toml()
        proc = self.run_cortex("sync", "--no-copilot-access")
        self.assertIn("has an entry of your own that lets Copilot read", proc.err)
        self.assertEqual(self.allowed(), [self.entry])

    def test_a_file_it_cannot_read_is_a_warning_naming_the_line(self):
        self.settings.parent.mkdir(parents=True)
        broken = '{\n  "a": 1\n  "b": 2\n}\n'
        self.settings.write_text(broken, encoding="utf-8")
        self.toml("copilot_access = true\n")
        proc = self.run_cortex("sync")
        self.assertIn(f"warning: {display(self.settings)} is no JSON with comments cortex can read", proc.err)
        self.assertIn("at line 3", proc.err)
        self.assertIn(f'"{KEY}": ["{self.entry}"]', proc.err)
        self.assertNotIn("Traceback", proc.err)
        self.assertEqual(self.settings.read_text(encoding="utf-8"), broken)
        self.assertNotIn("copilot_entry", self.local())

    def test_the_entry_stays_while_another_registered_project_sets_it(self):
        other = self.tmp / "other"
        self.toml("copilot_access = true\n")
        self.toml("copilot_access = true\n", project=other)
        self.run_cortex("sync")
        self.run_cortex("sync", cwd=other)
        # The second found the entry the first wrote: Cortex's, both record it.
        self.assertIn(f'copilot_entry = "{self.entry}"', self.local(other))
        self.assertEqual(self.allowed(), [self.entry])
        self.toml()
        proc = self.run_cortex("sync")
        self.assertIn("still sets copilot_access", proc.out)
        self.assertIn("other", proc.out)
        self.assertEqual(self.allowed(), [self.entry])
        self.assertNotIn("copilot_entry", self.local())
        self.toml(project=other)
        self.run_cortex("sync", cwd=other)
        self.assertEqual(self.allowed(), [])
        self.assertNotIn("copilot_entry", self.local(other))

    def test_a_project_that_set_it_but_never_wrote_it_keeps_nothing(self):
        other = self.tmp / "other"
        self.toml()
        self.toml(project=other)
        self.run_cortex("sync", cwd=other)              # registered, without the key
        self.toml("copilot_access = true\n", project=other)     # set, not synced
        self.run_cortex("sync", "--copilot-access")
        self.run_cortex("sync", "--no-copilot-access")
        self.assertEqual(self.allowed(), [])

    def test_the_team_locks_it_to_false(self):
        (self.project / "agents").mkdir()
        (self.project / "agents" / "cortex.team.toml").write_text(
            'copilot_access = false\nlocked = ["copilot_access"]\n', encoding="utf-8")
        self.toml()
        proc = self.run_cortex("sync", "--copilot-access", code=1)
        self.assertIn("copilot_access is locked to false by agents/cortex.team.toml", proc.err)
        (self.project / "cortex.local.toml").write_text("copilot_access = true\n", encoding="utf-8")
        proc = self.run_cortex("sync")
        self.assertIn("copilot_access is locked by agents/cortex.team.toml", proc.err)
        self.assertIsNone(self.allowed())
        proc = self.run_cortex("config")
        self.assertRegex(proc.out, r"copilot_access\s+false\s+agents/cortex.team.toml — locked")
        (self.project / "agents" / "cortex.team.toml").write_text(
            'copilot_access = true\nlocked = ["copilot_access"]\n', encoding="utf-8")
        proc = self.run_cortex("sync", code=1)
        self.assertIn("copilot_access is locked to false only", proc.err)

    def test_config_says_where_it_comes_from(self):
        self.toml("copilot_access = true\n")
        self.assertRegex(self.run_cortex("config").out, r"copilot_access\s+true\s+cortex.toml")
        self.run_cortex("sync", "--no-copilot-access")
        self.assertRegex(self.run_cortex("config").out, r"copilot_access\s+false\s+cortex.local.toml")

    def test_a_link_still_asks_and_says_so(self):
        self.toml('sync = "link"\ncopilot_access = true\n')
        proc = self.run_cortex("sync")
        self.assertEqual(self.allowed(), [self.entry])
        self.assertIn("note: copilot_access does not reach cortex/, a link: Copilot still asks", proc.err)
        self.assertIn("`cortex sync --store`", proc.err)
        proc = self.run_cortex("sync", "--store")       # the command the note names
        self.assertNotIn("does not reach cortex/", proc.err)
        self.assertIn("The spec is read where it is", proc.out)

    def test_init_writes_the_teams_setting(self):
        proc = self.run_cortex("init", "--tool", "copilot", "--copilot-access")
        self.assertIn("copilot_access = true", (self.project / "cortex.toml").read_text(encoding="utf-8"))
        self.assertEqual(self.allowed(), [self.entry])
        self.assertNotIn("tip: VS Code's Copilot", proc.out)

    def test_a_second_init_keeps_copilot_access_but_for_the_option_given(self):
        self.run_cortex("init", "--tool", "copilot", "--copilot-access")
        self.run_cortex("init", "--tool", "copilot", "--force")
        self.assertIn("copilot_access = true", (self.project / "cortex.toml").read_text(encoding="utf-8"))
        self.run_cortex("init", "--tool", "copilot", "--no-copilot-access")
        self.assertIn("copilot_access = false", (self.project / "cortex.toml").read_text(encoding="utf-8"))
        self.assertEqual(self.allowed(), [])

    def test_init_unattended_says_how(self):
        proc = self.run_cortex("init", "--tool", "copilot")
        self.assertIn("tip: VS Code's Copilot asks before it reads the spec", proc.out)
        self.assertNotIn("copilot_access", (self.project / "cortex.toml").read_text(encoding="utf-8"))
        self.assertIsNone(self.allowed())
        proc = self.run_cortex("sync", "--copilot-access")      # the command the tip names
        self.assertEqual(self.allowed(), [self.entry])

    def test_init_for_another_tool_says_nothing_of_copilot(self):
        proc = self.run_cortex("init", "--tool", "cursor")
        self.assertNotIn("Copilot", proc.out)

    def on_a_terminal(self, answer, *args):
        import pty

        shutil.rmtree(self.project)
        self.project.mkdir()
        primary, secondary = pty.openpty()
        try:
            os.write(primary, answer)
            return self.run_cortex("init", "--tool", "copilot", *args, stdin=secondary)
        finally:
            os.close(primary)
            os.close(secondary)

    @unittest.skipIf(os.name == "nt", "a pseudo-terminal is POSIX's")
    def test_init_on_a_terminal_asks_and_no_is_the_default(self):
        for answer, expected in ((b"y\n", True), (b"\n", False), (b"n\n", False), (b"\x04", False)):
            with self.subTest(answer=answer):
                proc = self.on_a_terminal(answer)
                self.assertIn("copilot_access = true in cortex.toml? [y/N]", proc.out)
                self.assertEqual("copilot_access = true" in (self.project / "cortex.toml").read_text(encoding="utf-8"),
                                 expected)

    @unittest.skipIf(os.name == "nt", "a pseudo-terminal is POSIX's")
    def test_init_on_a_terminal_with_an_option_or_a_copy_does_not_ask(self):
        for args in (("--copilot-access",), ("--no-copilot-access",), ("--copy",)):
            with self.subTest(args=args):
                proc = self.on_a_terminal(b"y\n", *args)
                self.assertNotIn("Let VS Code's Copilot read", proc.out)
                self.assertEqual("copilot_access = true" in (self.project / "cortex.toml").read_text(encoding="utf-8"),
                                 args == ("--copilot-access",))


if __name__ == "__main__":
    unittest.main()
