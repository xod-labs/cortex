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


if __name__ == "__main__":
    unittest.main()
