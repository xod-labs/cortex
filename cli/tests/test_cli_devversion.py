"""The version a build of no release prepares, read from the changelog — ADR-009 §9, 2026-11-09.

The computation (``cortex_cli.devversion``); a source checkout's ``--version``, which reads it from
its own ``CHANGELOG.md``; and ``cli/build.py --development-version``, which stamps the CI's builds.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the harness, not a package named tests
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "core")]

import cli_harness as harness  # noqa: E402
from cortex_cli import devversion, markdown_lines  # noqa: E402
from cortex_cli.semver import Version  # noqa: E402

HEAD = "# Changelog\n\nAll notable changes.\n\n"


def changelog(unreleased="", *sections):
    """A changelog: ``[Unreleased]`` holding ``unreleased``, then a section per version, in the order given."""
    text = HEAD + "## [Unreleased]\n\n" + unreleased
    for version in sections:
        text += f"\n## [{version}] - 2026-11-01 — A Name _(Released)_\n[Full notes](changelog/{version}.md)\n\n" \
                "### Fixed\n- **Something.** It was broken.\n"
    return text


def prepared(text):
    return str(devversion.prepared(text))


class BaseTests(unittest.TestCase):
    """The base is the highest version among the sections, by semver order."""

    def test_a_fix_carried_up_from_an_older_line_never_lowers_it(self):
        # On a 3.x branch, 1.7.1's section, carried up from the line 1.x, sits among the others: wherever it is.
        text = changelog("### Fixed\n- a fix\n", "1.7.1", "3.0.0", "2.4.0", "1.7.0", "1.6.0")
        self.assertEqual(max(devversion.released(text)), Version("3.0.0"))
        self.assertEqual(prepared(text), "3.0.1")
        self.assertEqual(prepared(changelog("### Added\n- a thing\n", "2.4.1", "1.7.2", "3.0.0")), "3.1.0")

    def test_a_line_s_own_changelog_prepares_its_next_patch(self):
        # The line 1.x, branched at 1.7.0: its changelog holds 1.x's sections only.
        self.assertEqual(prepared(changelog("### Fixed\n- a fix\n", "1.7.1", "1.7.0", "1.6.0")), "1.7.2")

    def test_a_pre_release_above_the_base_is_a_floor(self):
        # The higher of its release and the bump's: a removal after 3.1.0-rc.1 breaks 3.0.0's users.
        for unreleased, expected in (("", "3.1.0"), ("### Fixed\n- a fix\n", "3.1.0"), ("### Added\n- a thing\n", "3.1.0"),
                                     ("### Removed\n- a thing\n", "4.0.0"), ("### Changed\n- 💥 a break\n", "4.0.0")):
            with self.subTest(unreleased=unreleased):
                self.assertEqual(prepared(changelog(unreleased, "3.1.0-rc.1", "3.0.0")), expected)
        self.assertEqual(prepared(changelog("", "3.0.0-beta.2", "3.0.0-alpha.1", "2.9.0")), "3.0.0")
        self.assertEqual(prepared(changelog("### Added\n- a thing\n", "3.0.1-rc.1", "3.0.0")), "3.1.0")
        self.assertEqual(prepared(changelog("### Removed\n- a thing\n", "4.0.0-rc.2", "3.0.0")), "4.0.0")
        self.assertEqual(prepared(changelog("", "1.0.0-rc.1")), "1.0.0")                   # no release yet

    def test_a_pre_release_is_a_floor_wherever_its_section_is(self):
        # A section carried up from an older line written first: the floor below it still counts.
        self.assertEqual(prepared(changelog("### Fixed\n- a fix\n", "2.9.5", "3.1.0-rc.1", "3.0.0")), "3.1.0")
        self.assertEqual(prepared(changelog("### Fixed\n- a fix\n", "3.0.0", "3.1.0-rc.1")), "3.1.0")

    def test_a_pre_release_below_a_release_is_no_base(self):
        self.assertEqual(prepared(changelog("### Added\n- a thing\n", "2.0.0", "2.0.0-rc.1", "1.9.0")), "2.1.0")

    def test_a_changelog_with_no_version_starts_from_0_0_0(self):
        self.assertEqual(prepared(changelog()), "0.0.1")
        self.assertEqual(prepared(changelog("### Added\n- a thing\n")), "0.1.0")
        self.assertEqual(prepared(changelog("### Removed\n- a thing\n")), "1.0.0")
        self.assertEqual(prepared(""), "0.0.1")

    def test_what_is_no_version_is_no_base(self):
        text = HEAD + "## [Unreleased]\n\n## [nightly]\n\n## [1.0] - old\n\n## [v2.0.0] - old\n\n## [1.2.0] - 2026-01-01\n" \
                      "\n## 9.0.0 - not a section\n\n### [8.0.0]\n"
        self.assertEqual(devversion.released(text), [Version("1.2.0")])
        self.assertEqual(prepared(text), "1.2.1")


class BumpTests(unittest.TestCase):
    """The bump comes from [Unreleased]: Removed or a breaking entry, a major; Added, Changed or
    Deprecated, a minor; else a patch."""

    def test_each_kind(self):
        for unreleased, expected in (
                ("", "2.3.5"),                                                   # nothing yet
                ("### Fixed\n- a fix\n", "2.3.5"),
                ("### Security\n- a hole closed\n", "2.3.5"),
                ("### Fixed\n- a fix\n\n### Security\n- a hole closed\n", "2.3.5"),
                ("### Added\n- a thing\n", "2.4.0"),
                ("### Changed\n- a thing\n", "2.4.0"),
                ("### Deprecated\n- a thing\n", "2.4.0"),
                ("### Fixed\n- a fix\n\n### Added\n- a thing\n", "2.4.0"),
                ("### Removed\n- a thing\n", "3.0.0"),
                ("### Added\n- a thing\n\n### Removed\n- a thing\n", "3.0.0"),
                ("### Changed\n- 💥 **A breaking change.** Said so.\n", "3.0.0"),
                ("### Changed\n- **💥 A breaking change.** Said so.\n", "3.0.0"),
                ("### Fixed\n- 💥 a breaking fix\n", "3.0.0"),
                ("### Notes\n- a note\n", "2.3.5")):                         # no kind of Keep a Changelog
            with self.subTest(unreleased=unreleased):
                self.assertEqual(prepared(changelog(unreleased, "2.3.4", "2.3.3")), expected)

    def test_a_kind_without_an_entry_asks_nothing(self):
        for unreleased in ("### Added\n\n### Fixed\n- a fix\n", "### Removed\n\n", "### Added\nSome words, no entry.\n"):
            with self.subTest(unreleased=unreleased):
                self.assertEqual(prepared(changelog(unreleased, "2.3.4")), "2.3.5")

    def test_an_entry_of_no_kind_asks_nothing(self):
        text = changelog("- 💥 a line before any kind\n\n### Fixed\n- a fix\n", "2.3.4")
        self.assertEqual(devversion.unreleased(text), {"Fixed": ["a fix"]})
        self.assertEqual(prepared(text), "2.3.5")

    def test_only_an_entry_s_first_word_marks_it_breaking(self):
        text = changelog("### Fixed\n- a fix, where 💥 marked a breaking change\n  💥 its second line\n", "2.3.4")
        self.assertEqual(prepared(text), "2.3.5")

    def test_entries_of_released_sections_ask_nothing(self):
        text = changelog("### Fixed\n- a fix\n", "2.3.4") + "\n### Removed\n- 💥 old, released\n"
        self.assertEqual(prepared(text), "2.3.5")
        text = HEAD + "## [2.3.4] - 2026-11-01 — A Name _(Released)_\n\n### Removed\n- 💥 a thing\n"
        self.assertEqual(prepared(text), "2.3.5")                                # no [Unreleased]: nothing yet

    def test_a_kind_is_read_in_any_case_an_emoji_or_a_note_around_it(self):
        for heading, expected in (("### ✨ Added", "2.4.0"), ("### Added (CLI)", "2.4.0"), ("### added", "2.4.0"),
                                  ("### 🔥 REMOVED", "3.0.0"), ("### Fixed — the CLI", "2.3.5"),
                                  ("### Additions", "2.3.5")):                          # no kind of Keep a Changelog
            with self.subTest(heading=heading):
                self.assertEqual(prepared(changelog(f"{heading}\n- a thing\n", "2.3.4")), expected)
        text = changelog("### ✨ Added\n-\ta thing\n", "2.3.4")
        self.assertEqual(devversion.unreleased(text), {"Added": ["a thing"]})
        self.assertEqual(prepared(text.replace("## [Unreleased]", "## [unreleased]")), "2.4.0")

    def test_a_heading_is_three_hashes_and_a_space(self):
        # `####` is a heading of its own, under the kind; `###Added`, no heading at all (CommonMark).
        for unreleased in ("### Fixed\n- a fix\n#### Added notes\n- a note\n", "### Fixed\n- a fix\n###Added\n- a note\n",
                           "### Fixed\n- a fix\n####Removed\n- a note\n"):
            with self.subTest(unreleased=unreleased):
                self.assertEqual(prepared(changelog(unreleased, "2.3.4")), "2.3.5")

    def test_an_empty_heading_names_no_kind(self):
        # CommonMark: `###` alone is a heading, with no text — the entries below it are of no kind, which asks nothing.
        self.assertEqual(devversion.unreleased(changelog("### Added\n###\n- old\n")), {"": ["old"]})
        self.assertEqual(prepared(changelog("### Added\n###\n- old\n", "2.3.4")), "2.3.5")
        self.assertEqual(prepared(changelog("### Added\n   ###\n- 💥 old\n", "2.3.4")), "3.0.0")
        self.assertEqual(prepared(changelog("### Added\n###x\n- old\n", "2.3.4")), "2.4.0")

    def test_a_heading_marked_breaking_is_a_major(self):
        for heading in ("### 💥 Breaking", "### Breaking changes", "### 💥 Changed", "### BREAKING"):
            with self.subTest(heading=heading):
                self.assertEqual(prepared(changelog(f"{heading}\n- a thing\n", "2.3.4")), "3.0.0")
        self.assertEqual(devversion.unreleased(changelog("### 💥 Changed\n- a thing\n")), {"Breaking": ["a thing"]})
        # The word first, or the mark: a heading that says a change is not breaking is its kind.
        for heading, bumped in (("### Fixed (no breaking change)", "2.3.5"), ("### Non-breaking changes", "2.3.5"),
                                ("### Changed (non-breaking)", "2.4.0"), ("### ✨ Breaking", "3.0.0")):
            with self.subTest(heading=heading):
                self.assertEqual(prepared(changelog(f"{heading}\n- a thing\n", "2.3.4")), bumped)

    def test_markdown_s_headings_entries_and_line_ends(self):
        # A heading indented up to three spaces is one; four make it code, no heading.
        self.assertEqual(prepared(changelog("   ### Added\n- a thing\n", "2.3.4")), "2.4.0")
        self.assertEqual(prepared(changelog("### Fixed\n- a fix\n    ### Removed\n- a thing\n", "2.3.4")), "2.3.5")
        self.assertEqual(prepared(HEAD + "## [Unreleased]\n\n### Fixed\n- a fix\n\n  ## [2.3.4] - 2026-11-01\n"), "2.3.5")
        # An entry starts with -, * or +.
        self.assertEqual(devversion.unreleased(changelog("### Added\n+ a thing\n")), {"Added": ["a thing"]})
        # A line ends at a line feed or a carriage return alone: not at U+2028, U+0085, a form feed.
        for separator in ("\u2028", "\u0085", "\x0c", "\x1e"):
            with self.subTest(separator=repr(separator)):
                text = changelog(f"### Fixed\n- a fix{separator}## [99.0.0] - not a section\n", "2.3.4")
                self.assertEqual(prepared(text), "2.3.5")
        self.assertEqual(prepared(changelog("### Added\r- a thing\r", "2.3.4")), "2.4.0")

    def test_an_indented_entry_a_tab_after_the_hashes_and_a_kind_named_first(self):
        # Bluspark's second pass: an entry indented up to three spaces is one, four make it its entry's text; a tab
        # after ### makes a heading; the kind is the heading's first word — a note that names one after it is none.
        for indent, expected in (("", "2.4.0"), (" ", "2.4.0"), ("   ", "2.4.0"), ("    ", "2.3.5")):
            with self.subTest(indent=repr(indent)):
                self.assertEqual(prepared(changelog(f"### Fixed\n- a fix\n\n### Added\n{indent}- a thing\n", "2.3.4")),
                                 expected)
        self.assertEqual(prepared(changelog("###\tAdded\n- a thing\n", "2.3.4")), "2.4.0")
        self.assertEqual(prepared(changelog("### \tRemoved\n- a thing\n", "2.3.4")), "3.0.0")
        for heading in ("### Notes: nothing removed", "### What changed", "### Docs, added later"):
            with self.subTest(heading=heading):
                self.assertEqual(prepared(changelog(f"{heading}\n- a thing\n", "2.3.4")), "2.3.5")
        self.assertEqual(devversion.unreleased(changelog("### Notes: nothing removed\n- a thing\n")),
                         {"Notes: nothing removed": ["a thing"]})

    def test_a_fenced_code_block_holds_no_section_heading_nor_entry(self):
        # An example of a changelog, fenced, is none of this one's: its sections are no release, its headings and
        # entries ask nothing — backticks or tildes, indented up to three spaces, closed by as many or more.
        example = "```markdown\n## [99.0.0] - 2026-11-01\n### Removed\n- a thing\n```\n"
        self.assertEqual(prepared(changelog("### Fixed\n- a fix, shown:\n\n" + example, "2.3.4")), "2.3.5")
        self.assertNotIn(Version("99.0.0"), devversion.released(changelog(example, "2.3.4")))
        for fence, close in (("~~~", "~~~"), ("\nNotes:\n\n  ````", "`````"), ("```", "   ```")):
            with self.subTest(fence=fence):
                text = changelog(f"### Fixed\n- a fix\n{fence}\n### Removed\n- a thing\n{close}\n### Added\n- one\n",
                                 "2.3.4")
                self.assertEqual(devversion.unreleased(text), {"Fixed": ["a fix"], "Added": ["one"]})
        # A fence closes on its own character, as many or more, and nothing after it: until then, all is code.
        text = changelog("### Fixed\n- a fix\n````\n```\n~~~~\n```` x\n### Removed\n- a thing\n````\n", "2.3.4")
        self.assertEqual(prepared(text), "2.3.5")
        text = changelog("### Fixed\n- a fix\n~~~\n```\n### Removed\n- a thing\n~~~\n", "2.3.4")
        self.assertEqual(prepared(text), "2.3.5")
        # Backticks in the info string make no fence: a line of code spans, read as Markdown — the fence below it opens
        # a block of its own, which hides nothing.
        self.assertEqual(prepared(changelog("### Fixed\n- a fix\n``` a `b` c\n### Added\n- a thing\n```\n", "2.3.4")),
                         "2.4.0")
        # Indented four spaces, it is code, and no fence; two backticks are none either: what follows is read.
        for line in ("    ```", "``", "~~"):
            with self.subTest(line=line):
                text = changelog(f"### Fixed\n- a fix\n{line}\n### Added\n- a thing\n{line}\n", "2.3.4")
                self.assertEqual(prepared(text), "2.4.0")

    def test_a_fence_never_closed_hides_nothing(self):
        # Opened in an entry and never closed — Markdown closes it with the list item: the sections below are read,
        # and the branch prepares the release after the last one, not 0.0.1.
        text = changelog("### Fixed\n- a fix:\n  ```toml\n  version = \"1\"\n", "2.3.4")
        self.assertEqual(devversion.released(text), [Version("2.3.4")])
        self.assertEqual(prepared(text), "2.3.5")
        # A fence closed later still holds what it holds.
        self.assertEqual(prepared(changelog("### Fixed\n- a fix\n```\n### Removed\n```\n", "2.3.4")), "2.3.5")

    def test_a_fence_in_a_list_item_ends_with_the_item(self):
        # As CommonMark reads it: a fence opened in an entry ends at the first line indented less — the item's end —
        # never at a later fence of its character, the next example's.
        text = ("## [Unreleased]\n### Fixed\n- a fix:\n  ```toml\n  version = \"1\"\n## [2.3.4] - 2026-01-01\n### Fixed\n"
                "- b, its example:\n  ```\n  ## [9.9.9] - example\n  ```\n## [2.3.3] - 2025-01-01\n")
        self.assertEqual(devversion.released(text), [Version("2.3.4"), Version("2.3.3")])
        self.assertEqual(prepared(text), "2.3.5")
        text = "## [Unreleased]\n### Fixed\n- a:\n  ~~~\n  x\n### Added\n- b\n  ~~~\n## [2.3.4] - 2026-01-01\n"
        self.assertEqual(devversion.unreleased(text), {"Fixed": ["a:"], "Added": ["b"]})
        self.assertEqual(prepared(text), "2.4.0")
        # Open to the end of the file, it holds what the item holds there: an indented heading is code.
        text = "## [2.3.4] - x\n\n## [Unreleased]\n### Fixed\n- a:\n  ```\n  ### Added\n  - b\n"
        self.assertEqual(prepared(text), "2.3.5")
        # A blank line keeps the item open, and its fence closed in it holds what it holds.
        text = "## [Unreleased]\n### Fixed\n- a:\n\n  ```\n  ## [9.9.9]\n\n  ```\n### Added\n- b\n## [2.3.4] - x\n"
        self.assertEqual(devversion.released(text), [Version("2.3.4")])
        self.assertEqual(prepared(text), "2.4.0")

    def test_a_fence_in_an_item_is_bounded_by_the_item_s_content(self):
        # Bluspark's third pass. Above the fence, a line of the item's text, as indented as it: the fence is still the
        # item's, and ends with it — never left open at the top level, where it would hide nothing.
        text = "## [Unreleased]\n### Fixed\n- a:\n  its text\n  ```\n  ## [9.9.9]\n- b\n## [2.3.4] - x\n"
        self.assertEqual(devversion.released(text), [Version("2.3.4")])
        # Indented one space under an item whose content is at two, a fence is the top level's: it holds what follows
        # to its closing fence, a line at column 0 included.
        text = "## [Unreleased]\n### Fixed\n- a\n ```\n## [9.9.9]\n### Removed\n- b\n ```\n## [2.3.4] - x\n"
        self.assertEqual(devversion.released(text), [Version("2.3.4")])
        self.assertEqual(prepared(text), "2.3.5")
        # Indented past the item's content, it is still the item's: a line as indented as the content is its code.
        text = "## [Unreleased]\n### Fixed\n- a\n   ```\n  ## [9.9.9]\n  ### Removed\n   ```\n## [2.3.4] - x\n"
        self.assertEqual(devversion.released(text), [Version("2.3.4")])
        self.assertEqual(prepared(text), "2.3.5")
        # Its closing fence is read past the item's content, up to three spaces: what follows it is read.
        text = "## [2.3.4] - x\n## [Unreleased]\n### Fixed\n- a\n  ```\n  x\n    ```\n  ### Removed\n  - b\n"
        self.assertEqual(prepared(text), "3.0.0")

    def test_a_fence_closes_as_indented_as_it_may_and_on_as_many_marks(self):
        # Bluspark's third pass: a closing fence indented up to three spaces closes the block — the fence at column 0
        # below, never closed, then hides nothing; a shorter one never closes a longer one.
        text = changelog("### Fixed\n- a fix\n\nNotes:\n\n  ```\n  x\n  ```\n### Removed\n- a thing\n```\n", "2.3.4")
        self.assertEqual(prepared(text), "3.0.0")
        text = changelog("### Fixed\n- a fix\n\n````\n```\n### Removed\n- a thing\n```\n````\n", "2.3.4")
        self.assertEqual(prepared(text), "2.3.5")

    def test_an_html_comment_holds_no_section_heading_nor_entry(self):
        # Bluspark's third pass: from <!-- to the line that holds -->, as Markdown hides it.
        for comment in ("<!--\n### Removed\n- a thing\n## [9.9.9]\n-->",
                        "\nNotes:\n\n  <!-- a note\n### Removed\n- a thing -->",
                        "- a fix\n  <!--\n  ### Removed\n  - a thing\n  -->"):
            with self.subTest(comment=comment):
                text = changelog(f"### Fixed\n- a fix\n{comment}\n", "2.3.4")
                self.assertEqual(devversion.released(text), [Version("2.3.4")])
                self.assertEqual(prepared(text), "2.3.5")
        # On one line, it hides that line only; never closed at the top level, it hides nothing.
        self.assertEqual(prepared(changelog("<!-- ### Removed -->\n### Added\n- a thing\n", "2.3.4")), "2.4.0")
        text = changelog("### Fixed\n- a fix\n\n<!-- a note\n\n### Added\n- a thing\n", "2.3.4")
        self.assertEqual(prepared(text), "2.4.0")

    def test_an_entry_is_an_item_of_the_section_s_own_list(self):
        # Bluspark's third pass: an item in an entry is its text — its 💥 marks no entry, its kind asks nothing more.
        for kind, bumped in (("Changed", "2.4.0"), ("Fixed", "2.3.5")):
            for nested in ("- a\n  - 💥 b\n", "- a\n\n  - 💥 b\n", "- a\nlazily continued\n  - 💥 b\n",
                           "- a\n  - b\n    - 💥 c\n", "-   a\n    - 💥 b\n"):
                with self.subTest(kind=kind, nested=nested):
                    text = changelog(f"### {kind}\n{nested}", "2.3.4")
                    self.assertEqual(prepared(text), bumped)
        self.assertEqual(devversion.unreleased(changelog("### Fixed\n- a\n  - b\n* c\n")), {"Fixed": ["a", "c"]})
        # In an ordered item — no entry of its own — too.
        self.assertEqual(prepared(changelog("### Fixed\n- a\n\n### Changed\n1. a\n   - 💥 b\n", "2.3.4")), "2.3.5")
        # Less indented than the content of the entry above, an item is the list's own: an entry.
        for sibling in ("- a\n - 💥 b\n", " - a\n- 💥 b\n", "-   a\n   - 💥 b\n"):
            with self.subTest(sibling=sibling):
                self.assertEqual(prepared(changelog(f"### Fixed\n{sibling}", "2.3.4")), "3.0.0")
        # A paragraph after a code block is not the entry's: the list ends there, and the item below starts one.
        text = changelog("### Fixed\n- a\n  ```\n  x\n  ```\ntext\n  - 💥 b\n", "2.3.4")
        self.assertEqual(prepared(text), "3.0.0")
        # A thematic break is no entry.
        self.assertEqual(prepared(changelog("### Removed\n- - -\n", "2.3.4")), "2.3.5")

    def test_what_opens_or_closes_a_block_and_what_does_not(self):
        # Indented four spaces from its container, a fence is code: the fence at column 0 below opens the block.
        text = changelog("### Fixed\n- a fix\n\nNotes:\n\n    ```\n### Removed\n- a thing\n```\n", "2.3.4")
        self.assertEqual(prepared(text), "3.0.0")
        # Nor does one indented four spaces close it.
        text = changelog("### Fixed\n- a fix\n\n```\nx\n    ```\n### Removed\n- a thing\n```\n", "2.3.4")
        self.assertEqual(prepared(text), "2.3.5")
        # `<!-->` is a comment of its own, closed on its line.
        text = changelog("### Fixed\n- a fix\n\n<!-->\n### Removed\n- a thing\n-->\n", "2.3.4")
        self.assertEqual(prepared(text), "3.0.0")
        # A blank line in an item's block keeps it open.
        text = "## [Unreleased]\n### Fixed\n- a:\n  ```\n\n  ## [9.9.9]\n  ```\n### Added\n- b\n## [2.3.4] - x\n"
        self.assertEqual(devversion.released(text), [Version("2.3.4")])
        # A fence on the item's own line opens the item's block.
        text = "## [Unreleased]\n### Fixed\n- ```\n  ## [9.9.9]\n  ```\n## [2.3.4] - x\n"
        self.assertEqual(devversion.released(text), [Version("2.3.4")])
        # Five spaces after the marker: the item's content is one past it, code — what is indented two is in it.
        self.assertEqual(prepared(changelog("### Fixed\n-     a\n  - 💥 b\n", "2.3.4")), "2.3.5")
        # No space after the marker, no item: what follows is the list's own.
        self.assertEqual(prepared(changelog("### Fixed\n-a\n  - 💥 b\n", "2.3.4")), "3.0.0")
        # An empty item holds no paragraph: a line at column 0 ends it, and the item below starts a list.
        self.assertEqual(prepared(changelog("### Fixed\n-\ntext\n  - 💥 b\n", "2.3.4")), "3.0.0")

    def test_every_line_is_read_once_with_what_markdown_makes_of_it(self):
        text = "### Fixed\n- a\nlazy\n  - b\n  ```\n  x\n  ```\n- ```\n  y\n  ```\n\n<!--\nz\n-->\ntext\n"
        self.assertEqual([tuple(line) for line in markdown_lines.lines(text)], [
            ("### Fixed", False, False, False), ("- a", False, False, True), ("lazy", False, True, False),
            ("  - b", False, True, True), ("  ```", True, True, False), ("  x", True, True, False),
            ("  ```", True, True, False), ("- ```", False, False, True), ("  y", True, True, False),
            ("  ```", True, True, False), ("", False, True, False), ("<!--", True, False, False),
            ("z", True, False, False), ("-->", True, False, False), ("text", False, False, False),
            ("", False, False, False)])

    def test_a_section_heading_takes_a_tab(self):
        text = "## [Unreleased]\n\n### Fixed\n- a fix\n\n##\t[2.3.4] - 2026-11-01\n"
        self.assertEqual(devversion.released(text), [Version("2.3.4")])
        self.assertEqual(prepared(text), "2.3.5")
        # With nothing after its ##, it is no heading — as ###Added is none.
        text = "## [Unreleased]\n### Fixed\n- a\n##[2.3.4] - 2026-01-01\n## [2.3.3]\n"
        self.assertEqual(devversion.released(text), [Version("2.3.3")])

    def test_a_byte_order_mark_is_no_part_of_the_text(self):
        text = "\ufeff## [Unreleased]\n\n### Added\n- a thing\n\n## [2.3.4] - 2026-11-01 — A Name _(Released)_\n"
        self.assertEqual(prepared(text), "2.4.0")

    def test_a_block_quote_ends_the_list_and_holds_no_entry(self):
        # The counter-review of the third pass: a block quote's line is no lazy line of the entry above — the list
        # ends there, and an item below starts one. In the entry, as indented as its content, it is the entry's.
        for quote in ("> **Note**\n\n", "> **Note**\n", "> [!NOTE]\n> It moved.\n", "> [!NOTE]\n> It moved.\n\n"):
            with self.subTest(quote=quote):
                self.assertEqual(prepared(changelog(f"### Fixed\n- a\n{quote}  - 💥 b\n", "2.3.4")), "3.0.0")
        self.assertEqual(prepared(changelog("### Fixed\n- a\n  > a note\n  - 💥 b\n", "2.3.4")), "2.3.5")
        # Its own items are none of the section's. Its paragraph, continued lazily or not, is no container of an
        # item's line, which any item then starts — an empty one too; an empty quote holds no paragraph to continue.
        self.assertEqual(prepared(changelog("### Fixed\n- a\n\n> - 💥 b\n> ### Removed\n", "2.3.4")), "2.3.5")
        for quote in ("> a note\n", "> a note\ncontinued lazily\n"):
            with self.subTest(quote=quote):
                self.assertEqual(prepared(changelog(f"### Removed\n{quote}-\n", "2.3.4")), "3.0.0")
        self.assertEqual(prepared(changelog("### Fixed\n- a\n  >\nfoo\n  - 💥 b\n", "2.3.4")), "3.0.0")

    def test_what_a_paragraph_continues_and_what_interrupts_it(self):
        # Indented code is no paragraph: the line below it is no lazy line of the entry, and the list ends there.
        for code in ("- a\n\n      code\n", "-     code\n"):
            with self.subTest(code=code):
                self.assertEqual(prepared(changelog(f"### Fixed\n{code}text\n  - 💥 b\n", "2.3.4")), "3.0.0")
        # Neither an ordered item that does not start at 1, nor an empty one, interrupts a paragraph: below it, an
        # indented item starts the section's list. An item of the list goes on as its sibling.
        for line in ("2. June:", "*", "-"):
            with self.subTest(line=line):
                text = changelog(f"### Fixed\nNotes, as of\n{line}\n   - 💥 b\n", "2.3.4")
                self.assertEqual(prepared(text), "3.0.0")
        self.assertEqual(prepared(changelog("### Fixed\nNotes\n1. x\n   - 💥 b\n", "2.3.4")), "2.3.5")
        self.assertEqual(prepared(changelog("### Fixed\n1. a\n2. b\n   - 💥 c\n", "2.3.4")), "2.3.5")
        # Less indented than the entry's content, an empty item is no line of its paragraph: an entry of its own.
        self.assertEqual(devversion.unreleased(changelog("### Fixed\n- a\n*\n")), {"Fixed": ["a", ""]})
        # A line of - under a paragraph makes it a heading: the empty item below it is the section's.
        self.assertEqual(prepared(changelog("### Removed\nNotes\n-\n-\n  the old flag\n", "2.3.4")), "3.0.0")
        # A top-level fence never closed is a paragraph's line: an empty item does not interrupt it.
        self.assertEqual(prepared(changelog("### Fixed\n- a\n### Removed\n```\n-\n", "2.3.4")), "2.3.5")
        # An empty item ends at a blank line below it: what follows, indented as its content, is no item of it.
        self.assertEqual(prepared(changelog("### Fixed\n-\n\n  - 💥 b\n", "2.3.4")), "3.0.0")

    def test_a_heading_in_an_item_and_a_quote_s_heading_or_code_are_no_paragraph(self):
        # Bluspark's fourth pass: indented past three spaces, as far as an item's content, a heading is the item's —
        # the line below continues no paragraph, and the list ends there. CommonMark, as markdown-it reads it.
        self.assertEqual(prepared(changelog("### Fixed\n2. x\nlazy\n      # h\ntext\n   - 💥 y\n", "2.3.4")), "3.0.0")
        self.assertEqual(prepared(changelog("### Fixed\n2. x\nlazy\n      h\ntext\n   - 💥 y\n", "2.3.4")), "2.3.5")
        # A quote holding a heading, a fence, a break or code holds no paragraph: the line below starts one, which a
        # line of - makes a heading — no entry.
        for quoted in ("# h", "```", "~~~", "---", "___", "    code", "- ```", "> # h", "<!-- c", "-", "1. -", ">     x"):
            with self.subTest(quoted=quoted):
                self.assertEqual(prepared(changelog(f"### Changed\n> {quoted}\ntext\n-\n", "2.3.4")), "2.3.5")
        for quoted in ("text", "- a", "> b", "#tag", "__", "* *x", ">    x"):
            with self.subTest(quoted=quoted):
                self.assertEqual(prepared(changelog(f"### Changed\n> {quoted}\ntext\n-\n", "2.3.4")), "2.4.0")

    def test_a_quote_nested_thousands_of_times_is_read_in_one_pass(self):
        # The counter-review of the fourth pass: each quote or item nested in a quote's line was read again from its
        # start, by recursion — a line of a thousand `> ` raised RecursionError, in the CLI and in the release gate.
        start = time.monotonic()
        for nested in ("> " * 5000, "> " + "1. " * 5000, "> " + "- " * 5000, ">- " * 5000):
            with self.subTest(nested=nested[:6]):
                self.assertEqual(prepared(changelog(f"### Changed\n{nested}text\nlazy\n-\n", "2.3.4")), "2.4.0")
                self.assertEqual(prepared(changelog(f"### Changed\n{nested}# h\ntext\n-\n", "2.3.4")), "2.3.5")
                self.assertEqual(prepared(changelog(f"### Changed\n{nested}```\ntext\n-\n", "2.3.4")), "2.3.5")
        # A thematic break past the items: a break; one more character after it, a paragraph's text.
        self.assertEqual(prepared(changelog("### Changed\n> " + "1. " * 5000 + "* * *\ntext\n-\n", "2.3.4")), "2.3.5")
        self.assertEqual(prepared(changelog("### Changed\n> " + "- " * 5000 + "x\ntext\n-\n", "2.3.4")), "2.4.0")
        self.assertLess(time.monotonic() - start, 5)

    def test_an_html_block_other_than_a_comment_is_read_as_markdown(self):
        # A limit the reader states: CommonMark holds no Markdown in a <pre>, which is read here — its section too.
        text = changelog("### Fixed\n- a\n\n<pre>\n## [9.9.9]\n</pre>\n", "2.3.4")
        self.assertIn("9.9.9", [label for label, _ in markdown_lines.sections(text)])
        self.assertEqual(prepared(text), "9.9.10")

    def test_unreleased_is_read_in_any_case(self):
        text = changelog("### Removed\n- a thing\n", "2.3.4").replace("[Unreleased]", "[unreleased]")
        self.assertEqual(prepared(text), "3.0.0")
        self.assertTrue(markdown_lines.is_unreleased(" UnReleased "))
        self.assertFalse(markdown_lines.is_unreleased("Unreleased 2"))

    def test_an_entry_whose_marker_line_is_empty_counts_for_its_kind(self):
        text = changelog("### Removed\n-\n  the old flag\n", "2.3.4")
        self.assertEqual(devversion.unreleased(text), {"Removed": ["the old flag"]})
        self.assertEqual(prepared(text), "3.0.0")
        # Bluspark's fourth pass: its text is the line below — marked breaking there, the entry is (CommonMark).
        self.assertEqual(prepared(changelog("### Changed\n-\n  💥 the flag's meaning\n", "2.3.4")), "3.0.0")
        self.assertEqual(devversion.unreleased(changelog("### Changed\n-\n  - 💥 b\n- c\n-\n")),
                         {"Changed": ["", "c", ""]})     # an item below, or nothing: no text
        # Not indented as its content, the line below is no text of it: an empty item continues no paragraph.
        self.assertEqual(prepared(changelog("### Changed\n-\n💥 not its text\n", "2.3.4")), "2.4.0")
        # A line ending is CRLF's: no blank line between the marker and its text.
        text = changelog("### Changed\n-\n  💥 the flag's meaning\n", "2.3.4").replace("\n", "\r\n")
        self.assertEqual(prepared(text), "3.0.0")

    def test_what_ends_a_list_and_what_does_not(self):
        # A thematic break of stars or underscores ends the entry's list.
        for rule in ("***", "___", "* * *"):
            with self.subTest(rule=rule):
                self.assertEqual(prepared(changelog(f"### Fixed\n- a\n{rule}\n  - 💥 b\n", "2.3.4")), "3.0.0")
        # A tab reaches the next multiple of four columns: after a marker, it puts the entry's content at column 4 —
        # an item indented two is the list's own, one indented four the entry's.
        self.assertEqual(prepared(changelog("### Fixed\n-\ta\n  - 💥 b\n", "2.3.4")), "3.0.0")
        self.assertEqual(prepared(changelog("### Fixed\n-\ta\n    - 💥 b\n", "2.3.4")), "2.3.5")
        self.assertEqual(prepared(changelog("### Fixed\n- a\n\t- 💥 b\n", "2.3.4")), "2.3.5")
        # A comment opened after text on its line is none: what follows is read.
        self.assertEqual(prepared(changelog("### Fixed\n- a\n\ntext <!--\n### Removed\n- b\n-->\n", "2.3.4")), "3.0.0")
        # Indented four spaces, a heading is the entry's: it keeps the list open.
        self.assertEqual(prepared(changelog("### Fixed\n- a\n\n    ### x\n  - 💥 b\n", "2.3.4")), "2.3.5")
        # A heading of one #, indented up to three spaces, ends the list: an item below starts the section's.
        for heading in ("# Title", "  # Title", "   #"):
            with self.subTest(heading=heading):
                self.assertEqual(prepared(changelog(f"### Fixed\n- a\n{heading}\n  - 💥 b\n", "2.3.4")), "3.0.0")
        # CRLF ends a line, not two: the line below an entry's continues it lazily.
        text = changelog("### Fixed\n- a\nlazy\n  - 💥 b\n", "2.3.4")
        self.assertEqual(prepared(text.replace("\n", "\r\n")), "2.3.5")
        # A fenced block on an item's own line, closed: no paragraph, so the line below ends the list.
        self.assertEqual(prepared(changelog("### Fixed\n- ```\n  x\n  ```\ntext\n  - 💥 b\n", "2.3.4")), "3.0.0")
        # An empty item's content is one column past its marker: an item indented one is its sibling.
        self.assertEqual(prepared(changelog("### Fixed\n-\n - 💥 b\n", "2.3.4")), "3.0.0")
        # A line of other white space than spaces and tabs is no blank line: it is a paragraph's.
        for space in (" ", "\x0c", "\x85"):
            with self.subTest(space=repr(space)):
                self.assertEqual(prepared(changelog(f"### Fixed\n- a\n {space}\nfoo\n  - 💥 b\n", "2.3.4")), "2.3.5")

    def test_a_long_line_or_many_blocks_never_closed_are_read_at_once(self):
        # The counter-review of the third pass: each never-closed block scanned the rest of the file, and a heading's
        # spaces were matched twice — seconds, then minutes, on a few thousand lines.
        # A block never closed leaves a shorter one below to be read: a fence as long or longer closes it.
        text = changelog("### Fixed\n- a\n\n````\n```\n### Removed\n- b\n```\n", "2.3.4")
        self.assertEqual(prepared(text), "2.3.5")
        # A longer fence below a shorter one still closes it: what lies between is code.
        text = changelog("### Fixed\n- a\n\n````\n### Removed\n- b\n```\n`````\n", "2.3.4")
        self.assertEqual(prepared(text), "2.3.5")
        many = "".join(f"{opening}\n" for opening in ("<!--", "```", "~~~~") * 7000)
        start = time.monotonic()
        self.assertEqual(prepared(changelog("### Fixed\n- a\n\n" + many, "2.3.4")), "2.3.5")
        self.assertEqual(prepared(changelog("### Added" + " " * 100000 + "x\n- a\n", "2.3.4")), "2.4.0")
        # Bluspark's fourth pass: fences of a length each shorter than the one above, none closed.
        shorter = "".join("`" * length + "\n" for length in range(6003, 3, -1))
        # Below them, what closes none: a fence indented as code, and one with an info string.
        shorter += "    " + "`" * 7000 + "\n" + "`" * 7000 + "x\n"
        self.assertEqual(prepared(changelog("### Fixed\n- a\n\n" + shorter, "2.3.4")), "2.3.5")
        self.assertLess(time.monotonic() - start, 5)

    def test_entries_and_their_kinds(self):
        text = changelog("### Added\n- **One.** First line\n  and its second.\n* Two\n\n### Fixed\n- Three\n", "1.0.0")
        self.assertEqual(devversion.unreleased(text), {"Added": ["**One.** First line", "Two"], "Fixed": ["Three"]})
        self.assertEqual(devversion.unreleased(text.replace("\n", "\r\n")), devversion.unreleased(text))


class DevelopmentTests(unittest.TestCase):
    TEXT = changelog("### Added\n- a thing\n", "1.7.34")

    def test_a_checkout_s_and_a_ci_build_s(self):
        self.assertEqual(devversion.development(self.TEXT), "1.8.0-dev")
        self.assertEqual(devversion.development(self.TEXT, 0), "1.8.0-dev.0")
        self.assertEqual(devversion.development(self.TEXT, 412), "1.8.0-dev.412")
        self.assertEqual(devversion.development(changelog("### Fixed\n- a fix\n", "1.7.34"), 3), "1.7.35-dev.3")
        for version in ("1.8.0-dev", "1.8.0-dev.412"):
            self.assertLess(Version("1.7.34"), Version(version))
            self.assertLess(Version(version), Version("1.8.0"))

    def test_what_is_no_build_number(self):
        for build in (-1, True, "3", 1.0):
            with self.subTest(build=build), self.assertRaises(ValueError):
                devversion.development(self.TEXT, build)

    def test_this_repository_prepares_a_version_above_every_section(self):
        text = (harness.REPO / "CHANGELOG.md").read_text(encoding="utf-8")
        version = devversion.prepared(text)
        self.assertFalse(version.pre)
        self.assertGreater(version, max(devversion.released(text)))


class CheckoutTests(unittest.TestCase):
    """A source checkout reports the version its own CHANGELOG.md prepares — not the version of the
    checkout the tests run from: a copy of core/ and cli/ beside a changelog of its own."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-devversion-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        ignored = shutil.ignore_patterns("__pycache__", "_stamp.py", "_known_specs.py", "build")
        shutil.copytree(harness.CORE / "cortex_core", self.tmp / "core" / "cortex_core", ignore=ignored)
        shutil.copytree(harness.CLI / "cortex_cli", self.tmp / "cli" / "cortex_cli", ignore=ignored)

    def version(self, **env):
        proc = subprocess.run([sys.executable, "-I", "-S", "-c", harness.SOURCE_CALL, str(self.tmp / "core"),
                               str(self.tmp / "cli"), "--version"],
                              env={**harness.environment(), **env}, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.decode().strip()

    def test_it_reads_its_changelog(self):
        (self.tmp / "CHANGELOG.md").write_text(changelog("### Fixed\n- a fix\n", "1.7.1", "3.0.0"), encoding="utf-8")
        self.assertEqual(self.version(), "cortex 3.0.1-dev")
        (self.tmp / "CHANGELOG.md").write_text(changelog("### Added\n- a thing\n", "3.0.0"), encoding="utf-8-sig")
        self.assertEqual(self.version(), "cortex 3.1.0-dev")                                # a BOM first
        self.assertEqual(self.version(CORTEX_SOURCE_VERSION="9.9.9"), "cortex 9.9.9")    # what its tests stand for

    def test_without_a_changelog_it_says_0_0_0_dev(self):
        self.assertEqual(self.version(), "cortex 0.0.0-dev")

    def test_a_changelog_that_is_no_utf_8_is_none(self):
        (self.tmp / "CHANGELOG.md").write_bytes(b"## [Unreleased]\n\n### Added\n- caf\xe9\n\n## [1.2.3] - 2026-01-01\n")
        self.assertEqual(self.version(), "cortex 0.0.0-dev")


class BuildTests(unittest.TestCase):
    """``cli/build.py --development-version N``: the stamp of the CI's builds of no release."""

    def build(self, *args):
        return subprocess.run([sys.executable, str(harness.CLI / "build.py"), *args], capture_output=True,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def test_it_prints_the_version_the_branch_prepares(self):
        expected = devversion.development((harness.REPO / "CHANGELOG.md").read_text(encoding="utf-8"), 57)
        proc = self.build("--development-version", "57")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # LF alone, on Windows too: `$(…)` keeps the "\r" of a CRLF, and the version is then refused.
        self.assertEqual(proc.stdout, f"{expected}\n".encode("ascii"))
        self.assertTrue(expected.endswith("-dev.57"), expected)

    def test_what_is_no_build_number(self):
        for value in ("-1", "x", "", "+3", " 3", "٣"):
            with self.subTest(value=value):
                proc = self.build("--development-version", value)
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertIn(b"not a build number", proc.stderr)


if __name__ == "__main__":
    unittest.main()
