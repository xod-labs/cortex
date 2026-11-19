"""The bootstrap of format 4 — ADR-014 phase 1 (#409), §3.1 to §3.4, §3.6 and §3.7.

Cortex renders its block from the team's resolution: the version and the theme the team's files resolve to, the
Prompt Manager's character and card, and the team's folder are written in it, and its marker carries the version
and the theme. ``cortex init`` and ``cortex upgrade`` write it; ``cortex sync`` and ``cortex validate`` say when an
untouched one is behind the team's files; a block a team changed is kept, and said. A project kept on 1.8.x reads the
templates of format 3 its own spec ships, byte for byte. The rules the template gives the assistant are pinned by a
table — a case, the files it sets, what the assistant runs — for both templates, and the README's table with them.
"""

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))

import cli_harness as harness  # noqa: E402
from cortex_cli import init  # noqa: E402
from release_fixture import FakeRelease  # noqa: E402
from test_cli_team import DOWNLOADS, NEEDS_GIT, OWN, TEAM, TeamTestCase  # noqa: E402

SINGLE = (harness.REPO / "templates" / "bootstrap-instructions.md").read_bytes()
WORKSPACE = (harness.REPO / "templates" / "bootstrap-instructions-workspace.md").read_bytes()
TEMPLATES = (("single", SINGLE), ("workspace", WORKSPACE))
FORMAT_3 = (harness.FORMAT_3 / "bootstrap-instructions.md").read_bytes()
FORMAT_3_WORKSPACE = (harness.FORMAT_3 / "bootstrap-instructions-workspace.md").read_bytes()
README = (harness.REPO / "README.md").read_text(encoding="utf-8")
OURS = b"\n## Ours\n\nRun the linter first.\n"
# A release of 1.8.x, whose spec ships the templates of format 3 (ADR-014 §3.4).
KEPT = "1.8.7"


def where_the_spec_is(text):
    """The section *Where the spec is* of a rendered template, up to *Which command*."""
    return text[text.index("## Where the spec is"):text.index("**Which command**")]


def rules(text):
    """The numbered rules of *Where the spec is*, by number."""
    return {int(m.group(1)): m.group(2) for m in re.finditer(r"(?m)^(\d+)\. (.*)$", where_the_spec_is(text))}


def step_3(text):
    return text[text.index("### Step 3"):text.index("<!-- PERSONALITY:END -->")]


# §3.7: each case — the files it sets, where the template says what to do of it, and what the assistant runs or
# none. ``where``: a numbered rule of *Where the spec is*, ``"step 3"`` the personality step, or ``"after"`` the text
# after the rules; ``when`` the condition as the template writes it, ``then`` what the assistant does — the command it runs, or
# none — found after the condition, in the same rule; ``readme`` the README's row that says it.
CASES = (
    ("no cortex.local.toml", {}, 1, "No `cortex.local.toml`", "Run `cortex sync` at the {root} root", "cortex sync",
     "no `cortex.local.toml`, no `spec` in it, or no directory where `spec` points"),
    ("no spec in it", {"cortex.local.toml": ""}, 1, "no `spec` in it", "Run `cortex sync` at the {root} root",
     "cortex sync", "no `cortex.local.toml`, no `spec` in it"),
    ("a spec that points nowhere — pruned, or a link that leads nowhere",
     {"cortex.local.toml": 'spec = "/gone/versions/1.9.0"'}, 1, "no directory where `spec` points",
     "go no further until it succeeded", "cortex sync", "a `cortex/` link leads nowhere"),
    ("a try", {"cortex.local.toml": 'version = "1.9.1"\nsynced = {pinned = "1.9.0"}'}, 2,
     "A `version` in `cortex.local.toml` is the developer's try", "compare nothing", None,
     "a `version` in `cortex.local.toml` — your try"),
    ("a checkout", {"cortex.local.toml": 'synced = {pinned = "1.9.0", reads = ""}'}, 2,
     "`reads = \"\"` in `synced` is a checkout", "compare nothing", None, "`reads = \"\"` in `synced` — a checkout"),
    ("no synced", {"cortex.local.toml": 'spec = "…/versions/1.9.0"'}, 2, "no `synced`", "run `cortex sync`, once",
     "cortex sync", "no `synced`, or a `pinned` in `synced` other than the version the bootstrap names"),
    ("a teammate moved the pin", {"cortex.local.toml": 'synced = {pinned = "1.9.1"}'}, 2,
     "a `pinned` that is not `{version}`", "run `cortex sync`, once", "cortex sync",
     "a teammate moved the project"),
    ("in step", {"cortex.local.toml": 'synced = {pinned = "{version}"}'}, 2, "Compare the text as written",
     "run nothing while they match", None, "nothing to run while they match"),
    ("still different after the sync", {"cortex.local.toml": 'synced = {pinned = "1.9.1"}'}, 2,
     "when they still differ afterwards", "tell the user, and go no further", None,
     "still different afterwards, it tells you"),
    ("this cortex too old", {"cortex sync": "too old"}, 3, "says this cortex is too old for the {root}",
     "run the command it names to update it", "cortex self-update", "saying this cortex is too old"),
    ("a missing team's folder", {"cortex sync": "names a missing team's folder"}, 3,
     "It names a missing team's folder", "tell the user the command it names", None,
     "naming a missing team's folder"),
    ("a key refused", {"cortex sync": "refuses a key"}, 3,
     "It refuses `cortex.toml` or `cortex.local.toml` for a key", "run `cortex self-update`, then `cortex sync`, "
     "once each", "cortex self-update", "runs `cortex self-update`, then `cortex sync`, once each"),
    ("the developer's own theme", {"cortex.local.toml": 'theme = "breaking-bad"'}, "step 3",
     "A `theme` in `cortex.local.toml` is the developer's own, and replaces it",
     "find its Prompt Manager in its `characters.md`", None, "a `theme` in `cortex.local.toml` — yours"),
    ("the developer's theme none", {"cortex.local.toml": 'theme = "none"'}, "step 3", "**`none`**",
     "skip this step entirely", None, "`none`, no personality"),
    ("the team's theme", {}, "step 3", "With the team's theme, the Prompt Manager is", "**that is YOU**", None,
     "is the Prompt Manager the bootstrap names, and reads its card"),
    ("no cortex anywhere", {"PATH": ""}, "after", "**Neither, anywhere**", "tell the user to install it", None,
     "tells you to install it"),
)
# What format 3 asked of the assistant, which format 4 asks no more: the version's rules below 1.8.0, the team's
# folder compared, and the link read — #378's two actions for a link to a checkout, #376's PowerShell reading.
GONE = ("below 1.8.0", "**No `synced`**", "**A version moved**", "compare the files only", "(Get-Item cortex).Target",
        "ls -l cortex", "git ls-files", "`cortex --version`:", "**`cortex.toml` names another folder**", "1.7.3")


class RenderTests(unittest.TestCase):
    """What a template of format 4 is rendered with (§3.1)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-format4-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_the_values_name_the_prompt_manager_and_its_card(self):
        found = init.values_for(self.tmp, harness.REPO, "1.9.0", "h2g2", "agents")
        self.assertEqual(found, init.Values("1.9.0", "h2g2", "agents", "Oolon Colluphid",
                                            "cortex/agents/personalities/h2g2/Oolon-Colluphid.md"))
        self.assertEqual(init.values_for(self.tmp, harness.REPO, "1.9.0", "breaking-bad", "agents").character,
                         "Saul Goodman")

    def test_the_team_s_overlay_of_the_card_is_named_in_its_folder(self):
        overlay = self.tmp / "team" / "personalities" / "h2g2" / "Oolon-Colluphid.md"
        overlay.parent.mkdir(parents=True)
        overlay.write_text("<!-- OVERLAY -->\n", encoding="utf-8")
        self.assertEqual(init.values_for(self.tmp, harness.REPO, "1.9.0", "h2g2", "team").card,
                         "team/personalities/h2g2/Oolon-Colluphid.md")

    def test_a_theme_of_the_team_s_own_is_read_in_its_folder(self):
        theme = self.tmp / "agents" / "personalities" / "acme"
        theme.mkdir(parents=True)
        (theme / "characters.md").write_text(
            "| Role (`roles/`) | Acme Character | Alias | File |\n|---|---|---|---|\n"
            "| `prompt-manager` | Road Runner | @Beep | [📄](Road-Runner.md) |\n", encoding="utf-8")
        (theme / "Road-Runner.md").write_text("# Road Runner\n", encoding="utf-8")
        self.assertEqual(init.values_for(self.tmp, harness.REPO, "1.9.0", "acme", "agents"),
                         init.Values("1.9.0", "acme", "agents", "Road Runner",
                                     "agents/personalities/acme/Road-Runner.md"))

    def test_no_character_is_named_without_a_table_cortex_can_read(self):
        theme = self.tmp / "agents" / "personalities" / "acme"
        theme.mkdir(parents=True)
        for table in ("# Acme characters\n",                                           # no table
                      "| `prompt-manager` | Road Runner | @Beep | [📄](../../x.md) |\n",   # a card out of the theme
                      "| `prompt-manager` | Road Runner | @Beep | [📄](Gone.md) |\n",      # a card that is not there
                      "| `prompt-manager` | Road `Runner` | @Beep | [📄](Road-Runner.md) |\n",  # a name of markup
                      "| `prompt-manager` | Road Runner | @Beep | [📄](.Road-Runner.md) |\n"):  # a hidden card
            with self.subTest(table=table):
                (theme / "characters.md").write_text(table, encoding="utf-8")
                (theme / "Road-Runner.md").write_text("# Road Runner\n", encoding="utf-8")
                (theme / ".Road-Runner.md").write_text("# Road Runner\n", encoding="utf-8")
                found = init.values_for(self.tmp, harness.REPO, "1.9.0", "acme", "agents")
                self.assertEqual((found.character, found.card), (None, None))
                for name, template in TEMPLATES:
                    text = init.rendered(template, found)
                    self.assertNotIn(b"{{", text)
                    self.assertNotIn(b"cortex:character", text)
                    self.assertNotIn(b"the Prompt Manager is **", text)
                    self.assertIn(b"find the character assigned to the `prompt-manager` role", text)

    def test_both_templates_are_format_4_and_leave_no_placeholder(self):
        for name, template in TEMPLATES:
            with self.subTest(template=name):
                self.assertEqual(init.markers(template), [(name, 4)])
                self.assertIsNotNone(init.END_MARKER.search(template))
                for theme in ("h2g2", "none"):
                    text = harness.text(template, "1.9.0", theme=theme, folder="team")
                    self.assertNotIn(b"{{", text)
                    self.assertNotIn(b"cortex:character", text)
                    self.assertEqual(init.marker_values(init.stamped(text)), {"version": "1.9.0", "theme": theme})
                    self.assertIn(b"`team/`", text)
                    self.assertNotIn(b"`agents/`", text.replace(b"`agents/`, the team's folder", b""))
                    self.assertEqual(b"PERSONALITY:BEGIN" in text, theme != "none")

    def test_a_template_of_format_3_is_written_byte_for_byte(self):
        given = init.Values("1.9.0", "h2g2", "team", "Oolon Colluphid", "x.md")
        for template in (FORMAT_3, FORMAT_3_WORKSPACE):
            self.assertEqual(init.rendered(template, given), template)
        self.assertIsNone(init.marker_values(init.stamped(init.instructions(FORMAT_3, True))))

    def test_the_values_are_the_block_s_and_a_change_of_them_is_one(self):
        written = harness.written(SINGLE, "1.9.0")
        self.assertIs(init.untouched(written), True)
        self.assertIs(init.untouched(written.replace(b" theme=h2g2 -->", b" theme=none -->")), False)
        self.assertIs(init.untouched(written.replace(b" version=1.9.0 ", b" version=1.9.1 ")), False)


class TemplateTableTests(unittest.TestCase):
    """§3.7: the rules of both templates, pinned by a table (#376) — and the README's table against it (#373)."""

    def test_each_case_is_one_rule_and_one_action(self):
        for name, template in TEMPLATES:
            root = "workspace" if name == "workspace" else "project"
            text = harness.text(template, "1.9.0").decode("utf-8")
            numbered = rules(text)
            self.assertEqual(sorted(numbered), [1, 2, 3], name)
            for case, files, where, when, then, command, _ in CASES:
                with self.subTest(template=name, case=case, files=files):
                    when, then = when.format(root=root, version="1.9.0"), then.format(root=root, version="1.9.0")
                    rule = numbered[where] if isinstance(where, int) else step_3(text) if where == "step 3" else \
                        text[text.index("**Which command**"):text.index("## Bootstrap")]
                    self.assertIn(when, rule)
                    self.assertIn(then, rule[rule.index(when):])
                    if command is not None:
                        self.assertIn(f"`{command}", rule[rule.index(when):])

    def test_the_rule_of_the_version_names_the_version_rendered(self):
        for name, template in TEMPLATES:
            with self.subTest(template=name):
                text = harness.text(template, "1.9.3").decode("utf-8")
                self.assertIn("a `pinned` that is not `1.9.3`", rules(text)[2])
                self.assertIn("**Cortex 1.9.3**", text)
                # The try and the checkout are said before the comparison that would run a sync.
                rule = rules(text)[2]
                self.assertLess(rule.index("compare nothing"), rule.index("run `cortex sync`, once"))

    def test_what_format_3_asked_is_gone(self):
        # #378: a link to a checkout had two actions; #376: the link was read as PowerShell does not print it. Neither
        # rule is there to misread: the assistant reads no link, and orders no version.
        for name, template in TEMPLATES:
            text = harness.text(template, "1.9.0").decode("utf-8")
            for gone in GONE:
                with self.subTest(template=name, gone=gone):
                    self.assertNotIn(gone, text)

    def test_the_readme_has_a_row_for_each_case(self):
        section = README[README.index("### What the assistant does on its own"):README.index("## ⚙️ The Runtime")]
        for case, *_, readme in CASES:
            with self.subTest(case=case):
                self.assertIn(readme, section)
        for gone in ("from 1.8.0", "below 1.8.0", "git ls-files"):
            self.assertNotIn(gone, section)

    def test_the_rest_carries_over(self):
        for name, template in TEMPLATES:
            text = harness.text(template, "1.9.0").decode("utf-8")
            for kept in ("**Which command** — only once a rule above has you run one", "**Neither, anywhere**",
                         "Never run `cortex upgrade`, nor change `version` in `cortex.toml`, in `cortex.local.toml` "
                         "or in the team's file", "`cortex upgrade --dry-run` first"):
                with self.subTest(template=name, kept=kept):
                    self.assertIn(kept, text)
        workspace = harness.text(WORKSPACE, "1.9.0", folder="team").decode("utf-8")
        self.assertIn("`{workspace_root}/team/personalities/{theme}/{file}`", workspace)
        self.assertIn("`{service}/agents/personalities/{theme}/{file}`", workspace)
        self.assertIn("## Services declared in this workspace", workspace)


@DOWNLOADS
class CommandTests(TeamTestCase):
    """Who writes the block, and who says it is behind (§3.3, §3.4)."""

    def setUp(self):
        super().setUp()
        self.release.close()
        self.release = FakeRelease(("1.7.0", KEPT, "9.9.7", "9.9.8"))
        self.addCleanup(self.release.close)
        self.release.replace_spec(KEPT, {"templates/bootstrap-instructions.md": FORMAT_3,
                                         "templates/bootstrap-instructions-workspace.md": FORMAT_3_WORKSPACE,
                                         "templates/project-overview.md.template": b"# Overview\n",
                                         "templates/project-context.md.template": b"# Context\n"})
        self.env["CORTEX_RELEASES_URL"] = self.release.url

    def own(self, theme="h2g2", **kwargs):
        return harness.written(SINGLE, OWN, theme=theme, **kwargs)

    def test_init_writes_format_4_from_the_team_s_resolution(self):
        root = self.project(toml="", team=f'version = "{OWN}"\ntheme = "breaking-bad"\n', local='theme = "none"\n')
        proc = self.cortex(root, "init", "--tool", "claude")
        self.assert_ok(proc)
        written = (root / "CLAUDE.md").read_bytes()
        self.assertEqual(written, self.own("breaking-bad"))
        self.assertEqual(init.marker_values(written), {"version": OWN, "theme": "breaking-bad"})
        self.assertIn("the Prompt Manager is **Saul Goodman**", written.decode())
        self.assertIn("`cortex/agents/personalities/breaking-bad/Saul-Goodman.md`", written.decode())
        self.assertNotIn(b"theme=none", written)                 # the developer's theme never reaches it

    def test_a_developer_s_try_never_reaches_the_committed_block(self):
        self.release.replace_spec("9.9.8", {"templates/bootstrap-instructions.md": SINGLE,
                                            "templates/project-overview.md.template": b"# Overview\n",
                                            "templates/project-context.md.template": b"# Context\n"})
        root = self.project(toml='theme = "h2g2"\n', team='version = "9.9.8"\n',
                            local=f'version = "{OWN}"\ntheme = "breaking-bad"\n')
        self.assert_ok(self.cortex(root, "init", "--tool", "claude"))
        written = (root / "CLAUDE.md").read_bytes()
        self.assertEqual(init.marker_values(written), {"version": "9.9.8", "theme": "h2g2"})
        self.assertIn(b"This project reads **Cortex 9.9.8**", written)
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), written)
        self.assertNotIn("behind", proc.err)

    def test_a_theme_the_team_changes_leaves_the_block_behind_until_upgrade_writes_it(self):
        root = self.project(toml="", team=f'version = "{OWN}"\ntheme = "h2g2"\n')
        self.assert_ok(self.cortex(root, "init", "--tool", "claude"))
        (root / TEAM).write_text(f'version = "{OWN}"\ntheme = "breaking-bad"\n', encoding="utf-8")
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertIn(f"note: CLAUDE.md is behind: Cortex {OWN}, the team's version, writes another — "
                      f"`cortex upgrade {OWN}` writes it again\n", proc.err)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), self.own("h2g2"))     # sync never writes it
        proc = self.cortex(root, "validate")
        self.assert_ok(proc)
        self.assertIn("BOOTSTRAP_BEHIND", proc.out + proc.err)
        self.assertEqual(self.cortex(root, "validate", "--strict").returncode, 1)
        proc = self.cortex(root, "upgrade", OWN)                                # the command named, same version
        self.assert_ok(proc)
        self.assertIn("CLAUDE.md: written again", proc.out)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), self.own("breaking-bad"))
        proc = self.cortex(root, "validate", "--strict")
        self.assert_ok(proc)
        self.assertNotIn("BOOTSTRAP_BEHIND", proc.out + proc.err)

    def test_a_block_the_team_changed_is_kept_and_said_once(self):
        root = self.project(toml="", team=f'version = "{OWN}"\ntheme = "h2g2"\n')
        self.assert_ok(self.cortex(root, "init", "--tool", "claude"))
        end = b"<!-- cortex:bootstrap end -->"
        changed = self.own("h2g2").replace(end, b"## Our rule, in Cortex's block\n\n" + end)
        (root / "CLAUDE.md").write_bytes(changed)
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertNotIn("CLAUDE.md", proc.err)                         # the values are the resolution's
        (root / TEAM).write_text(f'version = "{OWN}"\ntheme = "breaking-bad"\n', encoding="utf-8")
        for command in (("sync",), ("validate",)):
            with self.subTest(command=command):
                proc = self.cortex(root, *command)
                self.assert_ok(proc)
                said = (f"CLAUDE.md is not rendered from the team's resolution — it says Cortex {OWN} and the theme "
                        f"h2g2, where the team's files say Cortex {OWN} and the theme breaking-bad — and a team changed "
                        "its block, so Cortex keeps it: `cortex init --tool claude --force`, at the project root, "
                        "writes it again, and keeps yours as .bak")
                self.assertEqual(proc.err.count(said), 1, proc.err)
                self.assertNotIn("is behind", proc.err)
                self.assertNotIn("BOOTSTRAP_BEHIND", proc.out)
        proc = self.cortex(root, "upgrade", OWN)
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), changed)
        self.assertIn("so it is kept", proc.err)
        proc = self.cortex(root, "init", "--tool", "claude", "--force")         # the command named
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), self.own("breaking-bad"))
        self.assertEqual((root / "CLAUDE.md.bak").read_bytes(), changed)

    def test_a_project_kept_on_1_8_x_keeps_format_3_byte_for_byte(self):
        root = self.project(toml=f'version = "{KEPT}"\ntheme = "h2g2"\n')
        self.assert_ok(self.cortex(root, "sync"))
        kept = init.stamped(init.instructions(FORMAT_3, True)) + OURS
        (root / "CLAUDE.md").write_bytes(kept)
        for command in (("sync",), ("validate", "--strict"), ("upgrade", "--dry-run"), ("upgrade", KEPT)):
            with self.subTest(command=command):
                proc = self.cortex(root, *command)
                self.assert_ok(proc)
                self.assertEqual((root / "CLAUDE.md").read_bytes(), kept)
                self.assertNotIn("behind", proc.err)
        self.assertIn(f"CLAUDE.md: would be written again from Cortex {OWN}'s template",
                      self.cortex(root, "upgrade", "--dry-run").out)
        # An upgrade to a version of format 4 moves an untouched format 3 block, the team's part kept.
        proc = self.cortex(root, "upgrade")
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), self.own("h2g2") + OURS)

    def test_a_changed_format_3_block_is_kept_and_said_on_the_upgrade(self):
        root = self.project(toml=f'version = "{KEPT}"\ntheme = "h2g2"\n')
        self.assert_ok(self.cortex(root, "sync"))
        changed = init.stamped(init.instructions(FORMAT_3, True)).replace(b"Cortex AI Team\n", b"Cortex AI Team, ours\n",
                                                                            1)
        (root / "CLAUDE.md").write_bytes(changed)
        proc = self.cortex(root, "upgrade")
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), changed)
        self.assertEqual(proc.err.count("CLAUDE.md is not what Cortex wrote, by the digest in its marker"), 1, proc.err)

    def test_a_workspace_names_its_team_s_folder_and_its_services_stay_below_the_end_marker(self):
        root = self.project(toml=f'version = "{OWN}"\ntheme = "h2g2"\nagents = "team"\n', committed=True)
        (root / "team").mkdir()
        proc = self.cortex(root, "init", "--workspace", "--tool", "claude", "--service", "api")
        self.assert_ok(proc)
        written = (root / "CLAUDE.md").read_bytes()
        self.assertEqual(written, harness.written(WORKSPACE, OWN, folder="team"))
        self.assertIn(b"`{workspace_root}/team/` is the team's folder", written)
        services = written + b"\n| @alias | Folder | Description |\n|---|---|---|\n| `@api` | api/ | the API |\n"
        (root / "CLAUDE.md").write_bytes(services)
        (root / "cortex.toml").write_text(f'version = "{OWN}"\ntheme = "breaking-bad"\nagents = "team"\n',
                                          encoding="utf-8")
        proc = self.cortex(root, "upgrade", OWN)
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(),
                         harness.written(WORKSPACE, OWN, theme="breaking-bad", folder="team") +
                         services[len(written):])

    @harness.outside_a_repository
    def test_the_developer_s_own_bootstrap_is_rendered_from_the_full_resolution(self):
        root = self.project(toml="", team=f'version = "{OWN}"\ntheme = "h2g2"\n', local='theme = "breaking-bad"\n',
                            committed=False)
        self.assert_ok(self.cortex(root, "init", "--tool", "claude"))
        self.assertEqual((root / "CLAUDE.md").read_bytes(), self.own("breaking-bad"))
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertNotIn("CLAUDE.md", proc.out + proc.err)                     # in step: nothing to say

    @harness.outside_a_repository
    def test_the_developer_s_own_bootstrap_follows_a_theme_moved_with_no_team_s_file(self):
        root = self.project(toml=f'version = "{OWN}"\ntheme = "h2g2"\n', committed=False)
        self.assert_ok(self.cortex(root, "init", "--tool", "claude"))
        (root / "CLAUDE.md").write_bytes((root / "CLAUDE.md").read_bytes() + OURS)
        (root / "cortex.toml").write_text(f'version = "{OWN}"\ntheme = "breaking-bad"\n', encoding="utf-8")
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertIn("CLAUDE.md: written again", proc.out)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), self.own("breaking-bad") + OURS)
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertNotIn("CLAUDE.md", proc.out + proc.err)                     # in step: nothing to say
        self.assert_ok(self.cortex(root, "validate", "--strict"))

    @DOWNLOADS
    @harness.outside_a_repository
    def test_the_developer_s_own_bootstrap_follows_a_version_moved_with_no_team_s_file(self):
        self.release.replace_spec("9.9.8", {"templates/bootstrap-instructions.md": SINGLE,
                                            "templates/project-overview.md.template": b"# Overview\n",
                                            "templates/project-context.md.template": b"# Context\n"})
        root = self.project(toml=f'version = "{OWN}"\ntheme = "h2g2"\n', committed=False)
        self.assert_ok(self.cortex(root, "init", "--tool", "claude"))
        (root / "CLAUDE.md").write_bytes((root / "CLAUDE.md").read_bytes() + OURS)
        (root / "cortex.toml").write_text('version = "9.9.8"\ntheme = "h2g2"\n', encoding="utf-8")
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        written = (root / "CLAUDE.md").read_bytes()
        self.assertEqual(init.marker_values(written), {"version": "9.9.8", "theme": "h2g2"})
        self.assertIn(b"This project reads **Cortex 9.9.8**", written)
        self.assertTrue(written.endswith(OURS))
        self.assertEqual(self.local(root)["synced"]["pinned"], "9.9.8")     # the version the block names
        self.assert_ok(self.cortex(root, "validate", "--strict"))

    @NEEDS_GIT
    def test_the_developer_s_own_bootstrap_follows_a_team_s_folder_moved_with_no_team_s_file(self):
        root = self.project(toml=f'version = "{OWN}"\ntheme = "h2g2"\n', committed=False)
        (root / "agents").mkdir()
        (root / "team").mkdir()
        (root / ".gitignore").write_text("CLAUDE.md\n", encoding="utf-8")
        self.git(root, "init", "-q")
        self.git(root, "add", "cortex.toml", ".gitignore")
        self.git(root, "commit", "-qm", "init")
        self.assert_ok(self.cortex(root, "init", "--workspace", "--tool", "claude", "--service", "api"))
        self.assertIn(b"`{workspace_root}/agents/` is the team's folder", (root / "CLAUDE.md").read_bytes())
        (root / "cortex.toml").write_text(f'version = "{OWN}"\ntheme = "h2g2"\nagents = "team"\n', encoding="utf-8")
        self.git(root, "commit", "-qam", "the team's folder")
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertIn(b"`{workspace_root}/team/` is the team's folder", (root / "CLAUDE.md").read_bytes())
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertNotIn("CLAUDE.md", proc.out + proc.err)                     # in step: nothing to say
