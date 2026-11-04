"""``cortex project`` and ``projects.toml`` — the Cortex projects of this machine (ADR-011 §3.1, §3.2).

The registry is ``$CORTEX_HOME/projects.toml``, in a home of each test's own. The tests run the
command — from source, or through a built binary — and, for what a process cannot show, such as a
write slowed down to meet another, the module itself.
"""

import errno
import io
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
        self.env = harness.environment(CORTEX_HOME=str(self.home))
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
        with self.in_process(), mock.patch.object(sync, "_try_lock", no_lock):
            self.assertEqual(registry.add(str(root)), (registry.Entry("acme", display(root)), True))

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


if __name__ == "__main__":
    unittest.main()
