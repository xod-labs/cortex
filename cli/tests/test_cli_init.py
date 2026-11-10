"""``cortex init`` — the setup script of Cortex 0.x, at parity (ADR-008 §3.7, phase 4).

The matrix (``init_matrix``) is what the script wrote, captured, and — since the script is gone —
what the command writes. ``cortex init`` must write the same files, byte for byte, plus its own
three.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the harness, not a package named tests
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "core")]
import cli_harness as harness  # noqa: E402
import init_matrix as matrix  # noqa: E402
from cortex_cli import init as init_command  # noqa: E402


def display(path):
    return str(path).replace(os.sep, "/")


def quote(path):
    """A path as the commands are shown for the shell: PowerShell on Windows."""
    return "'" + path.replace("'", "''") + "'" if os.name == "nt" else shlex.quote(path)


def remove(path):
    return f"Remove-Item -Recurse -Force {quote(path)}" if os.name == "nt" else f"rm -rf {quote(path)}"

EXPECTED = json.loads(matrix.EXPECTED.read_text(encoding="utf-8"))
OWN = harness.EXPECTED_VERSION if harness.BINARY else "9.9.9"
HAS_GIT = shutil.which("git") is not None
LISTS = "`cortex project list` lists this machine's projects"


class InitTestCase(unittest.TestCase):
    """A store holding the matrix's spec at this command's own version, and a project to init."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-init-")).resolve()
        self.addCleanup(self.cleanup)
        self.spec = matrix.build_spec(self.tmp)
        self.home = self.tmp / "cortex-home"
        shutil.copytree(self.spec, self.home / "versions" / OWN)
        self.env = harness.environment(CORTEX_HOME=str(self.home), CORTEX_SOURCE_VERSION=OWN)
        self.project = self.tmp / "init" / "project"

    def cleanup(self):
        for directory, subdirs, files in os.walk(self.tmp):
            for name in subdirs + files:
                path = os.path.join(directory, name)
                if not os.path.islink(path):
                    os.chmod(path, 0o700)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def init(self, *args, cwd=None, stdin=subprocess.DEVNULL):
        proc = harness.run("init", *args, cwd=cwd or self.project, env=self.env, stdin=stdin)
        proc.out, proc.err = proc.stdout.decode(), proc.stderr.decode()
        return proc

    def run_case(self, case):
        options, services, before = matrix.CASES[case]
        matrix.prepare(self.project, before)
        if HAS_GIT:     # a repository to ignore files in: outside of one, init writes no .gitignore
            subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        proc = self.init(*options, *[arg for name in services for arg in ("--service", name)])
        self.assertEqual(proc.returncode, 0, proc.err)
        return proc

    def read(self, name):
        return (self.project / name).read_text(encoding="utf-8")


class MatrixTests(InitTestCase):
    def test_cortex_init_writes_the_matrix(self):
        for case in matrix.CASES:
            if "git" in matrix.CASES[case][2].values() and not HAS_GIT:
                continue
            with self.subTest(case=case):
                if self.project.exists():
                    shutil.rmtree(self.project)
                self.run_case(case)
                self.assertEqual(matrix.snapshot(self.project, skip=("cortex", *matrix.OWN_FILES)),
                                 EXPECTED[case]["files"])
                # The one difference: the theme goes to cortex.toml, not to a marker in the spec.
                self.assertIn(f'theme = "{EXPECTED[case]["active_theme"]}"', self.read("cortex.toml"))
                self.assertIn(f'version = "{OWN}"', self.read("cortex.toml"))
                if HAS_GIT:
                    self.assertIn("cortex.local.toml", self.read(".gitignore").splitlines())
                self.assertIn("spec = ", self.read("cortex.local.toml"))
                self.assertIn(f'version = "{OWN}"', self.read("cortex.lock"))

    def test_the_comparison_catches_one_byte(self):
        self.run_case("single-claude")
        altered = dict(EXPECTED["single-claude"]["files"])
        text = altered["CLAUDE.md"]
        altered["CLAUDE.md"] = text[:100] + chr(ord(text[100]) ^ 1) + text[101:]
        self.assertNotEqual(matrix.snapshot(self.project, skip=("cortex", *matrix.OWN_FILES)), altered)

    def test_the_matrix_was_captured_for_every_case(self):
        self.assertEqual(sorted(EXPECTED), sorted(matrix.CASES))


class OptionTests(InitTestCase):
    def setUp(self):
        super().setUp()
        self.project.mkdir(parents=True)

    def test_a_workspace_with_services_runs_unattended_with_stdin_closed(self):
        read_end, write_end = os.pipe()
        os.close(write_end)
        try:
            proc = self.init("--workspace", "--service", "api", "--service", "core/web", stdin=read_end)
        finally:
            os.close(read_end)
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("<!-- @alias: web -->", self.read("core/web/project-overview.md"))

    def test_an_existing_instructions_file_is_kept_without_force(self):
        (self.project / "CLAUDE.md").write_text("# ours\n", encoding="utf-8")
        proc = self.init("--tool", "claude")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertEqual(self.read("CLAUDE.md"), "# ours\n")
        self.assertIn("kept — --force replaces it", proc.out)

    def test_and_replaced_with_it_the_old_one_kept_beside(self):
        (self.project / "CLAUDE.md").write_text("# ours\n", encoding="utf-8")
        proc = self.init("--tool", "claude", "--force")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertEqual(self.read("CLAUDE.md"), EXPECTED["single-claude"]["files"]["CLAUDE.md"])
        self.assertEqual(self.read("CLAUDE.md.bak"), "# ours\n")
        self.assertIn("CLAUDE.md.bak", proc.out)

    def test_force_writes_cortex_s_block_again_and_keeps_the_team_s_part(self):
        # Below the end marker, the file is the team's (ADR-012 §3.3): kept byte for byte, and the block
        # takes the file's line endings and byte order mark.
        expected = EXPECTED["single-claude"]["files"]["CLAUDE.md"].encode("utf-8")
        ours = b"## Our own rules\r\n\r\nRun the linter first.\r\n<!-- cortex:bootstrap end -->\r\nStill ours."
        old = init_command.BOM + expected.replace(b"Cortex AI Team", b"Cortex AI Team (Acme)", 1).replace(b"\n", b"\r\n") + ours
        (self.project / "CLAUDE.md").write_bytes(old)
        proc = self.init("--tool", "claude", "--force")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertEqual((self.project / "CLAUDE.md").read_bytes(),
                         init_command.BOM + expected.replace(b"\n", b"\r\n") + ours)
        self.assertEqual((self.project / "CLAUDE.md.bak").read_bytes(), old)

    def test_force_keeps_a_team_s_part_of_crlf_under_a_block_of_lf(self):
        expected = EXPECTED["single-claude"]["files"]["CLAUDE.md"].encode("utf-8")
        ours = b"## Our own rules\r\nWritten on Windows.\r\n"
        (self.project / "CLAUDE.md").write_bytes(expected.replace(b"Cortex AI Team", b"Cortex AI Team (Acme)", 1) + ours)
        proc = self.init("--tool", "claude", "--force")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertEqual((self.project / "CLAUDE.md").read_bytes(), expected + ours)

    def test_force_reads_a_run_of_crs_as_one_line_break(self):
        expected = EXPECTED["single-claude"]["files"]["CLAUDE.md"].encode("utf-8")
        ours = b"## Our own rules\r\r\n"
        (self.project / "CLAUDE.md").write_bytes(expected.replace(b"Cortex AI Team", b"Cortex AI Team (Acme)", 1)
                                                  .replace(b"\n", b"\r\r\n") + ours)
        proc = self.init("--tool", "claude", "--force")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertEqual((self.project / "CLAUDE.md").read_bytes(), expected.replace(b"\n", b"\r\n") + ours)

    def test_force_leaves_a_file_with_the_end_marker_that_only_git_or_an_editor_changed(self):
        # CRLF and a byte order mark are no change: with the end marker, the block takes them, nothing to replace.
        expected = EXPECTED["single-claude"]["files"]["CLAUDE.md"].encode("utf-8")
        kept = init_command.BOM + expected.replace(b"\n", b"\r\n")
        (self.project / "CLAUDE.md").write_bytes(kept)
        proc = self.init("--tool", "claude", "--force")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertEqual((self.project / "CLAUDE.md").read_bytes(), kept)
        self.assertIn("is what --force would write: left as it is", proc.out)
        self.assertFalse((self.project / "CLAUDE.md.bak").exists())

    def test_a_personality_block_quoted_in_the_team_s_part_is_its_text(self):
        proc = self.init("--tool", "claude", "--no-personality")
        self.assertEqual(proc.returncode, 0, proc.err)
        rule = self.project / "CLAUDE.md"
        rule.write_bytes(rule.read_bytes() + b"Our notes quote <!-- PERSONALITY:BEGIN --> as it is.\n")
        proc = self.init("--tool", "claude", "--no-personality")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertNotIn("personality block", proc.err)

    def test_a_bootstrap_of_format_1_kept_is_said_older(self):
        old = EXPECTED["single-claude"]["files"]["CLAUDE.md"].replace("<!-- cortex:bootstrap end -->\n", "").replace("format=2", "format=1")
        (self.project / "CLAUDE.md").write_text(old, encoding="utf-8")
        proc = self.init("--tool", "claude")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertEqual(self.read("CLAUDE.md"), old)
        self.assertIn(f"note: {display(self.project / 'CLAUDE.md')} is an older Cortex bootstrap: format 1, where this "
                      "version writes format 2 — cortex init --tool claude --force writes it again, and keeps it as .bak\n", proc.err)

    def test_force_replaces_whole_a_file_without_the_end_marker(self):
        # No end marker, no team's part: a file of format 1, or one a team wrote, is replaced whole.
        expected = EXPECTED["single-claude"]["files"]["CLAUDE.md"]
        old = expected.replace("<!-- cortex:bootstrap end -->\n", "").replace("format=2", "format=1") + "## Ours\n"
        (self.project / "CLAUDE.md").write_text(old, encoding="utf-8")
        proc = self.init("--tool", "claude", "--force")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertEqual(self.read("CLAUDE.md"), expected)
        self.assertEqual(self.read("CLAUDE.md.bak"), old)

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_git_ignores_cortex_in_link_and_copy_modes(self):
        # A link is a file to git: `/cortex/` would not match it, and the link — with the absolute
        # path of this machine's store — would go into the commit.
        for flag in ("--link", "--copy"):
            with self.subTest(flag=flag):
                project = self.tmp / flag.strip("-")
                subprocess.run(["git", "init", "-q", str(project)], check=True)
                proc = self.init(flag, cwd=project)
                self.assertEqual(proc.returncode, 0, proc.err)
                self.assertIn(f'sync = "{flag[2:]}"', (project / "cortex.toml").read_text(encoding="utf-8"))
                self.assertTrue(os.path.lexists(project / "cortex"))
                check = subprocess.run(["git", "-C", str(project), "check-ignore", "-q", "cortex"])
                self.assertEqual(check.returncode, 0, "git does not ignore cortex")
                status = subprocess.run(["git", "-C", str(project), "status", "--porcelain", "--untracked-files=all"],
                                        capture_output=True, text=True, check=True).stdout
                self.assertNotIn("cortex/", status)
                self.assertNotRegex(status, r"(?m) cortex$")
                self.assertNotIn("note:", proc.err)

    def test_an_existing_gitignore_keeps_its_bytes(self):
        (self.project / ".gitignore").write_text("node_modules/", encoding="utf-8")
        self.init()
        self.assertEqual(self.read(".gitignore"), "node_modules/\ncortex.local.toml\n")
        self.init()
        self.assertEqual(self.read(".gitignore"), "node_modules/\ncortex.local.toml\n")
        # A byte order mark, CRLF line endings and a byte that is no UTF-8 stay as they were.
        (self.project / ".gitignore").write_bytes(b"\xef\xbb\xbfcortex.local.toml\r\ncaf\xe9/\r\n")
        proc = self.init("--copy")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertEqual((self.project / ".gitignore").read_bytes(),
                         b"\xef\xbb\xbfcortex.local.toml\r\ncaf\xe9/\r\n/cortex\r\n/.cortex-sync-*\r\n")

    def test_a_second_init_keeps_cortex_toml_but_for_the_options_given(self):
        self.init("--theme", "acme", "--link")
        (self.project / "cortex.toml").write_text(self.read("cortex.toml") + "# the team's note\n", encoding="utf-8")
        before = self.read("cortex.toml")
        proc = self.init()
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertEqual(self.read("cortex.toml"), before)
        self.assertIn("kept", proc.out)
        self.assertTrue(os.path.islink(self.project / "cortex") or os.path.isdir(self.project / "cortex"))
        proc = self.init("--theme", "h2g2")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertEqual(self.read("cortex.toml"), before.replace('theme = "acme"', 'theme = "h2g2"'))
        self.assertIn('theme = "h2g2" — the rest kept', proc.out)
        proc = self.init("--copy", "--no-personality")
        self.assertEqual(proc.returncode, 0, proc.err)
        text = self.read("cortex.toml")
        self.assertIn('theme = "none"', text)
        self.assertIn('sync = "copy"', text)
        self.assertIn("# the team's note", text)
        self.assertTrue((self.project / "cortex" / ".synced").is_file())

    def test_force_keeps_cortex_toml(self):
        # --force replaces the instructions file, and only it: the team's theme, its sync mode and
        # its version stay, and so does the copy.
        self.init("--theme", "acme", "--copy")
        before = self.read("cortex.toml")
        proc = self.init("--force")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertEqual(self.read("cortex.toml"), before)
        self.assertTrue((self.project / "cortex" / ".synced").is_file())
        self.assertIn("PERSONALITY", self.read(".github/copilot-instructions.md"))

    def test_a_version_is_never_downgraded(self):
        (self.project / "cortex.toml").write_text('version = "99.0.0"\ntheme = "h2g2"\n', encoding="utf-8")
        for args in ([], ["--force"]):
            with self.subTest(args=args):
                proc = self.init(*args)
                self.assertEqual(proc.returncode, 1)
                self.assertIn("newer than this cortex", proc.err)
                self.assertEqual(self.read("cortex.toml"), 'version = "99.0.0"\ntheme = "h2g2"\n')
                self.assertEqual(sorted(p.name for p in self.project.iterdir()), ["cortex.toml"])

    def test_a_lock_sync_refuses_stops_init_before_its_first_write(self):
        toml = f'version = "{OWN}"\ntheme = "h2g2"\n'
        other = "sha256:" + "0" * 64
        for lock, said in ((f'format = 1\nversion = "{OWN}"\ndigest = "{other}"\n', "this is not the release the project locked"),
                           ("<<<<<<< ours\n", "delete it, and `cortex sync` writes it again")):
            with self.subTest(said=said):
                (self.project / "cortex.toml").write_text(toml, encoding="utf-8")
                (self.project / "cortex.lock").write_text(lock, encoding="utf-8")
                proc = self.init("--copy")
                self.assertEqual(proc.returncode, 1)
                self.assertIn(said, proc.err)
                self.assertEqual(self.read("cortex.toml"), toml)
                self.assertEqual(self.read("cortex.lock"), lock)
                self.assertEqual(sorted(p.name for p in self.project.iterdir()), ["cortex.lock", "cortex.toml"])

    def test_an_unreadable_cortex_toml_is_refused_and_kept(self):
        (self.project / "cortex.toml").write_text('version = "1.0.0"\ntheme = "../../templates"\n', encoding="utf-8")
        for args in ([], ["--force"]):
            with self.subTest(args=args):
                proc = self.init(*args)
                self.assertEqual(proc.returncode, 1)
                self.assertIn("fix it, or remove it to start over", proc.err)
                self.assertEqual(sorted(p.name for p in self.project.iterdir()), ["cortex.toml"])

    def test_a_theme_found_nowhere(self):
        proc = self.init("--theme", "no-such-theme")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("theme 'no-such-theme' is neither in", proc.err)
        # The spec's themes, whatever Cortex ships: a new theme must not break this test (#148).
        themes = sorted(p.name for p in (self.spec / "agents" / "personalities").iterdir() if p.is_dir())
        self.assertTrue({"acme", "h2g2"} <= set(themes))
        self.assertIn(f"the themes are: {', '.join(themes)}\n", proc.err)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_a_theme_is_a_name(self):
        for theme in ("../../templates", 'a"b', "h2g2\n"):
            with self.subTest(theme=theme):
                proc = self.init("--theme", theme)
                self.assertEqual(proc.returncode, 1)
                self.assertIn("no theme name", proc.err)
                self.assertEqual(list(self.project.iterdir()), [])

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_hook_s_git_dir_names_no_repository_for_a_root_in_none(self):
        # A hook exports GIT_DIR: git asked of the root by its folder, not of that repository — a root in no repository
        # is said to be in none, and gets no .gitignore, which would keep nothing out of any commit.
        if not init_command.sync.in_no_repository(self.project.parent):
            self.skipTest("needs a temporary directory in no git repository — set TMPDIR outside one")
        other = self.tmp / "other"
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        self.env = dict(self.env, PATH=os.path.dirname(shutil.which("git")), GIT_DIR=str(other / ".git"))
        proc = self.init()
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("is in no git repository", proc.err)
        self.assertFalse((self.project / ".gitignore").exists())
        self.assertFalse((other / ".gitignore").exists())

    @harness.outside_a_repository
    def test_a_root_that_is_no_repository_is_said_and_gets_no_gitignore(self):
        proc = self.init()
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("is in no git repository", proc.err)
        self.assertFalse((self.project / ".gitignore").exists())
        if HAS_GIT:
            subprocess.run(["git", "init", "-q", str(self.project)], check=True)
            proc = self.init()
            self.assertNotIn("is in no git repository", proc.err)
            self.assertIn("cortex.local.toml", self.read(".gitignore").splitlines())

    def test_a_root_inside_a_repository_is_not_said_to_be_in_none(self):
        # The repository is a .git at the root or above it, git or not on the PATH — as cortex upgrade tells it.
        # Without git to ask, .gitignore's own lines say what is ignored, in a subdirectory too: it is written.
        (self.project.parent / ".git").mkdir(parents=True)
        proc = self.init()
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertNotIn("is in no git repository", proc.err)
        self.assertIn("cortex.local.toml", self.read(".gitignore").splitlines())

    def test_without_git_a_gitignore_s_byte_order_mark_hides_none_of_its_lines(self):
        # Read as add_to_gitignore reads it: the line is there — no note that git does not ignore it, nothing added.
        (self.project.parent / ".git").mkdir(parents=True, exist_ok=True)
        self.project.mkdir(exist_ok=True)
        gitignore = "\ufeffcortex.local.toml\n/cortex\n".encode("utf-8")
        (self.project / ".gitignore").write_bytes(gitignore)
        proc = self.init()
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertNotIn("git does not ignore", proc.err)
        self.assertEqual((self.project / ".gitignore").read_bytes(), gitignore)

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_repository_git_refuses_above_the_root_still_gets_the_gitignore(self):
        # git asked, and refusing — here a worktree whose repository is gone — is no "no repository": .gitignore's own
        # lines say what is ignored, and the file is written.
        self.project.parent.mkdir(parents=True, exist_ok=True)
        (self.project.parent / ".git").write_text("gitdir: ../nowhere\n", encoding="utf-8")
        self.env = dict(self.env, PATH=os.pathsep.join([os.path.dirname(shutil.which("git")), self.env["PATH"]]))
        proc = self.init()
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertNotIn("is in no git repository", proc.err)
        self.assertIn("cortex.local.toml", self.read(".gitignore").splitlines())

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_copy_turned_into_a_link_is_ignored_as_a_link(self):
        # Asked before the sync, git saw cortex/ still a directory, which `cortex/` matches: the
        # link that took its place was then a file git did not ignore.
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        (self.project / ".gitignore").write_text("cortex/\n", encoding="utf-8")
        self.assertEqual(self.init("--copy").returncode, 0)
        proc = self.init("--link")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertTrue(os.path.islink(self.project / "cortex") or os.name == "nt")
        self.assertEqual(subprocess.run(["git", "-C", str(self.project), "check-ignore", "-q", "cortex"]).returncode, 0)
        self.assertNotIn("git does not ignore", proc.err)

    def test_a_new_project_keeps_the_default_tool_whatever_file_is_there(self):
        # A CLAUDE.md written by hand is no reason to take Claude for the tool: it holds no Cortex
        # bootstrap, and --force would have replaced it. The script wrote copilot's file, and said nothing.
        (self.project / "CLAUDE.md").write_text("# Our own notes\n", encoding="utf-8")
        proc = self.init()
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertTrue((self.project / ".github" / "copilot-instructions.md").is_file())
        self.assertEqual(self.read("CLAUDE.md"), "# Our own notes\n")
        self.assertIn("CLAUDE.md is there and was kept: it holds no Cortex bootstrap — "
                      "cortex init --tool claude --force replaces it", proc.err)

    def test_an_older_bootstrap_is_said(self):
        # Written before cortex.toml, it reads cortex/ as the submodule it was: there is no spec there.
        older = "# Cortex AI Team\n\nRead `cortex/agents/roles/prompt-manager.md` first.\n"
        (self.project / "CLAUDE.md").write_text(older, encoding="utf-8")
        proc = self.init()
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertTrue((self.project / ".github" / "copilot-instructions.md").is_file())
        self.assertEqual(self.read("CLAUDE.md"), older)
        self.assertIn("CLAUDE.md is there and was kept: an older Cortex bootstrap, written before the bootstrap "
                      "marker — cortex init --tool claude --force replaces it", proc.err)
        proc = self.init("--tool", "claude")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("CLAUDE.md is an older Cortex bootstrap, written before the bootstrap marker", proc.err)
        self.assertEqual(self.read("CLAUDE.md"), older)

    def test_a_kept_file_without_the_bootstrap_is_said(self):
        (self.project / "CLAUDE.md").write_text("# Our own notes\n", encoding="utf-8")
        proc = self.init("--tool", "claude")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("CLAUDE.md holds no Cortex bootstrap: the tool will not find Cortex", proc.err)

    def test_a_second_init_of_a_custom_tool_says_what_it_wrote(self):
        self.assertEqual(self.init("--tool", "custom", "--instructions-file", "docs/ai.md").returncode, 0)
        proc = self.init()
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("no instructions file of a known tool was here", proc.err)

    def test_a_bootstrap_for_the_other_layout_is_said(self):
        self.assertEqual(self.init("--tool", "claude").returncode, 0)
        proc = self.init("--tool", "claude", "--workspace")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("was written for a single project", proc.err)

    def test_the_other_layout_s_note_names_the_option_force_needs(self):
        # --force alone keeps the file's layout (#329): the note named a command that left the file as it was.
        self.assertEqual(self.init("--tool", "claude").returncode, 0)
        proc = self.init("--workspace")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("was written for a single project — cortex init --tool claude --workspace --force writes it "
                      "again, and keeps it as .bak\n", proc.err)
        before = self.read("CLAUDE.md")
        proc = self.init("--tool", "claude", "--workspace", "--force")          # the command the note names
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertNotEqual(before, self.read("CLAUDE.md"))
        self.assertIn("layout=workspace", self.read("CLAUDE.md"))
        self.assertEqual((self.project / "CLAUDE.md.bak").read_text(encoding="utf-8"), before)

    def test_a_bootstrap_a_team_retitled_keeps_its_layout(self):
        # #141: the first line was compared with the template's, and a retitled workspace bootstrap was
        # taken for a single project's. The marker says the layout, whatever the title.
        self.assertEqual(self.init("--tool", "claude", "--workspace").returncode, 0)
        retitled = self.read("CLAUDE.md").replace("# Cortex AI Team — Workspace Mode\n",
                                                  "# Cortex AI Team — Workspace Mode (Acme)\n", 1)
        self.assertIn("Workspace Mode (Acme)", retitled)
        (self.project / "CLAUDE.md").write_text(retitled, encoding="utf-8")
        proc = self.init("--tool", "claude", "--workspace")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertEqual([line for line in proc.err.splitlines() if line.startswith("note:") and "CLAUDE.md" in line], [])
        self.assertEqual(self.read("CLAUDE.md"), retitled)

    def test_force_without_workspace_keeps_a_workspace_s_layout(self):
        # #329: --workspace alone chose the template, and --force wrote a single project's over a workspace's.
        self.assertEqual(self.init("--tool", "claude", "--workspace", "--service", "svc").returncode, 0)
        team = "\n## Workspace services\n\n| @alias | Folder | Description |\n|---|---|---|\n| `@svc` | svc/ | x |\n"
        (self.project / "CLAUDE.md").write_text(self.read("CLAUDE.md") + team, encoding="utf-8")
        proc = self.init("--tool", "claude")                                       # kept: no other layout said
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertNotIn("was written for a workspace", proc.err)
        before = self.read("CLAUDE.md").replace("<!-- cortex:bootstrap layout=workspace format=2",
                                                "<!-- cortex:bootstrap layout=workspace format=1", 1)
        (self.project / "CLAUDE.md").write_text(before, encoding="utf-8")          # an older format, to write again
        proc = self.init("--tool", "claude", "--force")
        self.assertEqual(proc.returncode, 0, proc.err)
        written = self.read("CLAUDE.md")
        self.assertTrue(written.startswith("# Cortex AI Team — Workspace Mode\n"), written[:80])
        self.assertIn("<!-- cortex:bootstrap layout=workspace format=2 digest=", written)
        self.assertTrue(written.endswith(team))
        self.assertIn("CLAUDE.md was written for a workspace, and is written for one again — to write a single "
                      "project's, remove it and run `cortex init --tool claude`, then copy the team's part back from ",
                      proc.err)
        self.assertIn("CLAUDE.md.bak, below the end marker", proc.err)
        self.assertTrue((self.project / "CLAUDE.md.bak").read_text(encoding="utf-8").endswith(team))
        # Followed: the project has no instructions file left, and `cortex init` alone would write Copilot's.
        (self.project / "CLAUDE.md").unlink()
        proc = self.init("--tool", "claude")                                       # the command the note names
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("<!-- cortex:bootstrap layout=single format=2 digest=", self.read("CLAUDE.md"))
        self.assertFalse((self.project / ".github" / "copilot-instructions.md").exists())

    def test_the_notes_name_the_tool_where_several_instructions_files_are(self):
        # --force without --tool is refused where several instructions files are: each note names the tool, and
        # the command it names writes that tool's file.
        self.assertEqual(self.init("--tool", "claude").returncode, 0)
        self.assertEqual(self.init("--tool", "agents").returncode, 0)
        before = self.read("CLAUDE.md")
        proc = self.init("--tool", "claude", "--workspace")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("CLAUDE.md was written for a single project — cortex init --tool claude --workspace --force "
                      "writes it again", proc.err)
        proc = self.init("--tool", "claude", "--workspace", "--force")          # the command the note names
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("layout=workspace", self.read("CLAUDE.md"))
        self.assertEqual(self.read("CLAUDE.md.bak"), before)
        # A file no longer fitting the theme: the note names --tool too, and the command it names writes it.
        proc = self.init("--tool", "agents", "--no-personality")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("AGENTS.md was written with the personality block this theme needs removed or added — cortex "
                      "init --tool agents --force writes it again", proc.err)
        proc = self.init("--tool", "agents", "--force")                         # the command the note names
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertNotIn("PERSONALITY:BEGIN", self.read("AGENTS.md"))

    def test_a_custom_file_s_note_names_its_tool_and_file(self):
        # A custom instructions file, named again with --instructions-file: the note's command writes it.
        self.assertEqual(self.init("--tool", "custom", "--instructions-file", "docs/ai.md").returncode, 0)
        path = self.project / "docs" / "ai.md"
        path.write_text(self.read("docs/ai.md").replace("format=2", "format=1", 1), encoding="utf-8")
        proc = self.init("--tool", "custom", "--instructions-file", "docs/ai.md")
        self.assertEqual(proc.returncode, 0, proc.err)
        named = f"cortex init --tool custom --instructions-file {quote(display(path))} --force"
        self.assertIn(named, proc.err)
        proc = self.init("--tool", "custom", "--instructions-file", str(path), "--force")   # the command the note names
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("format=2", self.read("docs/ai.md"))

    def test_a_workspace_s_file_writes_no_team_pair_without_the_option(self):
        # The layout read from the file chooses the template, and nothing else: the team's repository is the option's.
        (self.project / "agents" / ".git").mkdir(parents=True)
        self.assertEqual(self.init("--tool", "claude", "--workspace").returncode, 0)
        (self.project / "agents" / "project-overview.md").unlink()
        for args in (("--tool", "claude"), ("--tool", "claude", "--force")):
            proc = self.init(*args)
            self.assertEqual(proc.returncode, 0, proc.err)
            self.assertFalse((self.project / "agents" / "project-overview.md").exists(), args)
            self.assertNotIn("agents/project-overview.md", proc.out)
        self.assertIn("✓ agents/project-overview.md", self.init("--tool", "claude", "--workspace").out)

    def test_another_tool_s_file_of_the_other_layout_names_force(self):
        # --tool X --force keeps a file's own layout now (#329), and its team's part, with a .bak: never "remove it",
        # which at a root in no repository would lose the team's part.
        self.assertEqual(self.init("--tool", "claude", "--workspace").returncode, 0)
        self.assertEqual(self.init("--tool", "agents").returncode, 0)                   # a single project's AGENTS.md
        team = "\n## Workspace services\n\n| @alias | Folder | Description |\n|---|---|---|\n| `@svc` | svc/ | x |\n"
        (self.project / "CLAUDE.md").write_text(self.read("CLAUDE.md") + team, encoding="utf-8")
        # Up to date as a workspace's: --force, which keeps its layout, would write it as it is — no command is named,
        # never one that writes nothing, and the note says so on every run.
        proc = self.init()
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("note: CLAUDE.md is there and was kept: a bootstrap written for a workspace, up to date as one\n",
                      proc.err)
        self.assertNotIn("--force", proc.err)
        self.assertIn("is what --force would write: left as it is", self.init("--tool", "claude", "--force").out)
        proc = self.init("--workspace")
        self.assertIn("note: AGENTS.md is there and was kept: a bootstrap written for a single project — cortex init "
                      "--tool agents --workspace --force writes a workspace's, and keeps it as .bak", proc.err)
        self.assertNotIn("remove it", proc.err)
        # An older one: --force writes it again — the note names it, and, followed, it keeps the team's part and
        # the layout.
        before = self.read("CLAUDE.md").replace("<!-- cortex:bootstrap layout=workspace format=2",
                                                "<!-- cortex:bootstrap layout=workspace format=1", 1)
        (self.project / "CLAUDE.md").write_text(before, encoding="utf-8")
        proc = self.init()
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("note: CLAUDE.md is there and was kept: a bootstrap written for a workspace — cortex init --tool "
                      "claude --force writes it again, a workspace's as it was written, and keeps it as .bak", proc.err)
        self.assertNotIn("remove it", proc.err)
        proc = self.init("--tool", "claude", "--force")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("<!-- cortex:bootstrap layout=workspace format=2 digest=", self.read("CLAUDE.md"))
        self.assertTrue(self.read("CLAUDE.md").endswith(team))
        self.assertEqual((self.project / "CLAUDE.md.bak").read_text(encoding="utf-8"), before)

    def test_force_without_workspace_knows_a_workspace_written_before_the_marker(self):
        # The file of 1.3.0, which every workspace a team changed is sent to `cortex init --force` with.
        old = "# Cortex AI Team — Workspace Mode\n\nRead cortex.local.toml first.\n"
        (self.project / "CLAUDE.md").write_text(old, encoding="utf-8")
        proc = self.init("--tool", "claude", "--force")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("<!-- cortex:bootstrap layout=workspace format=2 digest=", self.read("CLAUDE.md"))
        self.assertIn("was written for a workspace, and is written for one again", proc.err)

    def test_workspace_on_a_single_project_s_file_before_the_marker_names_the_option(self):
        # --force alone keeps a single project's file of 1.3.0 single: the note of a --workspace run names the option,
        # and the command it names writes a workspace's.
        old = "# Cortex AI Team\n\nRead cortex.local.toml first.\n"
        (self.project / "CLAUDE.md").write_text(old, encoding="utf-8")
        proc = self.init("--tool", "claude", "--workspace")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("CLAUDE.md is an older Cortex bootstrap, written before the bootstrap marker — cortex init --tool "
                      "claude --workspace --force writes it again", proc.err)
        proc = self.init("--tool", "claude", "--workspace", "--force")          # the command the note names
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("<!-- cortex:bootstrap layout=workspace format=2 digest=", self.read("CLAUDE.md"))

    def test_the_note_names_the_backup_written(self):
        # A .bak of other content there already: the team's part went to .bak.1, which the note names.
        self.assertEqual(self.init("--tool", "claude", "--workspace").returncode, 0)
        team = "\n## Ours\n"
        before = self.read("CLAUDE.md").replace("<!-- cortex:bootstrap layout=workspace format=2",
                                                "<!-- cortex:bootstrap layout=workspace format=1", 1) + team
        (self.project / "CLAUDE.md").write_text(before, encoding="utf-8")
        (self.project / "CLAUDE.md.bak").write_text("# something else\n", encoding="utf-8")
        proc = self.init("--tool", "claude", "--force")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertEqual(self.read("CLAUDE.md.bak.1"), before)
        self.assertIn(f"then copy the team's part back from {self.project / 'CLAUDE.md.bak.1'}, below the end marker",
                      proc.err.replace(str(self.project.resolve()), str(self.project)))

    def test_force_without_workspace_finds_the_title_below_a_line_a_team_wrote_above_it(self):
        # A file of before the marker, opened by the team's own line: the title is still the templates' — it was
        # written single, in silence.
        old = "<!-- our notes -->\n# Cortex AI Team — Workspace Mode\n\nRead cortex.local.toml first.\n"
        (self.project / "CLAUDE.md").write_text(old, encoding="utf-8")
        proc = self.init("--tool", "claude", "--force")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("<!-- cortex:bootstrap layout=workspace format=2 digest=", self.read("CLAUDE.md"))
        self.assertIn("was written for a workspace, and is written for one again", proc.err)
        self.assertEqual(self.read("CLAUDE.md.bak"), old)

    def test_force_with_workspace_says_nothing_of_a_layout_read_from_the_file(self):
        # The note is of the layout read from the file, without the option: with --workspace, the option chose it.
        self.assertEqual(self.init("--tool", "claude", "--workspace").returncode, 0)
        before = self.read("CLAUDE.md").replace("<!-- cortex:bootstrap layout=workspace format=2",
                                                "<!-- cortex:bootstrap layout=workspace format=1", 1)
        (self.project / "CLAUDE.md").write_text(before, encoding="utf-8")          # an older format, to write again
        proc = self.init("--tool", "claude", "--workspace", "--force")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("<!-- cortex:bootstrap layout=workspace format=2 digest=", self.read("CLAUDE.md"))
        self.assertNotIn("was written for a workspace", proc.err)

    def test_force_keeps_a_single_project_single_and_workspace_still_moves_it(self):
        self.assertEqual(self.init("--tool", "claude").returncode, 0)
        proc = self.init("--tool", "claude", "--force", "--no-personality")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("<!-- cortex:bootstrap layout=single format=2 digest=", self.read("CLAUDE.md"))
        self.assertNotIn("written for a workspace", proc.err)
        proc = self.init("--tool", "claude", "--force", "--workspace")             # the option says the layout
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("<!-- cortex:bootstrap layout=workspace format=2 digest=", self.read("CLAUDE.md"))
        self.assertNotIn("written for a workspace", proc.err)

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_the_backup_is_said_to_be_left_to_git(self):
        self.project.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        (self.project / "CLAUDE.md").write_text("# mine\n", encoding="utf-8")
        self.env = dict(self.env, PATH=os.pathsep.join([os.path.dirname(shutil.which("git")), self.env["PATH"]]))
        proc = self.init("--tool", "claude", "--force")
        self.assertIn("CLAUDE.md.bak: the file --force replaces, as it was — git does not ignore it: carry over",
                      proc.out)

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_backup_git_ignores_is_not_said_to_be_left_to_it(self):
        # A team's .gitignore that holds *.bak: git ignores the backup, and the line asks git rather than saying so.
        self.project.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        (self.project / ".gitignore").write_text("*.bak\n", encoding="utf-8")
        (self.project / "CLAUDE.md").write_text("# mine\n", encoding="utf-8")
        self.env = dict(self.env, PATH=os.pathsep.join([os.path.dirname(shutil.which("git")), self.env["PATH"]]))
        proc = self.init("--tool", "claude", "--force")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("CLAUDE.md.bak: the file --force replaces, as it was — carry over what you wrote in it", proc.out)
        self.assertNotIn("git does not ignore it", proc.out)

    @harness.outside_a_repository
    def test_outside_a_repository_the_backup_is_not_said_to_be_left_to_git(self):
        self.project.mkdir(parents=True, exist_ok=True)
        (self.project / "CLAUDE.md").write_text("# mine\n", encoding="utf-8")
        proc = self.init("--tool", "claude", "--force")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("CLAUDE.md.bak: the file --force replaces, as it was — carry over what you wrote in it", proc.out)
        self.assertNotIn("git does not ignore it", proc.out)

    @harness.outside_a_repository
    def test_outside_a_repository_an_existing_gitignore_is_said_to_do_nothing(self):
        (self.project / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
        proc = self.init()
        self.assertIn("its .gitignore keeps nothing out of a commit", proc.err)
        self.assertNotIn("no .gitignore is written", proc.err)

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_what_a_killed_sync_leaves_is_ignored_too(self):
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        self.assertEqual(self.init("--link").returncode, 0)
        check = subprocess.run(["git", "-C", str(self.project), "check-ignore", "-q", ".cortex-sync-1234-abcd"])
        self.assertEqual(check.returncode, 0)

    def test_a_file_where_a_folder_is_to_be_made_is_refused_first(self):
        (self.project / "api").write_text("a file\n", encoding="utf-8")
        (self.project / ".cursor").write_text("a file\n", encoding="utf-8")
        for args in (["--workspace", "--service", "api/x"], ["--tool", "cursor"]):
            with self.subTest(args=args):
                proc = self.init(*args)
                self.assertEqual(proc.returncode, 1)
                self.assertIn("is a file, not a folder", proc.err)
                self.assertNotIn("Traceback", proc.err)
                self.assertEqual(sorted(p.name for p in self.project.iterdir()), [".cursor", "api"])
        if os.name != "nt":                                 # a link that leads nowhere is no folder either
            (self.project / ".cursor").unlink()
            (self.project / ".cursor").symlink_to(self.project / "gone")
            proc = self.init("--tool", "cursor")
            self.assertEqual(proc.returncode, 1)
            self.assertIn("is a file, not a folder", proc.err)
            self.assertEqual(sorted(p.name for p in self.project.iterdir()), [".cursor", "api"])

    def test_force_never_overwrites_a_backup(self):
        (self.project / "CLAUDE.md").write_text("# mine, first\n", encoding="utf-8")
        self.assertEqual(self.init("--tool", "claude", "--force").returncode, 0)
        (self.project / "CLAUDE.md").write_text("# mine, second\n", encoding="utf-8")
        proc = self.init("--tool", "claude", "--force")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertEqual(self.read("CLAUDE.md.bak"), "# mine, first\n")
        self.assertEqual(self.read("CLAUDE.md.bak.1"), "# mine, second\n")
        # Already what --force writes: nothing to back up, nothing rewritten.
        proc = self.init("--tool", "claude", "--force")
        self.assertIn("is what --force would write", proc.out)
        self.assertFalse((self.project / "CLAUDE.md.bak.2").exists())

    def test_a_second_init_writes_the_instructions_file_of_the_tool_already_there(self):
        self.assertEqual(self.init("--tool", "claude").returncode, 0)
        proc = self.init()
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertFalse((self.project / ".github" / "copilot-instructions.md").exists())
        self.assertIn("CLAUDE.md kept", proc.out)
        (self.project / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
        proc = self.init("--force")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("several instructions files are there", proc.err)

    def test_a_kept_instructions_file_that_no_longer_fits_the_theme_is_said(self):
        self.assertEqual(self.init("--tool", "claude").returncode, 0)
        proc = self.init("--tool", "claude", "--no-personality")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("was written with the personality block", proc.err)
        self.assertIn("cortex init --tool claude --force writes it again", proc.err)

    def test_cortex_toml_keeps_its_comments_and_its_byte_order_mark(self):
        self.assertEqual(self.init("--theme", "acme").returncode, 0)
        text = self.read("cortex.toml").replace('theme = "acme"', 'theme = "acme"  # the team\'s choice')
        (self.project / "cortex.toml").write_bytes(("\ufeff" + text).encode("utf-8"))
        self.assertEqual(self.init("--theme", "h2g2").returncode, 0)
        written = (self.project / "cortex.toml").read_bytes().decode("utf-8")
        self.assertTrue(written.startswith("\ufeff"))
        self.assertIn('theme = "h2g2"  # the team\'s choice', written)

    def test_a_theme_of_the_projects_own(self):
        # docs/creating-a-theme.md: a custom theme lives in the project, with no base in Cortex.
        (self.project / "agents" / "personalities" / "ours").mkdir(parents=True)
        proc = self.init("--theme", "ours")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn('theme = "ours"', self.read("cortex.toml"))
        self.assertNotIn("warning: theme", proc.err)

    def test_from_a_checkout_its_templates_and_its_spec(self):
        # CONTRIBUTING's loop: a template edited in a checkout reaches the project at once.
        checkout = self.tmp / "checkout"
        shutil.copytree(self.spec, checkout)
        template = checkout / "templates" / "bootstrap-instructions.md"
        template.write_bytes(template.read_bytes().replace(b"# Cortex AI Team", b"# Cortex AI Team, edited", 1))
        proc = self.init("--tool", "claude", "--from", str(checkout))
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertTrue(self.read("CLAUDE.md").startswith("# Cortex AI Team, edited\n"))
        self.assertIn(str(checkout).replace(os.sep, "/"), self.read("cortex.local.toml"))
        self.assertIn("no version is checked (--from)", proc.err)

    def test_a_link_init_from_a_checkout_replaces_a_link_to_one_that_is_gone(self):
        # #325: as `cortex sync --link --from` does (#310) — a junction on Windows, no privilege needed.
        checkout, moved = self.tmp / "checkout", self.tmp / "moved"
        shutil.copytree(self.spec, checkout)
        proc = self.init("--tool", "claude", "--link", "--from", str(checkout))
        self.assertEqual(proc.returncode, 0, proc.err)
        checkout.rename(moved)
        pinned = self.read("cortex.toml")
        proc = self.init("--tool", "claude", "--copy", "--from", str(moved))       # a copy: refused, as sync does,
        self.assertEqual(proc.returncode, 1, proc.err)                               # before anything is written
        self.assertIn("which leads nowhere", proc.err)
        self.assertEqual(self.read("cortex.toml"), pinned)
        proc = self.init("--tool", "claude", "--link", "--no-personality")         # no checkout named: refused,
        self.assertEqual(proc.returncode, 1, proc.err)                               # the theme key left as it was
        self.assertEqual(self.read("cortex.toml"), pinned)
        proc = self.init("--tool", "claude", "--link", "--from", str(moved))
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("which led nowhere: replaced.", proc.out)
        self.assertEqual(os.path.realpath(self.project / "cortex"), os.path.realpath(moved))
        again = self.tmp / "moved-again"
        moved.rename(again)
        proc = self.init("--tool", "claude", "--from", str(again))                  # sync = "link" in cortex.toml
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("which led nowhere: replaced.", proc.out)
        self.assertEqual(os.path.realpath(self.project / "cortex"), os.path.realpath(again))

    def test_a_link_cortex_made_is_its_own_once_cortex_toml_is_gone(self):
        # #328: without cortex.toml, the link is judged by the spec cortex.local.toml still names.
        checkout, moved = self.tmp / "checkout", self.tmp / "moved"
        shutil.copytree(self.spec, checkout)
        proc = self.init("--tool", "claude", "--link", "--from", str(checkout))
        self.assertEqual(proc.returncode, 0, proc.err)
        (self.project / "cortex.toml").unlink()
        proc = self.init("--tool", "claude", "--link", "--from", str(checkout))
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertNotIn("which Cortex did not make", proc.err)
        self.assertTrue((self.project / "cortex.toml").is_file())
        (self.project / "cortex.toml").unlink()
        checkout.rename(moved)                                                       # a link that leads nowhere:
        proc = self.init("--tool", "claude")                                         # no sync runs without the pin
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("which leads nowhere", proc.err)
        self.assertIn("`cortex init --link --from` the checkout it should follow", proc.err)
        self.assertNotIn("cortex sync", proc.err)
        proc = self.init("--tool", "claude", "--link", "--from", str(moved))        # replaced as #325 has it
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("which led nowhere: replaced.", proc.out)
        self.assertEqual(os.path.realpath(self.project / "cortex"), os.path.realpath(moved))
        (self.project / "cortex.toml").unlink()
        (self.project / "cortex.local.toml").write_text("spec = [\n")             # unreadable: as before #328
        proc = self.init("--tool", "claude", "--link", "--from", str(moved))
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("which Cortex did not make", proc.err)

    def test_a_directory_given(self):
        proc = self.init("sub/app", cwd=self.project)
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertTrue((self.project / "sub" / "app" / "cortex.toml").is_file())
        self.assertTrue((self.project / "sub" / "app" / ".github" / "copilot-instructions.md").is_file())

    def test_bad_arguments(self):
        for args in (["--service", "api"], ["--tool", "vim"], ["--link", "--copy"]):
            with self.subTest(args=args):
                self.assertEqual(self.init(*args).returncode, 2)
        (self.project / "notes").write_text("mine\n", encoding="utf-8")
        (self.project / "docs").mkdir()
        for args, message in ((["--tool", "custom"], "needs --instructions-file"),
                              (["--instructions-file", "x.md"], "goes with --tool custom"),
                              (["--workspace", "--service", "api", "--service", "../out"], "a folder inside the workspace"),
                              (["--workspace", "--service", "notes"], "is a file, not a folder"),
                              (["--tool", "custom", "--instructions-file", "docs"], "a directory"),
                              (["--tool", "custom", "--instructions-file", "cortex.toml"], "writes for itself"),
                              (["--tool", "custom", "--instructions-file", ".gitignore"], "writes for itself"),
                              (["--tool", "custom", "--instructions-file", "cortex"], "writes for itself"),
                              (["--tool", "custom", "--instructions-file", "CORTEX.TOML"], "writes for itself"),
                              (["--tool", "custom", "--instructions-file", ".git/config", "--force"], "of git's own"),
                              (["--workspace", "--service", "api", "--tool", "custom", "--instructions-file",
                                "api/project-overview.md"], "writes for itself")):
            with self.subTest(args=args):
                proc = self.init(*args)
                self.assertEqual(proc.returncode, 1)
                self.assertIn(message, proc.err)
                self.assertNotIn("Traceback", proc.err)
                # Nothing is written before a refusal.
                self.assertEqual(sorted(p.name for p in self.project.iterdir()), ["docs", "notes"])
        self.assertFalse((self.tmp / "init" / "out").exists())

    @unittest.skipIf(os.name == "nt", "a backslash separates folders on Windows")
    def test_a_backslash_is_part_of_a_service_name_outside_windows(self):
        proc = self.init("--workspace", "--service", "a\\b")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("<!-- @alias: a\\b -->", self.read("a\\b/project-overview.md"))

    def test_services_on_stdin_without_a_terminal_are_said_to_be_ignored(self):
        proc = self.init("--workspace", stdin=subprocess.PIPE)
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn("no service created — --service NAME adds one", proc.err)

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_submodule_is_refused_and_the_commands_printed(self):
        # A real submodule: the superproject's git state is exactly what the commands would change.
        upstream = self.tmp / "upstream"
        subprocess.run(["git", "init", "-q", str(upstream)], check=True)
        (upstream / "README.md").write_text("cortex\n", encoding="utf-8")
        git = ["git", "-c", "user.email=ci@example.com", "-c", "user.name=CI", "-c", "protocol.file.allow=always"]
        subprocess.run([*git, "-C", str(upstream), "add", "."], check=True)
        subprocess.run([*git, "-C", str(upstream), "commit", "-q", "-m", "x"], check=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run([*git, "-C", str(self.project), "submodule", "add", "-q", str(upstream), "cortex"], check=True)
        before = {p.relative_to(self.project).as_posix(): p.read_bytes() for p in self.project.rglob("*") if p.is_file()}
        for command in (["init"], ["sync"]):
            with self.subTest(command=command[0]):
                if command == ["sync"]:
                    (self.project / "cortex.toml").write_text(f'version = "{OWN}"\ntheme = "h2g2"\n', encoding="utf-8")
                    before["cortex.toml"] = (self.project / "cortex.toml").read_bytes()
                proc = harness.run(*command, cwd=self.project, env=self.env)
                err = proc.stderr.decode()
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn("cortex/ is a git submodule", err)
                # Its repository under .git/modules/ may hold work of theirs: the checks come first (#170).
                self.assertIn(f"Its repository, {display(self.project / '.git' / 'modules' / 'cortex')}, goes with", err)
                self._checks_printed(err, self.project / "cortex")
                self.assertLess(err.index("stash list"), err.index("submodule deinit -f"))
                root = display(self.project)
                for line in (f"git -C {quote(root)} submodule deinit -f cortex", f"git -C {quote(root)} rm cortex",
                             remove(display(self.project / ".git" / "modules" / "cortex"))):
                    self.assertIn(f"    {line}\n", err)
                after = {p.relative_to(self.project).as_posix(): p.read_bytes() for p in self.project.rglob("*") if p.is_file()}
                self.assertEqual(after, before)

    def _submodule_stack(self, over_a_clone=False, another=False, inside=""):
        """A project whose cortex/ is a submodule, pushed to an origin, and a teammate's clone of it
        with the submodule initialised. ``over_a_clone``: added on a clone of Cortex already there,
        which keeps its repository in cortex/.git. ``another``: a second submodule, which stays.
        ``inside``: the project is that subdirectory of the repository."""
        git = ["git", "-c", "user.email=ci@example.com", "-c", "user.name=CI", "-c", "protocol.file.allow=always"]
        upstream, origin, teammate = self.tmp / "upstream", self.tmp / "origin.git", self.tmp / "teammate"
        subprocess.run(["git", "init", "-q", str(upstream)], check=True)
        (upstream / "README.md").write_text("cortex\n", encoding="utf-8")
        (upstream / ".gitignore").write_text("agents/personalities/.active-*\n", encoding="utf-8")
        subprocess.run([*git, "-C", str(upstream), "add", "."], check=True)
        subprocess.run([*git, "-C", str(upstream), "commit", "-q", "-m", "x"], check=True)
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        project = self.project / inside
        project.mkdir(parents=True, exist_ok=True)
        if over_a_clone:
            subprocess.run([*git, "clone", "-q", str(upstream), str(project / "cortex")], check=True)
        subprocess.run([*git, "-C", str(project), "submodule", "add", "-q", str(upstream), "cortex"], check=True)
        if another:
            subprocess.run([*git, "-C", str(self.project), "submodule", "add", "-q", str(upstream), "other"], check=True)
        subprocess.run([*git, "-C", str(self.project), "commit", "-q", "-m", "the submodule"], check=True)
        subprocess.run([*git, "-C", str(self.project), "push", "-q", str(origin), "HEAD:refs/heads/main"], check=True)
        subprocess.run([*git, "clone", "-q", "--recurse-submodules", "-b", "main", str(origin), str(teammate)],
                       check=True, capture_output=True)
        return git, origin, teammate

    def _with_git(self):
        return dict(self.env, PATH=os.pathsep.join([os.path.dirname(shutil.which("git")), self.env["PATH"]]))

    @staticmethod
    def _shown(err):
        """The commands a refusal prints to be run, in order."""
        block = err.split("shown here, not run:\n\n", 1)[1].split("\n\n", 1)[0]
        return [line.strip() for line in block.splitlines()]

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_the_last_submodule_leaves_no_empty_gitmodules(self):
        # git rm empties .gitmodules of its last section and keeps the file, tracked.
        self._submodule_stack()
        proc = harness.run("init", cwd=self.project, env=self._with_git())
        self.assertEqual(proc.returncode, 1)
        commands = self._shown(proc.stderr.decode())
        self.assertIn(f"git -C {quote(display(self.project))} rm -f .gitmodules", commands)
        if os.name == "nt":
            return
        for command in commands:
            subprocess.run(command, shell=True, check=True, capture_output=True)
        self.assertFalse((self.project / ".gitmodules").exists())
        status = subprocess.run(["git", "-C", str(self.project), "status", "--short"], capture_output=True, text=True)
        self.assertEqual(sorted(status.stdout.splitlines()), ["D  .gitmodules", "D  cortex"])

    def _teammate_after_the_pull(self):
        """The teammate's clone once they pulled the commit that removes the submodule."""
        git, origin, teammate = self._submodule_stack()
        for command in (["submodule", "deinit", "-q", "-f", "cortex"], ["rm", "-q", "cortex"], ["rm", "-q", "-f", ".gitmodules"]):
            subprocess.run([*git, "-C", str(self.project), *command], check=True)
        self._rmtree(self.project / ".git" / "modules" / "cortex")
        (self.project / "cortex.toml").write_text(f'version = "{OWN}"\ntheme = "h2g2"\n', encoding="utf-8")
        subprocess.run([*git, "-C", str(self.project), "add", "cortex.toml"], check=True)
        subprocess.run([*git, "-C", str(self.project), "commit", "-q", "-m", "the store"], check=True)
        subprocess.run([*git, "-C", str(self.project), "push", "-q", str(origin), "HEAD:refs/heads/main"], check=True)
        subprocess.run([*git, "-C", str(teammate), "pull", "-q"], capture_output=True)       # warns: cortex/ is not empty
        self.assertTrue((teammate / "cortex" / ".git").is_file())
        # The theme the scripts chose, written inside cortex/ and ignored there, before 1.0.0.
        (teammate / "cortex" / "agents" / "personalities").mkdir(parents=True)
        (teammate / "cortex" / "agents" / "personalities" / ".active-marker").write_text("h2g2\n", encoding="utf-8")
        return teammate

    CHECKS = (["status", "--short", "--ignored", "--", ".", ":(exclude)agents/personalities/.active-*"],
              ["log", "--format=%h %an: %s", "HEAD", "--branches", "--reflog", "--not", "--remotes"],
              ["stash", "list"])

    def _checks_printed(self, err, cortex):
        # The very checks the tests run, as the refusal prints them: quoted where the shell needs it.
        lines = "".join(f"    git -C {quote(display(cortex))} {' '.join(quote(a) if ':' in a else a for a in check)}\n"
                        for check in self.CHECKS)
        self.assertIn(lines, err)

    def _listed(self, cortex, check):
        proc = subprocess.run(["git", "-C", str(cortex), *check], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    @staticmethod
    def _rmtree(path):
        # git's objects are read-only, and Windows removes no read-only file.
        writable = {"onexc" if sys.version_info >= (3, 12) else "onerror":
                    lambda remove, name, _: (os.chmod(name, 0o700), remove(name))}
        shutil.rmtree(path, **writable)

    def _sync_after_running(self, teammate, err):
        """Run what a refusal printed, then sync again: it must pass."""
        if os.name == "nt":
            return
        for command in self._shown(err):
            subprocess.run(command, shell=True, check=True, capture_output=True)
        proc = harness.run("sync", cwd=teammate, env=self._with_git())
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_teammate_who_pulled_the_removal_is_given_commands_that_work(self):
        # After the pull, git has dropped cortex from the index and kept the directory, the
        # submodule's repository and its settings: deinit and rm have nothing to act on.
        teammate = self._teammate_after_the_pull()
        without_git = harness.run("sync", cwd=teammate, env=self.env)
        self.assertIn("submodule deinit", without_git.stderr.decode())      # no git to ask: the migrator's commands

        proc = harness.run("sync", cwd=teammate, env=self._with_git())
        err = proc.stderr.decode()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("was removed by a commit you pulled", err)
        # What changed in it, ignored files included but the scripts' marker, and the commits no remote holds.
        self._checks_printed(err, teammate / "cortex")
        for check in self.CHECKS:
            self.assertEqual(self._listed(teammate / "cortex", check), "", check)      # a clean clone: nothing listed
        self.assertTrue(self._listed(teammate / "cortex", ["status", "--short", "--ignored"]).startswith("!! agents/"))
        self.assertNotIn("submodule deinit", err)
        root = quote(display(teammate))
        self.assertEqual(self._shown(err), [f"git -C {root} config --remove-section submodule.cortex",
                                            remove(display(teammate / "cortex")),
                                            remove(display(teammate / ".git" / "modules" / "cortex"))])
        self._sync_after_running(teammate, err)

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_teammate_who_ran_the_commands_1_0_0_printed(self):
        # Of the three, only the removal of .git/modules/cortex ran: cortex/.git names a repository
        # that is gone, and git cannot show what changed in cortex/.
        teammate = self._teammate_after_the_pull()
        self._rmtree(teammate / ".git" / "modules" / "cortex")
        proc = harness.run("sync", cwd=teammate, env=self._with_git())
        err = proc.stderr.decode()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Its repository is already gone, so git cannot show what changed in it", err)
        self.assertNotIn("status --short", err)
        self.assertNotIn("with the submodule's repository", err)
        self.assertEqual(self._shown(err), [f"git -C {quote(display(teammate))} config --remove-section submodule.cortex",
                                            remove(display(teammate / "cortex"))])
        self._sync_after_running(teammate, err)

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_the_first_check_lists_what_is_the_developer_s(self):
        # Only the scripts' marker is left out: a changed file, and a file of theirs beside the marker, show.
        teammate = self._teammate_after_the_pull()
        cortex = teammate / "cortex"
        (cortex / "README.md").write_text("changed\n", encoding="utf-8")
        (cortex / "agents" / "personalities" / "notes.md").write_text("mine\n", encoding="utf-8")
        listed = self._listed(cortex, self.CHECKS[0])
        self.assertIn(" M README.md", listed)
        self.assertIn("?? agents/", listed)

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_teammate_s_stash_and_lost_commits_are_listed(self):
        # A commit made on a detached HEAD and left behind is in the reflog only; a stash is in no branch.
        teammate = self._teammate_after_the_pull()
        cortex = teammate / "cortex"
        git = ["git", "-c", "user.email=ci@example.com", "-c", "user.name=CI", "-C", str(cortex)]
        subprocess.run([*git, "checkout", "-q", "--detach"], check=True)
        (cortex / "lost.md").write_text("lost\n", encoding="utf-8")
        subprocess.run([*git, "add", "lost.md"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "lost work"], check=True)
        subprocess.run([*git, "checkout", "-q", "--detach", "HEAD~1"], check=True)
        (cortex / "README.md").write_text("stashed\n", encoding="utf-8")
        subprocess.run([*git, "stash", "-q"], check=True)
        self.assertIn("CI: lost work", self._listed(cortex, self.CHECKS[1]))
        self.assertIn("stash@{0}", self._listed(cortex, self.CHECKS[2]))

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_teammate_s_commits_on_another_branch_are_listed(self):
        # Committed on a branch of the submodule, then put back on its pinned commit by an update.
        teammate = self._teammate_after_the_pull()
        cortex = teammate / "cortex"
        git = ["git", "-c", "user.email=ci@example.com", "-c", "user.name=CI", "-C", str(cortex)]
        subprocess.run([*git, "checkout", "-q", "-b", "my-overlay"], check=True)
        (cortex / "mine.md").write_text("mine\n", encoding="utf-8")
        subprocess.run([*git, "add", "mine.md"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "my overlay"], check=True)
        subprocess.run([*git, "checkout", "-q", "--detach", "HEAD~1"], check=True)
        self.assertIn("my overlay", self._listed(cortex, self.CHECKS[1]))

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_submodule_whose_repository_is_its_own_git_directory(self):
        # A clone made a submodule in place keeps its .git directory; `git rm --cached` then takes it
        # out of the index. Its repository is there: the checks are printed, not "already gone".
        git, _, _ = self._submodule_stack()
        subprocess.run([*git, "-C", str(self.project), "submodule", "deinit", "-q", "-f", "cortex"], check=True)
        subprocess.run([*git, "-C", str(self.project), "rm", "-q", "--cached", "cortex"], check=True)
        self._rmtree(self.project / "cortex")
        self._rmtree(self.project / ".git" / "modules" / "cortex")
        subprocess.run([*git, "clone", "-q", str(self.tmp / "upstream"), str(self.project / "cortex")], check=True)
        self.assertTrue((self.project / "cortex" / ".git").is_dir())
        proc = harness.run("init", cwd=self.project, env=self._with_git())
        err = proc.stderr.decode()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("with the submodule's repository", err)
        self.assertNotIn("already gone", err)
        self._checks_printed(err, self.project / "cortex")
        commands = self._shown(err)
        self.assertEqual(commands[-1], remove(display(self.project / "cortex")))
        self.assertFalse(any(".git/modules" in command.replace("\\", "/") for command in commands), commands)

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_teammate_whose_settings_hold_no_submodule_section(self):
        teammate = self._teammate_after_the_pull()
        subprocess.run(["git", "-C", str(teammate), "config", "--remove-section", "submodule.cortex"], check=True)
        # Another submodule's section, whose name starts the same, is not cortex's.
        subprocess.run(["git", "-C", str(teammate), "config", "submodule.cortex-old.url", "x"], check=True)
        # A section in the user's own settings is none of this project's: --local.
        (self.tmp / "gitconfig").write_text('[submodule "cortex"]\n\turl = elsewhere\n', encoding="utf-8")
        proc = harness.run("sync", cwd=teammate, env=dict(self._with_git(), GIT_CONFIG_GLOBAL=str(self.tmp / "gitconfig")))
        err = proc.stderr.decode()
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self._shown(err), [remove(display(teammate / "cortex")),
                                            remove(display(teammate / ".git" / "modules" / "cortex"))])
        self._sync_after_running(teammate, err)

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_second_submodule_keeps_gitmodules(self):
        # .gitmodules is removed only when cortex was its last section.
        git, _, _ = self._submodule_stack()
        other = self.tmp / "other"
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        subprocess.run([*git, "-C", str(other), "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        subprocess.run([*git, "-C", str(self.project), "submodule", "add", "-q", str(other), "libs/other"], check=True)
        proc = harness.run("init", cwd=self.project, env=self._with_git())
        self.assertEqual(proc.returncode, 1)
        commands = self._shown(proc.stderr.decode())
        self.assertIn(f"git -C {quote(display(self.project))} rm cortex", commands)
        self.assertFalse(any(".gitmodules" in command for command in commands), commands)

    def test_a_refusal_init_prints_names_no_sync_it_never_ran(self):
        # cortex init runs the same checks as sync: its refusals name Cortex, and the command to run again.
        (self.project / "cortex").mkdir()
        (self.project / "cortex" / ".synced").write_text("", encoding="utf-8")
        proc = self.init()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("holds a .synced file Cortex cannot read: Cortex did not write this directory", proc.err)
        self.assertIn("then run the command again", proc.err)
        self.assertNotIn("cortex sync", proc.err)

    def test_a_clone_is_refused_and_its_removal_printed(self):
        (self.project / "cortex" / ".git").mkdir(parents=True)
        proc = self.init()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cortex/ is a git clone", proc.err)
        # A clone may hold commits or a stash of the developer's: the checks come before its removal (#170).
        self._checks_printed(proc.err, self.project / "cortex")
        self.assertLess(proc.err.index("stash list"), proc.err.index(remove(display(self.project / "cortex"))))
        self.assertIn(f"    {remove(display(self.project / 'cortex'))}\n", proc.err)
        self.assertEqual(sorted(p.name for p in self.project.iterdir()), ["cortex"])

    def test_the_commands_run_from_any_directory_and_hold_any_path(self):
        # Shown from ~ for projects/foo, `rm -rf cortex` would remove ~/cortex; a space would split it.
        project = self.tmp / "init" / "my app"
        (project / "cortex" / ".git").mkdir(parents=True)
        proc = self.init("my app", cwd=self.tmp / "init")
        self.assertEqual(proc.returncode, 1)
        command = remove(display(project / "cortex"))
        self.assertIn(f"    {command}\n", proc.err)
        if os.name != "nt":
            self.assertEqual(shlex.split(command), ["rm", "-rf", str(project / "cortex")])

    def test_a_submodule_with_its_own_git_directory(self):
        # Older git kept a submodule's repository in its .git directory: .gitmodules still says so.
        (self.project / "cortex" / ".git").mkdir(parents=True)
        (self.project / ".gitmodules").write_text('[submodule "cortex"]\n\tpath = cortex\n\turl = x\n',
                                                  encoding="utf-8")
        proc = self.init()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cortex/ is a git submodule", proc.err)
        self.assertIn(f"git -C {quote(display(self.project))} rm cortex", proc.err)
        # absorbgitdirs moves a repository kept in cortex/.git under .git/modules/ — deinit refuses it
        # there before git 2.35 — and it is removed there (#157).
        self.assertIn(f"    git -C {quote(display(self.project))} submodule absorbgitdirs cortex\n", proc.err)
        self.assertIn(f"    {remove(display(self.project / '.git' / 'modules' / 'cortex'))}\n", proc.err)

    def test_a_removal_that_could_reach_another_repository_is_not_printed(self):
        # absorbgitdirs refuses a directory that is there already, and git ignores a name with .. in its
        # path: the commands would fail, and a removal there could only reach another repository.
        (self.project / "cortex" / ".git").mkdir(parents=True)
        modules = self.project / ".git" / "modules"
        for name, there in (("../../src", None), ("a/../../src", None), ("..\\..\\src", None), (".", None),
                            ("./", None), ("x\x1b[31mred", None), ("cortex", "HEAD"), ("cortex", ""),
                            ("/srv/elsewhere", None)):
            with self.subTest(name=name, there=there):
                (self.project / ".gitmodules").write_text(f'[submodule "{name}"]\n\tpath = cortex\n\turl = x\n',
                                                          encoding="utf-8")
                if there is not None:
                    (modules / name).mkdir(parents=True)
                    if there:
                        (modules / name / there).write_text("ref: refs/heads/main\n", encoding="utf-8")
                proc = self.init()
                if there is not None:
                    shutil.rmtree(modules / name)
                self.assertEqual(proc.returncode, 1)
                if name.startswith("/") or there == "":
                    # git joins an absolute name to modules/, as any other; it moves a repository onto an
                    # empty directory, as rename does.
                    self.assertEqual(self._shown(proc.err)[-1], remove(display(modules / name.lstrip("/"))))
                    continue
                self.assertNotIn("shown here, not run", proc.err)
                self.assertNotIn("rm -rf", proc.err)
                self.assertNotIn("Remove-Item", proc.err)
                if there:
                    self.assertIn(f"git moves it to {display(modules / name)} before the submodule goes — but a "
                                  "repository is there already", proc.err)
                else:
                    # A control character is written out, never sent to the terminal (#85).
                    written = name.replace("\x1b", "\\x1b")
                    self.assertIn(f'the name .gitmodules gives the submodule, "{written}", is not '
                                  "one Cortex can follow under .git/modules/", proc.err)
                    self.assertNotIn("\x1b", proc.err)

    def test_a_link_in_the_place_is_no_empty_directory(self):
        # git follows the link, and would move the repository out of .git/: Cortex prints no command.
        (self.project / "cortex" / ".git").mkdir(parents=True)
        (self.project / ".gitmodules").write_text('[submodule "cortex"]\n\tpath = cortex\n\turl = x\n', encoding="utf-8")
        (self.project / "elsewhere").mkdir()
        (self.project / ".git" / "modules").mkdir(parents=True)
        try:
            (self.project / ".git" / "modules" / "cortex").symlink_to(self.project / "elsewhere", target_is_directory=True)
        except OSError:
            self.skipTest("no symbolic links here")
        proc = self.init()
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("shown here, not run", proc.err)

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_clone_beside_the_settings_of_another_submodule(self):
        # submodule.cortex-old.* is another submodule's section: cortex/ is a clone, not one left behind.
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(["git", "-C", str(self.project), "config", "submodule.cortex-old.url", "x"], check=True)
        (self.project / "cortex" / ".git").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.project / "cortex")], check=True)
        proc = harness.run("init", cwd=self.project, env=self._with_git())
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cortex/ is a git clone", proc.stderr.decode())

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_place_an_earlier_submodule_left_is_named_not_removed(self):
        # git rm keeps the repository of the submodule it removes: a clone made a submodule in place, by
        # the same name, cannot be moved there — and that repository may be in use.
        git, _, _ = self._submodule_stack()
        for command in (["submodule", "deinit", "-q", "-f", "cortex"], ["rm", "-q", "cortex"]):
            subprocess.run([*git, "-C", str(self.project), *command], check=True)
        subprocess.run([*git, "-C", str(self.project), "commit", "-q", "-m", "no submodule"], check=True)
        subprocess.run([*git, "clone", "-q", str(self.tmp / "upstream"), str(self.project / "cortex")], check=True)
        subprocess.run([*git, "-C", str(self.project), "submodule", "add", "-q", str(self.tmp / "upstream"), "cortex"],
                       check=True, capture_output=True)
        modules = self.project / ".git" / "modules" / "cortex"
        self.assertTrue(modules.is_dir())
        proc = harness.run("init", cwd=self.project, env=self._with_git())
        err = proc.stderr.decode()
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"git moves it to {display(modules)} before the submodule goes", err)
        self.assertNotIn("shown here, not run", err)
        # git itself refuses: the commands would only have failed.
        absorb = subprocess.run(["git", "-C", str(self.project), "submodule", "absorbgitdirs", "cortex"],
                                capture_output=True)
        self.assertNotEqual(absorb.returncode, 0)
        self.assertTrue((self.project / "cortex" / ".git").is_dir())

    def test_without_git_gitmodules_is_read_as_git_reads_it(self):
        # A header in capitals or with a comment opens a section; one of another kind closes it; of two
        # sections with that path, git takes the last.
        (self.project / "cortex" / ".git").mkdir(parents=True)
        modules = self.project / ".git" / "modules"
        (modules / "other").mkdir(parents=True)
        other = '[submodule "other"]\n\tpath = other\n\turl = x\n'
        for text, name in ((other + '[Submodule "team-spec"]  # Cortex\n\tpath = cortex\n\turl = x\n', "team-spec"),
                           (other + '[core]\n\tpath = cortex\n', "cortex"),
                           ('[submodule "first"]\n\tpath = cortex\n[submodule "second"]\n\tpath = cortex\n', "second")):
            with self.subTest(name=name):
                (self.project / ".gitmodules").write_text(text, encoding="utf-8")
                proc = self.init()
                self.assertEqual(proc.returncode, 1)
                self.assertEqual(self._shown(proc.err)[-1], remove(display(modules / name)))

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_submodule_added_over_a_clone_leaves_no_repository_behind(self):
        # git submodule add, run on a clone, keeps its .git directory; absorbgitdirs moves it under .git/modules/.
        self._submodule_stack(over_a_clone=True)
        self.assertTrue((self.project / "cortex" / ".git").is_dir())
        proc = harness.run("init", cwd=self.project, env=self._with_git())
        self.assertEqual(proc.returncode, 1)
        err = proc.stderr.decode()
        commands = self._shown(err)
        self.assertEqual(commands[0], f"git -C {quote(display(self.project))} submodule absorbgitdirs cortex")
        self.assertEqual(commands[-1], remove(display(self.project / ".git" / "modules" / "cortex")))
        # The clone's history goes too: the teammate's checks come first (#160).
        self._checks_printed(err, self.project / "cortex")
        if os.name == "nt":
            return
        for command in commands:
            subprocess.run(command, shell=True, check=True, capture_output=True)
        self.assertFalse((self.project / ".git" / "modules" / "cortex").exists())
        status = subprocess.run(["git", "-C", str(self.project), "status", "--short"], capture_output=True, text=True)
        self.assertEqual(sorted(status.stdout.splitlines()), ["D  .gitmodules", "D  cortex"])

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_submodule_added_over_a_clone_in_a_linked_worktree(self):
        # absorbgitdirs moves the repository under .git/worktrees/<worktree>/modules/, as git says, and
        # the main worktree's own submodule stays.
        git, _, _ = self._submodule_stack()
        linked = self.tmp / "linked"
        subprocess.run([*git, "-C", str(self.project), "worktree", "add", "-q", str(linked)], check=True, capture_output=True)
        subprocess.run([*git, "clone", "-q", str(self.tmp / "upstream"), str(linked / "cortex")], check=True)
        gitdir = Path(subprocess.run(["git", "-C", str(linked), "rev-parse", "--absolute-git-dir"],
                                     capture_output=True, encoding="utf-8", check=True).stdout.strip())
        proc = harness.run("init", cwd=linked, env=self._with_git())
        self.assertEqual(proc.returncode, 1)
        commands = self._shown(proc.stderr.decode())
        self.assertEqual(commands[0], f"git -C {quote(display(linked))} submodule absorbgitdirs cortex")
        self.assertEqual(commands[-1], remove(display(gitdir / "modules" / "cortex")))
        if os.name == "nt":
            return
        for command in commands:
            subprocess.run(command, shell=True, check=True, capture_output=True)
        self.assertFalse((gitdir / "modules" / "cortex").exists())
        self.assertTrue((self.project / ".git" / "modules" / "cortex").is_dir())
        status = subprocess.run(["git", "-C", str(linked), "status", "--short"], capture_output=True, text=True)
        self.assertEqual(sorted(status.stdout.splitlines()), ["D  .gitmodules", "D  cortex"])

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_comment_after_the_header_is_no_other_section(self):
        self._the_removal_after_another_section('[submodule "cortex"]  # the team\'s spec')

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_header_in_capitals_is_no_other_section(self):
        self._the_removal_after_another_section('[Submodule "cortex"]')

    def _the_removal_after_another_section(self, header):
        # git reads .gitmodules: read by hand, cortex's section was taken for the one before it, and the
        # removal named that submodule's repository.
        git, _, _ = self._submodule_stack(over_a_clone=True, another=True)
        gitmodules = self.project / ".gitmodules"
        other = next(section for section in gitmodules.read_text(encoding="utf-8").split("[submodule ")
                     if section.startswith('"other"'))
        gitmodules.write_text(f"[submodule {other}{header}\n\tpath = cortex\n\turl = {self.tmp / 'upstream'}\n",
                              encoding="utf-8")
        subprocess.run([*git, "-C", str(self.project), "add", ".gitmodules"], check=True)
        proc = harness.run("init", cwd=self.project, env=self._with_git())
        self.assertEqual(proc.returncode, 1)
        commands = self._shown(proc.stderr.decode())
        self.assertEqual(commands[-1], remove(display(self.project / ".git" / "modules" / "cortex")))
        if os.name == "nt":
            return
        for command in commands:
            subprocess.run(command, shell=True, check=True, capture_output=True)
        self.assertFalse((self.project / ".git" / "modules" / "cortex").exists())
        self.assertTrue((self.project / ".git" / "modules" / "other").is_dir())

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_of_two_sections_with_its_path_git_takes_the_last(self):
        git, _, _ = self._submodule_stack(over_a_clone=True)
        gitmodules = self.project / ".gitmodules"
        gitmodules.write_text(f'[submodule "first"]\n\tpath = cortex\n\turl = x\n{gitmodules.read_text(encoding="utf-8")}',
                              encoding="utf-8")
        subprocess.run([*git, "-C", str(self.project), "add", ".gitmodules"], check=True)
        proc = harness.run("init", cwd=self.project, env=self._with_git())
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self._shown(proc.stderr.decode())[-1], remove(display(self.project / ".git" / "modules" / "cortex")))

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_project_in_a_subdirectory_of_the_repository(self):
        # git names the submodule by its path from the top — app/cortex — and keeps the top's own.
        git, _, _ = self._submodule_stack()
        app = self.project / "app"
        subprocess.run([*git, "clone", "-q", str(self.tmp / "upstream"), str(app / "cortex")], check=True)
        subprocess.run([*git, "-C", str(app), "submodule", "add", "-q", str(self.tmp / "upstream"), "cortex"],
                       check=True, capture_output=True)
        subprocess.run([*git, "-C", str(self.project), "commit", "-q", "-m", "app"], check=True)
        proc = harness.run("init", cwd=app, env=self._with_git())
        self.assertEqual(proc.returncode, 1)
        commands = self._shown(proc.stderr.decode())
        self.assertEqual(commands[-1], remove(display(self.project / ".git" / "modules" / "app" / "cortex")))
        if os.name == "nt":
            return
        # Reached through a link, the project's .. is the repository's, as git answers it — not the link's.
        link = self.tmp / "elsewhere" / "app"
        link.parent.mkdir()
        link.symlink_to(app)
        through = harness.run("init", cwd=link, env=self._with_git())
        self.assertEqual(self._shown(through.stderr.decode())[-1], commands[-1])
        for command in commands:
            subprocess.run(command, shell=True, check=True, capture_output=True)
        self.assertFalse((self.project / ".git" / "modules" / "app" / "cortex").exists())
        self.assertTrue((self.project / ".git" / "modules" / "cortex").is_dir())

    @unittest.skipUnless(HAS_GIT and os.name != "nt", "needs git and a POSIX shell")
    def test_an_older_git_is_told_where_the_repository_goes(self):
        # git before 2.31 knows no --path-format: rev-parse prints it back, and the path after it.
        self._submodule_stack(over_a_clone=True)
        bin_dir = self.tmp / "older-git"
        bin_dir.mkdir()
        (bin_dir / "git").write_text('#!/bin/sh\nfor a do\n  shift\n'
                                     '  case $a in --path-format=*) echo "$a" ;; *) set -- "$@" "$a" ;; esac\n'
                                     f'done\nexec {quote(shutil.which("git"))} "$@"\n', encoding="utf-8")
        (bin_dir / "git").chmod(0o755)
        proc = harness.run("init", cwd=self.project, env=dict(self.env, PATH=str(bin_dir)))
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self._shown(proc.stderr.decode())[-1],
                         remove(display(self.project / ".git" / "modules" / "cortex")))

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_clone_beside_a_submodule_moved_elsewhere(self):
        # The submodule's settings belong to the one git mv moved: the cortex/ cloned since is a clone.
        git = ["git", "-c", "user.email=ci@example.com", "-c", "user.name=CI", "-c", "protocol.file.allow=always"]
        upstream = self.tmp / "upstream"
        subprocess.run(["git", "init", "-q", str(upstream)], check=True)
        subprocess.run([*git, "-C", str(upstream), "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run([*git, "-C", str(self.project), "submodule", "add", "-q", str(upstream), "cortex"], check=True)
        (self.project / "vendor").mkdir()
        subprocess.run([*git, "-C", str(self.project), "mv", "cortex", "vendor/cortex"], check=True)
        # git reads a section's header in any case.
        gitmodules = self.project / ".gitmodules"
        gitmodules.write_text(gitmodules.read_text(encoding="utf-8").replace("[submodule", "[Submodule"), encoding="utf-8")
        subprocess.run([*git, "-C", str(self.project), "add", ".gitmodules"], check=True)
        subprocess.run([*git, "-C", str(self.project), "commit", "-q", "-m", "moved"], check=True)
        subprocess.run([*git, "clone", "-q", str(upstream), str(self.project / "cortex")], check=True)
        proc = harness.run("init", cwd=self.project, env=self._with_git())
        err = proc.stderr.decode()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cortex/ is a git clone", err)
        self.assertNotIn("submodule", err)

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_the_developer_who_added_it_over_a_clone_after_the_pull(self):
        # The pull takes .gitmodules away, and cortex/.git is a directory: a submodule left behind, no clone.
        self._the_adder_after_the_pull(another=False)

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_the_developer_who_added_it_over_a_clone_keeps_another_submodule(self):
        # .gitmodules stays, for the other submodule: it names no cortex, and cortex/ is still no clone.
        self._the_adder_after_the_pull(another=True)

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_the_developer_who_added_it_in_a_subdirectory_after_the_pull(self):
        # git names a submodule added in app/ by its path from the top: its settings are submodule.app/cortex —
        # here with a space and parentheses, which the shell and git's pattern must both take as they are.
        self._the_adder_after_the_pull(another=False, inside="my app (2)")

    def _the_adder_after_the_pull(self, another, inside=""):
        git, origin, teammate = self._submodule_stack(over_a_clone=True, another=another, inside=inside)
        project, name = self.project / inside, f"{inside}/cortex" if inside else "cortex"
        removal = [["submodule", "deinit", "-q", "-f", name], ["rm", "-q", name]]
        for command in removal if another else [*removal, ["rm", "-q", "-f", ".gitmodules"]]:
            subprocess.run([*git, "-C", str(teammate), *command], check=True)
        self._rmtree(teammate / ".git" / "modules" / name)
        (teammate / inside / "cortex.toml").write_text(f'version = "{OWN}"\ntheme = "h2g2"\n', encoding="utf-8")
        subprocess.run([*git, "-C", str(teammate / inside), "add", "cortex.toml"], check=True)
        subprocess.run([*git, "-C", str(teammate), "commit", "-q", "-m", "the store"], check=True)
        subprocess.run([*git, "-C", str(teammate), "push", "-q", "origin", "HEAD:refs/heads/main"], check=True)
        subprocess.run([*git, "-C", str(self.project), "pull", "-q", str(origin), "main"], capture_output=True)
        self.assertTrue((project / "cortex" / ".git").is_dir())
        self.assertEqual((self.project / ".gitmodules").exists(), another)
        proc = harness.run("sync", cwd=project, env=self._with_git())
        err = proc.stderr.decode()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("was removed by a commit you pulled", err)
        self.assertIn("with the submodule's repository", err)
        self._checks_printed(err, project / "cortex")
        section = quote(f"submodule.{name}") if inside else f"submodule.{name}"
        self.assertEqual(self._shown(err), [f"git -C {quote(display(project))} config --remove-section {section}",
                                            remove(display(project / "cortex"))])
        self._sync_after_running(project, err)
        if os.name != "nt":
            left = subprocess.run(["git", "-C", str(self.project), "config", "--local", "--get-regexp", "^submodule\\."],
                                  capture_output=True, text=True)
            self.assertNotIn(f"submodule.{name}.", left.stdout)

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_submodule_staged_never_committed(self):
        # git rm refuses a submodule that is only in the index: -f.
        git = ["git", "-c", "user.email=ci@example.com", "-c", "user.name=CI", "-c", "protocol.file.allow=always"]
        upstream = self.tmp / "upstream"
        subprocess.run(["git", "init", "-q", str(upstream)], check=True)
        subprocess.run([*git, "-C", str(upstream), "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run([*git, "-C", str(self.project), "commit", "-q", "--allow-empty", "-m", "root"], check=True)
        subprocess.run([*git, "-C", str(self.project), "submodule", "add", "-q", str(upstream), "cortex"], check=True)
        proc = harness.run("init", cwd=self.project, env=self._with_git())
        self.assertEqual(proc.returncode, 1)
        commands = self._shown(proc.stderr.decode())
        self.assertIn(f"git -C {quote(display(self.project))} rm -f cortex", commands)
        if os.name == "nt":
            return
        for command in commands:
            subprocess.run(command, shell=True, check=True, capture_output=True)
        status = subprocess.run(["git", "-C", str(self.project), "status", "--short"], capture_output=True, text=True)
        self.assertEqual(status.stdout, "")
        self.assertFalse((self.project / "cortex").exists())

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_submodule_in_a_linked_worktree_of_the_project(self):
        # Its repository is under .git/worktrees/<worktree>/modules/: a submodule, not a worktree.
        git, _, _ = self._submodule_stack()
        linked = self.tmp / "linked"
        subprocess.run([*git, "-C", str(self.project), "worktree", "add", "-q", str(linked)], check=True, capture_output=True)
        subprocess.run([*git, "-C", str(linked), "submodule", "update", "-q", "--init"], check=True, capture_output=True)
        # git writes UTF-8 whatever Windows' code page: text=True would read a path with an accent wrongly.
        modules = Path(subprocess.run(["git", "-C", str(linked / "cortex"), "rev-parse", "--absolute-git-dir"],
                                      capture_output=True, encoding="utf-8", check=True).stdout.strip())
        self.assertIn("worktrees", modules.parts)
        proc = harness.run("init", cwd=linked, env=self._with_git())
        err = proc.stderr.decode()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cortex/ is a git submodule", err)
        self.assertNotIn("worktree remove", err)
        root = quote(display(linked))
        self.assertEqual(self._shown(err), [f"git -C {root} submodule deinit -f cortex", f"git -C {root} rm cortex",
                                            f"git -C {root} rm -f .gitmodules", remove(display(modules))])
        if os.name == "nt":
            return
        for command in self._shown(err):
            subprocess.run(command, shell=True, check=True, capture_output=True)
        self.assertFalse(modules.exists())
        status = subprocess.run(["git", "-C", str(linked), "status", "--short"], capture_output=True, text=True)
        self.assertEqual(sorted(status.stdout.splitlines()), ["D  .gitmodules", "D  cortex"])

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_submodule_with_a_new_commit_staged(self):
        # The index differs from the last commit: git rm refuses it without -f, even after deinit -f.
        git = ["git", "-c", "user.email=ci@example.com", "-c", "user.name=CI", "-c", "protocol.file.allow=always"]
        upstream = self.tmp / "upstream"
        subprocess.run(["git", "init", "-q", str(upstream)], check=True)
        for message in ("one", "two"):
            subprocess.run([*git, "-C", str(upstream), "commit", "-q", "--allow-empty", "-m", message], check=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run([*git, "-C", str(self.project), "submodule", "add", "-q", str(upstream), "cortex"], check=True)
        subprocess.run([*git, "-C", str(self.project), "commit", "-q", "-m", "the submodule"], check=True)
        subprocess.run([*git, "-C", str(self.project / "cortex"), "checkout", "-q", "HEAD~1"], check=True)
        subprocess.run([*git, "-C", str(self.project), "add", "cortex"], check=True)
        proc = harness.run("init", cwd=self.project, env=self._with_git())
        self.assertEqual(proc.returncode, 1)
        commands = self._shown(proc.stderr.decode())
        self.assertIn(f"git -C {quote(display(self.project))} rm -f cortex", commands)
        if os.name == "nt":
            return
        for command in commands:
            subprocess.run(command, shell=True, check=True, capture_output=True)
        status = subprocess.run(["git", "-C", str(self.project), "status", "--short"], capture_output=True, text=True)
        self.assertEqual(sorted(status.stdout.splitlines()), ["D  .gitmodules", "D  cortex"])

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_submodule_its_settings_hide_from_diff(self):
        # submodule.cortex.ignore = all hides a staged submodule from git diff, not from git rm.
        git = ["git", "-c", "user.email=ci@example.com", "-c", "user.name=CI", "-c", "protocol.file.allow=always"]
        upstream = self.tmp / "upstream"
        subprocess.run(["git", "init", "-q", str(upstream)], check=True)
        subprocess.run([*git, "-C", str(upstream), "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run([*git, "-C", str(self.project), "commit", "-q", "--allow-empty", "-m", "root"], check=True)
        subprocess.run([*git, "-C", str(self.project), "submodule", "add", "-q", str(upstream), "cortex"], check=True)
        subprocess.run(["git", "-C", str(self.project), "config", "submodule.cortex.ignore", "all"], check=True)
        proc = harness.run("init", cwd=self.project, env=self._with_git())
        self.assertEqual(proc.returncode, 1)
        commands = self._shown(proc.stderr.decode())
        self.assertIn(f"git -C {quote(display(self.project))} rm -f cortex", commands)
        if os.name == "nt":
            return
        for command in commands:
            subprocess.run(command, shell=True, check=True, capture_output=True)
        status = subprocess.run(["git", "-C", str(self.project), "status", "--short"], capture_output=True, text=True)
        self.assertEqual(status.stdout, "")

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_project_in_a_directory_named_worktrees(self):
        # Its submodule's repository is .git/modules/worktrees/cortex: no worktree's, which holds commondir.
        git = ["git", "-c", "user.email=ci@example.com", "-c", "user.name=CI", "-c", "protocol.file.allow=always"]
        upstream, project = self.tmp / "upstream", self.project / "worktrees"
        subprocess.run(["git", "init", "-q", str(upstream)], check=True)
        subprocess.run([*git, "-C", str(upstream), "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        project.mkdir()
        subprocess.run([*git, "-C", str(project), "submodule", "add", "-q", str(upstream), "cortex"], check=True)
        subprocess.run([*git, "-C", str(self.project), "commit", "-q", "-m", "the submodule"], check=True)
        proc = harness.run("init", cwd=project, env=self._with_git())
        err = proc.stderr.decode()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cortex/ is a git submodule", err)
        self.assertNotIn("worktree remove", err)

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_worktree_is_no_submodule(self):
        other = self.tmp / "other"
        git = ["git", "-c", "user.email=ci@example.com", "-c", "user.name=CI"]
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        subprocess.run([*git, "-C", str(other), "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        subprocess.run(["git", "-C", str(other), "worktree", "add", "-q", str(self.project / "cortex")], check=True)
        proc = self.init()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cortex/ is a git worktree", proc.err)
        self.assertIn(f"worktree remove {quote(display(self.project / 'cortex'))}", proc.err)
        self.assertNotIn("submodule deinit", proc.err)

    def test_the_team_tier_is_its_own_repository_not_the_projects(self):
        # The setup script asked git whether agents/ was in a working tree — true inside the project's own
        # repository too. The team tier is for an agents/ that is a repository of its own.
        if not HAS_GIT:
            self.skipTest("needs git")
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        (self.project / "agents").mkdir()
        self.init("--workspace", "--service", "api")
        self.assertFalse((self.project / "agents" / "project-overview.md").exists())

    def test_a_cursor_rule_is_written_for_every_conversation_and_one_without_it_said(self):
        proc = self.init("--tool", "cursor")
        self.assertEqual(proc.returncode, 0, proc.err)
        rule = self.project / ".cursor" / "rules" / "cortex.mdc"
        written = rule.read_bytes()
        self.assertTrue(written.startswith(b"---\ndescription: Cortex"), written[:80])
        self.assertIn(b"\nalwaysApply: true\n---\n# Cortex AI Team\n", written)
        self.assertTrue(init_command.untouched(written))
        rule.write_bytes(init_command.without_frontmatter(written))              # an earlier cortex's
        proc = self.init("--tool", "cursor")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn(f"note: {display(rule)} has no `alwaysApply: true` line in a frontmatter, as Cursor's "
                      "documentation writes it: Cursor may not apply it in every conversation — cortex init --tool cursor --force writes "
                      "it again, and keeps it as .bak\n", proc.err)
        self.assertEqual(rule.read_bytes(), init_command.without_frontmatter(written))
        rule.write_bytes(written + b"\n## Our own rules\n")                     # a team's, Cursor applies it always
        proc = self.init("--tool", "cursor")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertNotIn("alwaysApply", proc.err)

    def test_a_rule_of_cursor_named_otherwise_opens_on_the_frontmatter_too(self):
        proc = self.init("--tool", "custom", "--instructions-file", ".cursor/rules/team.mdc")
        self.assertEqual(proc.returncode, 0, proc.err)
        written = (self.project / ".cursor" / "rules" / "team.mdc").read_bytes()
        self.assertTrue(written.startswith(init_command.CURSOR_RULE + b"# Cortex AI Team\n"), written[:120])

class RegisterTests(InitTestCase):
    """Once all of it succeeded, init registers the project in the machine's registry, and says so
    once — not its sync, which may succeed where the rest fails (ADR-011 §3.3)."""

    def setUp(self):
        super().setUp()
        self.registry = self.home / "projects.toml"

    def entries(self):
        data = tomllib.loads(self.registry.read_text(encoding="utf-8"))
        return [(entry["name"], entry["path"]) for entry in data.get("project", [])]

    def test_init_registers_the_project_and_says_it_once_and_init_again_nothing(self):
        self.project.mkdir(parents=True)
        proc = self.init()
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn(f"\nregistered as project — {LISTS}\n\nCortex {OWN} is ready", proc.out)   # its last write
        self.assertEqual(proc.out.count("registered"), 1)
        self.assertEqual(self.entries(), [("project", display(self.project))])
        before = self.registry.read_bytes()
        proc = self.init()
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertNotIn("registered", proc.out + proc.err)
        self.assertEqual(self.registry.read_bytes(), before)

    def test_the_root_registered_is_the_directory_it_initialises(self):
        for args, name in ((("init/acme",), "acme"), (("init/checkout", "--from", str(harness.REPO)), "checkout")):
            with self.subTest(args=args):
                proc = self.init(*args, cwd=self.tmp)
                self.assertEqual(proc.returncode, 0, proc.err)
                self.assertIn(f"registered as {name} — {LISTS}\n", proc.out)
        self.assertEqual(self.entries(), [("acme", display(self.tmp / "init" / "acme")),
                                          ("checkout", display(self.tmp / "init" / "checkout"))])

    def test_an_init_that_fails_after_its_sync_registers_nothing(self):
        self.project.mkdir(parents=True)
        try:
            # A file init writes after the sync, which leads nowhere it can write.
            os.symlink(self.tmp / "nowhere" / "overview.md", self.project / "project-overview.md")
        except OSError:
            self.skipTest("no symbolic link here")
        proc = self.init()
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertTrue((self.project / "cortex.local.toml").is_file())          # its sync ran
        self.assertNotIn("registered", proc.out + proc.err)
        self.assertFalse(self.registry.exists())

    def test_a_registry_that_cannot_be_written_is_a_warning_and_init_succeeds(self):
        self.project.mkdir(parents=True)
        (self.home / "projects.toml.lock").mkdir()                  # no file to lock, as root too
        proc = self.init()
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn(f"warning: {display(self.project)} is not registered — {display(self.registry)} cannot be "
                      "written — ", proc.err)
        self.assertNotIn("registered as", proc.out)
        self.assertIn(f"Cortex {OWN} is ready", proc.out)
        self.assertFalse(self.registry.exists())

    def test_cortex_no_register_set_writes_nothing_and_empty_counts_as_unset(self):
        self.project.mkdir(parents=True)
        self.env["CORTEX_NO_REGISTER"] = "1"
        proc = self.init()
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertNotIn("registered", proc.out + proc.err)
        self.assertEqual(sorted(path.name for path in self.home.iterdir()), ["versions"])
        self.env["CORTEX_NO_REGISTER"] = ""
        proc = self.init()
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn(f"registered as project — {LISTS}\n", proc.out)
        self.assertEqual(self.entries(), [("project", display(self.project))])


class BootstrapMarkerTests(unittest.TestCase):
    """A kept file read through the marker of the template the pinned version writes (ADR-010 §3.1)."""

    def template(self, layout="workspace", format=1):
        title = "# Cortex AI Team — Workspace Mode" if layout == "workspace" else "# Cortex AI Team"
        return (f"{title}\n\n<!-- cortex:bootstrap layout={layout} format={format} -->\n"
                "<!-- Keep the line above as it is. -->\n\nRead cortex.local.toml first.\n").encode("utf-8")

    def test_a_kept_file_of_the_same_layout_and_format_is_a_bootstrap_of_this_version(self):
        self.assertIsNone(init_command.kept_bootstrap(self.template(), self.template()))

    def unmarked(self, layout="workspace"):
        """A template of a version before the bootstrap marker — 1.3.0's, say."""
        title = "# Cortex AI Team — Workspace Mode" if layout == "workspace" else "# Cortex AI Team"
        return f"{title}\n\nRead cortex.local.toml first.\n".encode("utf-8")

    def test_a_line_above_the_title_hides_neither_the_bootstrap_nor_its_layout(self):
        # One reading of the title for every caller: below a line a team wrote above it, a workspace's file is a
        # workspace's to init, to init --force and to upgrade alike — CRLF and a byte order mark hide nothing either.
        for line in (b"<!-- our notes -->\n", b"\xef\xbb\xbf<!-- our notes -->\r\n"):
            for layout, other in (("workspace", "single"), ("single", "workspace")):
                with self.subTest(line=line, layout=layout):
                    kept = line + self.unmarked(layout)
                    self.assertTrue(init_command.bootstrap_of_cortex(kept))
                    self.assertEqual(init_command.written_for_workspace(kept), layout == "workspace")
                    self.assertIsNone(init_command.kept_bootstrap(kept, self.unmarked(layout)))
                    self.assertEqual(init_command.kept_bootstrap(kept, self.unmarked(other)).sentence,
                                     f"was written for {'a workspace' if layout == 'workspace' else 'a single project'}")
                    # Against a version that writes the marker: an older bootstrap, never "no Cortex bootstrap".
                    self.assertEqual(init_command.kept_bootstrap(kept, self.template(layout, 2)).sentence,
                                     "is an older Cortex bootstrap, written before the bootstrap marker")
        self.assertIsNone(init_command.title_line(b"<!-- our notes -->\nRead cortex.local.toml first.\n"))

    def test_the_title_below_a_line_of_text_is_none(self):
        # Below blank lines and comments, the heading is the title; below a line of text — a team's notes quoting
        # an example, in a fence or not — it is none, and the file is no Cortex bootstrap.
        workspace = self.unmarked().decode("utf-8")
        for above in ("\n\n", "<!-- ours -->\n\n<!-- and\nours -->\n", "<!-- a --><!-- b -->\n"):
            with self.subTest(above=above):
                kept = (above + workspace).encode("utf-8")
                self.assertEqual(init_command.title_line(kept), b"# Cortex AI Team \xe2\x80\x94 Workspace Mode")
                self.assertTrue(init_command.written_for_workspace(kept))
        for above in ("# Our notes\n\n```\n", "Our notes\n", "<!-- ours --> and text\n", "<!-- never closed\n"):
            with self.subTest(above=above):
                kept = (above + workspace).encode("utf-8")
                self.assertIsNone(init_command.title_line(kept))
                self.assertFalse(init_command.bootstrap_of_cortex(kept))
                self.assertFalse(init_command.written_for_workspace(kept))
                self.assertIs(init_command.kept_bootstrap(kept, self.template(format=2)), init_command.NO_BOOTSTRAP)

    def test_one_marker_says_the_layout_against_the_title(self):
        # A single marker is the layout, whatever the title says — a team that retitled the file, or a title of the
        # other layout's: the title is read only where the markers say none, or disagree.
        for layout, title in (("workspace", "# Cortex AI Team"), ("single", "# Cortex AI Team — Workspace Mode")):
            with self.subTest(layout=layout):
                kept = (f"{title}\n\n<!-- cortex:bootstrap layout={layout} format=2 -->\n\nRead cortex.local.toml "
                        "first.\n").encode("utf-8")
                self.assertEqual(init_command.written_for_workspace(kept), layout == "workspace")
                self.assertIsNone(init_command.kept_bootstrap(kept, self.template(layout, 2)))

    def test_the_marker_says_the_layout_whatever_the_title(self):
        retitled = self.template().replace("Workspace Mode".encode(), "Workspace Mode (Acme)".encode())
        self.assertIsNone(init_command.kept_bootstrap(retitled, self.template()))
        self.assertEqual(init_command.kept_bootstrap(self.template("single"), self.template()).sentence,
                         "was written for a single project")
        self.assertEqual(init_command.kept_bootstrap(self.template(), self.template("single")).sentence,
                         "was written for a workspace")

    def test_the_layout_is_said_before_the_format(self):
        # The order of ADR-010 §3.1: the layout (3), then the format (4).
        self.assertEqual(init_command.kept_bootstrap(self.template("single", 1), self.template("workspace", 2)).sentence,
                         "was written for a single project")

    def test_an_older_format_is_said_with_force_and_a_later_one_without(self):
        older = init_command.kept_bootstrap(self.template(format=1), self.template(format=2))
        self.assertEqual(older.sentence, "is an older Cortex bootstrap: format 1, where this version writes format 2")
        self.assertTrue(older.force)
        # --force would write this version's older template over it.
        later = init_command.kept_bootstrap(self.template(format=3), self.template(format=2))
        self.assertEqual(later.sentence, "was written by a later Cortex: format 3, where this version writes format 2")
        self.assertFalse(later.force)

    def test_the_team_s_part_follows_the_first_end_marker_after_the_marker(self):
        end = b"<!-- cortex:bootstrap end -->"
        block = self.template(format=2) + end + b"\n"
        cases = (
            (block + b"ours\n", (block, b"ours\n")),
            (block + b"a\n" + end + b"\nb\n", (block, b"a\n" + end + b"\nb\n")),      # a second one is the team's text
            (block.replace(b"\n", b"\r\n") + b"ours\r\n", (block.replace(b"\n", b"\r\n"), b"ours\r\n")),
            (block[:-1], (block[:-1], b"")),                                       # the last line, no line break
            (end + b"\n" + block + b"ours\n", (end + b"\n" + block, b"ours\n")),  # one above the marker ends nothing
            (self.template(format=2) + b"ours\n", (self.template(format=2) + b"ours\n", b"")),   # no end marker
            (b"# Ours\n" + end + b"\nmore\n", (b"# Ours\n" + end + b"\nmore\n", b"")),     # no marker at all
        )
        for content, expected in cases:
            with self.subTest(content=content):
                self.assertEqual(init_command.parts(content), expected)

    def test_a_template_without_an_end_marker_is_written_whole(self):
        # A version before format 2, after a move down: the team's part would join Cortex's block, and the
        # file would never follow Cortex again — it stays in the .bak.
        kept = self.template(format=2) + b"<!-- cortex:bootstrap end -->\nours\n"
        written = init_command.stamped(self.template(format=1))
        self.assertEqual(init_command.with_team_part(written, kept), written)

    def test_cortex_s_block_holds_no_table_a_team_fills(self):
        # A workspace fills its table of services: in Cortex's block, it would keep the file from following Cortex.
        for name in ("bootstrap-instructions.md", "bootstrap-instructions-workspace.md"):
            with self.subTest(name):
                block = init_command.parts((harness.REPO / "templates" / name).read_bytes())[0]
                self.assertIsNotNone(init_command.END_MARKER.search(block))
                self.assertNotRegex(block, rb"(?m)^\|")

    def test_a_marker_in_the_team_s_part_is_its_text(self):
        block = self.template(format=2) + b"<!-- cortex:bootstrap end -->\n"
        kept = block + b"<!-- cortex:bootstrap layout=single format=1 -->\n"
        self.assertEqual(init_command.markers(kept), [("workspace", 2)])
        self.assertIsNone(init_command.kept_bootstrap(kept, self.template(format=2)))

    def test_two_markers_are_none(self):
        twice = self.template() + b"<!-- cortex:bootstrap layout=single format=1 -->\n"
        self.assertEqual(init_command.kept_bootstrap(twice, self.template()).sentence,
                         "holds several bootstrap markers, so none is read")

    def test_only_the_whole_line_is_a_marker(self):
        for line in (b"<!-- cortex:bootstrap layout=workspace format=01 -->", b"<!-- cortex:bootstrap layout=both format=1 -->",
                     b"  <!-- cortex:bootstrap layout=workspace format=1 -->", b"<!-- cortex:bootstrap layout=workspace format=1 --> x",
                     b"<!--cortex:bootstrap layout=workspace format=1-->"):
            with self.subTest(line=line):
                self.assertEqual(init_command.markers(b"# Cortex AI Team\n\n" + line + b"\n"), [])
        self.assertEqual(init_command.markers(b"# T\r\n<!-- cortex:bootstrap layout=single format=12 -->\r\n"), [("single", 12)])

    def test_the_marker_is_read_anywhere_in_the_file(self):
        # A Cursor rule opens with its front matter, not with the title.
        front_matter = b"---\ndescription: Cortex\nalwaysApply: true\n---\n" + self.template()
        self.assertEqual(init_command.markers(front_matter), [("workspace", 1)])
        self.assertIsNone(init_command.kept_bootstrap(front_matter, self.template()))
        notes_first = b"Our notes come first.\n\n" + self.template().split(b"\n", 1)[1]
        self.assertIsNone(init_command.kept_bootstrap(notes_first, self.template()))
        last_line = b"Our notes.\n\n<!-- cortex:bootstrap layout=single format=1 -->"
        self.assertEqual(init_command.markers(last_line), [("single", 1)])

    def test_a_cursor_rule_opens_on_the_frontmatter_that_applies_it_always(self):
        # Without `alwaysApply: true`, Cursor does not apply a rule in every conversation (#306).
        text = init_command.instructions(self.template("single"), True)
        rule = init_command.stamped(init_command.for_tool(Path(".cursor/rules/cortex.mdc"), text))
        self.assertTrue(rule.startswith(init_command.CURSOR_RULE + b"# Cortex AI Team\n"))
        self.assertTrue(init_command.applies_always(rule))
        self.assertTrue(init_command.untouched(rule))                             # the digest covers the frontmatter
        self.assertFalse(init_command.untouched(rule.replace(b"alwaysApply: true", b"alwaysApply: false")))
        for name in ("CLAUDE.md", "AGENTS.md", ".github/copilot-instructions.md"):
            self.assertEqual(init_command.for_tool(Path(name), text), text)
        # Read through it: a Cortex bootstrap, of the layout its title says when the templates carry no marker.
        before = b"# Cortex AI Team\n\nRead cortex.local.toml first.\n"
        self.assertTrue(init_command.bootstrap_of_cortex(init_command.CURSOR_RULE + before))
        self.assertIsNone(init_command.kept_bootstrap(init_command.CURSOR_RULE + before, before))
        for kept in (before, b"---\ndescription: Cortex\n---\n" + before, b"---\nalwaysApply: false\n---\n" + before):
            self.assertFalse(init_command.applies_always(kept))

    def test_the_frontmatter_is_the_first_block_and_only_it(self):
        body = b"# Cortex AI Team\n\n---\n\nalwaysApply: true\n\n---\n"     # a rule, and a body that quotes one
        kept = b"---\ndescription: ours\n---\n" + body
        self.assertEqual(init_command.without_frontmatter(kept), body)
        self.assertFalse(init_command.applies_always(kept))

    def test_always_apply_is_read_as_cursor_documents_it(self):
        for line, always in ((b"alwaysApply: true", True), (b"alwaysApply: true ", True), (b"alwaysApply: True", False),
                             (b"alwaysApply: true  # every conversation", False), (b"alwaysApply: false", False),
                             (b"alwaysApply: trueish", False), (b"description: alwaysApply: true", False),
                             (b"alwaysApply:true", False)):
            with self.subTest(line=line):
                rule = b"---\r\n" + line + b"\r\n---\r\n# Cortex AI Team\r\n"
                self.assertIs(init_command.applies_always(rule), always)

    def test_a_frontmatter_left_open_is_none_and_read_at_once(self):
        # CRLF lines, as Git for Windows checks a file out, opening on `---` and never closing it: read in a
        # process of its own, so that a match that runs for ever fails the test instead of hanging the suite.
        probe = ("import sys; sys.path[:0] = sys.argv[1:]; from cortex_cli import init; "
                 "kept = b'---\\r\\n' + b'line\\r\\n' * 40; "
                 "assert init.without_frontmatter(kept) == kept and not init.applies_always(kept)")
        here = Path(__file__).resolve()
        proc = subprocess.run([sys.executable, "-c", probe, str(here.parents[1]), str(here.parents[2] / "core")],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_a_byte_order_mark_hides_nothing(self):
        bom = b"\xef\xbb\xbf"
        self.assertEqual(init_command.markers(bom + b"<!-- cortex:bootstrap layout=single format=1 -->\n"),
                         [("single", 1)])
        self.assertIsNone(init_command.kept_bootstrap(bom + self.template(), self.template()))
        before = b"# Cortex AI Team\n\nRead cortex.local.toml first.\n"
        self.assertTrue(init_command.bootstrap_of_cortex(bom + before))
        self.assertEqual(init_command.kept_bootstrap(bom + before, self.template("single")).sentence,
                         "is an older Cortex bootstrap, written before the bootstrap marker")
        # A version whose templates carry no marker reads the layout from the title.
        self.assertIsNone(init_command.kept_bootstrap(bom + before, before))

    def test_each_template_carries_one_marker_on_line_3_and_the_comment_under_it(self):
        templates = Path(__file__).resolve().parents[2] / "templates"
        for name, layout in (("bootstrap-instructions.md", "single"), ("bootstrap-instructions-workspace.md", "workspace")):
            with self.subTest(template=name):
                content = (templates / name).read_bytes()
                lines = content.split(b"\n")
                self.assertEqual([found for found, _ in init_command.markers(content)], [layout])
                self.assertTrue(init_command.BOOTSTRAP_MARKER.fullmatch(lines[2]), lines[2])
                self.assertTrue(lines[3].startswith(b"<!-- Keep the line above as it is"), lines[3])

    def test_a_file_without_the_marker(self):
        before = b"# Cortex AI Team\n\nRead cortex.local.toml first.\n"
        self.assertEqual(init_command.kept_bootstrap(before, self.template()).sentence,
                         "is an older Cortex bootstrap, written before the bootstrap marker")
        self.assertEqual(init_command.kept_bootstrap(b"# Our notes\n", self.template()).sentence,
                         "holds no Cortex bootstrap: the tool will not find Cortex")

    def test_a_workspace_is_known_by_every_marker_or_else_by_the_title(self):
        # #329, and the three mutants Bluspark's second counter-review of 1.7.0 left (WS2 to WS4).
        workspace, single = self.template(), self.template("single")
        self.assertTrue(init_command.written_for_workspace(workspace))
        self.assertFalse(init_command.written_for_workspace(single))
        two = workspace + workspace.split(b"\n\n", 1)[1]
        self.assertTrue(init_command.written_for_workspace(two))                  # several markers, all a workspace's
        mixed = workspace + single.split(b"\n\n", 1)[1]
        self.assertEqual(init_command.markers(mixed), [("workspace", 1), ("single", 1)])
        self.assertTrue(init_command.written_for_workspace(mixed))                # WS2: markers that disagree,
        mixed = single + workspace.split(b"\n\n", 1)[1]                         # the title says it
        self.assertEqual(init_command.markers(mixed), [("single", 1), ("workspace", 1)])
        self.assertFalse(init_command.written_for_workspace(mixed))
        old = "# Cortex AI Team — Workspace Mode\n\nRead cortex.local.toml first.\n".encode("utf-8")
        self.assertTrue(init_command.written_for_workspace(old))
        self.assertTrue(init_command.written_for_workspace(b"\xef\xbb\xbf" + old.replace(b"\n", b"\r\n")))
        said = b"# Cortex AI Team\n\nNot the Workspace Mode: read cortex.local.toml first.\n"
        self.assertFalse(init_command.written_for_workspace(said))                # WS3: the title, not the text
        rule = b"---\nalwaysApply: true\n---\n" + old
        self.assertTrue(init_command.written_for_workspace(rule))                 # WS4: after a frontmatter
        self.assertFalse(init_command.written_for_workspace("# Workspace Mode notes\n".encode("utf-8")))
        above = b"<!-- our notes -->\n" + old                                    # a line of the team's above the title
        self.assertTrue(init_command.written_for_workspace(above))
        self.assertTrue(init_command.written_for_workspace(b"---\nalwaysApply: true\n---\n" + above))
        self.assertFalse(init_command.written_for_workspace(b"<!-- our notes -->\n" + said))

    def test_a_version_whose_templates_carry_no_marker_keeps_its_checks(self):
        # A project pinned to 1.3: its templates have no marker, and its bootstrap is not "older".
        single = b"# Cortex AI Team\n\nRead cortex.local.toml first.\n"
        workspace = "# Cortex AI Team — Workspace Mode\n\nRead cortex.local.toml first.\n".encode("utf-8")
        self.assertIsNone(init_command.kept_bootstrap(single, single))
        self.assertEqual(init_command.kept_bootstrap(b"# Cortex AI Team\n\nRead cortex/ first.\n", single).sentence,
                         "is an older Cortex bootstrap: it does not read cortex.local.toml, which names the spec now")
        self.assertEqual(init_command.kept_bootstrap(workspace, single).sentence, "was written for a workspace")
        self.assertEqual(init_command.kept_bootstrap(single, workspace).sentence, "was written for a single project")


if __name__ == "__main__":
    unittest.main()
