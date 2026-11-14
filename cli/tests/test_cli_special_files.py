"""Named pipes, sockets and other special files at paths Cortex reads (#358).

1.8.0 reads the project's ``.gitignore`` and the team's file without blocking. A named pipe at any other file Cortex
reads would hold the command with no limit, and anything there that is no file was read as no file at all: each is
said not to be a file, never waited on.
"""

import os
import socket
import stat
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cli_harness as harness  # noqa: E402
from test_cli_team import DOWNLOADS, GIT, GIT_ENV, OWN, TeamTestCase, harness_path  # noqa: E402

from cortex_cli.paths import display  # noqa: E402

PIPES = unittest.skipUnless(hasattr(os, "mkfifo"), "no named pipe here")
LINKS = unittest.skipIf(os.name == "nt", "a link needs a privilege on Windows")
# What is no file and no folder, as a test makes it here: a named pipe, a socket.
SPECIALS = (("pipe",) if hasattr(os, "mkfifo") else ()) + (("socket",) if hasattr(socket, "AF_UNIX") else ())


class SpecialFileTestCase(TeamTestCase):
    def within(self, cwd, *args, seconds=30, **env):
        """``cortex ARGS``, failed — never waited on — when it does not end within ``seconds``."""
        try:
            proc = subprocess.run(harness.command(*args), cwd=cwd, env={**self.env, "PWD": str(cwd), **env},
                                  stdin=subprocess.DEVNULL, capture_output=True, timeout=seconds)
        except subprocess.TimeoutExpired:
            self.fail(f"cortex {' '.join(args)} did not end: it waits on a named pipe")
        proc.out, proc.err = proc.stdout.decode(), proc.stderr.decode()
        return proc

    def make(self, path, kind):
        """At ``path``: a file, a named pipe, a socket or — as root — a device, as ``/dev/null`` is."""
        if kind == "file":
            path.write_bytes(b"notes\n")
        elif kind == "pipe":
            os.mkfifo(path)
        elif kind == "device":
            os.mknod(path, stat.S_IFCHR | 0o666, os.makedev(1, 3))
        else:
            # Bound by its name alone, from its folder: an AF_UNIX path holds about 104 bytes, and macOS's
            # TMPDIR (/private/var/folders/…) alone comes close to that.
            here = os.getcwd()
            os.chdir(path.parent)
            try:
                with socket.socket(socket.AF_UNIX) as bound:
                    bound.bind(path.name)
            finally:
                os.chdir(here)


class GitmodulesTests(SpecialFileTestCase):
    """``.gitmodules``, which says whether ``cortex/`` or the team's folder is a submodule."""

    NOTE = ("note: .gitmodules is not a file, which git cannot read, and Cortex reads it as naming no submodule: make "
            "it a file, or remove it\n")

    @PIPES
    @harness.outside_a_repository
    def test_a_named_pipe_at_gitmodules_holds_neither_sync_nor_validate_beside_a_team_s_folder(self):
        # The team's folder there, empty: whether it is a submodule not checked out is asked of .gitmodules — and of
        # git, where it is on the PATH. Neither waits on the pipe; the note says what it is, and its remedy, followed,
        # ends it.
        kinds = ("no git",) + (("git",) if GIT is not None and os.name != "nt" else ())
        for kind in kinds:
            with self.subTest(kind=kind):
                root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', name=f"modules-{kind}",
                                    committed=kind == "no git")
                if kind == "git":
                    self.git(root, "init", "-q")
                (root / "agents").mkdir()
                os.mkfifo(root / ".gitmodules")
                env = {"PATH": os.path.dirname(GIT), **GIT_ENV} if kind == "git" else {}
                for args in (("sync",), ("validate",)):
                    proc = self.within(root, *args, **env)
                    self.assert_ok(proc)
                    self.assertIn(self.NOTE, proc.err)
                    self.assertNotIn("Traceback", proc.err)
                (root / ".gitmodules").unlink()
                for args in (("sync",), ("validate",)):
                    proc = self.within(root, *args, **env)
                    self.assert_ok(proc)
                    self.assertNotIn(".gitmodules", proc.err)

    @PIPES
    @harness.outside_a_repository
    def test_a_named_pipe_at_gitmodules_holds_no_sync_link_beside_a_cortex_directory(self):
        # A cortex/ that sync did not make: what it is — a submodule, a clone — is asked of .gitmodules, and of git.
        # A clone made by hand, and a submodule's cortex/, whose .git names its repository: how to leave it reads
        # .gitmodules again, for whether it is the only submodule.
        kinds = ("no git",) + (("git",) if GIT is not None and os.name != "nt" else ())
        for kind in kinds:
            for layout in ("clone", "submodule"):
                with self.subTest(kind=kind, layout=layout):
                    root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', name=f"link-{kind}-{layout}",
                                        committed=kind == "no git")
                    if kind == "git":
                        self.git(root, "init", "-q")
                    (root / "cortex").mkdir()
                    if layout == "clone":
                        (root / "cortex" / ".git").mkdir()
                    else:
                        (root / "cortex" / ".git").write_text("gitdir: ../.git/modules/cortex\n", encoding="utf-8")
                    os.mkfifo(root / ".gitmodules")
                    env = {"PATH": os.path.dirname(GIT), **GIT_ENV} if kind == "git" else {}
                    proc = self.within(root, "sync", "--link", **env)
                    self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                    self.assertIn(GitmodulesTests.NOTE, proc.err)
                    self.assertIn(f"cortex sync: cortex/ is a git {layout}, which Cortex did not write.", proc.err)


class InstructionsFileTests(SpecialFileTestCase):
    """The tool's instructions file, which ``cortex init`` reads before it keeps or replaces it."""

    @PIPES
    @harness.outside_a_repository
    def test_a_named_pipe_at_the_instructions_file_is_refused_before_init_writes_anything(self):
        for force in ((), ("--force",)):
            with self.subTest(force=force):
                root = self.tmp / f"init{''.join(force)}"
                root.mkdir()
                os.mkfifo(root / "CLAUDE.md")
                proc = self.within(root, "init", "--tool", "claude", *force)
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn(f"the instructions file {display(str(root / 'CLAUDE.md'))} is not a file: remove "
                              "it, then run cortex init again, which writes it\n", proc.err)
                self.assertEqual(sorted(path.name for path in root.iterdir()), ["CLAUDE.md"])
                (root / "CLAUDE.md").unlink()
                self.assert_ok(self.within(root, "init", "--tool", "claude", *force))
                self.assertTrue((root / "CLAUDE.md").is_file())


class FolderToMakeTests(SpecialFileTestCase):
    """A folder ``cortex init`` makes — a service's, the tool's — where something that is no folder is."""

    @harness.outside_a_repository
    def test_what_is_no_folder_where_init_makes_one_is_said_as_it_is(self):
        # A named pipe or a socket there was said to be a file. It is named as it is — a link to one as a link — and
        # refused before anything is written; removed, init makes the folder.
        links = (False,) + ((True,) if os.name != "nt" else ())
        for kind in SPECIALS:
            for link in links:
                for args, name in ((("--workspace", "--service", "api/x"), "api"), (("--tool", "cursor"), ".cursor")):
                    with self.subTest(kind=kind, link=link, name=name):
                        root = self.tmp / f"make-{kind}-{link}-{name}"
                        root.mkdir()
                        if link:
                            self.make(self.tmp / f"{root.name}-target", kind)
                            (root / name).symlink_to(self.tmp / f"{root.name}-target")
                        else:
                            self.make(root / name, kind)
                        what = {"pipe": "a named pipe", "socket": "a socket"}[kind]
                        what = f"a link to {what}" if link else what
                        where = (f"--service api/x: {display(str(root / 'api'))}" if name == "api" else
                                 f"the instructions file {display(str(root / '.cursor' / 'rules' / 'cortex.mdc'))}: "
                                 f"{display(str(root / '.cursor'))}")
                        proc = self.within(root, "init", *args)
                        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                        self.assertIn(f"{where} is {what}, not a folder\n", proc.err)
                        self.assertNotIn("is a file", proc.err)
                        self.assertEqual(sorted(path.name for path in root.iterdir()), [name])
                        (root / name).unlink()
                        self.assert_ok(self.within(root, "init", *args))
                        self.assertTrue((root / name).is_dir())

    def assert_said_as_it_is(self, name, kind, what):
        """``cortex init``, where it makes the folder ``name``, refused: ``what`` is there, as ``kind``, or a link to
        it — said once, and a link as a link; removed, init makes the folder."""
        args = ("--workspace", "--service", "api/x") if name == "api" else ("--tool", "cursor")
        for link in (False,) + ((True,) if os.name != "nt" else ()):
            with self.subTest(kind=kind, link=link, name=name):
                root = self.tmp / f"said-{kind}-{link}-{name}"
                root.mkdir()
                if link:
                    self.make(self.tmp / f"{root.name}-target", kind)
                    (root / name).symlink_to(self.tmp / f"{root.name}-target")
                else:
                    self.make(root / name, kind)
                said = f"a link to {what}" if link else what
                where = (f"--service api/x: {display(str(root / 'api'))}" if name == "api" else
                         f"the instructions file {display(str(root / '.cursor' / 'rules' / 'cortex.mdc'))}: "
                         f"{display(str(root / '.cursor'))}")
                proc = self.within(root, "init", *args)
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn(f"{where} is {said}, not a folder\n", proc.err)
                message = proc.err
                for shown in sorted({display(str(root)), str(root), os.path.realpath(root),
                                     display(os.path.realpath(root))}, key=len, reverse=True):
                    message = message.replace(shown, "ROOT")     # macOS's TMPDIR holds "folders"
                self.assertEqual(message.count("folder"), 1, proc.err)
                self.assertEqual(sorted(path.name for path in root.iterdir()), [name])
                (root / name).unlink()
                self.assert_ok(self.within(root, "init", *args))
                self.assertTrue((root / name).is_dir())

    @harness.outside_a_repository
    def test_a_file_where_init_makes_a_folder_is_said_a_file_and_a_link_to_one_a_link(self):
        for name in ("api", ".cursor"):
            self.assert_said_as_it_is(name, "file", "a file")

    # Root only, the reverse of the usual rule: only root can make a device.
    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "only root can make a device")
    @harness.outside_a_repository
    def test_a_device_where_init_makes_a_folder_is_said_a_device_and_a_link_to_one_a_link(self):
        # A device was said to be "neither a file nor a folder, not a folder", and a link to one lost the word link.
        for name in ("api", ".cursor"):
            self.assert_said_as_it_is(name, "device", "a device")


class ProjectFileTests(SpecialFileTestCase):
    """``cortex.toml``, which makes a directory a Cortex project."""

    @harness.outside_a_repository
    def test_a_cortex_toml_that_is_not_a_file_is_said_not_a_file_never_missing(self):
        # Read as no cortex.toml at all, it was said missing, and `cortex init` named — which keeps it. What it is
        # is said, with its remedy, which, followed, lets the commands run.
        kinds = ("directory",) + (("pipe",) if hasattr(os, "mkfifo") else ())
        for kind in kinds:
            with self.subTest(kind=kind):
                root = self.tmp / f"project-{kind}"
                root.mkdir()
                if kind == "directory":
                    (root / "cortex.toml").mkdir()
                else:
                    os.mkfifo(root / "cortex.toml")
                said = (f"{display(str(root / 'cortex.toml'))} is not a file: remove it, then `cortex init` makes the "
                        "directory a Cortex project\n")
                for args in (("sync",), ("validate",), ("upgrade",), ("upgrade", OWN), ("upgrade", "--local", OWN),
                             ("upgrade", "--team")):
                    proc = self.within(root, *args)
                    self.assertNotEqual(proc.returncode, 0, proc.out + proc.err)
                    self.assertIn(f"cortex {args[0]}: {said}", proc.err)
                    self.assertNotIn("no cortex.toml", proc.err)
                if kind == "directory":
                    (root / "cortex.toml").rmdir()
                else:
                    (root / "cortex.toml").unlink()
                self.assert_ok(self.within(root, "init", "--tool", "claude"))
                for args in (("sync",), ("validate",)):
                    self.assert_ok(self.within(root, *args))

    @harness.outside_a_repository
    def test_a_cortex_toml_that_is_not_a_file_above_the_folder_a_command_runs_in_is_said_not_a_file(self):
        # Looked for from a folder of the project, as a project is found: the nearest cortex.toml above it, not a
        # file, is named where it is — and its remedy, followed, lets the commands run from that folder.
        kinds = ("directory",) + (("pipe",) if hasattr(os, "mkfifo") else ())
        for kind in kinds:
            with self.subTest(kind=kind):
                root = self.tmp / f"above-{kind}"
                below = root / "src" / "app"
                below.mkdir(parents=True)
                if kind == "directory":
                    (root / "cortex.toml").mkdir()
                else:
                    os.mkfifo(root / "cortex.toml")
                said = (f"{display(str(root / 'cortex.toml'))} is not a file: remove it, then `cortex init` makes the "
                        "directory a Cortex project\n")
                for args in (("sync",), ("validate",), ("upgrade",), ("upgrade", "--local", OWN)):
                    proc = self.within(below, *args)
                    self.assertNotEqual(proc.returncode, 0, proc.out + proc.err)
                    self.assertIn(f"cortex {args[0]}: {said}", proc.err)
                    self.assertNotIn("no cortex.toml", proc.err)
                if kind == "directory":
                    (root / "cortex.toml").rmdir()
                else:
                    (root / "cortex.toml").unlink()
                self.assert_ok(self.within(root, "init", "--tool", "claude"))
                for args in (("sync",), ("validate",)):
                    self.assert_ok(self.within(below, *args))


class LockTests(SpecialFileTestCase):
    """``cortex.lock``, and the team's lock beside the team's file."""

    @harness.outside_a_repository
    def test_a_lock_that_is_not_a_file_is_said_not_a_file_never_missing(self):
        # Read as no lock, it was said missing — LOCK_MISSING, `--locked: no cortex.lock` — and a sync wrote over a
        # named pipe. What it is is said, with its remedy, which, followed, lets the sync write it again.
        kinds = ("directory",) + (("pipe",) if hasattr(os, "mkfifo") else ())
        for kind in kinds:
            for lock in ("cortex.lock", "agents/cortex.team.lock"):
                with self.subTest(kind=kind, lock=lock):
                    team = lock.startswith("agents/")
                    pin = f'version = "{OWN}"\n'
                    root = self.project('theme = "h2g2"\n' if team else pin + 'theme = "h2g2"\n',
                                        team=pin if team else None, name=f"lock-{kind}-{team}")
                    self.assert_ok(self.within(root, "sync"))
                    (root / lock).unlink()
                    if kind == "directory":
                        (root / lock).mkdir()
                    else:
                        os.mkfifo(root / lock)
                    said = f"{lock} is not a file — delete it, and `cortex sync` writes it again"
                    proc = self.within(root, "validate")
                    self.assert_ok(proc)
                    self.assertIn(f"LOCK_INVALID — {said}\n", proc.out)
                    self.assertNotIn("LOCK_MISSING", proc.out)
                    for args in (("sync",), ("sync", "--locked")):
                        proc = self.within(root, *args)
                        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                        self.assertIn(f"cortex sync: {said}\n", proc.err)
                    if kind == "directory":
                        (root / lock).rmdir()
                    else:
                        (root / lock).unlink()
                    self.assert_ok(self.within(root, "sync"))
                    self.assertTrue((root / lock).is_file())
                    self.assertNotIn("LOCK_", self.within(root, "validate").out)


class LocalFileTests(SpecialFileTestCase):
    """``cortex.local.toml``, which ``cortex sync`` writes."""

    @harness.outside_a_repository
    def test_a_cortex_local_toml_that_is_not_a_file_is_said_before_the_sync_writes_anything(self):
        # Read as none, as `cortex config` reads it, a directory there failed the sync only when it wrote the file,
        # with a raw `Is a directory`. Said first, with its remedy, which, followed, lets the sync write it.
        kinds = ("directory",) + (("pipe",) if hasattr(os, "mkfifo") else ())
        for kind in kinds:
            with self.subTest(kind=kind):
                root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', name=f"local-{kind}")
                if kind == "directory":
                    (root / "cortex.local.toml").mkdir()
                else:
                    os.mkfifo(root / "cortex.local.toml")
                before = sorted(path.name for path in root.iterdir())
                proc = self.within(root, "sync")
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn("cortex sync: cortex.local.toml is not a file — remove it, and `cortex sync` writes it "
                              "again. Nothing of the project was changed\n", proc.err)
                self.assertNotIn(".tmp", proc.err)
                self.assertEqual(sorted(path.name for path in root.iterdir()), before)
                if kind == "directory":
                    (root / "cortex.local.toml").rmdir()
                else:
                    (root / "cortex.local.toml").unlink()
                self.assert_ok(self.within(root, "sync"))
                self.assertTrue((root / "cortex.local.toml").is_file())

    @DOWNLOADS
    @harness.outside_a_repository
    def test_a_cortex_local_toml_that_is_not_a_file_is_said_before_upgrade_local_writes_the_try(self):
        # The try written first, then synced: a directory there failed with a raw `Is a directory`, and a named pipe
        # was written over. Said first, as the sync says it, and nothing changed — the store neither: its remedy,
        # followed, lets the try be written.
        kinds = ("directory",) + (("pipe",) if hasattr(os, "mkfifo") else ())
        for kind in kinds:
            with self.subTest(kind=kind):
                root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n', name=f"try-{kind}")
                self.assert_ok(self.within(root, "sync"))
                (root / "cortex.local.toml").unlink()
                if kind == "directory":
                    (root / "cortex.local.toml").mkdir()
                else:
                    os.mkfifo(root / "cortex.local.toml")
                before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
                stored = sorted(os.listdir(self.home / "versions"))
                for args in (("--local", OWN), ("--local", OWN, "--dry-run"), ("--local", "9.9.8")):
                    proc = self.within(root, "upgrade", *args)
                    self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                    self.assertEqual("cortex upgrade: cortex.local.toml is not a file — remove it, and `cortex sync` "
                                     "writes it again. Nothing was changed\n", proc.err)
                    self.assertEqual({path: path.read_bytes() for path in root.rglob("*") if path.is_file()}, before)
                    self.assertEqual(sorted(os.listdir(self.home / "versions")), stored)
                    self.assertFalse((root / "cortex.local.toml").is_file())
                if kind == "directory":
                    (root / "cortex.local.toml").rmdir()
                else:
                    (root / "cortex.local.toml").unlink()
                self.assert_ok(self.within(root, "sync"))
                self.assertTrue((root / "cortex.local.toml").is_file())
                self.assert_ok(self.within(root, "upgrade", "--local", OWN))
                self.assertIn(f'version = "{OWN}"', (root / "cortex.local.toml").read_text(encoding="utf-8"))

    def make_a_broken_link(self, root, kind):
        """At ``root``'s ``cortex.local.toml``: a link that leads nowhere, or a link to itself."""
        local = root / "cortex.local.toml"
        local.symlink_to(root / "nowhere.toml" if kind == "nowhere" else local)

    @LINKS
    @harness.outside_a_repository
    def test_a_cortex_local_toml_link_that_leads_nowhere_or_to_itself_is_replaced_by_the_sync(self):
        # No file there, read as none: the sync writes the file in its place, never refused as not a file, and never
        # through the link.
        for kind in ("nowhere", "loop"):
            with self.subTest(kind=kind):
                root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', name=f"broken-{kind}")
                self.make_a_broken_link(root, kind)
                proc = self.within(root, "sync")
                self.assert_ok(proc)
                self.assertNotIn("not a file", proc.err)
                self.assertFalse((root / "cortex.local.toml").is_symlink())
                self.assertTrue((root / "cortex.local.toml").is_file())
                self.assertFalse((root / "nowhere.toml").exists())

    @LINKS
    @DOWNLOADS
    @harness.outside_a_repository
    def test_a_cortex_local_toml_link_that_leads_nowhere_or_to_itself_is_replaced_by_upgrade_local(self):
        # As the sync does: the try is written in its place, never refused as not a file.
        for kind in ("nowhere", "loop"):
            with self.subTest(kind=kind):
                root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n', name=f"broken-try-{kind}")
                self.assert_ok(self.within(root, "sync"))
                (root / "cortex.local.toml").unlink()
                self.make_a_broken_link(root, kind)
                proc = self.within(root, "upgrade", "--local", OWN)
                self.assert_ok(proc)
                self.assertNotIn("not a file", proc.err)
                self.assertFalse((root / "cortex.local.toml").is_symlink())
                self.assertIn(f'version = "{OWN}"', (root / "cortex.local.toml").read_text(encoding="utf-8"))
                self.assertFalse((root / "nowhere.toml").exists())

    @harness.outside_a_repository
    def test_validate_names_the_remedy_of_the_sync_for_a_cortex_local_toml_that_is_not_a_file(self):
        # Read as none, it named no spec, and `run cortex sync` — which refuses it. The sync's own remedy is named,
        # which, followed, lets validate run.
        kinds = ("directory",) + (("pipe",) if hasattr(os, "mkfifo") else ())
        for kind in kinds:
            with self.subTest(kind=kind):
                root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', name=f"check-{kind}")
                self.assert_ok(self.within(root, "sync"))
                (root / "cortex.local.toml").unlink()
                if kind == "directory":
                    (root / "cortex.local.toml").mkdir()
                else:
                    os.mkfifo(root / "cortex.local.toml")
                proc = self.within(root, "validate")
                self.assertEqual(proc.returncode, 2, proc.out + proc.err)
                self.assertEqual("cortex validate: cortex.local.toml is not a file — remove it, and `cortex sync` "
                                 "writes it again\n", proc.err)
                if kind == "directory":
                    (root / "cortex.local.toml").rmdir()
                else:
                    (root / "cortex.local.toml").unlink()
                self.assert_ok(self.within(root, "sync"))
                self.assert_ok(self.within(root, "validate"))


class RenamedTeamFolderTests(SpecialFileTestCase):
    """A team's folder ``cortex.toml`` renames, where something that is no folder is (``TEAM_FOLDER_MISSING``)."""

    FOLDER = "team-agents"

    @LINKS
    def test_what_is_no_folder_at_a_renamed_team_s_folder_is_named_with_its_remedies(self):
        # #350, #375: a file, a named pipe, a socket, or a link to one, was said not to be there — check it out
        # there, which no checkout does over it. Named as the default agents/ is, each remedy, followed, ends it.
        folder = self.FOLDER
        cases = [("file", "remove it")] + [(kind, "remove it") for kind in SPECIALS] + \
            [(f"link to {kind}", remedy) for kind in ("file",) + SPECIALS
             for remedy in ("lead to the team's folder", "remove it")]
        for n, (case, remedy) in enumerate(cases):
            with self.subTest(case=case, remedy=remedy):
                root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\nagents = "{folder}"\n', name=f"app-{n}")
                kind = case.rpartition(" ")[2]
                if case.startswith("link"):
                    aside = self.tmp / f"aside-{n}"
                    self.make(aside, kind)
                    (root / folder).symlink_to(aside)
                    what = "a file" if kind == "file" else "which is no folder"
                    said = (f'{folder}/ is a link to "{harness_path(aside)}", {what}: make it lead to the team\'s '
                            "folder, or remove it and check that folder out in its place")
                else:
                    self.make(root / folder, kind)
                    what = "a file, not the team's folder" if kind == "file" else "no folder"
                    said = f"{folder} is {what}: remove it and check the team's folder out in its place"
                for args in (("sync",), ("init", "--tool", "claude")):
                    proc = self.within(root, *args)
                    self.assert_ok(proc)
                    self.assertIn(f"warning: {said}\n", proc.err)
                    self.assertNotIn("is not there", proc.err)
                proc = self.within(root, "sync", "--locked")
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn(f"--locked: {said}. Nothing of the project was changed", proc.err)
                proc = self.within(root, "validate")
                self.assertIn(f"TEAM_FOLDER_MISSING — {said}\n", proc.out)
                (root / folder).unlink()
                if remedy == "remove it":
                    (root / folder).mkdir()                             # the team's folder, checked out in its place
                else:
                    team = self.tmp / f"team-{n}"                       # where the team's folder is
                    team.mkdir()
                    (root / folder).symlink_to(team, target_is_directory=True)
                proc = self.within(root, "sync")
                self.assert_ok(proc)
                self.assertNotIn(f"{folder}", proc.err)
                self.assertNotIn("TEAM_FOLDER_MISSING", self.within(root, "validate").out)


class DefaultTeamFolderTests(SpecialFileTestCase):
    """The default ``agents``, beside a ``cortex.toml`` without ``version``: what is no folder there (#375)."""

    @LINKS
    @unittest.skipUnless(SPECIALS, "no named pipe, nor socket, here")
    def test_a_named_pipe_or_a_socket_at_agents_or_a_link_to_one_is_named_no_folder(self):
        # 1.8.4's branches for what is neither a file nor a folder, untested: never "is not there", with
        # `git submodule update --init`, which leaves it. Each remedy, followed, lets the sync run.
        cases = [(kind, "remove it") for kind in SPECIALS] + \
            [(f"link to {kind}", remedy) for kind in SPECIALS for remedy in ("lead to the team's folder", "remove it")]
        for n, (case, remedy) in enumerate(cases):
            with self.subTest(case=case, remedy=remedy):
                root = self.project('theme = "h2g2"\n', name=f"default-{n}")
                kind = case.rpartition(" ")[2]
                if case.startswith("link"):
                    aside = self.tmp / f"aside-{n}"
                    self.make(aside, kind)
                    (root / "agents").symlink_to(aside)
                    said = (f'agents/ is a link to "{harness_path(aside)}", which is no folder: make it lead to the '
                            "team's folder, or remove it and check that folder out in its place\n")
                else:
                    self.make(root / "agents", kind)
                    said = "agents is no folder: remove it and check the team's folder out in its place\n"
                for args in (("sync",), ("init", "--tool", "claude")):
                    proc = self.within(root, *args)
                    self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                    self.assertIn(f'cortex.toml: "version" is required, or in agents/cortex.team.toml — {said}',
                                  proc.err)
                    self.assertNotIn("git submodule update", proc.err)
                    self.assertNotIn("is not there", proc.err)
                (root / "agents").unlink()
                if remedy == "remove it":
                    (root / "agents").mkdir()                           # the team's folder, checked out in its place
                    team = root / "agents"
                else:
                    team = self.tmp / f"team-{n}"                       # where the team's folder is
                    team.mkdir()
                    (root / "agents").symlink_to(team, target_is_directory=True)
                (team / "cortex.team.toml").write_text(f'version = "{OWN}"\n', encoding="utf-8")
                self.assert_ok(self.within(root, "sync"))


if __name__ == "__main__":
    unittest.main()
