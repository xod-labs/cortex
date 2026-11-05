"""``cortex project`` and ``projects.toml`` — the Cortex projects of this machine (ADR-011 §3.1, §3.2).

The registry is ``$CORTEX_HOME/projects.toml``, in a home of each test's own. The tests run the
command — from source, or through a built binary — and, for what a process cannot show, such as a
write slowed down to meet another, the module itself.
"""

import errno
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the harness, not a package named tests
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "core")]
import cli_harness as harness  # noqa: E402
from cortex_cli import config, registry, sync  # noqa: E402

HEADER = "# The Cortex projects of this machine (ADR-011). Written by cortex — `cortex project` edits it.\n"
OWN = harness.EXPECTED_VERSION if harness.BINARY else "9.9.9"
AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
PERMISSIONS = unittest.skipIf(AS_ROOT or os.name == "nt", "root reads every directory, and Windows has no chmod 000")
NONE = "No project is registered on this machine — `cortex project add` registers the one of the current directory\n"


def display(path):
    return str(path).replace(os.sep, "/")


def snapshot(root):
    """Everything under ``root``, files as bytes."""
    return {str(p.relative_to(root)): p.read_bytes() if p.is_file() else "dir" for p in sorted(root.rglob("*"))}


class ProjectTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-project-")).resolve()
        self.addCleanup(self.cleanup)
        self.home = self.tmp / "cortex-home"
        self.registry = self.home / "projects.toml"
        self.env = harness.environment(CORTEX_HOME=str(self.home), CORTEX_SOURCE_VERSION=OWN)
        self.code = self.tmp / "code"

    def cleanup(self):
        for directory, subdirs, files in os.walk(self.tmp):
            for name in subdirs + files:
                path = os.path.join(directory, name)
                if not os.path.islink(path):
                    os.chmod(path, 0o700 if os.path.isdir(path) else 0o600)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def project(self, path, version="1.4.0"):
        root = self.code / path
        root.mkdir(parents=True, exist_ok=True)
        (root / "cortex.toml").write_text(f'version = "{version}"\ntheme = "h2g2"\n', encoding="utf-8")
        return root

    def run_cortex(self, cwd, *args, env=None):
        proc = harness.run(*args, cwd=cwd, env=env or self.env)
        proc.out, proc.err = proc.stdout.decode(), proc.stderr.decode()
        return proc

    def assert_ok(self, proc):
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)

    def entries(self):
        """The registry's entries, as the file lists them: (name, path) each."""
        data = tomllib.loads(self.registry.read_text(encoding="utf-8"))
        return [(entry["name"], entry["path"]) for entry in data.get("project", [])]

    def write_registry(self, *entries, top="format = 1\n"):
        self.home.mkdir(exist_ok=True)
        text = top + "".join(f'\n[[project]]\nname = "{name}"\npath = "{display(path)}"\n' for name, path in entries)
        self.registry.write_text(text, encoding="utf-8")

    def in_process(self):
        """The environment of a test that calls the module: this test's home."""
        return mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)})


class AddTests(ProjectTestCase):
    def test_it_registers_the_project_of_the_current_directory_and_says_so(self):
        root = self.project("acme")
        (root / "src").mkdir()
        proc = self.run_cortex(root / "src", "project", "add")         # the nearest cortex.toml, as sync finds it
        self.assert_ok(proc)
        self.assertEqual(proc.out, f"registered {display(root)} as acme — `cortex project list` lists this "
                                   "machine's projects\n")
        self.assertEqual(self.registry.read_text(encoding="utf-8"),
                         HEADER + f'format = 1\n\n[[project]]\nname = "acme"\npath = "{display(root)}"\n')

    def test_run_again_it_says_the_path_is_listed_and_changes_nothing(self):
        root = self.project("acme")
        self.assert_ok(self.run_cortex(root, "project", "add"))
        before = self.registry.read_bytes()
        for args in ((), ("--name", "other")):
            with self.subTest(args=args):
                proc = self.run_cortex(root, "project", "add", *args)
                self.assert_ok(proc)
                self.assertTrue(proc.out.startswith(f"{display(root)} is registered already, as acme: nothing changed"),
                                proc.out)
                self.assertEqual(self.registry.read_bytes(), before)
        self.assertIn("`cortex project remove acme` first, to register it as other", proc.out)

    def test_a_path_names_the_project(self):
        root = self.project("deep/acme")
        proc = self.run_cortex(self.code, "project", "add", "deep/acme")
        self.assert_ok(proc)
        self.assertEqual(self.entries(), [("acme", display(root))])
        self.assert_ok(self.run_cortex(self.tmp, "project", "add", str(self.project("other"))))
        self.assertEqual(self.entries(), [("acme", display(root)), ("other", display(self.code / "other"))])

    def test_name_names_it_and_a_taken_name_is_refused(self):
        one, two = self.project("one"), self.project("two")
        self.assert_ok(self.run_cortex(one, "project", "add", "--name", "acme"))
        before = self.registry.read_bytes()
        proc = self.run_cortex(two, "project", "add", "--name", "acme")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f'the name "acme" is taken, by {display(one)} — choose another, with --name', proc.err)
        self.assertEqual(self.registry.read_bytes(), before)
        self.assertEqual(self.entries(), [("acme", display(one))])

    def test_a_name_that_is_none_is_refused(self):
        root = self.project("acme")
        for name in ("Acme", "-acme", "a/b", "x" * 65):
            with self.subTest(name=name):
                proc = self.run_cortex(root, "project", "add", f"--name={name}")
                self.assertEqual(proc.returncode, 2)
                self.assertIn("is no project name — lowercase letters", proc.err)
        self.assertFalse(self.home.exists())

    def test_the_name_is_the_directorys_lowered_and_numbered_when_taken(self):
        roots = [self.project("a/My Project"), self.project("b/my-project"), self.project("c/MY.PROJECT"),
                 self.project("d/My_Project"), self.project("e/my project")]
        for root in roots:
            self.assert_ok(self.run_cortex(root, "project", "add"))
        self.assertEqual(self.entries(), [("my-project", display(roots[0])), ("my-project-2", display(roots[1])),
                                          ("my-project-3", display(roots[4])), ("my.project", display(roots[2])),
                                          ("my_project", display(roots[3]))])

    def test_default_names(self):
        for root, taken, name in (("/code/Acme", (), "acme"),
                                  ("/code/acme/", (), "acme"),
                                  ("/code/.hidden", (), "hidden"),
                                  ("/code/-x-", (), "x-"),
                                  ("/code/caf\u00e9 cr\u00e8me", (), "caf--cr-me"),
                                  ("/code/" + "a" * 70, (), "a" * 64),
                                  ("/code/" + "a" * 70, ("a" * 64,), "a" * 62 + "-2"),
                                  ("/code/acme", ("acme", "acme-2"), "acme-3"),
                                  ("/code/...", (), "project"),
                                  ("/", (), "project")):
            with self.subTest(root=root, taken=taken):
                self.assertEqual(registry.default_name(root, taken), name)

    def test_a_directory_without_cortex_toml_is_refused(self):
        (self.code / "plain").mkdir(parents=True)
        for cwd, args in ((self.code / "plain", ()), (self.code, ("plain",))):
            with self.subTest(args=args):
                proc = self.run_cortex(cwd, "project", "add", *args)
                self.assertEqual(proc.returncode, 1)
                self.assertIn("`cortex init` makes a directory a Cortex project", proc.err)
                self.assertIn(display(self.code / "plain"), proc.err)
        self.assertFalse(self.home.exists())

    def test_a_path_that_is_no_directory_is_refused(self):
        self.code.mkdir()
        proc = self.run_cortex(self.code, "project", "add", "nowhere")
        self.assertEqual(proc.returncode, 2)
        self.assertIn(f"{display(self.code / 'nowhere')}: no such directory", proc.err)

    def test_its_home_is_made_when_there_is_none(self):
        self.env["CORTEX_HOME"] = str(self.tmp / "new" / "home")
        self.assert_ok(self.run_cortex(self.project("acme"), "project", "add"))
        self.assertTrue((self.tmp / "new" / "home" / "projects.toml").is_file())

    def test_a_path_through_a_link_is_written_as_the_shell_names_it_and_counted_once(self):
        root = self.project("real/acme")
        link = self.tmp / "link"
        try:
            os.symlink(root, link, target_is_directory=True)
        except OSError:
            self.skipTest("no symbolic link here")
        self.assert_ok(self.run_cortex(link, "project", "add"))
        self.assertEqual(self.entries(), [("link", display(link))])      # not resolved, and named so
        proc = self.run_cortex(root, "project", "add")
        self.assert_ok(proc)
        self.assertIn(f"{display(root)} is registered already, as link", proc.out)
        self.assertEqual(self.entries(), [("link", display(link))])

    def test_a_pwd_not_written_plainly_is_not_written_so(self):
        root = self.project("acme")
        (root / "sub").mkdir()
        proc = self.run_cortex(root, "project", "add", env=dict(self.env, PWD=f"{root}/sub/.."))
        self.assert_ok(proc)
        self.assertEqual(self.entries(), [("acme", display(root))])

    def test_a_directory_the_resolved_paths_miss_is_found_by_the_file_system(self):
        # A case macOS ignores, which realpath keeps: os.path.samefile tells, as it does through a
        # link that realpath is kept from resolving here.
        root = self.project("acme")
        link = self.tmp / "link"
        try:
            os.symlink(root, link, target_is_directory=True)
        except OSError:
            self.skipTest("no symbolic link here")
        with self.in_process(), mock.patch("os.path.realpath", side_effect=lambda path: str(path)):
            self.assertEqual(registry.add(str(root)), (registry.Entry("acme", display(root)), True))
            self.assertEqual(registry.add(str(link)), (registry.Entry("acme", display(root)), False))
        self.assertEqual(self.entries(), [("acme", display(root))])

    def test_a_project_listed_as_written_is_found_without_resolving_any_path(self):
        roots = [self.project(name) for name in ("one", "two", "three")]
        with self.in_process():
            for root in roots:
                registry.add(str(root))
            with mock.patch.object(registry, "same_directory", side_effect=AssertionError("resolved")):
                self.assertEqual(registry.add(str(roots[1]) + "/"), (registry.Entry("two", display(roots[1])), False))
                self.assertEqual(registry.find(registry.load(), str(roots[2])).name, "three")

    def test_a_temporary_file_a_killed_write_left_is_swept(self):
        self.home.mkdir()
        stale = self.home / "projects.toml.4242.tmp"
        stale.write_text("format = 1\n", encoding="utf-8")
        others = ["notes.tmp", "projects.toml.bak", "projects.toml.tmp"]        # the sweep takes the registry's only
        for name in others:
            (self.home / name).write_text("x\n", encoding="utf-8")
        self.assert_ok(self.run_cortex(self.project("acme"), "project", "add"))
        self.assertFalse(stale.exists())
        self.assertEqual(sorted(os.listdir(self.home)), sorted(["projects.toml", "projects.toml.lock", *others]))

    def test_a_rename_refused_for_a_moment_is_tried_again(self):
        # Windows, while a reader has the file open: PermissionError, for the milliseconds of a read.
        root, write, refused = self.project("acme"), config.write_text, []

        def busy(path, text):
            if len(refused) < 2:
                refused.append(path)
                raise PermissionError(errno.EACCES, "Access is denied")
            write(path, text)

        with self.in_process(), mock.patch.object(registry, "REPLACE_TRIES", 3), \
                mock.patch.object(registry, "REPLACE_PAUSE", 0), mock.patch.object(config, "write_text", busy):
            self.assertEqual(registry.add(str(root))[1], True)
            refused.clear()
            with mock.patch.object(registry, "REPLACE_TRIES", 2):
                with self.assertRaisesRegex(registry.RegistryError, "projects.toml cannot be written — .*Access is denied"):
                    registry.remove("acme")
        self.assertEqual(self.entries(), [("acme", display(root))])

    def test_a_registry_it_refuses_is_never_overwritten(self):
        root = self.project("acme")
        for text, fragment in (('format = 1\nextra = "x"\n', 'unknown key "extra"'),
                               ('format = 1\n\n[[project]]\nname = "Acme"\npath = "/x"\n', 'name "Acme" is no project'),
                               ("format = 1\n[[project]\n", "projects.toml: "),
                               ("format = 2\n", "format 2 is written by a later Cortex")):
            with self.subTest(text=text):
                self.home.mkdir(exist_ok=True)
                self.registry.write_text(text, encoding="utf-8")
                proc = self.run_cortex(root, "project", "add")
                self.assertEqual(proc.returncode, 1)
                self.assertIn(f"{display(self.registry)}", proc.err)
                self.assertIn(fragment, proc.err)
                self.assertEqual(self.registry.read_text(encoding="utf-8"), text)

    def test_a_later_format_names_self_update(self):
        self.write_registry(top="format = 2\n")
        proc = self.run_cortex(self.project("acme"), "project", "add")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"cortex project add: {display(self.registry)}: format 2 is written by a later Cortex — this one "
                      "reads format 1: this cortex is older than the one that wrote it — update it:\n"
                      "    cortex self-update\n", proc.err)

    def test_a_registry_that_cannot_be_written_is_said(self):
        # A home that is a file: nothing can be made in it, as root too, on every system.
        self.home.parent.mkdir(exist_ok=True)
        self.home.write_text("not a directory\n", encoding="utf-8")
        proc = self.run_cortex(self.project("acme"), "project", "add")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"cortex project add: {display(self.registry)} cannot be", proc.err)
        self.assertEqual(self.home.read_text(encoding="utf-8"), "not a directory\n")

    @unittest.skipIf(os.name == "nt", "a file name holds no control character on Windows")
    def test_a_path_the_file_cannot_hold_is_refused_and_the_registry_kept(self):
        # TOML takes no raw DEL in a string, and JSON's escapes, which write it, do not escape it.
        self.assert_ok(self.run_cortex(self.project("acme"), "project", "add"))
        before = self.registry.read_bytes()
        proc = self.run_cortex(self.project("odd\x7fname"), "project", "add")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("is not written: it would not read back", proc.err)
        self.assertEqual(self.registry.read_bytes(), before)
        self.assert_ok(self.run_cortex(self.project("next"), "project", "add"))


class WarnTests(ProjectTestCase):
    """``add`` with ``warn``: what a command whose work is done calls, and which never fails it."""

    def test_a_registry_that_cannot_be_written_is_a_warning(self):
        root = self.project("acme")
        err = io.StringIO()
        with self.in_process(), mock.patch.object(config, "write_text", side_effect=PermissionError(13, "Permission denied",
                                                                                                    str(self.registry))):
            self.assertEqual(registry.add(str(root), warn=err), (None, False))
        self.assertTrue(err.getvalue().startswith(f"warning: {display(root)} is not registered — "
                                                  f"{display(self.registry)} cannot be written — Permission denied"),
                        err.getvalue())
        self.assertFalse(self.registry.exists())

    def test_a_registry_it_refuses_is_a_warning_too(self):
        self.write_registry(top="format = 2\n")
        err = io.StringIO()
        with self.in_process():
            self.assertEqual(registry.add(str(self.project("acme")), warn=err), (None, False))
        self.assertIn("format 2 is written by a later Cortex", err.getvalue())
        self.assertIn("cortex self-update", err.getvalue())

    def test_without_warn_it_raises(self):
        with self.in_process(), mock.patch.object(config, "write_text", side_effect=PermissionError(13, "denied")):
            with self.assertRaisesRegex(registry.RegistryError, "cannot be written"):
                registry.add(str(self.project("acme")))

    def test_what_it_returns(self):
        root = self.project("acme")
        with self.in_process():
            entry, added = registry.add(str(root), warn=io.StringIO())
            self.assertEqual((entry, added), (registry.Entry("acme", display(root)), True))
            self.assertEqual(registry.add(str(root), warn=io.StringIO()), (entry, False))
            self.assertEqual(registry.load(), [entry])


class ConcurrencyTests(ProjectTestCase):
    """Writes one at a time, under a lock beside the file: two that run at once both land."""

    def test_adds_run_at_once_all_land(self):
        roots = [self.project(f"p{number}") for number in range(8)]
        processes = []
        for root in roots:
            env = dict(self.env, PWD=str(root))
            processes.append(subprocess.Popen(harness.command("project", "add"), cwd=root, env=env,
                                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        for process in processes:
            out, err = process.communicate(timeout=120)
            self.assertEqual(process.returncode, 0, (out + err).decode())
        self.assertEqual(self.entries(), [(f"p{number}", display(root)) for number, root in enumerate(roots)])

    def test_a_write_slowed_down_does_not_lose_another(self):
        # The window between reading the registry and writing it, widened: without the lock, the
        # second write would put back the registry the first had read, without the first project.
        roots = [self.project("one"), self.project("two"), self.project("three")]
        write, failures = config.write_text, []

        def slowly(path, text):
            time.sleep(0.3)
            write(path, text)

        def add(root):
            try:
                registry.add(str(root))
            except BaseException as error:      # noqa: BLE001 — said by the test, not lost in the thread
                failures.append(error)

        with self.in_process(), mock.patch.object(config, "write_text", slowly):
            threads = [threading.Thread(target=add, args=(root,)) for root in roots]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(failures, [])
        self.assertEqual(sorted(name for name, _ in self.entries()), ["one", "three", "two"])

    def test_a_lock_held_too_long_is_said(self):
        root = self.project("acme")
        with self.in_process(), mock.patch.object(registry, "LOCK_WAIT", 0.3):
            with registry._locked(self.registry):
                with self.assertRaisesRegex(registry.RegistryError,
                                            "another cortex command has held .*projects.toml.lock for 0.3 seconds"):
                    registry.add(str(root))
            self.assertEqual(registry.add(str(root))[1], True)           # released: it runs

    def test_a_file_system_without_the_lock_writes_without_it(self):
        def no_lock(fd):
            raise OSError(errno.EBADF, os.strerror(errno.EBADF))

        root = self.project("acme")
        self.home.mkdir()
        live = self.home / "projects.toml.4242.tmp"        # another write's, under way: without the lock, no sweep
        live.write_text("format = 1\n", encoding="utf-8")
        with self.in_process(), mock.patch.object(sync, "_try_lock", no_lock):
            self.assertEqual(registry.add(str(root)), (registry.Entry("acme", display(root)), True))
        self.assertTrue(live.is_file())

    def test_the_lock_is_a_file_beside_the_registry_never_replaced(self):
        self.assert_ok(self.run_cortex(self.project("acme"), "project", "add"))
        self.assertEqual(sorted(entry.name for entry in self.home.iterdir()), ["projects.toml", "projects.toml.lock"])


class RemoveTests(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.acme = self.project("acme")
        (self.acme / "src").mkdir()
        (self.acme / "src" / "main.py").write_bytes(b"print('hello')\r\n")
        self.other = self.project("other")
        for root in (self.acme, self.other):
            self.assert_ok(self.run_cortex(root, "project", "add"))

    def test_by_name_by_path_and_the_projects_files_byte_for_byte(self):
        files = snapshot(self.code)
        for cwd, key in ((self.tmp, "acme"), (self.tmp, "code/acme"), (self.code, "./acme"), (self.code / "other", ".."
                         "/acme"), (self.tmp, str(self.acme))):
            with self.subTest(key=key):
                proc = self.run_cortex(cwd, "project", "remove", key)
                self.assert_ok(proc)
                self.assertEqual(proc.out, f"dropped acme, {display(self.acme)}, from the registry — the project's files "
                                           "are as they were\n")
                self.assertEqual(self.entries(), [("other", display(self.other))])
                self.assertEqual(snapshot(self.code), files)
                self.assert_ok(self.run_cortex(self.acme, "project", "add"))

    def test_a_name_comes_before_a_path(self):
        # "other" names an entry, and ./other — from code/acme/.. — names another one's directory.
        self.write_registry(("other", self.acme), ("x", self.other))
        proc = self.run_cortex(self.code, "project", "remove", "other")
        self.assert_ok(proc)
        self.assertEqual(self.entries(), [("x", display(self.other))])

    def test_a_project_gone_is_removed_by_name_and_by_path(self):
        shutil.rmtree(self.acme)
        self.assert_ok(self.run_cortex(self.tmp, "project", "remove", str(self.acme)))
        shutil.rmtree(self.other)
        self.assert_ok(self.run_cortex(self.tmp, "project", "remove", "other"))
        self.assertEqual(self.entries(), [])

    def test_a_project_gone_through_a_link_is_removed_by_its_resolved_path(self):
        root = self.project("real/gone")
        link = self.tmp / "link"
        try:
            os.symlink(root.parent, link, target_is_directory=True)
        except OSError:
            self.skipTest("no symbolic link here")
        self.assert_ok(self.run_cortex(link / "gone", "project", "add"))
        shutil.rmtree(root)
        self.assert_ok(self.run_cortex(self.tmp, "project", "remove", str(root)))
        self.assertEqual([name for name, _ in self.entries()], ["acme", "other"])

    def test_what_names_no_entry_is_refused(self):
        before = self.registry.read_bytes()
        proc = self.run_cortex(self.tmp, "project", "remove", "nope")
        self.assertEqual(proc.returncode, 1)
        self.assertIn('no project is registered as "nope", by name or by path — `cortex project list` lists this '
                      "machine's projects", proc.err)
        self.assertEqual(self.registry.read_bytes(), before)

    def test_without_a_registry_nothing_is_made(self):
        self.env["CORTEX_HOME"] = str(self.tmp / "elsewhere")
        proc = self.run_cortex(self.tmp, "project", "remove", "acme")
        self.assertEqual(proc.returncode, 1)
        self.assertFalse((self.tmp / "elsewhere").exists())

    def test_drop_drops_the_entries_still_listed_in_one_write(self):
        with self.in_process():
            acme, other = registry.load()
            moved = registry.Entry("other", display(self.code / "moved"))      # not what the registry holds
            self.assertEqual(registry.drop([acme, moved]), [acme])
            self.assertEqual(registry.load(), [other])
            self.assertEqual(registry.drop([]), [])



class StateTestCase(ProjectTestCase):
    def register(self, root):
        self.assert_ok(self.run_cortex(root, "project", "add"))
        return root

    def listed(self):
        """The projects of list --json, its format checked: the cockpit reads it so."""
        proc = self.run_cortex(self.tmp, "project", "list", "--json")
        self.assert_ok(proc)
        found = json.loads(proc.out)
        self.assertEqual(list(found), ["format", "projects"])
        self.assertEqual(found["format"], 1)
        return found["projects"]

    def one(self, root):
        """What list --json says of the project at ``root``."""
        return next(found for found in self.listed() if found["path"] == display(root))

    def synced_elsewhere(self, root, version):
        """``cortex.local.toml`` naming a version of the store, as a sync writes it."""
        (self.home / "versions" / version).mkdir(parents=True, exist_ok=True)
        (root / "cortex.local.toml").write_text(f'spec = "{display(self.home / "versions" / version)}"\n',
                                                encoding="utf-8")


class ListTests(StateTestCase):
    def test_no_registry_and_an_empty_one_are_said(self):
        for case in ("none", "empty"):
            with self.subTest(registry=case):
                if case == "empty":
                    self.write_registry()
                proc = self.run_cortex(self.tmp, "project", "list")
                self.assert_ok(proc)
                self.assertEqual(proc.out, NONE)
                self.assertEqual(self.listed(), [])

    def test_each_entry_in_name_order_with_its_state_and_versions(self):
        ok = self.register(self.project("zeta", version=OWN))
        self.assert_ok(self.run_cortex(ok, "sync"))
        gone = self.register(self.project("gone"))
        shutil.rmtree(gone)
        bare = self.register(self.project("bare"))
        (bare / "cortex.toml").unlink()
        odd = self.register(self.project("odd"))
        (odd / "cortex.toml").write_text('version = "1.4.0"\ntheme = "h2g2"\nlater = "x"\n', encoding="utf-8")
        why = 'cortex.toml: unknown key "later" — cortex.toml takes version, theme, sync and claude_access'
        self.assertEqual(self.listed(), [
            {"name": "bare", "path": display(bare), "state": "no-project", "pinned": None, "reads": None,
             "layout": None, "why": None},
            {"name": "gone", "path": display(gone), "state": "missing", "pinned": None, "reads": None, "layout": None,
             "why": None},
            {"name": "odd", "path": display(odd), "state": "unreadable", "pinned": "1.4.0", "reads": None,
             "layout": None, "why": why},
            {"name": "zeta", "path": display(ok), "state": "ok", "pinned": OWN, "reads": OWN, "layout": None,
             "why": None}])
        proc = self.run_cortex(self.tmp, "project", "list")
        self.assert_ok(proc)
        width, pinned = len("no-project"), max(len("PINNED"), len(OWN))       # OWN is a binary's version
        self.assertEqual(proc.out.splitlines(), [
            f"NAME  {'STATE':<{width}}  {'PINNED':<{pinned}}  {'READS':<{len(OWN)}}  LAYOUT  PATH",
            f"bare  no-project  {'-':<{pinned}}  {'-':<{len(OWN)}}  -       {display(bare)}",
            f"gone  missing     {'-':<{pinned}}  {'-':<{len(OWN)}}  -       {display(gone)}",
            f"odd   unreadable  {'1.4.0':<{pinned}}  {'-':<{len(OWN)}}  -       {display(odd)} — {why}",
            f"zeta  ok          {OWN:<{pinned}}  {OWN}  -       {display(ok)}"])

    def test_a_file_written_by_hand_is_listed_in_name_order(self):
        self.write_registry(("zeta", self.code / "z"), ("alpha", self.code / "a"))
        self.assertEqual([found["name"] for found in self.listed()], ["alpha", "zeta"])

    def test_the_version_read_now_is_the_spec_of_cortex_local_toml(self):
        root = self.register(self.project("acme", version="9.9.8"))
        self.assertEqual(self.one(root)["reads"], None)                  # not synced here
        self.synced_elsewhere(root, "9.9.7")                              # a bump pulled, not synced yet
        self.assertEqual((self.one(root)["pinned"], self.one(root)["reads"]), ("9.9.8", "9.9.7"))
        os.rmdir(self.home / "versions" / "9.9.7")                       # pruned since
        self.assertEqual(self.one(root)["reads"], None)
        checkout = self.tmp / "checkout"
        checkout.mkdir()
        (root / "cortex.local.toml").write_text(f'spec = "{display(checkout)}"\n', encoding="utf-8")
        self.assertEqual(self.one(root)["reads"], None)                  # cortex sync --from: no version

    def test_the_layout_is_the_marker_of_the_bootstrap(self):
        root = self.register(self.project("acme"))
        marker = "# Cortex\n<!-- cortex:bootstrap layout={} format=1 digest=0123456789abcdef -->\n"
        self.assertEqual(self.one(root)["layout"], None)                 # no bootstrap
        (root / "CLAUDE.md").write_bytes(b"\xef\xbb\xbf" + marker.format("workspace").replace("\n", "\r\n").encode())
        self.assertEqual(self.one(root)["layout"], "workspace")
        (root / "AGENTS.md").write_text(marker.format("workspace"), encoding="utf-8")
        self.assertEqual(self.one(root)["layout"], "workspace")
        (root / ".github").mkdir()
        (root / ".github" / "copilot-instructions.md").write_text(marker.format("single"), encoding="utf-8")
        self.assertEqual(self.one(root)["layout"], None)                 # two layouts: not known
        (root / "CLAUDE.md").write_text("# Mine, with no marker\n", encoding="utf-8")
        (root / "AGENTS.md").unlink()
        self.assertEqual(self.one(root)["layout"], "single")

    @PERMISSIONS
    def test_a_directory_that_cannot_be_read_is_unreadable_never_gone(self):
        inside = self.register(self.project("closed/acme"))
        locked = self.register(self.project("locked"))
        os.chmod(self.code / "closed", 0)
        os.chmod(locked, 0)
        for root in (inside, locked):
            with self.subTest(root=root.name):
                found = self.one(root)
                self.assertEqual(found["state"], "unreadable")
                self.assertIn("Permission denied", found["why"])

    @PERMISSIONS
    def test_a_project_file_that_cannot_be_read_is_unreadable(self):
        root = self.register(self.project("acme"))
        os.chmod(root / "cortex.toml", 0)
        found = self.one(root)
        self.assertEqual((found["state"], found["pinned"]), ("unreadable", None))
        self.assertIn("Permission denied", found["why"])

    def test_a_registry_it_refuses_names_the_file_and_what_is_wrong(self):
        acme = self.code / "acme"
        for text, fragment in (('format = 1\nextra = "x"\n', 'unknown key "extra"'),
                               (f'format = 1\n\n[[project]]\nname = "Acme"\npath = "{display(acme)}"\n',
                                'project 1: name "Acme" is no project name'),
                               (f'format = 1\n\n[[project]]\nname = "a"\npath = "{display(acme)}"\n\n[[project]]\n'
                                f'name = "b"\npath = "{display(acme)}/"\n', "project 2: path .* names the directory of "
                                                                            "project 1"),
                               (f'format = 1\n\n[[project]]\nname = "a"\npath = "{display(acme)}\\u0000"\n',
                                "project 1: path .* holds a control character"),
                               ("format = 2\n", "format 2 is written by a later Cortex .* cortex self-update")):
            for action in (("list",), ("list", "--json"), ("check",)):
                with self.subTest(text=text, action=action):
                    self.home.mkdir(exist_ok=True)
                    self.registry.write_text(text, encoding="utf-8")
                    proc = self.run_cortex(self.tmp, "project", *action)
                    self.assertEqual(proc.returncode, 1)
                    self.assertEqual(proc.out, "")
                    self.assertRegex(proc.err.replace("\n", " "), f"^cortex project {action[0]}: "
                                     + display(self.registry).replace(".", "\\.") + f": .*{fragment}")


class CheckTests(StateTestCase):
    def setUp(self):
        super().setUp()
        self.ok = self.register(self.project("acme"))

    def test_every_project_ok_exits_0(self):
        proc = self.run_cortex(self.tmp, "project", "check")
        self.assert_ok(proc)
        self.assertEqual(proc.out, "1 project(s) registered, every one ok\n")

    def test_no_registry_and_an_empty_one_exit_0(self):
        self.registry.unlink()
        for case in ("none", "empty"):
            with self.subTest(registry=case):
                if case == "empty":
                    self.write_registry()
                proc = self.run_cortex(self.tmp, "project", "check")
                self.assert_ok(proc)
                self.assertEqual(proc.out, NONE)

    def test_each_entry_not_ok_exits_1_and_nothing_is_removed(self):
        def gone(root):
            shutil.rmtree(root)

        def bare(root):
            (root / "cortex.toml").unlink()

        def odd(root):
            (root / "cortex.toml").write_text("version = \n", encoding="utf-8")

        for name, spoil, said in (("gone", gone, "gone: missing — no directory at {}"),
                                  ("bare", bare, "bare: no-project — {} holds no cortex.toml"),
                                  ("odd", odd, "odd: unreadable — {}: cortex.toml: Invalid value")):
            with self.subTest(state=name):
                root = self.register(self.project(name))
                spoil(root)
                before, files = self.registry.read_bytes(), snapshot(self.code)
                proc = self.run_cortex(self.tmp, "project", "check")
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertTrue(proc.out.startswith(said.format(display(root))), proc.out)
                self.assertNotIn("acme", proc.out)
                self.assertTrue(proc.out.endswith("1 of 2 registered project(s) not ok. Nothing was removed: `cortex "
                                                  "project remove NAME` drops an entry, and `cortex prune` drops those "
                                                  "whose project is gone\n"), proc.out)
                self.assertEqual(self.registry.read_bytes(), before)
                self.assertEqual(snapshot(self.code), files)
                self.assert_ok(self.run_cortex(self.tmp, "project", "remove", name))


class StateTests(StateTestCase):
    """``state``, as the next commands call it: what it says of each entry, and that it reads only."""

    def test_a_path_that_is_a_file_is_missing(self):
        self.code.mkdir()
        (self.code / "file").write_text("x\n", encoding="utf-8")
        entry = registry.Entry("file", display(self.code / "file"))
        self.assertEqual(registry.state(entry), registry.State(entry, registry.MISSING))

    def test_a_path_under_a_file_is_missing(self):
        self.code.mkdir()
        (self.code / "file").write_text("x\n", encoding="utf-8")
        entry = registry.Entry("under", display(self.code / "file" / "acme"))
        self.assertEqual(registry.state(entry), registry.State(entry, registry.MISSING))

    def test_project_files_that_cannot_be_read_still_give_their_versions(self):
        # cortex.local.toml that cannot be read, as root too: its versions read as prune reads them.
        root = self.project("acme", version="9.9.8")
        self.synced_elsewhere(root, "9.9.7")
        entry = registry.Entry("acme", display(root))
        with self.in_process(), mock.patch.object(config, "load", side_effect=PermissionError(errno.EACCES,
                                                                                               "Permission denied")):
            found = registry.state(entry)
        self.assertEqual((found.state, found.pinned, found.reads), (registry.UNREADABLE, "9.9.8", "9.9.7"))
        self.assertIn("Permission denied", found.why)

    def test_a_cortex_local_toml_left_says_the_version_it_reads(self):
        root = self.project("away", version="9.9.8")
        self.synced_elsewhere(root, "9.9.7")
        (root / "cortex.toml").unlink()                 # a branch without Cortex checked out
        entry = registry.Entry("away", display(root))
        self.assertEqual(registry.state(entry), registry.State(entry, registry.NO_PROJECT, reads="9.9.7"))
        shutil.rmtree(self.home / "versions" / "9.9.7")     # the version it read is gone
        self.assertEqual(registry.state(entry), registry.State(entry, registry.NO_PROJECT))

    def test_a_cortex_toml_that_is_a_directory_is_no_project(self):
        root = self.code / "odd"
        (root / "cortex.toml").mkdir(parents=True)
        entry = registry.Entry("odd", display(root))
        self.assertEqual(registry.state(entry).state, registry.NO_PROJECT)

    def test_it_writes_nothing(self):
        root = self.register(self.project("acme", version=OWN))
        self.assert_ok(self.run_cortex(root, "sync"))
        files, before = snapshot(self.tmp), self.registry.read_bytes()
        with self.in_process():
            found = registry.state(registry.load()[0])
        self.assertEqual((found.state, found.pinned, found.reads), (registry.OK, OWN, OWN))
        self.assertEqual(snapshot(self.tmp), files)
        self.assertEqual(self.registry.read_bytes(), before)


class HelpTests(ProjectTestCase):
    def test_the_command_is_listed_and_its_actions_too(self):
        proc = self.run_cortex(self.tmp, "--help")
        self.assertIn("\n  project      List this machine's Cortex projects", proc.out)
        proc = self.run_cortex(self.tmp, "project", "--help")
        self.assert_ok(proc)
        for action in ("add", "list", "remove", "check"):
            self.assertIn(f"    {action} ", proc.out)

    def test_no_action_is_a_bad_argument(self):
        proc = self.run_cortex(self.tmp, "project")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("ACTION", proc.err)


if __name__ == "__main__":
    unittest.main()
