"""Reading a changelog and its notes."""

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from changelog import DEFAULT_SECTION, first_after_unreleased, has_section, headings, note_version, section_text  # noqa: E402

TEXT = """# Changelog

## [Unreleased]

### Added
- a thing

## [1.4.0] - 2026-11-03 — A Name _(Released)_
[Full notes](changelog/1.4.0.md)

### Fixed
- another

## [1.3.0] - 2026-11-01 — Older _(Released)_
"""


class ChangelogTests(unittest.TestCase):
    def test_headings_in_order(self):
        self.assertEqual([label for label, _ in headings(TEXT)], ["Unreleased", "1.4.0", "1.3.0"])

    def test_the_first_section_after_unreleased(self):
        self.assertEqual(first_after_unreleased(TEXT), "## [1.4.0] - 2026-11-03 — A Name _(Released)_")
        self.assertEqual(first_after_unreleased("## [2.0.0] - 2026-01-01 — X _(Released)_"),
                         "## [2.0.0] - 2026-01-01 — X _(Released)_")
        self.assertIsNone(first_after_unreleased("# Changelog\n\n## [Unreleased]\n"))

    def test_the_default_section_reads_version_and_date(self):
        match = re.fullmatch(DEFAULT_SECTION, first_after_unreleased(TEXT))
        self.assertEqual((match.group("version"), match.group("date"), match.group("name")), ("1.4.0", "2026-11-03", "A Name"))
        self.assertIsNone(re.fullmatch(DEFAULT_SECTION, "## [1.4.0] - 2026-11-03 — A Name"))

    def test_a_released_heading_is_read_without_its_trailing_white_space(self):
        # Bluspark's fourth pass: a space at the end of the heading is no part of what the default shape reads.
        heading = "## [1.4.0] - 2026-11-03 — A Name _(Released)_"
        self.assertEqual(first_after_unreleased(f"## [Unreleased]\n\n{heading} \t\n"), heading)
        self.assertIsNotNone(re.fullmatch(DEFAULT_SECTION, first_after_unreleased(f"{heading} \n")))

    def test_has_section(self):
        self.assertTrue(has_section(TEXT, "1.3.0"))
        self.assertFalse(has_section(TEXT, "1.3"))

    def test_a_section_body(self):
        self.assertEqual(section_text(TEXT, "Unreleased"), "### Added\n- a thing")
        self.assertEqual(section_text(TEXT, "1.3.0"), "")
        self.assertIsNone(section_text(TEXT, "9.9.9"))

    def test_note_versions(self):
        self.assertEqual(note_version("changelog/1.4.0.md", "changelog"), "1.4.0")
        self.assertEqual(note_version("docs/notes/1.4.0.md", "docs/notes/"), "1.4.0")
        for path in ("changelog/1.4.0-rc.1.md", "changelog/README.md", "changelog/old/1.0.0.md",
                     "other/1.4.0.md", "changelog/01.4.0.md"):
            with self.subTest(path=path):
                self.assertIsNone(note_version(path, "changelog"))


class MarkdownTests(unittest.TestCase):
    """Bluspark's third pass: the gate reads the sections the CLI's development version reads, as Markdown has
    them."""

    def test_a_fenced_section_is_none(self):
        text = ("## [Unreleased]\n\n### Fixed\n- a fix, its example:\n\n  ```\n"
                "  ## [9.9.9] - 2026-11-01 — X _(Released)_\n  ```\n\n"
                "<!--\n## [9.9.8] - 2026-11-01 — X _(Released)_\n-->\n\n"
                "## [1.4.0] - 2026-11-03 — A Name _(Released)_\n")
        self.assertEqual([label for label, _ in headings(text)], ["Unreleased", "1.4.0"])
        self.assertEqual(first_after_unreleased(text), "## [1.4.0] - 2026-11-03 — A Name _(Released)_")
        self.assertFalse(has_section(text, "9.9.9"))
        self.assertFalse(has_section(text, "9.9.8"))
        # The section's body holds its example, as written.
        self.assertIn("  ## [9.9.9] - 2026-11-01", section_text(text, "Unreleased"))

    def test_an_indented_section_and_a_tab_after_its_hashes(self):
        for heading in (" ## [1.8.1] - 2026-11-03 — A Name _(Released)_",
                        "##\t[1.8.1] - 2026-11-03 — A Name _(Released)_"):
            with self.subTest(heading=heading):
                text = (f"## [Unreleased]\n\n{heading}\n### Fixed\n- a\n\n"
                        "## [1.8.0] - 2026-11-01 — Older _(Released)_\n")
                self.assertTrue(has_section(text, "1.8.1"))
                # It is the first section, which the default shape refuses: the gate says so, and names it.
                self.assertEqual(first_after_unreleased(text), heading)
                self.assertIsNone(re.fullmatch(DEFAULT_SECTION, heading))
                self.assertEqual(section_text(text, "1.8.1"), "### Fixed\n- a")

    def test_unreleased_is_read_in_any_case_as_the_cli_reads_it(self):
        # The counter-review of the third pass: the CLI read [unreleased] as [Unreleased], the gate did not.
        for label in ("Unreleased", "unreleased", "UNRELEASED"):
            with self.subTest(label=label):
                text = TEXT.replace("[Unreleased]", f"[{label}]")
                self.assertEqual(first_after_unreleased(text), "## [1.4.0] - 2026-11-03 — A Name _(Released)_")
                self.assertEqual(section_text(text, "Unreleased"), "### Added\n- a thing")
        # Another label is matched as written.
        self.assertIsNone(section_text(TEXT.replace("[1.4.0]", "[V1.4.0]"), "v1.4.0"))

    @unittest.skipUnless((HERE.parents[3] / "cli" / "cortex_cli").is_dir(), "not in the repository of the CLI")
    def test_the_cli_reads_through_the_same_file(self):
        # One rule: cli/cortex_cli/markdown_lines.py and this lib's are the same, byte for byte.
        self.assertEqual((HERE.parent / "markdown_lines.py").read_bytes(),
                         (HERE.parents[3] / "cli" / "cortex_cli" / "markdown_lines.py").read_bytes())


@unittest.skipUnless((HERE.parents[3] / "CHANGELOG.md").is_file(), "not in the repository that calls the actions")
class ThisRepositoryTests(unittest.TestCase):
    def test_every_released_section_of_this_changelog_has_the_default_shape(self):
        text = (HERE.parents[3] / "CHANGELOG.md").read_text(encoding="utf-8")
        released = [line for label, line in headings(text) if label != "Unreleased"]
        self.assertGreater(len(released), 10)
        spent = " _(Not published)_"   # a version whose draft was never published (ADR-009 §3.3)
        for line in released[:12]:
            with self.subTest(line=line):
                shaped = line[:-len(spent)] + " _(Released)_" if line.endswith(spent) else line
                self.assertIsNotNone(re.fullmatch(DEFAULT_SECTION, shaped))


if __name__ == "__main__":
    unittest.main()
