"""The version a build of no release prepares, read from the changelog — ADR-009 §9, 2026-11-09.

The computation (``cortex_cli.devversion``); a source checkout's ``--version``, which reads it from
its own ``CHANGELOG.md``; and ``cli/build.py --development-version``, which stamps the CI's builds.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the harness, not a package named tests
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "core")]

import cli_harness as harness  # noqa: E402
from cortex_cli import devversion  # noqa: E402
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

    def test_a_byte_order_mark_is_no_part_of_the_text(self):
        text = "\ufeff## [Unreleased]\n\n### Added\n- a thing\n\n## [2.3.4] - 2026-11-01 — A Name _(Released)_\n"
        self.assertEqual(prepared(text), "2.4.0")

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
