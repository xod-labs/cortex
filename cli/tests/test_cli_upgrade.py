"""``cortex upgrade`` — a project moved to another version, said first (ADR-010 §3.5).

From source the command stands for Cortex 9.9.9, written to the store from this checkout, and the
fake releases 9.9.7 and 9.9.8 are served from this machine (``release_fixture``): 9.9.8's spec is
four files, so that moving between the two adds or removes most of the agents. A built binary is a
build of no release and downloads nothing: the tests that need a second version skip there.
"""

import functools
import hashlib
import io
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the harness, not a package named tests
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "core")]
import cli_harness as harness  # noqa: E402
from cortex_cli import init, self_update, store, upgrade  # noqa: E402
from cortex_cli.semver import Version  # noqa: E402
from cortex_cli.store import digest  # noqa: E402
from release_fixture import FakeRelease  # noqa: E402

OWN = harness.EXPECTED_VERSION if harness.BINARY else "9.9.9"
DOWNLOADS = unittest.skipIf(harness.BINARY, "a build of no release downloads no version")
OLD_BOOTSTRAP = b"# Cortex AI Team\n\nWritten by 9.9.8: read cortex.local.toml first.\n"
MARKED = b"# Cortex AI Team\n\n<!-- cortex:bootstrap layout=single format=1 -->\n\nWritten by 9.9.8, with the marker.\n"
PERSONAL = (b"# Cortex AI Team\n\n<!-- PERSONALITY:BEGIN -->\nBe a character.\n<!-- PERSONALITY:END -->\n\n"
            b"Written by 9.9.8: read cortex.local.toml first.\n")
OLD_WORKSPACE = b"# Cortex AI Team \xe2\x80\x94 Workspace Mode\n\nWritten by 9.9.8: read cortex.local.toml first.\n"
TEMPLATE = (harness.REPO / "templates" / "bootstrap-instructions.md").read_bytes()
END = b"<!-- cortex:bootstrap end -->"
OVERLAY = ("<!-- OVERLAY\n     Base: cortex/agents/roles/engineering/architect.md\n     Scope: workspace\n"
           "     Semantic: additive\n-->\n\n# Architect — our rules\n")


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() if p.is_file() else "dir" for p in sorted(root.rglob("*"))
            if not os.path.islink(p)}


class UpgradeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-upgrade-")).resolve()
        self.addCleanup(self.cleanup)
        self.home = self.tmp / "cortex-home"
        self.release = FakeRelease(("9.9.7", "9.9.8"))
        self.addCleanup(self.release.close)
        self.release.replace_spec("9.9.8", {"templates/bootstrap-instructions.md": OLD_BOOTSTRAP})
        self.env = harness.environment(CORTEX_HOME=str(self.home), CORTEX_RELEASES_URL=self.release.url,
                                       CORTEX_SOURCE_VERSION=OWN)

    def cleanup(self):
        for directory, subdirs, files in os.walk(self.tmp):
            for name in subdirs + files:
                path = os.path.join(directory, name)
                if not os.path.islink(path):
                    os.chmod(path, 0o700 if os.path.isdir(path) else 0o600)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def project(self, version, theme="h2g2", synced=True):
        root = self.tmp / "app"
        root.mkdir(parents=True)
        (root / "cortex.toml").write_text(f'# ours\nversion = "{version}"  # the team\'s pin\ntheme = "{theme}"\n',
                                          encoding="utf-8")
        if synced:
            self.assert_ok(self.run_cortex(root, "sync"))
        return root

    def run_cortex(self, project, *args):
        proc = harness.run(*args, cwd=project, env=self.env)
        proc.out, proc.err = proc.stdout.decode(), proc.stderr.decode()
        return proc

    def upgrade(self, project, *args):
        return self.run_cortex(project, "upgrade", *args)

    def assert_ok(self, proc):
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)

    def pinned(self, project):
        return (project / "cortex.toml").read_text(encoding="utf-8")


@DOWNLOADS
class UpgradeTests(UpgradeTestCase):
    def test_it_moves_the_project_to_this_cortex_and_says_what_changes(self):
        project = self.project("9.9.8")
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertIn(f"Cortex 9.9.8 → {OWN}: an upgrade — the release notes: {self.release.url}/tag/{OWN}", proc.out)
        self.assertRegex(proc.out, r"  roles +\d+ added")
        self.assertIn("      + engineering/architect\n", proc.out)
        self.assertIn("      ~ prompt-manager\n", proc.out)       # 9.9.8's has other words
        self.assertIn("  templates ", proc.out)
        self.assertEqual(self.pinned(project), f'# ours\nversion = "{OWN}"  # the team\'s pin\ntheme = "h2g2"\n')
        lock = (project / "cortex.lock").read_text(encoding="utf-8")
        self.assertIn(f'version = "{OWN}"', lock)
        self.assertIn(f'digest = "{digest(self.home / "versions" / OWN)}"', lock)
        self.assertIn(f"versions/{OWN}", (project / "cortex.local.toml").read_text(encoding="utf-8"))
        self.assertIn(f"Cortex {OWN} is ready. Written: cortex.toml and cortex.lock — in no git work tree", proc.out)
        self.assertEqual(proc.out.count("Project: "), 1)

    def test_to_an_older_version_is_a_downgrade(self):
        project = self.project(OWN)
        proc = self.upgrade(project, "9.9.8")
        self.assert_ok(proc)
        self.assertIn(f"Cortex {OWN} → 9.9.8: a downgrade", proc.out)
        self.assertIn("      - engineering/architect\n", proc.out)
        self.assertIn('version = "9.9.8"', self.pinned(project))
        self.assertIn('version = "9.9.8"', (project / "cortex.lock").read_text(encoding="utf-8"))

    def test_the_pinned_version_moves_nothing(self):
        project = self.project(OWN)
        before = snapshot(project)
        proc = self.upgrade(project, OWN)
        self.assert_ok(proc)
        self.assertIn(f"Cortex {OWN}: already on it — nothing to move", proc.out)
        self.assertEqual(snapshot(project), before)

    def test_a_version_newer_than_this_cortex_is_refused_with_the_self_update_command(self):
        project = self.project("9.9.8")
        proc = self.upgrade(project, "9.10.0")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f"Cortex 9.10.0 is newer than this cortex, {OWN} — update this cortex first, then run "
                      "`cortex upgrade 9.10.0` again:\n    cortex self-update 9.10.0", proc.err)
        self.assertIn('version = "9.9.8"', self.pinned(project))

    def test_a_malformed_version_exits_2(self):
        project = self.project("9.9.8")
        for version in ("1.4", "v1.4.0", "1.4.0\n"):
            with self.subTest(version=version):
                proc = self.upgrade(project, version)
                self.assertEqual(proc.returncode, 2, proc.err)
                self.assertIn("is no version", proc.err)

    def test_dry_run_changes_nothing_in_the_project(self):
        project = self.project("9.9.8")
        before = snapshot(project)
        proc = self.upgrade(project, "--dry-run")
        self.assert_ok(proc)
        self.assertIn("--dry-run: nothing of the project was changed", proc.out)
        self.assertIn("      + engineering/architect\n", proc.out)
        self.assertEqual(snapshot(project), before)

    def test_an_overlay_the_target_breaks_stops_it_unless_forced(self):
        project = self.project(OWN)
        overlay = project / "agents" / "roles" / "engineering" / "architect.md"
        overlay.parent.mkdir(parents=True)
        overlay.write_text(OVERLAY, encoding="utf-8")
        before = snapshot(project)
        proc = self.upgrade(project, "9.9.8")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("BASE_NOT_FOUND", proc.out)
        self.assertIn("the overlays would fail against Cortex 9.9.8 — nothing was changed", proc.err)
        self.assertEqual(snapshot(project), before)
        self.assertEqual(self.upgrade(project, "9.9.8", "--dry-run").returncode, 1)
        self.assertEqual(self.upgrade(project, "9.9.8", "--dry-run", "--force").returncode, 0)   # as the real run
        self.assertEqual(snapshot(project), before)
        proc = self.upgrade(project, "9.9.8", "--force")
        self.assert_ok(proc)
        self.assertIn('version = "9.9.8"', self.pinned(project))

    def test_the_ending_says_what_to_commit_in_a_repository_and_outside_one_that_nothing_is(self):
        # At a root that is no work tree — a 2.B workspace — cortex init says the files are committed nowhere,
        # and cortex upgrade said to commit them. A .git above the root makes it a work tree, git or not here.
        project = self.project("9.9.8")
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertIn(f"\nCortex {OWN} is ready. Written: cortex.toml and cortex.lock — in no git work tree, so "
                      "committed nowhere: each developer moves their own (ADR-008 §9).\n", proc.out)
        self.assertNotIn("commit it", proc.out)                    # nor sync's line on the lock
        self.assertNotIn("teammates", proc.out.lower())
        shutil.rmtree(project)
        (self.tmp / ".git").mkdir()
        project = self.project("9.9.8")
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertIn(f"\nCortex {OWN} is ready. Commit cortex.toml, cortex.lock; teammates run `cortex sync` after "
                      "they pull.\n", proc.out)

    @unittest.skipIf(os.name == "nt", "a link to a file needs a privilege on Windows")
    def test_outside_a_repository_a_bootstrap_linked_into_one_is_to_commit_where_it_is(self):
        # A 2.B root whose CLAUDE.md links to the team's repository: that file is to commit there.
        project = self.project("9.9.8")
        team = self.tmp / "team"
        (team / ".git").mkdir(parents=True)
        (team / "CLAUDE.md").write_bytes(init.instructions(OLD_BOOTSTRAP, True))
        (project / "CLAUDE.md").symlink_to(team / "CLAUDE.md")
        proc = self.upgrade(project)
        self.assert_ok(proc)
        where = os.path.realpath(team / "CLAUDE.md").replace(os.sep, "/")
        self.assertIn(f"\nCortex {OWN} is ready. Written: cortex.toml and cortex.lock — in no git work tree, so committed "
                      f"nowhere: each developer moves their own (ADR-008 §9); {where} — outside its git work tree: "
                      "commit it where it is.\n", proc.out)

    @unittest.skipIf(os.name == "nt", "a link to a file needs a privilege on Windows")
    def test_outside_a_repository_with_only_a_file_linked_into_one_written_nothing_is_said_committed_nowhere(self):
        # On the version pinned, the bootstrap alone is written again — and it is the team's, to commit where it is:
        # no list of files committed nowhere, empty, is said.
        project = self.project(OWN)
        team = self.tmp / "team"
        (team / ".git").mkdir(parents=True)
        older = TEMPLATE.replace(b"\n\n", b"\n\nAn older wording.\n\n", 1)
        self.assertNotEqual(older, TEMPLATE)
        (team / "CLAUDE.md").write_bytes(init.stamped(init.instructions(older, True)))
        (project / "CLAUDE.md").symlink_to(team / "CLAUDE.md")
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((team / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(TEMPLATE, True)))
        where = os.path.realpath(team / "CLAUDE.md").replace(os.sep, "/")
        self.assertTrue(proc.out.endswith(f"\n{where} — outside its git work tree: commit it where it is.\n"), proc.out)
        self.assertNotIn("committed nowhere", proc.out)

    def test_a_bootstrap_nobody_changed_is_written_again(self):
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.instructions(OLD_BOOTSTRAP, True))
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertIn(f"CLAUDE.md: written again from Cortex {OWN}'s template — no one changed it", proc.out)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(TEMPLATE, True)))
        self.assertIn("Written: cortex.toml, cortex.lock and CLAUDE.md — in no git work tree", proc.out)

    def test_a_bootstrap_checked_out_with_crlf_is_still_nobodys_change(self):
        # git's core.autocrlf, on Windows.
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.instructions(OLD_BOOTSTRAP, True).replace(b"\n", b"\r\n"))
        self.assert_ok(self.upgrade(project))
        self.assertEqual((project / "CLAUDE.md").read_bytes(),
                         init.stamped(init.instructions(TEMPLATE, True)).replace(b"\n", b"\r\n"))

    def test_a_dry_run_says_the_bootstrap_too_when_the_overlays_stop_it(self):
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.instructions(OLD_BOOTSTRAP, True))
        (project / "agents" / "roles" / "engineering").mkdir(parents=True)
        (project / "agents" / "roles" / "engineering" / "architect.md").write_text(
            OVERLAY.replace("architect.md", "nowhere.md"), encoding="utf-8")
        before = snapshot(project)
        proc = self.upgrade(project, "--dry-run")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f"CLAUDE.md: would be written again from Cortex {OWN}'s template", proc.out)
        self.assertIn("--dry-run: nothing of the project was changed", proc.out)
        self.assertEqual(snapshot(project), before)

    def test_a_bootstrap_a_team_changed_is_kept(self):
        project = self.project("9.9.8")
        ours = OLD_BOOTSTRAP + b"\n## Our own rules\n"
        (project / "CLAUDE.md").write_bytes(ours)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), ours)
        self.assertIn("CLAUDE.md is a bootstrap without the marker, not what Cortex 9.9.8 writes — you changed it, or "
                      "an older Cortex wrote it — so it is kept, and the target's template is not written: cortex init "
                      "--force writes it again, and keeps yours as .bak", proc.err)

    def test_a_layer_readme_changed_is_said_under_its_layer(self):
        self.release.replace_spec("9.9.8", {"agents/roles/README.md": b"# Roles, as 9.9.8 had them\n"})
        project = self.project("9.9.8")
        proc = self.upgrade(project, "--dry-run")
        self.assert_ok(proc)
        roles = proc.out[proc.out.index("  roles "):proc.out.index("  capabilities ")]
        self.assertIn("      ~ README.md\n", roles)
        self.assertNotIn("      + README.md\n", roles)

    def test_a_dry_run_says_the_sync_would_refuse_as_the_run_does(self):
        project = self.project("9.9.8")
        (project / "cortex").mkdir()
        (project / "cortex" / "notes.md").write_text("ours\n", encoding="utf-8")    # sync refuses a cortex/ of others
        before = snapshot(project)
        proc = self.upgrade(project, "--dry-run")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("cortex upgrade: the sync would refuse it — cortex/ is ", proc.err)
        self.assertIn("--dry-run: nothing of the project was changed", proc.out)
        proc = self.upgrade(project)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("cortex upgrade: cortex/ is ", proc.err)
        self.assertEqual(snapshot(project), before)

    def test_a_sync_that_fails_leaves_the_pin_as_it_was(self):
        project = self.project("9.9.8")
        (project / "cortex").mkdir()
        (project / "cortex" / "notes.md").write_text("ours\n", encoding="utf-8")    # sync refuses a cortex/ of others
        before = self.pinned(project)
        proc = self.upgrade(project)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertEqual(self.pinned(project), before)
        self.assertIn('version = "9.9.8"', (project / "cortex.lock").read_text(encoding="utf-8"))


@DOWNLOADS
class DigestTests(UpgradeTestCase):
    """A bootstrap nobody changed is known by the digest in its marker, and written again whenever
    the target writes another file — whatever the formats, either way (ADR-010 §3.5, step 5)."""

    def marked(self, version="9.9.8", template=MARKED, name="bootstrap-instructions.md"):
        self.release.replace_spec(version, {f"templates/{name}": template})
        return init.stamped(init.instructions(template, True))

    def test_an_untouched_bootstrap_of_the_same_format_is_written_again(self):
        written = self.marked()
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(written)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(TEMPLATE, True)))
        self.assertIn("Written: cortex.toml, cortex.lock and CLAUDE.md — in no git work tree", proc.out)

    def test_an_untouched_bootstrap_the_target_writes_alike_is_left_as_it_is(self):
        written = self.marked(template=TEMPLATE)
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(written)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), written)
        self.assertNotIn("CLAUDE.md", proc.out + proc.err)

    def cursor_rule(self, project, content):
        rule = project / ".cursor" / "rules" / "cortex.mdc"
        rule.parent.mkdir(parents=True)
        rule.write_bytes(content)
        return rule

    def test_a_cursor_rule_is_written_again_with_the_frontmatter_that_applies_it(self):
        # Written before 1.6.0 with no frontmatter: read, by inference from Cursor's documentation, only where a
        # chat mentions it (#306; #137 is to see it in a real conversation).
        written = self.marked(template=TEMPLATE)        # the target writes the same text: the frontmatter alone is new
        project = self.project("9.9.8")
        rule = self.cursor_rule(project, written)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        wanted = init.stamped(init.CURSOR_RULE + init.instructions(TEMPLATE, True))
        self.assertEqual(rule.read_bytes(), wanted)
        self.assertIn("Written: cortex.toml, cortex.lock and .cursor/rules/cortex.mdc — in no git work tree", proc.out)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual(rule.read_bytes(), wanted)
        self.assertNotIn("cortex.mdc", proc.out + proc.err)

    def test_a_cursor_rule_without_the_marker_is_known_with_its_frontmatter_or_without(self):
        # 9.9.8's template has no marker, so no digest: the rule is known by being 9.9.8's file — as an
        # earlier cortex wrote it, or as this one does, the frontmatter first.
        text = init.instructions(OLD_BOOTSTRAP, True)
        for kept in (text, init.CURSOR_RULE + text):
            with self.subTest(frontmatter=kept != text):
                project = self.project("9.9.8")
                rule = self.cursor_rule(project, kept)
                self.assert_ok(self.upgrade(project))
                self.assertEqual(rule.read_bytes(), init.stamped(init.CURSOR_RULE + init.instructions(TEMPLATE, True)))
                shutil.rmtree(project)

    def test_a_cursor_rule_a_team_changed_is_kept_and_said_to_apply_only_where_mentioned(self):
        ours = self.marked(template=TEMPLATE).replace(END, b"## Our own rules\n\n" + END)   # above it: Cortex's block
        project = self.project("9.9.8")
        rule = self.cursor_rule(project, ours)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual(rule.read_bytes(), ours)
        self.assertIn(".cursor/rules/cortex.mdc is not what Cortex wrote — you changed it — and it has no `alwaysApply: "
                      "true` line in a frontmatter, as Cursor's documentation writes it: Cursor may not apply it in every "
                      "conversation. It is kept: cortex init --force writes it again, and keeps yours as .bak — your own rules go "
                      "below its end marker, where Cortex keeps them\n", proc.err)

    def test_a_cursor_rule_a_team_changed_says_both_when_the_target_writes_another(self):
        ours = self.marked() + b"\n## Our own rules\n"          # 9.9.8's template is not the target's
        project = self.project("9.9.8")
        rule = self.cursor_rule(project, ours)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual(rule.read_bytes(), ours)
        self.assertIn(".cursor/rules/cortex.mdc is not what Cortex wrote, by the digest in its marker — you changed it "
                      "— so it is kept, and the target's template is not written; and with no `alwaysApply: true` line in a "
                      "frontmatter, as Cursor's documentation writes it, Cursor may not apply it in every conversation: "
                      "cortex init --force writes it again, and keeps yours as .bak — your own rules go below its end marker, where "
                      "Cortex keeps them\n", proc.err)

    def test_a_cursor_rule_a_team_changed_that_applies_always_is_kept_unsaid(self):
        ours = init.stamped(init.CURSOR_RULE + init.instructions(TEMPLATE, True)).replace(END, b"## Our own rules\n\n" + END)
        self.marked(template=TEMPLATE)                          # the target writes the same text
        project = self.project("9.9.8")
        rule = self.cursor_rule(project, ours)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual(rule.read_bytes(), ours)
        self.assertNotIn("cortex.mdc", proc.out + proc.err)

    def test_a_downgrade_writes_an_untouched_bootstrap_again(self):
        self.marked()
        project = self.project(OWN)
        (project / "CLAUDE.md").write_bytes(init.stamped(init.instructions(TEMPLATE, True)))
        proc = self.upgrade(project, "9.9.8")
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(MARKED, True)))

    def test_a_downgrade_to_format_1_keeps_a_file_with_a_team_s_part(self):
        # A template of format 1 has no end marker: written, the team's part would end up inside Cortex's block.
        self.marked()
        project = self.project(OWN)
        kept = init.stamped(init.instructions(TEMPLATE, True)) + b"## Our own rules\n"
        (project / "CLAUDE.md").write_bytes(kept)
        proc = self.upgrade(project, "9.9.8")
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), kept)
        self.assertIn("CLAUDE.md has a part of its own below its end marker, and the target's template has no end marker "
                      "to keep it below — so it is kept as it is\n", proc.err)

    def test_a_downgrade_keeps_a_team_s_part_of_spaces(self):
        # Spaces are text: written after a block of format 1, they would make a team's change of it.
        self.marked()
        project = self.project(OWN)
        kept = init.stamped(init.instructions(TEMPLATE, True)) + b"   \n"
        (project / "CLAUDE.md").write_bytes(kept)
        self.assert_ok(self.upgrade(project, "9.9.8"))
        self.assertEqual((project / "CLAUDE.md").read_bytes(), kept)

    def test_a_downgrade_writes_a_file_whose_part_below_the_end_marker_is_blank(self):
        self.marked()
        project = self.project(OWN)
        for blank in (b"\n\n", b"\r\n\r\n", b"\r\r\n"):
            with self.subTest(blank=blank):
                (project / "CLAUDE.md").write_bytes(init.stamped(init.instructions(TEMPLATE, True)) + blank)
                self.assert_ok(self.upgrade(project, "9.9.8"))
                self.assertEqual((project / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(MARKED, True)) + blank)
                self.assert_ok(self.upgrade(project, OWN))                  # back up, for the next case

    def test_line_endings_the_digest_reads_as_one_write_a_file_with_no_team_s_part(self):
        # Nothing to lose: CR CR LF in a file of format 1 nobody changed, or of format 2 with no team's part.
        older = TEMPLATE.replace(b"## Where the spec is", b"## Where the spec is, in 9.9.8", 1)
        for template in (MARKED, older):
            with self.subTest(template=template[:40]):
                kept = self.marked(template=template).replace(b"\n", b"\r\r\n")
                shutil.rmtree(self.tmp / "app", ignore_errors=True)       # each case its own project
                project = self.project("9.9.8")
                (project / "CLAUDE.md").write_bytes(kept)
                proc = self.upgrade(project)
                self.assert_ok(proc)
                self.assertEqual((project / "CLAUDE.md").read_bytes(),
                                 init.stamped(init.instructions(TEMPLATE, True)).replace(b"\n", b"\r\n"))
                self.assertNotIn("line endings", proc.err)

    def test_without_a_digest_a_block_of_the_pinned_version_is_read_whatever_the_team_s_part(self):
        older = TEMPLATE.replace(b"## Where the spec is", b"## Where the spec is, in 9.9.8", 1)
        self.marked(template=older)
        team = b"## Our own rules\n"
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.instructions(older, True) + team)     # its marker, with no digest
        self.assert_ok(self.upgrade(project))
        self.assertEqual((project / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(TEMPLATE, True)) + team)

    def test_any_run_of_crs_before_a_line_feed_is_one_line_break_to_the_digest_and_the_split(self):
        # CR CR LF, as a Windows script that writes text over CRLF leaves it, or more CRs: read as one line break
        # by the digest and by the split alike, so the block is written again and the team's part stays.
        older = TEMPLATE.replace(b"## Where the spec is", b"## Where the spec is, in 9.9.8", 1)
        for crs in (b"\r\r", b"\r\r\r", b"\r\r\r\r"):
            with self.subTest(crs=crs):
                shutil.rmtree(self.tmp / "app", ignore_errors=True)       # each case its own project
                team = b"## Our own rules" + crs + b"\n"
                written = self.marked(template=older).replace(b"\n", crs + b"\n")
                project = self.project("9.9.8")
                (project / "CLAUDE.md").write_bytes(written + team)
                proc = self.upgrade(project)
                self.assert_ok(proc)
                self.assertEqual((project / "CLAUDE.md").read_bytes(),
                                 init.stamped(init.instructions(TEMPLATE, True)).replace(b"\n", b"\r\n") + team)

    def test_crs_the_target_writes_alike_leave_the_file(self):
        written = self.marked(template=TEMPLATE)
        project = self.project("9.9.8")
        kept = written.replace(b"\n", b"\r\r\n") + b"## Our own rules\r\r\n"
        (project / "CLAUDE.md").write_bytes(kept)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), kept)
        self.assertNotIn("CLAUDE.md", proc.out + proc.err)

    def test_crs_without_a_digest_read_the_pinned_version_s_block(self):
        older = TEMPLATE.replace(b"## Where the spec is", b"## Where the spec is, in 9.9.8", 1)
        self.marked(template=older)
        team = b"## Our own rules\r\r\n"
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.instructions(older, True).replace(b"\n", b"\r\r\n") + team)
        self.assert_ok(self.upgrade(project))
        self.assertEqual((project / "CLAUDE.md").read_bytes(),
                         init.stamped(init.instructions(TEMPLATE, True)).replace(b"\n", b"\r\n") + team)

    def test_crs_on_the_marker_line_hide_no_layout(self):
        # The marker of a workspace read with its line break, however many CRs: written again in its own layout.
        workspace = (harness.REPO / "templates" / "bootstrap-instructions-workspace.md").read_bytes()
        older = workspace.replace(b"## Where the spec is", b"## Where the spec is, in 9.9.8", 1)
        written = self.marked(template=older, name="bootstrap-instructions-workspace.md")
        marker = written.split(b"\n")[2]
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(written.replace(marker + b"\n", marker + b"\r\r\r\n"))
        self.assert_ok(self.upgrade(project))
        self.assertEqual(init.markers((project / "CLAUDE.md").read_bytes()), [("workspace", 3)])

    def test_one_a_team_changed_is_kept_and_said_when_the_target_writes_another(self):
        changed = self.marked() + b"\n## Our own rules\n"
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(changed)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), changed)
        self.assertIn("CLAUDE.md is not what Cortex wrote, by the digest in its marker — you changed it — so it is "
                      "kept, and the target's template is not written: cortex init --force writes it again", proc.err)

    def test_one_a_team_changed_is_not_said_when_the_target_writes_the_same(self):
        changed = self.marked(template=TEMPLATE).replace(END, b"## Our own rules\n\n" + END)   # above it: Cortex's block
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(changed)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), changed)
        self.assertNotIn("CLAUDE.md", proc.err)

    def test_crlf_and_a_byte_order_mark_are_nobodys_change_and_kept(self):
        written = self.marked()
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(b"\xef\xbb\xbf" + written.replace(b"\n", b"\r\n"))
        self.assert_ok(self.upgrade(project))
        self.assertEqual((project / "CLAUDE.md").read_bytes(),
                         b"\xef\xbb\xbf" + init.stamped(init.instructions(TEMPLATE, True)).replace(b"\n", b"\r\n"))

    def test_the_team_s_part_follows_the_target_s_block_byte_for_byte(self):
        # ADR-012 §3.4: Cortex's block as Cortex wrote it is written again; what is below the end marker stays.
        older = TEMPLATE.replace(b"## Where the spec is", b"## Where the spec is, in 9.9.8", 1)
        written = self.marked(template=older)
        team = b"## Our own rules\r\n\r\nRun the linter first.\r\n<!-- cortex:bootstrap end -->\r\nStill ours."
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.BOM + written.replace(b"\n", b"\r\n") + team)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(),
                         init.BOM + init.stamped(init.instructions(TEMPLATE, True)).replace(b"\n", b"\r\n") + team)
        self.assertIn("Written: cortex.toml, cortex.lock and CLAUDE.md — in no git work tree", proc.out)

    def test_a_team_s_part_under_a_block_the_target_writes_alike_is_left_as_it_is(self):
        written = self.marked(template=TEMPLATE) + b"## Our own rules\n"
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(written)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), written)
        self.assertNotIn("CLAUDE.md", proc.out + proc.err)

    def test_the_block_keeps_its_own_line_endings_whatever_the_team_s_part_has(self):
        older = TEMPLATE.replace(b"## Where the spec is", b"## Where the spec is, in 9.9.8", 1)
        team = b"## Our own rules\r\nWritten on Windows.\r\n"
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(self.marked(template=older) + team)
        self.assert_ok(self.upgrade(project))
        self.assertEqual((project / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(TEMPLATE, True)) + team)

    def test_a_dry_run_says_the_team_s_part_stays_and_writes_nothing(self):
        older = TEMPLATE.replace(b"## Where the spec is", b"## Where the spec is, in 9.9.8", 1)
        kept = self.marked(template=older) + b"## Our own rules\n"
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(kept)
        proc = self.upgrade(project, "--dry-run")
        self.assert_ok(proc)
        self.assertIn(f"CLAUDE.md: would be written again from Cortex {OWN}'s template — no one changed Cortex's block, "
                      "and what is below its end marker stays as it is\n", proc.out)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), kept)

    def test_a_file_written_again_with_a_team_s_part_says_its_part_stays(self):
        older = TEMPLATE.replace(b"## Where the spec is", b"## Where the spec is, in 9.9.8", 1)
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(self.marked(template=older) + b"## Our own rules\n")
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertIn(f"CLAUDE.md: written again from Cortex {OWN}'s template — no one changed Cortex's block, and what "
                      "is below its end marker stays as it is\n", proc.out)

    def test_already_on_it_a_block_is_written_again_and_the_team_s_part_stays(self):
        # On the pinned version (an upgrade cut short, a pin moved by hand): the same split, the same words.
        older = TEMPLATE.replace(b"## Where the spec is", b"## Where the spec is, in 9.9.8", 1)
        team = b"## Our own rules\n"
        kept = self.marked(template=older) + team
        project = self.project(OWN)
        (project / "CLAUDE.md").write_bytes(kept)
        words = "template — no one changed Cortex's block, and what is below its end marker stays as it is\n"
        proc = self.upgrade(project, "--dry-run")
        self.assert_ok(proc)
        self.assertIn(f"CLAUDE.md: would be written again from Cortex {OWN}'s {words}", proc.out)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), kept)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertIn(f"CLAUDE.md: written again from Cortex {OWN}'s {words}", proc.out)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(TEMPLATE, True)) + team)

    def test_a_block_a_team_changed_is_kept_and_said_where_its_rules_go(self):
        older = TEMPLATE.replace(b"## Where the spec is", b"## Where the spec is, in 9.9.8", 1)
        changed = self.marked(template=older).replace(END, b"## Our own rules\n\n" + END)
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(changed)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), changed)
        self.assertIn("CLAUDE.md is not what Cortex wrote, by the digest in its marker — you changed it — so it is kept, "
                      "and the target's template is not written: cortex init --force writes it again, and keeps yours as "
                      ".bak — your own rules go below its end marker, where Cortex keeps them\n", proc.err)

    def test_already_on_it_a_bootstrap_without_the_marker_of_another_version_is_said(self):
        # #313: on the pinned version, a file of an older Cortex, or one a team changed, is said, not passed over.
        older = b"# Cortex AI Team\n\nWritten by an older Cortex: read cortex.local.toml first.\n"
        project = self.project(OWN)
        (project / "CLAUDE.md").write_bytes(older)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), older)
        self.assertIn(f"CLAUDE.md is a bootstrap without the marker, not what Cortex {OWN} writes — you changed it, "
                      "or an older Cortex wrote it — so it is kept: cortex init --force writes it again, and keeps yours "
                      "as .bak — your own rules go below its end marker, where Cortex keeps them\n", proc.err)

    def test_a_target_without_an_end_marker_is_not_said_to_keep_rules_below_it(self):
        # ADR-012 §3.4: below the end marker of the file `cortex init --force` writes — the target's, and a
        # target before format 2 has none: up, on the pinned version (#313), and down.
        older = MARKED.replace(b"Written by 9.9.8", b"Written by 9.9.7")
        self.marked(version="9.9.7", template=older)
        self.marked()
        newer = init.stamped(init.instructions(TEMPLATE, True)).replace(END, b"## Our own rules\n\n" + END)
        kept = " — so it is kept, and the target's template is not written: cortex init --force writes it again, and " \
               "keeps yours as .bak\n"
        cases = (
            ("9.9.7", init.stamped(init.instructions(older, True)) + b"\n## Our own rules\n",
             "CLAUDE.md is not what Cortex wrote, by the digest in its marker — you changed it" + kept),
            ("9.9.8", b"# Cortex AI Team\n\nWritten by an older Cortex: read cortex.local.toml first.\n",
             "CLAUDE.md is a bootstrap without the marker, not what Cortex 9.9.8 writes — you changed it, or an older "
             "Cortex wrote it — so it is kept: cortex init --force writes it again, and keeps yours as .bak\n"),
            (OWN, newer, "CLAUDE.md is of format 3, which Cortex 1.8.0 or later reads, and not what Cortex wrote" + kept),
        )
        for pinned, content, note in cases:
            with self.subTest(pinned=pinned):
                project = self.project(pinned)
                try:
                    (project / "CLAUDE.md").write_bytes(content)
                    proc = self.upgrade(project, "9.9.8")
                    self.assert_ok(proc)
                    self.assertEqual((project / "CLAUDE.md").read_bytes(), content)
                    self.assertIn(note, proc.err)
                finally:
                    shutil.rmtree(project)

    def test_already_on_it_a_marked_file_a_team_changed_is_kept_unsaid(self):
        # #313 is a bootstrap with no marker (ADR-012 §3.4). A file with one — a digest or not — that a team
        # changed is said when the target writes another file than the pinned version: on it, the target does not.
        written = self.marked()
        project = self.project("9.9.8")
        for changed in (init.instructions(MARKED, True) + b"\n## Our own rules\n", written + b"\n## Our own rules\n"):
            with self.subTest(digest=changed.startswith(written)):
                (project / "CLAUDE.md").write_bytes(changed)
                proc = self.upgrade(project, "9.9.8")
                self.assert_ok(proc)
                self.assertEqual((project / "CLAUDE.md").read_bytes(), changed)
                self.assertNotIn("CLAUDE.md", proc.out + proc.err)

    def test_a_workspace_bootstrap_without_the_marker_is_said_to_need_workspace(self):
        # Written before the marker, by a Cortex before 1.4.0, and a team listed its services in it: its title says
        # it is a workspace's.
        self.release.replace_spec("9.9.8", {"templates/bootstrap-instructions-workspace.md": OLD_WORKSPACE})
        changed = init.instructions(OLD_WORKSPACE, True) + b"\n| `@api` | api/ | Our API |\n"
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(changed)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), changed)
        self.assertIn("CLAUDE.md is a bootstrap without the marker, not what Cortex 9.9.8 writes — you changed it, or "
                      "an older Cortex wrote it — so it is kept, and the target's template is not written: cortex init "
                      "--force --workspace writes it again, and keeps yours as .bak", proc.err)

    def test_a_workspace_bootstrap_a_team_changed_is_said_to_need_workspace(self):
        # The note names --workspace for a workspace's file, which cortex init also reads without it (#329).
        name = "bootstrap-instructions-workspace.md"
        older = (harness.REPO / "templates" / name).read_bytes().replace(b"## Where the spec is",
                                                                        b"## Where the spec is, in 9.9.8", 1)
        changed = self.marked(template=older, name=name).replace(END, b"## Our own rules\n\n" + END)
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(changed)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), changed)
        self.assertIn("CLAUDE.md is not what Cortex wrote, by the digest in its marker — you changed it — so it is kept, "
                      "and the target's template is not written: cortex init --force --workspace writes it again, and "
                      "keeps yours as .bak — your own rules go below its end marker, where Cortex keeps them\n", proc.err)

    def test_already_on_it_writes_again_what_an_interrupted_upgrade_left(self):
        # Cut short after the switch, or a pin moved by hand: 9.9.8's file under the pin of this cortex.
        written = self.marked()
        project = self.project(OWN)
        (project / "CLAUDE.md").write_bytes(written)
        proc = self.upgrade(project, "--dry-run")
        self.assert_ok(proc)
        self.assertIn(f"CLAUDE.md: would be written again from Cortex {OWN}'s template — no one changed it", proc.out)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), written)
        (project / ".git").mkdir()                       # a repository: what is written is to commit
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertIn(f"Cortex {OWN}: already on it — nothing to move", proc.out)
        self.assertIn(f"CLAUDE.md: written again from Cortex {OWN}'s template — no one changed it", proc.out)
        self.assertIn("\nCommit CLAUDE.md.\n", proc.out)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(TEMPLATE, True)))

    def test_without_a_digest_and_the_pinned_version_it_is_not_checked_and_said_so(self):
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.instructions(OLD_BOOTSTRAP, True))
        store.remove_tree(self.home / "versions" / "9.9.8")        # read-only, as the store writes it
        self.env["CORTEX_RELEASES_URL"] = "http://127.0.0.1:9"        # pruned, and offline
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertIn("CLAUDE.md was not checked: its marker carries no digest, and Cortex 9.9.8, whose templates would "
                      "tell whether you changed it, is not in the store. It is kept: cortex init --force", proc.err)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), init.instructions(OLD_BOOTSTRAP, True))

    @unittest.skipIf(os.name == "nt", "a link to a file needs a privilege on Windows")
    def test_a_bootstrap_reached_through_a_link_is_written_where_it_points_and_the_link_stays(self):
        written = self.marked()
        project = self.project("9.9.8")
        (project / "AGENTS.md").write_bytes(written)
        (project / "CLAUDE.md").symlink_to("AGENTS.md")       # one file for two tools, as teams do
        self.assert_ok(self.upgrade(project))
        self.assertTrue((project / "CLAUDE.md").is_symlink())
        self.assertEqual((project / "AGENTS.md").read_bytes(), init.stamped(init.instructions(TEMPLATE, True)))

    def test_on_the_pinned_version_not_in_the_store_the_bootstrap_is_not_checked_and_said_so(self):
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.instructions(OLD_BOOTSTRAP, True))
        store.remove_tree(self.home / "versions" / "9.9.8")
        self.env["CORTEX_RELEASES_URL"] = "http://127.0.0.1:9"        # pruned, and offline
        proc = self.upgrade(project, "9.9.8")
        self.assert_ok(proc)
        self.assertIn("note: the bootstrap files were not checked — Cortex 9.9.8 is not in the store", proc.err)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), init.instructions(OLD_BOOTSTRAP, True))

    def adapted_with_the_pinned_version_gone(self, target):
        # 9.9.8 in the store, the pin moved to 9.9.7, then 9.9.7 pruned and the network gone: an
        # adapted file is said only when the target writes a later format than its marker's.
        self.release.replace_spec("9.9.8", {"templates/bootstrap-instructions.md": target})
        project = self.project("9.9.8")
        (project / "cortex.toml").write_text(self.pinned(project).replace('"9.9.8"', '"9.9.7"'), encoding="utf-8")
        self.assert_ok(self.run_cortex(project, "sync"))
        changed = init.stamped(init.instructions(MARKED, True)) + b"\n## Our own rules\n"
        (project / "CLAUDE.md").write_bytes(changed)
        store.remove_tree(self.home / "versions" / "9.9.7")
        self.env["CORTEX_RELEASES_URL"] = "http://127.0.0.1:9"
        proc = self.upgrade(project, "9.9.8")
        self.assert_ok(proc)
        self.assertIn("note: what changes is not said — Cortex 9.9.7 is not in the store", proc.err)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), changed)
        return proc

    def test_without_the_pinned_version_one_a_team_changed_is_said_for_a_later_format(self):
        proc = self.adapted_with_the_pinned_version_gone(MARKED.replace(b"format=1", b"format=2"))
        self.assertIn("CLAUDE.md is not what Cortex wrote, by the digest in its marker — you changed it — so it is "
                      "kept", proc.err)

    def test_without_the_pinned_version_one_a_team_changed_is_not_said_for_the_same_format(self):
        proc = self.adapted_with_the_pinned_version_gone(MARKED + b"\nAnother word.\n")
        self.assertNotIn("CLAUDE.md", proc.err)

    def test_several_markers_are_said(self):
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.instructions(MARKED, True) + b"\n" + MARKED)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertIn("CLAUDE.md holds several bootstrap markers, so none is read, and it is kept: cortex init --force "
                      "writes it again", proc.err)

    def test_several_markers_of_a_workspace_are_said_to_need_workspace(self):
        marked = MARKED.replace(b"layout=single", b"layout=workspace")
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.instructions(marked, True) + b"\n" + marked)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertIn("CLAUDE.md holds several bootstrap markers, so none is read, and it is kept: cortex init --force "
                      "--workspace writes it again", proc.err)

    def test_no_personality_is_compared_and_written_without_its_block(self):
        self.release.replace_spec("9.9.8", {"templates/bootstrap-instructions.md": PERSONAL})
        project = self.project("9.9.8", theme="none")
        (project / "CLAUDE.md").write_bytes(init.instructions(PERSONAL, False))
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(TEMPLATE, False)))
        self.assertNotIn(b"PERSONALITY:BEGIN", (project / "CLAUDE.md").read_bytes())

    def test_a_workspace_bootstrap_without_the_marker_is_known_by_its_template(self):
        self.release.replace_spec("9.9.8", {"templates/bootstrap-instructions-workspace.md": OLD_WORKSPACE})
        project = self.project("9.9.8")
        (project / "AGENTS.md").write_bytes(init.instructions(OLD_WORKSPACE, True))
        self.assert_ok(self.upgrade(project))
        workspace = (harness.REPO / "templates" / "bootstrap-instructions-workspace.md").read_bytes()
        self.assertEqual((project / "AGENTS.md").read_bytes(), init.stamped(init.instructions(workspace, True)))


class StampTests(unittest.TestCase):
    """The digest a bootstrap carries in its marker (ADR-010 §3.1), of Cortex's block (ADR-012 §3.2)."""

    def test_it_is_the_digest_of_the_file_with_its_marker_plain(self):
        written = init.stamped(init.instructions(TEMPLATE, True))
        digest = hashlib.sha256(init.instructions(TEMPLATE, True)).hexdigest()[:16]
        self.assertIn(f"<!-- cortex:bootstrap layout=single format=3 digest={digest} -->\n".encode(), written)
        self.assertEqual(init.markers(written), [("single", 3)])
        self.assertIs(init.untouched(written), True)
        self.assertEqual(init.stamped(written), written)

    def test_what_git_and_editors_do_to_a_file_is_no_change(self):
        written = init.stamped(init.instructions(TEMPLATE, True))
        for kept in (written.replace(b"\n", b"\r\n"), b"\xef\xbb\xbf" + written, written.rstrip(b"\n"),
                     written + b"\n\n", written.replace(b"\n", b"\r\r\r\r\n")):    # any run of CRs is one line break
            with self.subTest(kept=kept[:8] + kept[-4:]):
                self.assertIs(init.untouched(kept), True)

    def test_a_change_in_cortex_s_block_is_one(self):
        written = init.stamped(init.instructions(TEMPLATE, True))
        end = b"<!-- cortex:bootstrap end -->\n"
        for kept in (written.replace(b"Cortex", b"Kortex", 1), written.replace(end, b"## Ours\n" + end),
                     written.replace(end, b"") + b"## Ours\n",        # the end marker gone: all of it is the block
                     written.replace(b"format=3 digest=", b"format=4 digest="),
                     written.replace(b"layout=single", b"layout=workspace")):
            with self.subTest(kept=kept[-40:]):
                self.assertIs(init.untouched(kept), False)

    def test_the_team_s_part_is_no_change(self):
        written = init.stamped(init.instructions(TEMPLATE, True))
        for team in (b"## Ours\n", b"## Ours\r\n\r\nRun the linter first.",
                     b"<!-- cortex:bootstrap layout=workspace format=9 digest=0123456789abcdef -->\n"
                     b"<!-- cortex:bootstrap end -->\n"):          # quoted by the team: its own text
            with self.subTest(team=team):
                self.assertIs(init.untouched(written + team), True)
                self.assertEqual(init.markers(written + team), [("single", 3)])
                self.assertEqual(init.parts(written + team), (written, team))

    def test_no_digest_tells_nothing(self):
        written = init.stamped(init.instructions(TEMPLATE, True))
        twice = written.replace(b"<!-- cortex:bootstrap end -->", written.split(b"\n")[2] + b"\n<!-- cortex:bootstrap end -->")
        for kept in (init.instructions(TEMPLATE, True), OLD_BOOTSTRAP, twice):
            with self.subTest(kept=kept[:40]):
                self.assertIsNone(init.untouched(kept))
        self.assertEqual(init.stamped(OLD_BOOTSTRAP), OLD_BOOTSTRAP)


@DOWNLOADS
class RollbackTests(UpgradeTestCase):
    """A sync that fails once the lock is written: the pin and the lock are put back (ADR-010 §3.5, step 6)."""

    def test_the_pin_and_the_lock_are_put_back(self):
        project = self.project("9.9.8")
        committed = {name: (project / name).read_bytes() for name in ("cortex.toml", "cortex.lock")}
        sys.path[:0] = [str(harness.CLI), str(harness.CORE)]
        from cortex_cli import config, store, sync, upgrade

        def a_sync_that_fails_late(root, *args, **kwargs):
            config.write_lock(root, store.Version(OWN), "sha256:" + "1" * 64)     # as sync does, before its swap
            raise OSError("the swap failed")

        environment = {"CORTEX_HOME": str(self.home), "CORTEX_RELEASES_URL": self.release.url}
        with mock.patch.dict(os.environ, environment), mock.patch.object(sync, "sync", a_sync_that_fails_late), \
                mock.patch.object(store.Store, "__init__", functools.partialmethod(store.Store.__init__, own_version=OWN)):
            err = io.StringIO()
            with self.assertRaises(OSError):
                upgrade.upgrade(str(project), None, False, False, None, io.StringIO(), err)
        self.assertEqual({name: (project / name).read_bytes() for name in committed}, committed)
        self.assertIn("cortex.toml and cortex.lock are as they were", err.getvalue())

    def in_process(self, project, a_sync):
        sys.path[:0] = [str(harness.CLI), str(harness.CORE)]
        from cortex_cli import store, sync, upgrade
        environment = {"CORTEX_HOME": str(self.home), "CORTEX_RELEASES_URL": self.release.url}
        err = io.StringIO()
        with mock.patch.dict(os.environ, environment), mock.patch.object(sync, "sync", a_sync), \
                mock.patch.object(store.Store, "__init__", functools.partialmethod(store.Store.__init__, own_version=OWN)):
            with self.assertRaises(OSError):
                upgrade.upgrade(str(project), None, False, False, None, io.StringIO(), err)
        return err.getvalue()

    def test_the_bootstrap_written_again_is_put_back_too(self):
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.instructions(OLD_BOOTSTRAP, True))
        committed = {name: (project / name).read_bytes() for name in ("cortex.toml", "cortex.lock", "CLAUDE.md")}

        def a_sync_that_fails(root, *args, **kwargs):
            raise OSError("the swap failed")

        err = self.in_process(project, a_sync_that_fails)
        self.assertEqual({name: (project / name).read_bytes() for name in committed}, committed)
        self.assertIn("cortex.toml, cortex.lock and CLAUDE.md are as they were", err)

    def test_a_bootstrap_that_cannot_be_written_moves_nothing(self):
        project = self.project("9.9.8")
        old = init.instructions(OLD_BOOTSTRAP, True)
        (project / "CLAUDE.md").write_bytes(old)
        committed = {name: (project / name).read_bytes() for name in ("cortex.toml", "cortex.lock", "CLAUDE.md")}
        from cortex_cli import upgrade
        put = upgrade._put

        def a_disk_that_refuses_the_new_bootstrap(path, data):
            if path.name == "CLAUDE.md" and data != old:
                raise OSError("no space left on device")
            return put(path, data)

        with mock.patch.object(upgrade, "_put", a_disk_that_refuses_the_new_bootstrap):
            self.in_process(project, lambda *args, **kwargs: None)
        self.assertEqual({name: (project / name).read_bytes() for name in committed}, committed)


@DOWNLOADS
@unittest.skipIf(os.name == "nt", "a process killed, as a machine dies: SIGKILL")
class KilledTests(UpgradeTestCase):
    """An upgrade killed — no rollback runs — leaves what the next `cortex upgrade` finishes
    (ADR-010 §3.5, step 6): the bootstrap files first, each whole or not at all."""

    def killed(self, project, patch):
        child = textwrap.dedent(f"""\
            import os, signal, sys
            sys.path[:0] = [{str(harness.CLI)!r}, {str(harness.CORE)!r}]
            from cortex_cli import config, sync, upgrade

            def die(*args, **kwargs):
                os.kill(os.getpid(), signal.SIGKILL)
        """) + textwrap.dedent(patch) + "\nsys.exit(upgrade.run([]))\n"
        proc = subprocess.run([sys.executable, "-I", "-c", child], cwd=project, env=self.env, capture_output=True)
        self.assertEqual(proc.returncode, -signal.SIGKILL, proc.stdout.decode() + proc.stderr.decode())

    def test_killed_inside_its_sync_it_has_written_the_bootstrap_and_the_next_run_finishes(self):
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.instructions(OLD_BOOTSTRAP, True))
        self.killed(project, """
            config.write_local_text = die       # the lock written, cortex/ swapped: then the machine dies
        """)
        self.assertIn(f'version = "{OWN}"', self.pinned(project))
        self.assertEqual((project / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(TEMPLATE, True)))
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertIn(f"this machine reads Cortex 9.9.8, and cortex.toml pins Cortex {OWN} — an upgrade cut short, or a "
                      "pin moved by hand: syncing", proc.out)
        self.assertIn(f"versions/{OWN}", (project / "cortex.local.toml").read_text(encoding="utf-8"))
        self.assert_ok(self.run_cortex(project, "sync", "--locked"))
        again = self.upgrade(project)
        self.assert_ok(again)
        self.assertNotIn("syncing", again.out)
        self.assertNotIn("Commit", again.out)

    def test_killed_while_it_writes_a_bootstrap_it_leaves_the_old_one_whole(self):
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.instructions(OLD_BOOTSTRAP, True))
        committed = {name: (project / name).read_bytes() for name in ("cortex.toml", "cortex.lock", "CLAUDE.md")}
        self.killed(project, """
            import builtins, io
            real = io.open

            class Dying:
                def __init__(self, fh):
                    self.fh = fh

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.fh.close()

                def write(self, data):
                    self.fh.write(data[:len(data) // 2])
                    self.fh.flush()
                    die()

            def half_then_dies(file, mode="r", *args, **kwargs):
                fh = real(file, mode, *args, **kwargs)
                return Dying(fh) if "w" in mode and os.path.basename(os.fspath(file)).startswith("CLAUDE.md") else fh

            builtins.open = io.open = half_then_dies
        """)
        self.assertEqual({name: (project / name).read_bytes() for name in committed}, committed)


@DOWNLOADS
class ResumeTests(UpgradeTestCase):
    """On the pinned version, a machine or a lock not on the pin — an upgrade cut short, a pin moved
    by hand — is synced (ADR-010 §3.5, step 1)."""

    def test_a_pin_moved_by_hand_is_synced_and_the_lock_to_commit_said(self):
        project = self.project("9.9.8")
        (project / "cortex.toml").write_text(self.pinned(project).replace('"9.9.8"', f'"{OWN}"'), encoding="utf-8")
        before = snapshot(project)
        proc = self.upgrade(project, "--dry-run")
        self.assert_ok(proc)
        self.assertIn(f"this machine reads Cortex 9.9.8, and cortex.toml pins Cortex {OWN} — an upgrade cut short, or a "
                      "pin moved by hand: the sync would put them on it", proc.out)
        self.assertEqual(snapshot(project), before)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertIn(f"Cortex {OWN}: already on it — nothing to move", proc.out)
        self.assertIn(f'version = "{OWN}"', (project / "cortex.lock").read_text(encoding="utf-8"))
        self.assertIn("\nWritten: cortex.lock — in no git work tree, so committed nowhere: each developer moves their own "
                      "(ADR-008 §9).\n", proc.out)
        self.assert_ok(self.run_cortex(project, "sync", "--locked"))

    def test_a_lock_of_another_version_alone_is_synced(self):
        from cortex_cli import config
        project = self.project(OWN)
        config.write_lock(str(project), store.Version("9.9.8"), "sha256:" + "1" * 64)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertIn(f"cortex.lock names Cortex 9.9.8, and cortex.toml pins Cortex {OWN}", proc.out)
        self.assertIn(f'version = "{OWN}"', (project / "cortex.lock").read_text(encoding="utf-8"))

    def test_a_spec_synced_from_a_checkout_is_left_alone(self):
        project = self.project(OWN, synced=False)
        self.assert_ok(self.run_cortex(project, "sync", "--from", str(harness.REPO)))
        before = snapshot(project)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertNotIn("syncing", proc.out)
        self.assertEqual(snapshot(project), before)


class PinAfterThisCortexTests(UpgradeTestCase):
    """A project pinned to a version after this cortex's: with no version named, it does not move
    down — a teammate's older binary would undo the team's pin (ADR-010 §3.5, step 1)."""

    def test_it_is_refused_with_no_version_named(self):
        project = self.project("9.10.0", synced=False)
        before = snapshot(project)
        proc = self.upgrade(project)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f"this project uses Cortex 9.10.0, which its cortex.toml pins, newer than this cortex, {OWN} — "
                      "nothing was changed. Update this cortex to serve it:\n    cortex self-update 9.10.0\n", proc.err)
        if store.development(Version(OWN)):     # a build of no release names no move down to itself (ADR-009 §9)
            self.assertNotIn("move the project down", proc.err)
        else:
            self.assertIn(f"or move the project down to this cortex's version by naming it: cortex upgrade {OWN}",
                          proc.err)
        self.assertEqual(snapshot(project), before)

    def test_it_moves_down_to_the_version_named(self):
        project = self.project("9.10.0", synced=False)
        proc = self.upgrade(project, OWN)
        self.assert_ok(proc)
        self.assertIn(f"Cortex 9.10.0 → {OWN}: a downgrade", proc.out)
        self.assertIn(f'version = "{OWN}"', self.pinned(project))

    def test_moving_down_it_says_this_cortex_does_not_serve_the_pin_and_advises_nothing(self):
        project = self.project("9.10.0", synced=False)
        proc = self.upgrade(project, OWN)
        self.assert_ok(proc)
        self.assertIn(f"note: what changes is not said — this cortex, {OWN}, does not serve Cortex 9.10.0\n", proc.err)
        self.assertNotIn("newer than this cortex", proc.err)        # the opposite move, advised


class NewerReleaseTests(UpgradeTestCase):
    """With no VERSION, `cortex upgrade` says when a release newer than the binary is out (§3.5)."""

    def setUp(self):
        super().setUp()
        FakeRelease.publish(self.release.root / "download" / "99.0.0", "99.0.0")

    def latest_is(self, version):
        self.release.redirect("/latest/download/SHA256SUMS", f"{self.release.url}/download/{version}/SHA256SUMS")

    NOTICE = f"Cortex 99.0.0 is out, and this cortex is {OWN}: `cortex self-update`, then `cortex upgrade`, moves " \
             "the project to it."

    def test_a_newer_release_is_said_last(self):
        self.latest_is("99.0.0")
        proc = self.upgrade(self.project(OWN))
        self.assert_ok(proc)
        self.assertIn(f"Cortex {OWN}: already on it — nothing to move\n", proc.out)
        self.assertTrue(proc.out.endswith(f"\n\n{self.NOTICE}\n"), proc.out)

    def test_a_version_named_asks_for_no_release(self):
        self.latest_is("99.0.0")
        proc = self.upgrade(self.project(OWN), OWN)
        self.assert_ok(proc)
        self.assertNotIn("is out", proc.out)
        self.assertFalse([path for path in self.release.requested if path.startswith("/latest/")])

    def test_the_latest_not_newer_is_not_said(self):
        if not (self.release.root / "download" / OWN).exists():         # a build of no release
            FakeRelease.publish(self.release.root / "download" / OWN, OWN)
        self.latest_is(OWN)
        proc = self.upgrade(self.project(OWN))
        self.assert_ok(proc)
        self.assertNotIn("is out", proc.out)

    def test_cortex_no_update_check_asks_for_no_release(self):
        self.latest_is("99.0.0")
        project = self.project(OWN)
        self.env["CORTEX_NO_UPDATE_CHECK"] = "1"
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertNotIn("is out", proc.out)
        self.assertFalse([path for path in self.release.requested if path.startswith("/latest/")])
        self.env["CORTEX_NO_UPDATE_CHECK"] = ""         # empty is unset
        self.assertIn(self.NOTICE, self.upgrade(project).out)

    def test_update_check_off_in_the_machine_s_file_asks_for_no_release(self):
        # ADR-013 §3.7: the machine's config.toml, which the variable goes over — and none turns it back on.
        self.latest_is("99.0.0")
        project = self.project(OWN)
        self.home.mkdir(exist_ok=True)
        (self.home / "config.toml").write_text("update_check = false\n", encoding="utf-8")
        self.env["CORTEX_NO_UPDATE_CHECK"] = ""
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertNotIn("is out", proc.out)
        self.assertFalse([path for path in self.release.requested if path.startswith("/latest/")])
        (self.home / "config.toml").write_text("update_check = true\n", encoding="utf-8")
        self.assertIn(self.NOTICE, self.upgrade(project).out)

    def test_offline_nothing_is_said(self):
        project = self.project(OWN)
        self.env["CORTEX_RELEASES_URL"] = "http://127.0.0.1:9"
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertNotIn("is out", proc.out)


class NoticeTimeoutTests(unittest.TestCase):
    def test_the_notice_waits_a_few_seconds_at_most(self):
        answered = threading.Event()
        self.addCleanup(answered.set)

        def slow(own):
            answered.wait(30)
            return Version("99.0.0")

        out = io.StringIO()
        started = time.monotonic()
        with mock.patch.object(self_update, "newer_release", side_effect=slow), \
                mock.patch.object(self_update, "NOTICE_TIMEOUT", 0.2):
            upgrade._NewerRelease(Version("9.9.9")).say(out)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(out.getvalue(), "")

    def test_a_refused_upgrade_waits_for_the_question_before_it_ends(self):
        # A daemon thread still in its request as Python exits crashed it, -11, on a refusal.
        asked, answered = threading.Event(), threading.Event()
        self.addCleanup(answered.set)

        def slow(own):
            asked.set()
            answered.wait(30)
            return None

        def refused(*args, **kwargs):
            asked.wait(5)
            threading.Timer(0.3, answered.set).start()
            raise upgrade.UpgradeError("refused")

        with mock.patch.object(self_update, "newer_release", side_effect=slow), \
                mock.patch.object(upgrade, "_upgrade", side_effect=refused), \
                mock.patch.dict(os.environ, {"CORTEX_NO_UPDATE_CHECK": ""}):
            with self.assertRaises(upgrade.UpgradeError):
                upgrade.upgrade("/nowhere", None, False, False, None, io.StringIO(), io.StringIO())
        self.assertTrue(answered.is_set())
        self.assertFalse([t for t in threading.enumerate() if isinstance(t, upgrade._NewerRelease)])


class UpgradeHelpTests(unittest.TestCase):
    def test_the_command_is_listed(self):
        self.assertIn(b"  upgrade ", harness.run("--help").stdout)

    def test_outside_a_project_it_says_so(self):
        with tempfile.TemporaryDirectory() as empty:
            proc = harness.run("upgrade", cwd=empty, env=harness.environment(CORTEX_HOME=os.path.join(empty, "h")))
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"no cortex.toml", proc.stderr)


if __name__ == "__main__":
    unittest.main()
