"""Named pipes, sockets and other special files at paths Cortex reads (#358).

1.8.0 reads the project's ``.gitignore`` and the team's file without blocking. A named pipe at any other file Cortex
reads would hold the command with no limit, and anything there that is no file was read as no file at all: each is
said not to be a file, never waited on.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cli_harness as harness  # noqa: E402
from test_cli_team import GIT, GIT_ENV, OWN, TeamTestCase  # noqa: E402

from cortex_cli.paths import display  # noqa: E402

PIPES = unittest.skipUnless(hasattr(os, "mkfifo"), "no named pipe here")


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


if __name__ == "__main__":
    unittest.main()
