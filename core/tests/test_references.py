"""What a project's file refers to — ``REFERENCE_NOT_FOUND`` (#140, ADR-007's amendment of 2026-11-23).

The rule counts a reference only in the forms a file dispatches with: these tests hold what counts,
and — as much — what does not: Claude Code's subagents, service aliases, e-mail addresses, npm scopes,
placeholders, code blocks and comments.
"""

import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cortex_core import references  # noqa: E402
from cortex_core.references import Tiers, missing, prose_lines  # noqa: E402
from cortex_core.validate import Colors, validate  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

CHARACTERS = ("# Characters\n\n| Role | Character | Alias |\n|---|---|---|\n"
              "| `architect` | Slartibartfast | @Slartibartfast |\n| `lead-backend` | Hactar | @Hactar |\n")


class Project:
    """A base and a project beside it, in a temporary directory."""

    def __init__(self, test: unittest.TestCase):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-references-"))
        test.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.base = self.tmp / "spec"
        self.root = self.tmp / "host"
        for path, text in {
            "agents/roles/engineering/architect.md": "# Architect\n",
            "agents/roles/engineering/lead-backend.md": "# Lead backend\n",
            "agents/roles/prompt-manager.md": "# Prompt manager\n",
            "agents/workflows/engineering/code-review.md": "# Code review\n",
            "agents/personalities/h2g2/characters.md": CHARACTERS,
            "docs/extending-layers.md": "# Extending\n",
            "templates/workflow.md.template": "# Template\n",
        }.items():
            self.write(self.base / path, text)
        (self.root / "agents").mkdir(parents=True)

    @staticmethod
    def write(path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def tiers(self, *dirs: str, theme="h2g2") -> Tiers:
        return Tiers(str(self.base), [str(self.root / d) for d in (dirs or ("agents",))], theme)

    def missing(self, text: str, *dirs: str, theme="h2g2"):
        return [m.message for m in missing(text, self.tiers(*dirs, theme=theme))]


class WhatCountsTests(unittest.TestCase):
    def setUp(self):
        self.p = Project(self)

    def test_a_spec_path_names_a_file_or_a_directory_of_the_spec(self):
        text = ("See `cortex/agents/roles/engineering/architect.md`, cortex/docs/extending-layers.md and\n"
                "cortex/templates/workflow.md.template, in cortex/agents/roles/.\n"
                "But `cortex/agents/roles/ghost.md`, cortex/docs/ghost.md, cortex/templates/ghost.md.\n")
        self.assertEqual(self.p.missing(text), [
            "line 3: 'cortex/agents/roles/ghost.md' is no file of the spec",
            "line 3: 'cortex/docs/ghost.md' is no file of the spec",
            "line 3: 'cortex/templates/ghost.md' is no file of the spec",
        ])

    def test_a_spec_path_is_the_spec_s_only(self):
        # A file of the team's folder is no file of the spec: cortex/ names the spec.
        self.p.write(self.p.root / "agents/roles/product/market-analyst.md", "# Mine\n")
        self.assertEqual(self.p.missing("`cortex/agents/roles/product/market-analyst.md`\n"),
                         ["line 1: 'cortex/agents/roles/product/market-analyst.md' is no file of the spec"])

    def test_a_layer_path_names_a_file_of_the_spec_or_of_a_tier(self):
        self.p.write(self.p.root / "agents/roles/product/market-analyst.md", "# Mine\n")
        text = ("| 1 | `roles/engineering/architect.md` | `roles/product/market-analyst.md` |\n"
                "| 2 | `agents/workflows/engineering/code-review.md` | `roles/prompt-manager.md` |\n"
                "| 3 | `roles/product/ghost.md` | `workflows/intelligence/market-study.md` |\n"
                "| 4 | agents/capabilities/languages/php.md |\n")
        self.assertEqual(self.p.missing(text), [
            "line 3: 'roles/product/ghost.md' is in neither the spec nor an overlay tier of this file's cascade",
            "line 3: 'workflows/intelligence/market-study.md' is in neither the spec nor an overlay tier of this "
            "file's cascade",
            "line 4: 'agents/capabilities/languages/php.md' is in neither the spec nor an overlay tier of this "
            "file's cascade",
        ])

    def test_a_workflow_in_any_tier_is_the_one_the_cascade_serves(self):
        # Replacement: the most specific wins, and one tier holding it is enough.
        self.p.write(self.p.root / "agents/workflows/intelligence/market-study.md", "# Ours\n")
        self.p.write(self.p.root / "svc/agents/workflows/engineering/release.md", "# The service's\n")
        text = "`workflows/intelligence/market-study.md`, `workflows/engineering/release.md`\n"
        self.assertEqual(self.p.missing(text, "agents", "svc/agents"), [])

    def test_a_team_file_does_not_read_a_service_s_tier(self):
        self.p.write(self.p.root / "svc/agents/roles/data/data-analyst.md", "# The service's\n")
        text = "`roles/data/data-analyst.md`\n"
        self.assertEqual(self.p.missing(text, "agents"), [
            "line 1: 'roles/data/data-analyst.md' is in neither the spec nor an overlay tier of this file's cascade"])
        self.assertEqual(self.p.missing(text, "agents", "svc/agents"), [])

    def test_a_role_on_a_dispatch_line(self):
        text = ("**Agent:** `engineering/architect` + `product/ghost` · **Load:** `release/semver`\n"
                "**Agents :** `engineering/lead-backend`, `data/nobody`\n"
                "**Agent**: `security/nobody`\n"
                "Not a dispatch line: `release/semver`, `feat/branch`.\n")
        self.assertEqual(self.p.missing(text), [
            "line 1: the role 'product/ghost' is in neither the spec nor an overlay tier of this file's cascade — "
            "no roles/product/ghost.md",
            "line 2: the role 'data/nobody' is in neither the spec nor an overlay tier of this file's cascade — "
            "no roles/data/nobody.md",
            "line 3: the role 'security/nobody' is in neither the spec nor an overlay tier of this file's cascade — "
            "no roles/security/nobody.md",
        ])

    def test_a_character_tied_to_a_role(self):
        text = ("| 1 | `roles/engineering/architect.md` (@Slartibartfast) |\n"
                "| 2 | `roles/engineering/lead-backend.md` (@Hotblack) |\n"
                "| 3 | `prompt-manager` (@Oolon) + `engineering/architect` (@Zarniwoop) |\n"
                "- **@Hactar** (lead-backend), **@Marvin** (`architect`), @Ford (engineering/lead-backend)\n")
        self.assertEqual(self.p.missing(text), [
            "line 2: '@Hotblack' is no character of the theme 'h2g2' — its characters.md does not name it",
            "line 3: '@Oolon' is no character of the theme 'h2g2' — its characters.md does not name it",
            "line 3: '@Zarniwoop' is no character of the theme 'h2g2' — its characters.md does not name it",
            "line 4: '@Marvin' is no character of the theme 'h2g2' — its characters.md does not name it",
            "line 4: '@Ford' is no character of the theme 'h2g2' — its characters.md does not name it",
        ])

    def test_each_reference_is_said_once_at_its_first_line(self):
        text = "`roles/x/ghost.md`\n\n`roles/x/ghost.md` again\n"
        self.assertEqual(self.p.missing(text), [
            "line 1: 'roles/x/ghost.md' is in neither the spec nor an overlay tier of this file's cascade"])

    def test_an_anchor_a_line_number_and_punctuation_are_no_part_of_the_path(self):
        text = ("See cortex/docs/extending-layers.md#overlays, **`roles/engineering/architect.md`**:\n"
                "`agents/roles/engineering/architect.md:3`, and roles/engineering/lead-backend.md.\n")
        self.assertEqual(self.p.missing(text), [])

    def test_a_crlf_file_reads_as_its_lf_one(self):
        text = "line one\r\n`roles/x/ghost.md`\r\n```\r\n`roles/y/ghost.md`\r\n```\r\n"
        self.assertEqual(self.p.missing(text), [
            "line 2: 'roles/x/ghost.md' is in neither the spec nor an overlay tier of this file's cascade"])


class WhatDoesNotCountTests(unittest.TestCase):
    """No false positives: a rule that misses a reference is better than one that cries wolf."""

    def setUp(self):
        self.p = Project(self)

    def test_claude_code_s_subagents(self):
        text = ("**Agent :** @Explore / @Slartibartfast\n"
                "**Agents :** @Explore, @Plan, @general-purpose\n"
                "| 3 | `@Explore` + `roles/engineering/architect.md` (@Slartibartfast) |\n"
                "Search with @Explore (read-only), then (@Explore) and (@Plan).\n"
                "@Explore (subagent) — `explore` (@Explore) — @general-purpose (`architect`)\n")
        self.assertEqual(self.p.missing(text), [])

    def test_service_aliases(self):
        text = ("<!-- @alias: backend -->\n| `@nexus` | `acteeve/nexus/` |\n"
                "When a `@nexus` bump breaks @atlas, ask @backend or @cortex (@nexus).\n"
                "- [ ] @nexus extra-files configuration tested\n**Agent:** @backend\n")
        self.assertEqual(self.p.missing(text), [])

    def test_e_mail_addresses_and_npm_scopes(self):
        text = ("Write to ops@example.com or `Ops@Example.com` (Ops@Example).\n"
                "Install @types/node, `@types/node` (@types/node), uses: googleapis/release-please-action@v4\n")
        self.assertEqual(self.p.missing(text), [])

    def test_a_character_alone_is_no_dispatch(self):
        # Prose, a table cell, a bold name: none says which role the character plays.
        text = ("I recommend consulting @Ghost.\n| 1 | `roles/engineering/architect.md` | @Ghost |\n"
                "> Character: **@Ghost**.\n@Ghost (nobody-role) is tied to no role that exists.\n")
        self.assertEqual(self.p.missing(text), [])

    def test_placeholders_are_patterns(self):
        text = ("`{service}/agents/roles/{category}/{role}.md`, `roles/{category}/{role}.md`,\n"
                "`cortex/agents/personalities/{theme}/characters.md`, cortex/agents/roles/<category>/*.md,\n"
                "`workflows/…/x.md`, roles/.../x.md, `cortex/docs/$NAME.md`, roles/*.md\n")
        self.assertEqual(self.p.missing(text), [])

    def test_a_path_inside_another_path(self):
        text = ("ai/cortex/agents/roles/ghost.md, ../roles/ghost.md, ./workflows/ghost.md,\n"
                "https://github.com/xod-labs/cortex/blob/main/agents/roles/ghost.md, my-cortex/docs/ghost.md\n")
        self.assertEqual(self.p.missing(text), [])

    def test_paths_outside_the_spec_s_folders_and_the_layers(self):
        text = ("`cortex.toml`, `project-context.md`, `agents/project-context.md`, `docs/ghost.md`,\n"
                "`capabilities/ghost.md`, `personalities/ghost/theme.md`, `cortex/README.md`, `templates/ghost.md`\n")
        self.assertEqual(self.p.missing(text), [])

    def test_fenced_code_blocks_and_html_comments(self):
        text = ("```markdown\n**Agent:** `product/ghost` — `roles/ghost/a.md` (@Ghost), `cortex/docs/ghost.md`\n```\n"
                "~~~~\ncortex/templates/ghost.md\n```\nstill in the block: roles/ghost/b.md\n~~~~\n"
                "  ```text\n  roles/ghost/indented-fence.md\n  ```\n"
                "<!-- OVERLAY\n     Base: cortex/agents/roles/ghost.md\n-->\n"
                "text <!-- roles/ghost/inline.md --> text, then `roles/ghost/after.md`\n")
        self.assertEqual(self.p.missing(text), [
            "line 15: 'roles/ghost/after.md' is in neither the spec nor an overlay tier of this file's cascade"])

    def test_an_unclosed_fence_runs_to_the_end(self):
        self.assertEqual(self.p.missing("```\n`roles/ghost/a.md`\n"), [])

    def test_prose_lines_keep_the_file_s_line_numbers(self):
        lines = prose_lines("a\n<!--\nb\n-->\n```\nc\n```\nd\n")
        self.assertEqual(lines, ["a", "    ", " ", "   ", "", "", "", "d", ""])


class ThemeTests(unittest.TestCase):
    TEXT = "`roles/engineering/architect.md` (@Slartibartfast), `roles/engineering/lead-backend.md` (@Heisenberg)\n"

    def setUp(self):
        self.p = Project(self)
        self.p.write(self.p.base / "agents/personalities/breaking-bad/characters.md",
                     "| Role | Character | Alias |\n|---|---|---|\n| `lead-backend` | Walter | @Heisenberg |\n")

    def test_the_theme_given_is_the_one_read(self):
        self.assertEqual(self.p.missing(self.TEXT, theme="h2g2"), [
            "line 1: '@Heisenberg' is no character of the theme 'h2g2' — its characters.md does not name it"])
        self.assertEqual(self.p.missing(self.TEXT, theme="breaking-bad"), [
            "line 1: '@Slartibartfast' is no character of the theme 'breaking-bad' — its characters.md does not "
            "name it"])

    def test_no_theme_given_reads_every_theme_of_the_tiers(self):
        self.p.write(self.p.root / "agents/personalities/acme/characters.md",
                     "| Role | Character | Alias |\n|---|---|---|\n| `architect` | Road Runner | @Road-Runner |\n")
        text = self.TEXT + "`architect` (@Road-Runner), `architect` (@Nobody)\n"
        self.assertEqual(self.p.missing(text, theme=None),
                         ["line 2: '@Nobody' is named by no theme's characters.md"])

    def test_a_project_s_own_theme(self):
        self.p.write(self.p.root / "agents/personalities/acme/characters.md",
                     "| Role | Character | Alias |\n|---|---|---|\n| `architect` | Road Runner | @Road-Runner |\n")
        self.assertEqual(self.p.missing("`architect` (@Road-Runner)\n", theme="acme"), [])

    def test_no_characters_to_check_against(self):
        # The theme none, or a theme no tier holds: nothing to say of a character.
        self.assertEqual(self.p.missing(self.TEXT, theme="none"), [])
        self.assertEqual(self.p.missing(self.TEXT, theme="missing-theme"), [])


class ThisRepositoryTests(unittest.TestCase):
    def test_the_base_s_own_files_name_nothing_missing(self):
        # Every file the spec ships, read as a project's file would be: its references all hold.
        tiers = Tiers(str(REPO), [], "h2g2")
        found = {}
        for path in sorted((REPO / "agents").rglob("*.md")):
            gone = missing(path.read_text(encoding="utf-8"), tiers)
            if gone:
                found[str(path.relative_to(REPO))] = [m.message for m in gone]
        self.assertEqual(found, {})

    def test_the_base_s_workflows_dispatch_in_a_form_the_rule_counts(self):
        text = (REPO / "agents/workflows/intelligence/market-study.md").read_text(encoding="utf-8")
        tiers = Tiers(str(REPO), [], "h2g2")
        self.assertIn(("layer", "roles/product/market-analyst.md"),
                      [(r.kind, r.name) for r in references.references(text, tiers.role_exists)])


class ValidateTests(unittest.TestCase):
    """The check in the run: a warning, which only --strict fails on."""

    def setUp(self):
        self.p = Project(self)
        self.p.write(self.p.root / "agents/workflows/product/launch.md",
                     "# Launch\n\n| 1 | `roles/product/ghost.md` (@Hactar) |\n")

    def run_validate(self, strict):
        out = io.StringIO()
        code = validate(str(self.p.root), str(self.p.base), "", strict, out, Colors(False), theme="h2g2")
        return code, out.getvalue()

    def test_a_custom_addition_is_checked(self):
        code, out = self.run_validate(False)
        self.assertEqual(code, 0)
        self.assertIn("ℹ agents/workflows/product/launch.md (custom addition — no cortex base, skipping overlay "
                      "checks)\n⚠ agents/workflows/product/launch.md\n  REFERENCE_NOT_FOUND — line 3: "
                      "'roles/product/ghost.md' is in neither the spec nor an overlay tier of this file's cascade\n",
                      out)
        self.assertIn("Warnings: 1\n", out)

    def test_strict_fails_on_it(self):
        code, out = self.run_validate(True)
        self.assertEqual(code, 1)
        self.assertIn("Strict mode: warnings count as errors.\n", out)

    def test_a_file_in_error_is_not_checked_further(self):
        self.p.write(self.p.root / "agents/roles/engineering/ghost.md",
                     "<!-- OVERLAY\n Base: cortex/agents/roles/engineering/ghost.md\n Scope: workspace\n"
                     " Semantic: additive\n-->\n`roles/x/ghost.md`\n")
        _, out = self.run_validate(False)
        self.assertIn("BASE_NOT_FOUND", out)
        self.assertNotIn("roles/x/ghost.md", out)

    def test_a_service_file_reads_its_own_tier(self):
        self.p.write(self.p.root / "svc/project-overview.md", "# Service\n")
        self.p.write(self.p.root / "svc/agents/roles/product/ghost.md", "# The service's\n")
        self.p.write(self.p.root / "agents/roles/team/mine.md", "# The team's\n")
        self.p.write(self.p.root / "svc/agents/workflows/x/flow.md",
                     "`roles/product/ghost.md` (@Hactar), `roles/team/mine.md`, `roles/engineering/architect.md`\n")
        _, out = self.run_validate(False)
        self.assertNotIn("⚠ svc/agents/workflows/x/flow.md", out)
        self.assertIn("⚠ agents/workflows/product/launch.md", out)


if __name__ == "__main__":
    unittest.main()
